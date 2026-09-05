"""Deterministic answer grounding, coverage, and recoverable-failure checks."""

from __future__ import annotations

import re
from typing import Any

from app.agent.evidence import extract_numeric_values
from app.agent.scope import TEAM_INTENTS

FACTUAL_INTENTS = frozenset(
    {
        "team_roster",
        "player_hero_pool",
        "team_draft_tendencies",
        "team_opening_sequences",
        "team_combo_performance",
        "recent_team_trends",
        "hero_relationships",
        "hero_bp_stats",
        "meta_heroes",
        "draft_prediction",
        "draft_simulation",
        "lineup_recommendation",
        "lineup_score",
        "battle_draft",
        "patch_notes",
        "game_reference",
    }
)
NON_FACTUAL_INTENTS = frozenset({"coach_capabilities", "unsupported"})
ADVANTAGE_METRICS = frozenset(
    {"expected_advantage", "robust_advantage", "blue_advantage", "red_advantage"}
)
WIN_PROBABILITY_PATTERNS = (
    re.compile(r"\bwin probability\b", re.IGNORECASE),
    re.compile(r"\bcalibrated win\b", re.IGNORECASE),
    re.compile(r"获胜概率"),
    re.compile(r"胜率预测"),
    re.compile(r"有\s*\d+(?:\.\d+)?%\s*的?胜率"),
)
PERCENT_CLAIM = re.compile(r"(?P<value>\d{1,4}(?:\.\d+)?)\s*%", re.IGNORECASE)
NAMED_PERCENT = re.compile(
    r"(?P<subject>[A-Za-z0-9\u4e00-\u9fff.《》·._ -]{1,40}?)\s*"
    r"(?:has|have|is|was|with|at|的)\s*(?:an?\s+)?"
    r"(?P<value>\d{1,4}(?:\.\d+)?)\s*%",
    re.IGNORECASE,
)
ROUNDING_TOLERANCE = 0.015
IMPOSSIBLE_PERCENT = 100.0001
METRIC_HINTS: tuple[tuple[re.Pattern[str], frozenset[str]], ...] = (
    (
        re.compile(
            r"descriptive\s+(?:battle\s+)?win rate|历史胜率|描述性胜率",
            re.IGNORECASE,
        ),
        frozenset({"descriptive_win_rate", "descriptive_battle_win_rate"}),
    ),
    (re.compile(r"pick rate|选取率|选用率", re.IGNORECASE), frozenset({"pick_rate"})),
    (re.compile(r"ban rate|禁用率", re.IGNORECASE), frozenset({"ban_rate"})),
    (re.compile(r"presence rate|登场率|在场率", re.IGNORECASE), frozenset({"presence_rate"})),
    (re.compile(r"priority rate|优先级", re.IGNORECASE), frozenset({"early_priority_rate"})),
    (
        re.compile(r"selection (?:likelihood|probability)|选择概率", re.IGNORECASE),
        frozenset(
            {
                "policy_probability",
                "probability",
                "smoothed_probability_given_legal",
            }
        ),
    ),
    (
        re.compile(r"relative (?:lineup )?advantage|相对阵容优势", re.IGNORECASE),
        ADVANTAGE_METRICS,
    ),
)

LIMITATION_PHRASES = (
    "do not know",
    "no verified",
    "not enough verified",
    "cannot verify",
    "no verified source",
    "do not have a verified source",
    "this application does not",
    "does not estimate",
    "无法核实",
    "无法查询",
    "没有核实",
    "没有已核实",
    "没有经过核实",
    "无法验证",
    "不知道",
    "缺少证据",
    "没有已验证",
)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def looks_like_limitation(answer: str) -> bool:
    lowered = answer.casefold()
    return any(phrase in lowered for phrase in LIMITATION_PHRASES)


def _normalize_subject(value: str | None) -> str:
    return re.sub(r"\s+", "", (value or "").casefold())


def _rate_from_percent_or_fraction(value: float) -> float:
    if value > 1.0:
        return value / 100.0
    return value


def _claim_metrics(answer: str, start: int, end: int) -> frozenset[str]:
    context = answer[max(0, start - 20) : min(len(answer), end + 45)]
    for pattern, metrics in METRIC_HINTS:
        if pattern.search(context):
            return metrics
    return frozenset()


def extract_percent_claims(answer: str) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    seen: set[tuple[str, float]] = set()
    named_spans: list[tuple[int, int]] = []
    for match in NAMED_PERCENT.finditer(answer):
        subject = (match.group("subject") or "").strip()
        value = float(match.group("value"))
        key = (_normalize_subject(subject), value)
        if key in seen:
            continue
        seen.add(key)
        named_spans.append(match.span())
        claims.append(
            {
                "subject": subject,
                "value": value,
                "raw": match.group(0),
                "metrics": _claim_metrics(answer, match.start(), match.end()),
            }
        )
    for match in PERCENT_CLAIM.finditer(answer):
        if any(start <= match.start() and match.end() <= end for start, end in named_spans):
            continue
        subject = ""
        value = float(match.group("value"))
        key = (_normalize_subject(subject), value)
        if key in seen:
            continue
        seen.add(key)
        claims.append(
            {
                "subject": subject,
                "value": value,
                "raw": match.group(0),
                "metrics": _claim_metrics(answer, match.start(), match.end()),
            }
        )
    return claims


def _evidence_numbers(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    numbers: list[dict[str, Any]] = []
    for record in records:
        if record.get("status") == "failed":
            continue
        for item in record.get("numeric_values") or extract_numeric_values(
            record.get("payload") or {}
        ):
            numbers.append({**item, "evidence_id": record.get("id")})
        payload = record.get("payload") or {}
        if payload.get("interpretation"):
            numbers.append(
                {
                    "subject": None,
                    "metric": "interpretation",
                    "value": None,
                    "unit": "text",
                    "evidence_id": record.get("id"),
                    "interpretation": payload.get("interpretation"),
                }
            )
    return numbers


def _value_matches(claim_percent: float, evidence_value: float) -> bool:
    claim_rate = _rate_from_percent_or_fraction(claim_percent)
    evidence_rate = _rate_from_percent_or_fraction(evidence_value)
    return abs(claim_rate - evidence_rate) <= ROUNDING_TOLERANCE


def _subject_matches(claim_subject: str, evidence_subject: str | None) -> bool:
    if not claim_subject or not evidence_subject:
        return False
    left = _normalize_subject(claim_subject)
    right = _normalize_subject(evidence_subject)
    return left == right or left in right or right in left


def _has_usable_evidence(records: list[dict[str, Any]]) -> bool:
    return any(record.get("success") and record.get("status") in {"ok", "sparse"} for record in records)


def mentions_win_probability(answer: str) -> bool:
    return any(pattern.search(answer) for pattern in WIN_PROBABILITY_PATTERNS)


def advantage_evidence_only(records: list[dict[str, Any]]) -> bool:
    metrics = {
        item.get("metric")
        for record in records
        for item in record.get("numeric_values") or []
    }
    metrics.discard(None)
    return bool(metrics) and metrics <= ADVANTAGE_METRICS


def validate_answer(
    answer: str,
    *,
    intents: list[str],
    evidence_records: list[dict[str, Any]],
    truncated: bool = False,
) -> dict[str, Any]:
    """Return a validation decision for a candidate answer."""
    issues: list[str] = []
    repairable = False
    if truncated:
        issues.append("truncated_output")
        repairable = True
    factual = any(intent in FACTUAL_INTENTS for intent in intents)
    if not factual:
        return {
            "valid": not issues,
            "repairable": repairable,
            "issues": issues,
            "status": "complete" if not issues else "partial",
        }
    if looks_like_limitation(answer) and not extract_percent_claims(answer):
        return {
            "valid": True,
            "repairable": False,
            "issues": [],
            "status": "partial" if not _has_usable_evidence(evidence_records) else "complete",
        }
    if factual and not _has_usable_evidence(evidence_records) and not looks_like_limitation(answer):
        issues.append("factual_claim_without_evidence")
        repairable = True
    numbers = _evidence_numbers(evidence_records)
    claims = extract_percent_claims(answer)
    for claim in claims:
        value = claim["value"]
        if value >= IMPOSSIBLE_PERCENT:
            issues.append("impossible_percent")
            repairable = True
            continue
        matching_values = [
            item
            for item in numbers
            if item.get("value") is not None and _value_matches(value, float(item["value"]))
        ]
        if not matching_values:
            issues.append("unsupported_percent")
            repairable = True
            continue
        claimed_metrics = claim.get("metrics") or frozenset()
        if claimed_metrics and not any(
            item.get("metric") in claimed_metrics for item in matching_values
        ):
            issues.append("percent_metric_mismatch")
            repairable = True
            continue
        subject = claim.get("subject") or ""
        if subject and not any(
            _subject_matches(subject, item.get("subject")) for item in matching_values
        ):
            issues.append("percent_subject_mismatch")
            repairable = True
    if mentions_win_probability(answer) and advantage_evidence_only(evidence_records):
        issues.append("advantage_described_as_win_probability")
        repairable = True
    if "impossible_percent" in issues or "unsupported_percent" in issues:
        # A contradictory statistic cannot be finalized as supported.
        pass
    status = "complete"
    if issues:
        status = "partial"
    return {
        "valid": not issues,
        "repairable": repairable and bool(issues),
        "issues": issues,
        "status": status,
    }


def coverage_from_plan(
    plan: dict[str, Any],
    evidence_records: list[dict[str, Any]],
    *,
    validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Describe which requested parts were answered, missing, or not applicable."""
    requested = list(plan.get("subquestions") or [])
    required = list(plan.get("required_groups") or [])
    successful_tools = {
        record.get("tool")
        for record in evidence_records
        if record.get("success") and record.get("status") in {"ok", "sparse"}
    }
    answered: list[str] = []
    missing: list[str] = []
    not_applicable: list[str] = []
    for group in required:
        tools = set(group.get("tools") or [])
        label = group.get("label") or ",".join(sorted(tools))
        if tools & successful_tools:
            answered.append(label)
        elif group.get("optional"):
            not_applicable.append(label)
        else:
            missing.append(label)
    for extra in plan.get("uncovered_asks") or []:
        missing.append(str(extra))
    if validation and validation.get("issues"):
        missing.extend(validation["issues"])
    return {
        "requested": requested,
        "answered": answered,
        "missing": missing,
        "not_applicable": not_applicable,
    }


def missing_context_kind(intents: list[str], *, has_draft_state: bool, conversation_ref: dict[str, Any] | None) -> str | None:
    """Return a clarification kind when the turn cannot do useful work."""
    reference = conversation_ref or {}
    if reference.get("needs_clarification") == "other_team":
        return "other_team"
    historical = [intent for intent in intents if intent in TEAM_INTENTS or intent not in {"draft_prediction", "draft_simulation", "lineup_recommendation", "lineup_score"}]
    draft_only = [intent for intent in intents if intent in {"draft_prediction", "draft_simulation", "lineup_recommendation", "lineup_score"}]
    if draft_only and not has_draft_state and not historical:
        return "missing_board"
    return None


def limitation_answer(
    *,
    chinese: bool,
    issues: list[str],
    evidence_records: list[dict[str, Any]],
    uncovered_asks: list[str] | None = None,
) -> str:
    """Deterministic supported partial result when repair is exhausted."""
    usable = [
        record
        for record in evidence_records
        if record.get("success") and record.get("status") in {"ok", "sparse"}
    ]
    if chinese:
        if "advantage_described_as_win_probability" in issues:
            return "现有证据只提供相对阵容优势，不能当成对局胜率或获胜概率。"
        if usable and issues:
            return "已核实部分证据，但其余数字或比较无法从本次工具结果中得到支持。"
        if uncovered_asks:
            return "这个问题包含的部分超出了一次分析能覆盖的范围，请拆成更具体的问题。"
        return "目前没有已核实的证据来支持这个事实结论，所以我不能给出具体数字或结论。"
    if "advantage_described_as_win_probability" in issues:
        return "The available evidence reports relative lineup advantage, not battle-win probability."
    if usable and issues:
        return "Some evidence was verified, but the remaining numbers or comparisons are not supported by this turn's tool results."
    if uncovered_asks:
        return "This request includes more parts than one analysis can cover. Please narrow the question."
    return "There is no verified evidence for this factual claim, so I cannot give a specific figure or conclusion."


def clarification_answer(kind: str, *, chinese: bool) -> str:
    if kind == "other_team":
        return (
            "你说的另一支队伍是蓝方还是红方？"
            if chinese
            else "Which team do you mean by the other team, Blue or Red?"
        )
    return (
        "请先选择当前 BP 面板上的双方队伍，我才能分析这一手。"
        if chinese
        else "Please attach the live draft board so I can analyze this action."
    )
