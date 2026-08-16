"""Deterministic, evidence-first preparation reports for a selected team pair."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from app.agent.service import KimiConfigurationError, build_kimi_client
from app.agent.tool_registry import invoke_tool
from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

MAX_PLAYER_POOLS_PER_TEAM = 5
MAX_PRIORITY_HEROES_PER_TEAM = 2

BLUE_SECTION_MARKER = "[BLUE_TEAM_PROFILE]"
RED_SECTION_MARKER = "[RED_TEAM_PROFILE]"


SCOUT_REPORT_SYSTEM_PROMPT = """You write a pre-match KPL scouting report.

The user has already selected two teams for one recorded season. The evidence
packet contains only results returned by local, read-only KPL analysis tools.
Every factual statement must be supported by that packet. Do not use model
memory, general knowledge, or assumptions about current rosters.

Write in the requested language. Use the six required numbered headings supplied
below, in that order. Keep the two internal team markers exactly as supplied;
they will be removed before the report is shown to the user.

Keep the entire report below 800 Chinese characters or 450 English words. Use
at most three concise bullets in each team profile.

Describe tendencies as historical observations, not guarantees or optimal draft
advice. Player pools and rosters cover recorded season battles, not official
current rosters. Hero relationships are historical associations, not proven
gameplay counters. If evidence for a section is absent or sparse, say so
plainly. Do not claim that an unavailable result means the team, player, or hero
does not exist. Do not mention tools, prompts, internal plans, or token counts."""


def scout_report_system_prompt(language: str) -> str:
    """Add display headings in the user's language without exposing control tags."""
    if language == "zh-CN":
        headings = (
            "1. 对阵概览\n"
            f"2. {BLUE_SECTION_MARKER} 蓝方队伍档案\n"
            f"3. {RED_SECTION_MARKER} 红方队伍档案\n"
            "4. BP 关键压力点\n"
            "5. 备战关注点\n"
            "6. 数据说明"
        )
    else:
        headings = (
            "1. Matchup frame\n"
            f"2. {BLUE_SECTION_MARKER} Blue team profile\n"
            f"3. {RED_SECTION_MARKER} Red team profile\n"
            "4. BP pressure points\n"
            "5. Preparation watchlist\n"
            "6. Data caveats"
        )
    return SCOUT_REPORT_SYSTEM_PROMPT + "\n\nRequired headings:\n" + headings


class ScoutReportInput(BaseModel):
    """The selected simulator matchup that a report is allowed to investigate."""

    model_config = {"extra": "forbid"}

    league_id: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    blue_team_id: str = Field(min_length=1, max_length=32)
    blue_team_name: str = Field(min_length=1, max_length=64)
    red_team_id: str = Field(min_length=1, max_length=32)
    red_team_name: str = Field(min_length=1, max_length=64)
    language: Literal["en", "zh-CN"] = "en"

    @model_validator(mode="after")
    def teams_must_differ(self) -> "ScoutReportInput":
        if self.blue_team_id == self.red_team_id:
            raise ValueError("Blue and Red must be different teams")
        return self


class ScoutReportService:
    """Run a bounded report plan, then synthesize only its collected evidence."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.client = client or build_kimi_client(self.settings)

    def generate(
        self,
        request: ScoutReportInput,
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Gather a fixed evidence packet and write one report from it.

        This deliberately is not an open-ended tool-calling loop. The fixed plan
        makes report coverage predictable, limits source volume, and makes each
        missing source visible to the user as a caveat.
        """
        request_id = request_id or uuid4().hex
        tool_calls: list[dict[str, Any]] = []
        warnings: list[str] = []

        blue_profile = self._collect_team_profile(
            request,
            team_id=request.blue_team_id,
            team_name=request.blue_team_name,
            label="blue",
            tool_calls=tool_calls,
            warnings=warnings,
        )
        red_profile = self._collect_team_profile(
            request,
            team_id=request.red_team_id,
            team_name=request.red_team_name,
            label="red",
            tool_calls=tool_calls,
            warnings=warnings,
        )

        priority_heroes_by_team = {
            "blue": self._priority_heroes_for_team(blue_profile),
            "red": self._priority_heroes_for_team(red_profile),
        }
        priority_heroes = [
            hero_name
            for side in ("blue", "red")
            for hero_name in priority_heroes_by_team[side]
        ]
        relationships: list[dict[str, Any]] = []
        for side in ("blue", "red"):
            for hero_name in priority_heroes_by_team[side]:
                for relation in ("pick_synergy", "counter_pick"):
                    result = self._run_tool(
                        "get_hero_relationships",
                        {
                            "league_id": request.league_id,
                            "relation": relation,
                            "source_hero_name": hero_name,
                            "limit": 3,
                        },
                        subject=f"{side} priority hero relationship: {hero_name} ({relation})",
                        tool_calls=tool_calls,
                        warnings=warnings,
                    )
                    if result is not None:
                        relationships.append({"team_side": side, "data": result})

        evidence_packet = {
            "report_scope": {
                "league_id": request.league_id,
                "blue_team": {"team_id": request.blue_team_id, "team_name": request.blue_team_name},
                "red_team": {"team_id": request.red_team_id, "team_name": request.red_team_name},
            },
            "team_profiles": {"blue": blue_profile, "red": red_profile},
            "priority_heroes_by_team": priority_heroes_by_team,
            "hero_relationships": relationships,
            "collection_warnings": warnings,
        }
        answer, usage = self._write_report(
            request=request,
            evidence_packet=evidence_packet,
        )
        if not self._has_both_team_sections(answer, request):
            # Section markers are a presentation aid, not a reason to discard a
            # useful provider response. Guarantee both team portraits locally
            # from verified tool results when the model omits either marker.
            answer = self._append_balanced_profiles(
                answer,
                request=request,
                blue_profile=blue_profile,
                red_profile=red_profile,
            )
        answer = self._strip_section_markers(answer)
        logger.info(
            "scout_report_completed",
            extra={
                "request_id": request_id,
                "league_id": request.league_id,
                "tool_call_count": len(tool_calls),
                **usage,
            },
        )
        return {
            "request_id": request_id,
            "model": self.settings.kimi_model,
            "answer": answer,
            "tool_calls": tool_calls,
            "warnings": warnings,
            "usage": usage,
            "priority_heroes": priority_heroes,
            "priority_heroes_by_team": priority_heroes_by_team,
        }

    def _write_report(
        self,
        *,
        request: ScoutReportInput,
        evidence_packet: dict[str, Any],
    ) -> tuple[str, dict[str, int]]:
        """Ask for one report after the deterministic collection plan completes."""
        response = self.client.chat.completions.create(
            model=self.settings.kimi_model,
            messages=[
                {"role": "system", "content": scout_report_system_prompt(request.language)},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "language": "Chinese" if request.language == "zh-CN" else "English",
                            "task": "Prepare the requested Wolves-vs-AG-style scouting report from this evidence packet.",
                            "evidence_packet": evidence_packet,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
            max_tokens=self.settings.scout_report_max_output_tokens,
            extra_body={"thinking": {"type": "disabled"}},
        )
        answer = str(getattr(response.choices[0].message, "content", "") or "").strip()
        if not answer:
            raise RuntimeError("Kimi returned no scout report")
        return answer, self._usage(getattr(response, "usage", None))

    def _collect_team_profile(
        self,
        request: ScoutReportInput,
        *,
        team_id: str,
        team_name: str,
        label: str,
        tool_calls: list[dict[str, Any]],
        warnings: list[str],
    ) -> dict[str, Any]:
        base = {"league_id": request.league_id, "team_id": team_id, "team_name": team_name}
        roster = self._run_tool(
            "get_team_roster", base, subject=f"{label} roster", tool_calls=tool_calls, warnings=warnings
        )
        tendencies = {
            action: self._run_tool(
                "get_team_draft_tendencies",
                {**base, "action": action, "limit": 5},
                subject=f"{label} {action} tendencies",
                tool_calls=tool_calls,
                warnings=warnings,
            )
            for action in ("pick", "ban")
        }
        openings = self._run_tool(
            "get_team_opening_sequences",
            {**base, "limit": 3},
            subject=f"{label} opening sequences",
            tool_calls=tool_calls,
            warnings=warnings,
        )
        trends = {
            action: self._run_tool(
                "get_recent_team_trends",
                {**base, "action": action, "limit": 3},
                subject=f"{label} recent {action} trends",
                tool_calls=tool_calls,
                warnings=warnings,
            )
            for action in ("pick", "ban")
        }
        player_pools: list[dict[str, Any]] = []
        for player in (roster or {}).get("rows", [])[:MAX_PLAYER_POOLS_PER_TEAM]:
            player_name = str(player.get("player_name") or "").strip()
            if not player_name:
                continue
            result = self._run_tool(
                "get_player_hero_pool",
                {**base, "player_name": player_name, "limit": 5},
                subject=f"{label} player pool: {player_name}",
                tool_calls=tool_calls,
                warnings=warnings,
            )
            if result is not None:
                player_pools.append(result)
        return {
            "team_id": team_id,
            "team_name": team_name,
            "roster": roster,
            "tendencies": tendencies,
            "openings": openings,
            "recent_trends": trends,
            "player_pools": player_pools,
        }

    @staticmethod
    def _priority_heroes_for_team(profile: dict[str, Any]) -> list[str]:
        """Reserve relationship checks for this team's own pick evidence."""
        selected: list[str] = []
        sources = [profile.get("tendencies", {}).get("pick"), profile.get("recent_trends", {}).get("pick")]
        sources.extend(profile.get("player_pools", []))
        for source in sources:
            for row in (source or {}).get("rows", []):
                hero_name = str(row.get("hero_name") or "").strip()
                if hero_name and hero_name not in selected:
                    selected.append(hero_name)
                if len(selected) >= MAX_PRIORITY_HEROES_PER_TEAM:
                    return selected
        return selected

    @staticmethod
    def _has_both_team_sections(answer: str, request: ScoutReportInput) -> bool:
        if BLUE_SECTION_MARKER in answer and RED_SECTION_MARKER in answer:
            return True

        # Kimi may localize the invisible markers into labels such as
        # "[重庆狼队] Blue team profile". Accept genuine named profile headings
        # without accepting a team name mentioned only in the matchup frame.
        lines = answer.casefold().splitlines()

        def has_profile_heading(team_name: str) -> bool:
            expected = team_name.casefold()
            return any(
                expected in line
                and re.search(r"(?:profile|档案|球队画像|队伍画像)", line)
                for line in lines
            )

        return has_profile_heading(request.blue_team_name) and has_profile_heading(
            request.red_team_name
        )

    @staticmethod
    def _strip_section_markers(answer: str) -> str:
        return answer.replace(BLUE_SECTION_MARKER, "").replace(RED_SECTION_MARKER, "").strip()

    @staticmethod
    def _profile_snapshot(profile: dict[str, Any], *, language: str) -> str:
        """Render a concise team portrait directly from already verified rows."""
        def names(source: dict[str, Any] | None, field: str = "hero_name") -> str:
            values = [
                str(row.get(field) or "").strip()
                for row in (source or {}).get("rows", [])[:3]
            ]
            values = [value for value in values if value]
            return ", ".join(values)

        roster = names(profile.get("roster"), "player_name")
        picks = names(profile.get("tendencies", {}).get("pick"))
        bans = names(profile.get("tendencies", {}).get("ban"))
        recent = names(profile.get("recent_trends", {}).get("pick"))
        if language == "zh-CN":
            return "\n".join(
                [
                    f"- 已记录选手：{roster or '暂无已记录数据'}",
                    f"- 常见选择：{picks or '暂无已记录数据'}",
                    f"- 常见禁用：{bans or '暂无已记录数据'}",
                    f"- 最近选择变化：{recent or '暂无已记录数据'}",
                ]
            )
        return "\n".join(
            [
                f"- Recorded players: {roster or 'no recorded data'}",
                f"- Common picks: {picks or 'no recorded data'}",
                f"- Common bans: {bans or 'no recorded data'}",
                f"- Recent pick changes: {recent or 'no recorded data'}",
            ]
        )

    @classmethod
    def _append_balanced_profiles(
        cls,
        candidate: str,
        *,
        request: ScoutReportInput,
        blue_profile: dict[str, Any],
        red_profile: dict[str, Any],
    ) -> str:
        """Keep a useful synthesis while guaranteeing portraits for both teams."""
        if request.language == "zh-CN":
            title = "赛前侦察报告"
            blue_heading = f"2. 蓝方档案：{request.blue_team_name}"
            red_heading = f"3. 红方档案：{request.red_team_name}"
            analysis_heading = "4. 综合分析"
        else:
            title = "Pre-match scout report"
            blue_heading = f"2. Blue team profile: {request.blue_team_name}"
            red_heading = f"3. Red team profile: {request.red_team_name}"
            analysis_heading = "4. Scout analysis"
        return "\n\n".join(
            [
                title,
                blue_heading + "\n" + cls._profile_snapshot(blue_profile, language=request.language),
                red_heading + "\n" + cls._profile_snapshot(red_profile, language=request.language),
                analysis_heading + "\n" + candidate,
            ]
        )

    @staticmethod
    def _run_tool(
        name: str,
        arguments: dict[str, Any],
        *,
        subject: str,
        tool_calls: list[dict[str, Any]],
        warnings: list[str],
    ) -> dict[str, Any] | None:
        try:
            result = invoke_tool(name, arguments)
        except LookupError:
            warnings.append(f"No recorded data was available for {subject}.")
            tool_calls.append({"name": name, "subject": subject, "success": False, "error": "No recorded data."})
            return None
        except Exception as exc:
            logger.warning("scout_report_tool_failed", extra={"tool_name": name, "error_type": type(exc).__name__})
            warnings.append(f"{subject} could not be collected.")
            tool_calls.append({"name": name, "subject": subject, "success": False, "error": "Collection failed."})
            return None
        tool_calls.append({"name": name, "subject": subject, "success": True, "result": result})
        return result

    @staticmethod
    def _usage(usage: Any | None) -> dict[str, int]:
        return {
            "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        }
