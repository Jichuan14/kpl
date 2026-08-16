#!/usr/bin/env python3
"""Build a versioned Tencent hero-patch corpus for the KPL Scout.

The Tencent announcement APIs are the source of record. This importer keeps the
normalized source payload, a readable Markdown document, and an index with
source IDs/hashes. It never infers KPL effects from game-patch text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_ROOT = PROJECT_ROOT / "knowledge"
RAW_DIR = KNOWLEDGE_ROOT / "raw" / "tencent-announcements"
DOCUMENT_DIR = KNOWLEDGE_ROOT / "sources" / "official" / "patches"
INDEX_PATH = KNOWLEDGE_ROOT / "metadata" / "tencent-patch-index.json"

LIST_URL = "https://apps.game.qq.com/cmc/cross"
DETAIL_URL = "https://apps.game.qq.com/wmp/v3.1/public/searchNews.php"
TOKEN = "234ce0aef3020cb83887883877b64869"
SERVICE_ID = 18
SOURCE = "web_pc"
PATCH_TITLE_MARKERS = ("版本更新", "英雄平衡", "平衡性调整", "英雄调整")


class MarkdownTextExtractor(HTMLParser):
    """Turn announcement HTML into readable, minimally formatted text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.buffer: list[str] = []
        self.heading_level: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"h1", "h2", "h3", "h4"}:
            self._flush()
            self.heading_level = int(tag[1])
        elif tag == "li":
            self._flush()
            self.buffer.append("- ")
        elif tag == "br":
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in {"p", "div", "blockquote", "li", "h1", "h2", "h3", "h4"}:
            self._flush()
            if tag.startswith("h") and len(tag) == 2:
                self.heading_level = None

    def handle_data(self, data: str) -> None:
        text = re.sub(r"\s+", " ", data).strip()
        if text:
            self.buffer.append(text)

    def _flush(self) -> None:
        text = " ".join(self.buffer).strip()
        self.buffer = []
        if not text:
            return
        if self.heading_level is not None and not text.startswith("- "):
            text = "#" * self.heading_level + " " + text
        self.lines.append(text)

    def markdown(self) -> str:
        self._flush()
        output: list[str] = []
        for line in self.lines:
            if line.startswith("#") and output and output[-1] != "":
                output.append("")
            output.append(line)
            if line.startswith("#"):
                output.append("")
        return "\n".join(output).strip() + "\n"


def request_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": "Draft-Atlas-KPL-Scout/1.0"})
    with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed HTTPS endpoints
        payload = response.read().decode("utf-8")
    payload = payload.strip()
    if payload.startswith("var searchObj="):
        payload = payload[len("var searchObj=") :].rstrip(";")
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise ValueError("Announcement API did not return an object")
    return parsed


def announcement_list(start: int, page_size: int) -> list[dict[str, Any]]:
    timestamp = int(time.time())
    sign = hashlib.md5(f"{TOKEN}{SOURCE}{SERVICE_ID}{timestamp}".encode()).hexdigest()
    query = urlencode(
        {
            "serviceId": SERVICE_ID,
            "filter": "channel",
            "sortby": "sIdxTime",
            "source": SOURCE,
            "limit": page_size,
            "logic": "or",
            "typeids": 1,
            "withtop": "no",
            "chanid": 1762,
            "start": start,
            "exclusiveChannel": 4,
            "exclusiveChannelSign": sign,
            "time": timestamp,
        }
    )
    payload = request_json(f"{LIST_URL}?{query}")
    items = payload.get("data", {}).get("items", [])
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def parse_date(value: Any) -> date | None:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def is_patch(item: dict[str, Any]) -> bool:
    title = str(item.get("sTitle", ""))
    return any(marker in title for marker in PATCH_TITLE_MARKERS)


def detail(announcement_id: str) -> dict[str, Any]:
    query = urlencode({"p0": SERVICE_ID, "source": SOURCE, "id": announcement_id})
    payload = request_json(f"{DETAIL_URL}?{query}")
    message = payload.get("msg", {})
    if not isinstance(message, dict):
        raise ValueError(f"Announcement {announcement_id} has no detail payload")
    return message


def slug(title: str, announcement_id: str) -> str:
    ascii_slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return ascii_slug[:72] or f"announcement-{announcement_id}"


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def normalize_document(message: dict[str, Any], collected_at: str) -> tuple[str, dict[str, Any]]:
    announcement_id = str(message.get("iNewsId", ""))
    title = str(message.get("sTitle", "")).strip()
    published_at = str(message.get("sCreated", "")).strip()
    html = str(message.get("sContent", ""))
    extractor = MarkdownTextExtractor()
    extractor.feed(html)
    body = extractor.markdown()
    source_url = f"{DETAIL_URL}?{urlencode({'p0': SERVICE_ID, 'source': SOURCE, 'id': announcement_id})}"
    raw_payload = {
        "id": announcement_id,
        "title": title,
        "published_at": published_at,
        "author": message.get("sAuthor", ""),
        "game_version": message.get("sGameVersion", "") or None,
        "content_html": html,
    }
    raw_json = json.dumps(raw_payload, ensure_ascii=False, indent=2) + "\n"
    raw_hash = hashlib.sha256(raw_json.encode()).hexdigest()
    yaml = "\n".join(
        [
            "---",
            f'document_id: "tencent-patch-{announcement_id}"',
            "document_type: hero_or_version_patch",
            'publisher: "王者荣耀 / Tencent Games"',
            "source_class: primary",
            f'announcement_id: "{announcement_id}"',
            f"title: {json.dumps(title, ensure_ascii=False)}",
            f"published_at: {json.dumps(published_at + '+08:00', ensure_ascii=False)}",
            f"game_version: {json.dumps(raw_payload['game_version'], ensure_ascii=False)}",
            f"source_url: {json.dumps(source_url, ensure_ascii=False)}",
            f"raw_payload_sha256: {json.dumps(raw_hash)}",
            f"collected_at: {json.dumps(collected_at)}",
            "---",
            "",
            f"# {title}",
            "",
            "## Source boundary",
            "",
            "This is a normalized primary-source announcement. It establishes the",
            "announced game changes only; it does not establish a KPL pick/ban, win-rate,",
            "or optimal-draft effect without separate date-scoped KPL data evidence.",
            "",
            "## Announcement content",
            "",
            body.rstrip(),
            "",
        ]
    )
    return yaml, {"raw_json": raw_json, "raw_hash": raw_hash, "source_url": source_url}


def load_existing_records() -> dict[str, dict[str, Any]]:
    if not INDEX_PATH.exists():
        return {}
    try:
        payload = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        records = payload.get("documents", [])
        return {
            str(record["announcement_id"]): record
            for record in records
            if isinstance(record, dict) and record.get("announcement_id")
        }
    except (OSError, ValueError, TypeError):
        return {}


def sync(
    since: date,
    until: date,
    page_size: int,
    pause_seconds: float,
    start_offset: int,
    max_pages: int | None,
) -> dict[str, Any]:
    selected: dict[str, dict[str, Any]] = {}
    start = start_offset
    pages = 0
    seen_list_ids: set[str] = set()
    while True:
        items = announcement_list(start, page_size)
        pages += 1
        print(f"Scanned announcement page {pages} (offset {start}, {len(items)} items)", flush=True)
        if not items:
            break
        page_ids = {str(item.get("iId", "")) for item in items if item.get("iId")}
        if not page_ids or page_ids.issubset(seen_list_ids):
            break
        seen_list_ids.update(page_ids)
        dates = [parse_date(item.get("sCreated")) for item in items]
        for item in items:
            published = parse_date(item.get("sCreated"))
            if published and since <= published <= until and is_patch(item):
                announcement_id = str(item.get("iId", ""))
                if announcement_id:
                    selected[announcement_id] = item
        oldest = min((value for value in dates if value), default=None)
        if oldest and oldest < since:
            break
        start += len(items)
        if max_pages is not None and pages >= max_pages:
            break
        time.sleep(pause_seconds)

    collected_at = datetime.now().astimezone().isoformat(timespec="seconds")
    records: list[dict[str, Any]] = []
    for announcement_id, item in sorted(selected.items(), key=lambda pair: str(pair[1].get("sCreated", "")), reverse=True):
        message = detail(announcement_id)
        document, raw = normalize_document(message, collected_at)
        document_name = f"{str(message.get('sCreated', 'unknown'))[:10]}-{announcement_id}-{slug(str(message.get('sTitle', '')), announcement_id)}.md"
        raw_path = RAW_DIR / f"{announcement_id}.json"
        document_path = DOCUMENT_DIR / document_name
        write_text(raw_path, raw["raw_json"])
        write_text(document_path, document)
        records.append(
            {
                "announcement_id": announcement_id,
                "title": str(message.get("sTitle", "")),
                "published_at": str(message.get("sCreated", "")),
                "game_version": message.get("sGameVersion", "") or None,
                "source_url": raw["source_url"],
                "raw_payload": str(raw_path.relative_to(KNOWLEDGE_ROOT)),
                "raw_payload_sha256": raw["raw_hash"],
                "normalized_document": str(document_path.relative_to(KNOWLEDGE_ROOT)),
            }
        )
        if len(records) % 10 == 0 or len(records) == len(selected):
            print(f"Normalized {len(records)}/{len(selected)} patch documents", flush=True)
        time.sleep(pause_seconds)

    all_records = load_existing_records()
    all_records.update({record["announcement_id"]: record for record in records})
    merged_records = sorted(
        all_records.values(),
        key=lambda record: str(record.get("published_at", "")),
        reverse=True,
    )
    index = {
        "schema_version": 1,
        "source": "Tencent announcement API",
        "source_class": "primary",
        "since": since.isoformat(),
        "until": until.isoformat(),
        "collected_at": collected_at,
        "last_run": {
            "start_offset": start_offset,
            "pages_scanned": pages,
            "max_pages": max_pages,
        },
        "document_count": len(merged_records),
        "documents": merged_records,
    }
    write_text(INDEX_PATH, json.dumps(index, ensure_ascii=False, indent=2) + "\n")
    return index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", type=date.fromisoformat, default=date(2025, 1, 1))
    parser.add_argument("--until", type=date.fromisoformat, default=date.today())
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--pause-seconds", type=float, default=0.15)
    parser.add_argument("--start-offset", type=int, default=0)
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Limit one run to a resumable batch of list pages.",
    )
    args = parser.parse_args()
    if args.until < args.since:
        parser.error("--until must not precede --since")
    if not 1 <= args.page_size <= 300:
        parser.error("--page-size must be between 1 and 300")
    if args.start_offset < 0:
        parser.error("--start-offset must not be negative")
    if args.max_pages is not None and args.max_pages < 1:
        parser.error("--max-pages must be positive")
    index = sync(
        args.since,
        args.until,
        args.page_size,
        args.pause_seconds,
        args.start_offset,
        args.max_pages,
    )
    print(
        f"Corpus now contains {index['document_count']} patch documents from "
        f"{index['since']} through {index['until']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
