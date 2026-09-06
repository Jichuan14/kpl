"""Deterministic public evidence cards from registered tool results.

Cards are built from already-executed tool payloads. They never call the
model and never invent sample sizes, dates, or metric meanings.
"""

from __future__ import annotations

from typing import Any

ANSWER_VERSION = 1

FAMILY_BY_TOOL: dict[str, str] = {
    "predict_next_draft_action": "draft_recommendation",
    "recommend_value_draft_action": "draft_recommendation",
    "simulate_future_draft": "draft_recommendation",
    "get_hero_relationships": "hero_relationships",
    "get_team_synergies": "hero_relationships",
    "get_team_draft_tendencies": "team_statistics",
    "get_team_opening_sequences": "team_statistics",
    "get_team_combo_performance": "team_statistics",
    "get_recent_team_trends": "team_statistics",
    "get_hero_bp_stats": "team_statistics",
    "get_meta_heroes": "team_statistics",
    "get_team_roster": "roster",
    "get_player_hero_pool": "roster",
    "score_current_lineup": "lineup_score",
    "search_patch_notes": "patch_notes",
    "get_battle_draft": "battle_draft",
}

TITLE_BY_FAMILY = {
    "draft_recommendation": "Draft recommendation",
    "hero_relationships": "Hero relationships",
    "team_statistics": "Team or season statistics",
    "roster": "Roster / player pool",
    "lineup_score": "Completed lineup score",
    "patch_notes": "Official patch notes",
    "battle_draft": "Recorded battle draft",
}

METRIC_DEFINITIONS = {
    "early_priority_rate": "Opening-ban plus Blue-first-pick priority, not win probability.",
    "descriptive_win_rate": "Observed historical win rate among recorded games, not a causal or calibrated win probability.",
    "smoothed_probability_given_legal": "Smoothed historical selection tendency given legal availability.",
    "policy_probability": "Historical selection likelihood from the current draft model.",
    "expected_advantage": "Relative lineup advantage, not battle-win probability.",
    "pick_rate": "Share of recorded battles in which the hero was picked.",
    "ban_rate": "Share of recorded battles in which the hero was banned.",
    "presence_rate": "Share of recorded battles in which the hero was picked or banned.",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _optional_number(value: Any) -> float | int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _percent(value: Any) -> str | None:
    number = _optional_number(value)
    if number is None:
        return None
    if 0 <= number <= 1:
        return f"{number * 100:.1f}%"
    return f"{number:.1f}%"


def _result_status(success: bool, payload: dict[str, Any], error: str) -> str:
    if not success:
        return "failed"
    count = payload.get("result_count")
    rows = (
        _as_list(payload.get("rows"))
        or _as_list(payload.get("recommendations"))
        or _as_list(payload.get("next_action_probabilities"))
        or _as_list(payload.get("results"))
    )
    collection_present = any(
        key in payload
        for key in ("rows", "recommendations", "next_action_probabilities", "results")
    )
    if count == 0 or (count is None and collection_present and not rows):
        return "empty"
    warning = _text(payload.get("warning")) or " ".join(
        _text(item) for item in _as_list(payload.get("warnings"))
    )
    sparse_markers = ("small-sample", "small sample", "sparse", "unstable", "ambiguous")
    if any(marker in warning.casefold() for marker in sparse_markers):
        return "sparse"
    if payload.get("ambiguous"):
        return "sparse"
    return "ok"


def _source(payload: dict[str, Any]) -> dict[str, Any]:
    source: dict[str, Any] = {}
    if payload.get("artifact"):
        source["kind"] = "artifact"
        source["name"] = payload.get("artifact")
    elif payload.get("source"):
        source["kind"] = "source"
        source["name"] = payload.get("source")
    if payload.get("artifact_version"):
        source["version"] = payload.get("artifact_version")
    if payload.get("index_version"):
        source["index_version"] = payload.get("index_version")
    if payload.get("source_updated_at"):
        source["updated_at"] = payload.get("source_updated_at")
    if payload.get("model_generated_at"):
        source["model_generated_at"] = payload.get("model_generated_at")
    if payload.get("model_type"):
        source["model_type"] = payload.get("model_type")
    analytical_period = {
        key: payload[key]
        for key in (
            "period_start",
            "period_end",
            "recent_match_window",
            "season_start",
            "season_end",
        )
        if payload.get(key) not in (None, "")
    }
    if analytical_period:
        source["analytical_period"] = analytical_period
    patch_dates = [
        _text(row.get("published_at"))
        for row in _as_list(payload.get("results"))
        if isinstance(row, dict) and _text(row.get("published_at"))
    ]
    if patch_dates:
        # This is the newest date present in the returned local index results,
        # not a claim about the latest official patch outside that index.
        source["latest_indexed_date"] = max(patch_dates)
    return source


def _filters(payload: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "league_id",
        "team_id",
        "team_name",
        "player_name",
        "side",
        "action",
        "relation",
        "context_level",
        "opponent_team_id",
        "opponent_team_name",
        "risk_mode",
    )
    return {key: payload[key] for key in keys if payload.get(key) not in (None, "")}


def _sample_size(payload: dict[str, Any], items: list[dict[str, Any]]) -> int | None:
    for key in (
        "sample_size",
        "battle_count",
        "selection_count",
        "eligible_battle_count",
        "recorded_battle_count",
        "rollouts",
    ):
        number = _optional_number(payload.get(key))
        if number is not None:
            return int(number)
    for item in items:
        number = _optional_number(item.get("sample_size"))
        if number is not None:
            return int(number)
    for collection_key in (
        "rows",
        "recommendations",
        "next_action_probabilities",
        "results",
    ):
        for row in _as_list(payload.get(collection_key)):
            if not isinstance(row, dict):
                continue
            for key in (
                "sample_size",
                "eligible_battle_count",
                "recorded_battle_count",
                "battle_count",
                "selection_count",
            ):
                number = _optional_number(row.get(key))
                if number is not None:
                    return int(number)
    return None


def _items_from_rows(rows: list[Any], *, name_keys: tuple[str, ...], value_keys: tuple[str, ...]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in rows[:5]:
        if not isinstance(row, dict):
            continue
        label = next((_text(row.get(key)) for key in name_keys if _text(row.get(key))), "")
        value = None
        metric = None
        for key in value_keys:
            if row.get(key) is None:
                continue
            metric = key
            value = _percent(row.get(key)) if "rate" in key or "probability" in key or "share" in key else row.get(key)
            break
        detail_parts = []
        for key in ("selection_count", "battle_count", "pick_count", "recorded_battle_count"):
            if row.get(key) is not None:
                detail_parts.append(f"n={row.get(key)}")
                break
        items.append(
            {
                "label": label or "Result",
                "value": value,
                "detail": ", ".join(detail_parts) or None,
                "metric": metric,
                "subject": label or None,
            }
        )
    return items


def _card_items(tool: str, payload: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    if tool in {"predict_next_draft_action", "simulate_future_draft"}:
        combinations = _as_list(payload.get("pick_combinations"))
        if combinations:
            items = []
            for row in combinations[:5]:
                if not isinstance(row, dict):
                    continue
                names = " + ".join(_as_list(row.get("hero_names")))
                items.append(
                    {
                        "label": names or "Combination",
                        "value": _percent(row.get("probability")),
                        "detail": f"n={row.get('count')}" if row.get("count") is not None else None,
                        "metric": "probability",
                        "subject": names or None,
                    }
                )
            return items, "policy_probability"
        rows = _as_list(payload.get("next_action_probabilities"))
        items = []
        for row in rows[:5]:
            if not isinstance(row, dict):
                continue
            items.append(
                {
                    "label": _text(row.get("hero_name")) or str(row.get("hero_id") or "Hero"),
                    "value": _percent(row.get("probability")),
                    "detail": None,
                    "metric": "policy_probability",
                    "subject": _text(row.get("hero_name")) or None,
                }
            )
        return items, "policy_probability"
    if tool == "recommend_value_draft_action":
        items = []
        for row in _as_list(payload.get("recommendations"))[:5]:
            if not isinstance(row, dict):
                continue
            items.append(
                {
                    "label": _text(row.get("hero_name")) or str(row.get("hero_id") or "Hero"),
                    "value": _percent(row.get("expected_advantage"))
                    or _text(row.get("expected_advantage")),
                    "detail": _text(row.get("action")) or None,
                    "metric": "expected_advantage",
                    "subject": _text(row.get("hero_name")) or None,
                }
            )
        return items, "expected_advantage"
    if tool == "score_current_lineup":
        blue = _as_dict(payload.get("blue_team"))
        red = _as_dict(payload.get("red_team"))
        items = [
            {
                "label": _text(blue.get("team_name")) or "Blue",
                "value": payload.get("blue_advantage"),
                "detail": ", ".join(_as_list(blue.get("heroes"))[:5]) or None,
                "metric": "expected_advantage",
                "subject": _text(blue.get("team_name")) or "blue",
            },
            {
                "label": _text(red.get("team_name")) or "Red",
                "value": payload.get("red_advantage"),
                "detail": ", ".join(_as_list(red.get("heroes"))[:5]) or None,
                "metric": "expected_advantage",
                "subject": _text(red.get("team_name")) or "red",
            },
        ]
        return items, "expected_advantage"
    if tool == "search_patch_notes":
        items = []
        for row in _as_list(payload.get("results"))[:5]:
            if not isinstance(row, dict):
                continue
            items.append(
                {
                    "label": _text(row.get("title")) or "Patch note",
                    "value": _text(row.get("published_at")) or None,
                    "detail": _text(row.get("excerpt"))[:180] or None,
                    "metric": None,
                    "subject": _text(row.get("title")) or None,
                    "source_url": row.get("source_url"),
                }
            )
        return items, None
    if tool == "get_battle_draft":
        actions = _as_list(payload.get("actions") or payload.get("rows"))
        items = []
        for row in actions[:8]:
            if not isinstance(row, dict):
                continue
            items.append(
                {
                    "label": _text(row.get("hero_name")) or str(row.get("hero_id") or "Hero"),
                    "value": _text(row.get("action")) or None,
                    "detail": _text(row.get("side")) or None,
                    "metric": None,
                    "subject": _text(row.get("hero_name")) or None,
                }
            )
        return items, None
    if tool in {"get_team_roster", "get_player_hero_pool"}:
        name_keys = ("player_name", "hero_name")
        value_keys = ("recorded_battle_count", "pick_count", "pick_share", "descriptive_battle_win_rate")
        return _items_from_rows(_as_list(payload.get("rows")), name_keys=name_keys, value_keys=value_keys), None
    name_keys = ("hero_name", "target_hero_name", "response_hero_name", "player_name", "sequence")
    value_keys = (
        "early_priority_rate",
        "smoothed_probability_given_legal",
        "descriptive_win_rate",
        "presence_rate",
        "pick_rate",
        "ban_rate",
        "association_rate",
        "pick_share",
    )
    metric = None
    items = _items_from_rows(_as_list(payload.get("rows")), name_keys=name_keys, value_keys=value_keys)
    if items:
        metric = items[0].get("metric")
    return items, metric


def build_evidence_record(
    tool_call: dict[str, Any],
    *,
    evidence_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one internal evidence record plus its public card."""
    name = _text(tool_call.get("name"))
    success = bool(tool_call.get("success"))
    payload = _as_dict(tool_call.get("result"))
    error = _text(tool_call.get("error"))
    warning = _text(payload.get("warning"))
    if not warning and _as_list(payload.get("warnings")):
        warning = " ".join(_text(item) for item in _as_list(payload.get("warnings")) if _text(item))
    items, metric = _card_items(name, payload) if success else ([], None)
    status = _result_status(success, payload, error)
    family = FAMILY_BY_TOOL.get(name, "other")
    card = {
        "id": evidence_id,
        "family": family,
        "tool": name,
        "title": TITLE_BY_FAMILY.get(family, name),
        "status": status,
        "items": items,
        "sample_size": _sample_size(payload, items) if success else None,
        "metric": (
            {
                "name": metric,
                "definition": METRIC_DEFINITIONS.get(metric or "", ""),
            }
            if metric
            else None
        ),
        "filters": _filters(payload) if success else {},
        "source": _source(payload) if success else {},
        "warning": warning or (error if status == "failed" else ""),
        "confidence": payload.get("confidence") if success else None,
        "interpretation": _text(payload.get("interpretation")) or None,
    }
    return {
        "id": evidence_id,
        "tool": name,
        "success": success,
        "status": status,
        "family": family,
        "context": dict(context or {}),
        "payload": payload if success else {},
        "error": error,
        "warning": warning,
        "card": card,
        "numeric_values": extract_numeric_values(payload) if success else [],
    }


def extract_numeric_values(payload: Any, *, subject: str | None = None) -> list[dict[str, Any]]:
    """Collect subject/metric/value tuples from a tool payload for grounding."""
    values: list[dict[str, Any]] = []
    if not isinstance(payload, dict):
        return values
    default_subject = (
        _text(payload.get("team_name"))
        or _text(payload.get("player_name"))
        or _text(payload.get("hero_name"))
        or subject
    )
    metric_keys = (
        "early_priority_rate",
        "descriptive_win_rate",
        "smoothed_probability_given_legal",
        "policy_probability",
        "probability",
        "expected_advantage",
        "robust_advantage",
        "pick_rate",
        "ban_rate",
        "presence_rate",
        "pick_share",
        "blue_advantage",
        "red_advantage",
        "descriptive_battle_win_rate",
        "association_rate",
        "probability_ci95_low",
        "probability_ci95_high",
    )
    for key in metric_keys:
        number = _optional_number(payload.get(key))
        if number is None:
            continue
        values.append(
            {
                "subject": default_subject,
                "metric": key,
                "value": float(number),
                "unit": "rate" if "rate" in key or "probability" in key or "share" in key else "score",
            }
        )
    for collection_key in (
        "rows",
        "recommendations",
        "next_action_probabilities",
        "pick_combinations",
        "results",
    ):
        for row in _as_list(payload.get(collection_key)):
            if not isinstance(row, dict):
                continue
            row_subject = (
                _text(row.get("hero_name"))
                or " + ".join(_as_list(row.get("hero_names")))
                or _text(row.get("player_name"))
                or _text(row.get("target_hero_name"))
                or _text(row.get("response_hero_name"))
                or default_subject
            )
            for key in metric_keys:
                number = _optional_number(row.get(key))
                if number is None:
                    continue
                values.append(
                    {
                        "subject": row_subject,
                        "metric": key,
                        "value": float(number),
                        "unit": "rate" if "rate" in key or "probability" in key or "share" in key else "score",
                    }
                )
    return values


def build_evidence_cards(tool_calls: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Return public cards for successful and failed tool families."""
    return [record["card"] for record in build_evidence_records(tool_calls)]


def build_evidence_records(
    tool_calls: list[dict[str, Any]] | None,
    *,
    context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, call in enumerate(tool_calls or [], start=1):
        if not isinstance(call, dict) or not call.get("name"):
            continue
        records.append(
            build_evidence_record(
                call,
                evidence_id=f"ev_{index}",
                context=context,
            )
        )
    return records


def collect_warnings(tool_calls: list[dict[str, Any]] | None) -> list[str]:
    """Normalize singular warning and plural warnings from tool results."""
    warnings: list[str] = []
    for call in tool_calls or []:
        if not isinstance(call, dict):
            continue
        name = _text(call.get("name")) or "tool"
        if not call.get("success"):
            error = _text(call.get("error"))
            if error:
                warnings.append(f"{name}: {error}")
            continue
        payload = _as_dict(call.get("result"))
        singular = _text(payload.get("warning"))
        if singular:
            warnings.append(singular)
        for item in _as_list(payload.get("warnings")):
            text = _text(item)
            if text and text not in warnings:
                warnings.append(text)
    return warnings
