"""Build a self-contained interactive HTML report from BP statistics JSONL.

No third-party Python or JavaScript packages are required.

Examples:

    python3 analysis/visualize_bp_statistics.py
    python3 analysis/visualize_bp_statistics.py \
        --input-dir analysis/outputs \
        --output analysis/outputs/bp_statistics_report.html \
        --min-selections 2
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

from common import REPO_ROOT

DEFAULT_INPUT_DIR = REPO_ROOT / "analysis" / "outputs"
DEFAULT_OUTPUT = DEFAULT_INPUT_DIR / "bp_statistics_report.html"

STAT_FILES = {
    "ban_response": "ban_response_stats.jsonl",
    "pick_synergy": "pick_synergy_stats.jsonl",
    "counter_pick": "counter_pick_stats.jsonl",
    "counter_ban": "counter_ban_stats.jsonl",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON on line {line_number} of {path}: {exc}"
                ) from exc
            if not isinstance(row, dict):
                raise ValueError(
                    f"Line {line_number} of {path} is not a JSON object"
                )
            rows.append(row)
    return rows


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_row(
    relation: str,
    row: dict[str, Any],
) -> dict[str, Any]:
    if relation == "ban_response":
        source_id = int(row.get("trigger_hero_id") or 0)
        source_name = row.get("trigger_hero_name") or str(source_id)
        target_id = int(row.get("response_hero_id") or 0)
        target_name = row.get("response_hero_name") or str(target_id)
        response_action = row.get("response_action") or ""
        relationship = f"{source_name} → {response_action} {target_name}"
        side = row.get("response_side")
        slot = row.get("response_slot")
        trigger_side = row.get("trigger_side")
        trigger_slot = row.get("trigger_slot")
        win_rate = row.get("response_team_battle_win_rate")
        scope = row.get("response_scope") or ""
        scope_description = {
            "opponent_next_ban": "opponent's next ban",
            "banning_team_later_pick": "banning team's later pick",
            "opponent_later_pick": "opponent's later pick",
        }.get(scope, scope)
        context_description = (
            scope_description
            if row.get("context_level") == "overall"
            else (
                f"{scope_description} · after {trigger_side or 'unknown'} "
                f"ban {trigger_slot or '?'} · {side or 'unknown'} "
                f"{response_action} {slot or '?'}"
            )
        )
    else:
        source_id = int(row.get("ally_hero_id") or row.get("opponent_hero_id") or 0)
        source_name = (
            row.get("ally_hero_name")
            or row.get("opponent_hero_name")
            or str(source_id)
        )
        target_id = int(row.get("candidate_hero_id") or 0)
        target_name = row.get("candidate_hero_name") or str(target_id)
        response_action = row.get("response_action") or ""
        if relation == "pick_synergy":
            relationship = f"{source_name} + {target_name}"
        elif relation == "counter_pick":
            relationship = f"vs {source_name} → pick {target_name}"
        else:
            relationship = f"vs {source_name} → ban {target_name}"
        side = row.get("response_side")
        slot = row.get("response_slot")
        win_rate = row.get("battle_win_rate_when_selected")
        scope = ""
        context_description = (
            "all slots"
            if row.get("context_level") == "overall"
            else f"{side or 'unknown'} {response_action} {slot or '?'}"
        )

    return {
        "relation": relation,
        "context_level": row.get("context_level") or "overall",
        "is_peak_battle": bool(row.get("is_peak_battle")),
        "source_hero_id": source_id,
        "source_hero_name": source_name,
        "target_hero_id": target_id,
        "target_hero_name": target_name,
        "relationship": relationship,
        "response_action": response_action,
        "response_scope": scope,
        "side": side,
        "slot": slot,
        "context_description": context_description,
        "context_count": int(
            row.get("context_decision_count")
            or row.get("trigger_event_count")
            or 0
        ),
        "opportunities": int(row.get("legal_opportunity_count") or 0),
        "selections": int(row.get("selection_count") or 0),
        "availability_rate": as_float(row.get("availability_rate")),
        "raw_probability": as_float(
            row.get("raw_probability_given_legal")
        ),
        "smoothed_probability": as_float(
            row.get("smoothed_probability_given_legal")
        ),
        "baseline_probability": as_float(
            row.get("baseline_probability_given_legal")
        ),
        "smoothed_lift": as_float(row.get("smoothed_lift")),
        "ci_low": as_float(row.get("probability_ci95_low")),
        "ci_high": as_float(row.get("probability_ci95_high")),
        "win_rate": as_float(win_rate),
        "win_count": int(
            row.get("battle_win_count_when_selected")
            or row.get("response_team_battle_win_count")
            or 0
        ),
        "legal_overrides": int(row.get("legal_override_count") or 0),
        "flagged_selections": int(
            row.get("quality_flagged_selection_count") or 0
        ),
    }


def load_statistics(
    input_dir: Path,
    *,
    min_selections: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    normalized: list[dict[str, Any]] = []
    source_counts: dict[str, int] = {}
    for relation, filename in STAT_FILES.items():
        path = input_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Statistics JSONL not found: {path}")
        rows = read_jsonl(path)
        source_counts[relation] = len(rows)
        normalized.extend(
            normalize_row(relation, row)
            for row in rows
            if int(row.get("selection_count") or 0) >= min_selections
        )
    return normalized, source_counts


def safe_json_for_script(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("</", "<\\/")


def build_html(
    *,
    data: list[dict[str, Any]],
    source_counts: dict[str, int],
    input_dir: Path,
    min_selections: int,
) -> str:
    embedded_data = safe_json_for_script(data)
    embedded_counts = safe_json_for_script(source_counts)
    source_caption = html.escape(str(input_dir))

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>KPL BP Patterns</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f5f6f8;
      --surface: #ffffff;
      --surface-2: #eef1f5;
      --text: #18202a;
      --muted: #687386;
      --border: #d9dee7;
      --accent: #2869d8;
      --accent-soft: #dbe8ff;
      --good: #16784b;
      --warn: #9a5b00;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #11151b;
        --surface: #191f28;
        --surface-2: #222a35;
        --text: #eef2f8;
        --muted: #9aa7b8;
        --border: #303a48;
        --accent: #70a4ff;
        --accent-soft: #20375d;
        --good: #6dd5a4;
        --warn: #e9ae5e;
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI",
        sans-serif;
    }}
    main {{
      max-width: 1440px;
      margin: 0 auto;
      padding: 28px;
    }}
    h1 {{ margin: 0; font-size: 24px; }}
    .subtitle {{
      margin: 6px 0 22px;
      color: var(--muted);
    }}
    .controls {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 12px;
      padding: 16px;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
    }}
    label {{
      display: grid;
      gap: 5px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
    }}
    select, input {{
      width: 100%;
      padding: 8px 10px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--surface-2);
      color: var(--text);
    }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
      margin: 16px 0;
    }}
    .metric {{
      padding: 14px 16px;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
    }}
    .metric .value {{ font-size: 22px; font-weight: 700; }}
    .metric .label {{ color: var(--muted); font-size: 12px; }}
    .layout {{
      display: grid;
      grid-template-columns: minmax(420px, 0.9fr) minmax(600px, 1.4fr);
      gap: 16px;
      align-items: start;
    }}
    section {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      overflow: hidden;
    }}
    section h2 {{
      margin: 0;
      padding: 15px 18px 4px;
      font-size: 16px;
    }}
    .caption {{
      padding: 0 18px 12px;
      color: var(--muted);
      font-size: 12px;
    }}
    #bars {{ padding: 4px 18px 18px; }}
    .bar-row {{
      display: grid;
      grid-template-columns: minmax(170px, 1fr) minmax(130px, 1.4fr) 72px;
      gap: 10px;
      align-items: center;
      margin: 9px 0;
    }}
    .bar-label {{
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .bar-track {{
      height: 15px;
      border-radius: 4px;
      background: var(--surface-2);
      overflow: hidden;
    }}
    .bar-fill {{
      height: 100%;
      min-width: 2px;
      background: var(--accent);
    }}
    .bar-value {{
      text-align: right;
      font-variant-numeric: tabular-nums;
      font-weight: 650;
    }}
    .table-wrap {{ overflow: auto; max-height: 720px; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
    }}
    th {{
      position: sticky;
      top: 0;
      z-index: 1;
      padding: 9px 10px;
      text-align: left;
      background: var(--surface-2);
      border-bottom: 1px solid var(--border);
      white-space: nowrap;
    }}
    td {{
      padding: 8px 10px;
      border-bottom: 1px solid var(--border);
      vertical-align: top;
      font-variant-numeric: tabular-nums;
    }}
    tbody tr:hover {{ background: var(--surface-2); }}
    .relationship {{ min-width: 180px; font-weight: 600; }}
    .low-support {{ color: var(--warn); }}
    .positive {{ color: var(--good); }}
    .empty {{
      padding: 24px;
      color: var(--muted);
      text-align: center;
    }}
    footer {{
      margin-top: 14px;
      color: var(--muted);
      font-size: 12px;
    }}
    @media (max-width: 980px) {{
      .controls {{ grid-template-columns: repeat(2, 1fr); }}
      .summary {{ grid-template-columns: repeat(2, 1fr); }}
      .layout {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
<main>
  <h1>KPL BP Patterns</h1>
  <p class="subtitle">
    See which heroes tend to be banned or picked together. A hero only counts
    as an option when the team was actually allowed to choose it.
  </p>

  <div class="controls">
    <label>What do you want to explore?
      <select id="relation">
        <option value="ban_response">What happens after a ban?</option>
        <option value="pick_synergy">Heroes picked together</option>
        <option value="counter_pick">Picks into enemy heroes</option>
        <option value="counter_ban">Bans after enemy picks</option>
      </select>
    </label>
    <label id="responseScopeLabel">Whose follow-up?
      <select id="responseScope">
        <option value="opponent_next_ban">Opponent's next ban</option>
        <option value="banning_team_later_pick">Banning team's later picks</option>
        <option value="opponent_later_pick">Opponent's later picks</option>
        <option value="all">Show all three</option>
      </select>
    </label>
    <label>How specific?
      <select id="context">
        <option value="overall">All sides and pick slots</option>
        <option value="slot_context">Break down by side and slot</option>
      </select>
    </label>
    <label>Which side responds?
      <select id="side">
        <option value="all">All</option>
        <option value="blue">Blue</option>
        <option value="red">Red</option>
      </select>
    </label>
    <label>Sort results by
      <select id="metric">
        <option value="smoothed_lift">More common than usual</option>
        <option value="smoothed_probability">Most likely when available</option>
        <option value="selections">Most often seen</option>
        <option value="win_rate">Best battle win rate</option>
      </select>
    </label>
    <label>Seen at least this many times
      <input id="support" type="number" min="1" value="{min_selections}">
    </label>
    <label>Number of results to show
      <select id="topN">
        <option value="10">10</option>
        <option value="20" selected>20</option>
        <option value="50">50</option>
        <option value="100">100</option>
        <option value="all">Show all</option>
      </select>
    </label>
  </div>

  <div class="summary">
    <div class="metric"><div class="value" id="visibleCount">0</div><div class="label">patterns matching your filters</div></div>
    <div class="metric"><div class="value" id="medianLift">—</div><div class="label">typical increase over normal</div></div>
    <div class="metric"><div class="value" id="totalSelections">0</div><div class="label">times the shown patterns happened</div></div>
    <div class="metric"><div class="value" id="overrideCount">0</div><div class="label">picks outside our estimated legal pool</div></div>
  </div>

  <div class="layout">
    <section>
      <h2 id="chartTitle">Top BP patterns</h2>
      <div class="caption" id="chartCaption"></div>
      <div id="bars"></div>
    </section>
    <section>
      <h2>Pattern details</h2>
      <div class="caption">
        “Compared with normal” shows how much more or less often this happened
        than the hero's usual rate. Wider ranges mean less certainty.
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Pattern</th>
              <th>When</th>
              <th>Times chosen / chances</th>
              <th>How often available</th>
              <th>Chance when available</th>
              <th>Usual chance</th>
              <th>Compared with normal</th>
              <th>Battle win rate</th>
              <th>Likely range</th>
            </tr>
          </thead>
          <tbody id="results"></tbody>
        </table>
      </div>
    </section>
  </div>

  <footer>
    Data source: {source_caption} · Rows loaded:
    <span id="sourceCounts"></span>. These are patterns seen in past drafts;
    they do not prove that one hero caused another choice or a win.
  </footer>
</main>

<script>
const DATA = {embedded_data};
const SOURCE_COUNTS = {embedded_counts};
const RELATION_LABELS = {{
  ban_response: "What happens after a ban?",
  pick_synergy: "Heroes picked together",
  counter_pick: "Picks into enemy heroes",
  counter_ban: "Bans after enemy picks"
}};
const RESPONSE_SCOPE_LABELS = {{
  opponent_next_ban: "opponent's next ban",
  banning_team_later_pick: "banning team's later picks",
  opponent_later_pick: "opponent's later picks"
}};
const METRIC_LABELS = {{
  smoothed_lift: "how much more common than usual",
  smoothed_probability: "chance when the hero was available",
  selections: "how many times it happened",
  win_rate: "battle win rate"
}};

const controls = ["relation", "responseScope", "context", "side", "metric", "support", "topN"];
for (const id of controls) {{
  document.getElementById(id).addEventListener("input", render);
  document.getElementById(id).addEventListener("change", render);
}}

function pct(value) {{
  return value == null ? "—" : `${{(value * 100).toFixed(1)}}%`;
}}

function num(value, digits = 2) {{
  return value == null || Number.isNaN(value) ? "—" : Number(value).toFixed(digits);
}}

function metricValue(row, metric) {{
  const value = row[metric];
  return value == null ? -Infinity : Number(value);
}}

function metricText(row, metric) {{
  if (metric === "smoothed_probability" || metric === "win_rate") {{
    return pct(row[metric]);
  }}
  if (metric === "smoothed_lift") {{
    return row[metric] == null ? "—" : `${{num(row[metric])}}×`;
  }}
  return String(row[metric] ?? 0);
}}

function median(values) {{
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2
    ? sorted[middle]
    : (sorted[middle - 1] + sorted[middle]) / 2;
}}

function escapeHtml(value) {{
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}}

function render() {{
  const relation = document.getElementById("relation").value;
  const responseScope = document.getElementById("responseScope").value;
  const context = document.getElementById("context").value;
  const side = document.getElementById("side").value;
  const metric = document.getElementById("metric").value;
  const minSupport = Math.max(1, Number(document.getElementById("support").value) || 1);
  const topNValue = document.getElementById("topN").value;
  const topN = topNValue === "all"
    ? Number.POSITIVE_INFINITY
    : Math.max(5, Number(topNValue) || 20);

  document.getElementById("side").disabled = context === "overall";
  const scopeControl = document.getElementById("responseScopeLabel");
  scopeControl.hidden = relation !== "ban_response";

  let rows = DATA.filter(row =>
    row.relation === relation &&
    (
      relation !== "ban_response" ||
      responseScope === "all" ||
      row.response_scope === responseScope
    ) &&
    row.context_level === context &&
    !row.is_peak_battle &&
    row.selections >= minSupport &&
    (context === "overall" || side === "all" || row.side === side) &&
    metricValue(row, metric) !== -Infinity
  );
  rows.sort((a, b) =>
    metricValue(b, metric) - metricValue(a, metric) ||
    b.selections - a.selections ||
    a.relationship.localeCompare(b.relationship)
  );

  const shown = rows.slice(0, topN);
  document.getElementById("visibleCount").textContent = rows.length.toLocaleString();
  const lifts = shown.map(row => row.smoothed_lift).filter(value => value != null);
  const med = median(lifts);
  document.getElementById("medianLift").textContent = med == null ? "—" : `${{num(med)}}×`;
  document.getElementById("totalSelections").textContent =
    shown.reduce((sum, row) => sum + row.selections, 0).toLocaleString();
  document.getElementById("overrideCount").textContent =
    shown.reduce((sum, row) => sum + row.legal_overrides, 0).toLocaleString();

  const chartTitle = `${{RELATION_LABELS[relation]}} ranked by ${{METRIC_LABELS[metric]}}`;
  document.getElementById("chartTitle").textContent = chartTitle;
  const scopeCaption = relation === "ban_response"
    ? ` · ${{responseScope === "all" ? "all three follow-up groups" : RESPONSE_SCOPE_LABELS[responseScope]}}`
    : "";
  document.getElementById("chartCaption").textContent =
    `Normal BP battles${{scopeCaption}} · ${{context === "overall" ? "all sides and slots" : side === "all" ? "all side and slot combinations" : side + " side"}} · only patterns seen at least ${{minSupport}} times`;

  const bars = document.getElementById("bars");
  if (!shown.length) {{
    bars.innerHTML = '<div class="empty">No relationships match these filters.</div>';
  }} else {{
    const maximum = Math.max(...shown.map(row => Math.max(0, metricValue(row, metric))), 0.000001);
    bars.innerHTML = shown.map(row => {{
      const value = Math.max(0, metricValue(row, metric));
      const width = Math.max(0.8, 100 * value / maximum);
      const displayLabel = relation === "ban_response" && responseScope === "all"
        ? `${{row.relationship}} · ${{row.context_description}}`
        : row.relationship;
      return `<div class="bar-row" title="${{escapeHtml(row.context_description)}} · ${{row.selections}}/${{row.opportunities}}">
        <div class="bar-label">${{escapeHtml(displayLabel)}}</div>
        <div class="bar-track"><div class="bar-fill" style="width:${{width}}%"></div></div>
        <div class="bar-value">${{metricText(row, metric)}}</div>
      </div>`;
    }}).join("");
  }}

  const tbody = document.getElementById("results");
  if (!shown.length) {{
    tbody.innerHTML = "";
  }} else {{
    tbody.innerHTML = shown.map(row => {{
      const supportClass = row.selections < 5 ? "low-support" : "";
      const liftClass = row.smoothed_lift != null && row.smoothed_lift > 1 ? "positive" : "";
      return `<tr>
        <td class="relationship">${{escapeHtml(row.relationship)}}</td>
        <td>${{escapeHtml(row.context_description)}}</td>
        <td class="${{supportClass}}">${{row.selections}} / ${{row.opportunities}}</td>
        <td>${{pct(row.availability_rate)}}</td>
        <td>${{pct(row.smoothed_probability)}}</td>
        <td>${{pct(row.baseline_probability)}}</td>
        <td class="${{liftClass}}">${{row.smoothed_lift == null ? "—" : num(row.smoothed_lift) + "×"}}</td>
        <td>${{pct(row.win_rate)}}</td>
        <td>${{pct(row.ci_low)}}–${{pct(row.ci_high)}}</td>
      </tr>`;
    }}).join("");
  }}
}}

document.getElementById("sourceCounts").textContent = Object.entries(SOURCE_COUNTS)
  .map(([key, value]) => `${{RELATION_LABELS[key]}} ${{value.toLocaleString()}}`)
  .join(" · ");
render();
</script>
</body>
</html>
"""


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Visualize BP statistics JSONL as interactive HTML"
    )
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--min-selections",
        type=int,
        default=1,
        help="Exclude relationships below this support from embedded data",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.min_selections < 1:
        raise ValueError("--min-selections must be >= 1")

    data, source_counts = load_statistics(
        args.input_dir,
        min_selections=args.min_selections,
    )
    report = build_html(
        data=data,
        source_counts=source_counts,
        input_dir=args.input_dir,
        min_selections=args.min_selections,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(
        f"Wrote interactive BP statistics report with {len(data)} rows: "
        f"{args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
