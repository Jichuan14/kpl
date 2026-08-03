"""Kimi-backed, bounded orchestration for the KPL Draft Coach."""

from __future__ import annotations

import json
import logging
from time import perf_counter
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from app.agent.prompts import COACH_SYSTEM_PROMPT
from app.agent.tool_registry import available_tool_definitions, invoke_tool
from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

DRAFT_TOOL_OPTIONS: dict[str, frozenset[str]] = {
    "predict_next_draft_action": frozenset({"limit"}),
    "simulate_future_draft": frozenset(
        {"horizon", "rollouts", "choices_per_action", "seed"}
    ),
}


class KimiConfigurationError(RuntimeError):
    """Raised when the server has no usable Kimi API configuration."""


class CoachLoopLimitError(RuntimeError):
    """Raised when Kimi continues requesting tools past the configured bound."""


HeroId = Annotated[int, Field(gt=0)]


class CoachDraftState(BaseModel):
    """Validated active-board context supplied by the frontend."""

    model_config = {"extra": "forbid"}

    model_type: Literal["stats", "learnable"] = "stats"
    blue_team_id: str = Field(min_length=1, max_length=32)
    blue_team_name: str = Field(min_length=1, max_length=64)
    red_team_id: str = Field(min_length=1, max_length=32)
    red_team_name: str = Field(min_length=1, max_length=64)
    bp_order: int = Field(ge=1, le=20)
    blue_picks: list[HeroId] = Field(default_factory=list, max_length=10)
    red_picks: list[HeroId] = Field(default_factory=list, max_length=10)
    blue_bans: list[HeroId] = Field(default_factory=list, max_length=10)
    red_bans: list[HeroId] = Field(default_factory=list, max_length=10)
    blue_used_previous_battles: list[HeroId] = Field(
        default_factory=list,
        max_length=100,
    )
    red_used_previous_battles: list[HeroId] = Field(
        default_factory=list,
        max_length=100,
    )
    legal_hero_ids: list[HeroId] | None = Field(default=None, max_length=500)


class CoachHistoryTurn(BaseModel):
    model_config = {"extra": "forbid"}

    user: str = Field(min_length=1, max_length=4000)
    assistant: str = Field(min_length=1, max_length=4000)


class CoachInput(BaseModel):
    model_config = {"extra": "forbid"}

    message: str = Field(min_length=1, max_length=4000)
    league_id: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    draft_state: CoachDraftState | None = None
    history: list[CoachHistoryTurn] = Field(default_factory=list, max_length=8)


def build_kimi_client(settings: Settings | None = None):
    """Create the provider client lazily so imports never require a secret."""
    configuration = settings or get_settings()
    if configuration.moonshot_api_key is None:
        raise KimiConfigurationError(
            "MOONSHOT_API_KEY is not configured in the backend environment"
        )
    api_key = configuration.moonshot_api_key.get_secret_value().strip()
    if not api_key:
        raise KimiConfigurationError(
            "MOONSHOT_API_KEY is not configured in the backend environment"
        )
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise KimiConfigurationError(
            "The openai Python package is required for the Kimi client"
        ) from exc
    return OpenAI(
        api_key=api_key,
        base_url=configuration.kimi_base_url,
        timeout=configuration.kimi_timeout_seconds,
    )


class KimiCoachService:
    """Run a Kimi conversation with approved local tools and hard limits."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        settings: Settings | None = None,
    ):
        self.settings = settings or get_settings()
        self.client = client or build_kimi_client(self.settings)

    def ask(
        self,
        request: CoachInput,
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        request_id = request_id or uuid4().hex
        started = perf_counter()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": COACH_SYSTEM_PROMPT},
        ]
        for turn in request.history:
            messages.extend(
                [
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"prior_question": turn.user},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    },
                    {"role": "assistant", "content": turn.assistant},
                ]
            )
        messages.append(
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "question": request.message,
                        "league_id": request.league_id,
                        "draft_state": (
                            request.draft_state.model_dump(mode="json")
                            if request.draft_state is not None
                            else None
                        ),
                        "response_style": {
                            "language": "match the question",
                            "format": "concise plain language",
                            "normal_answer_max_sentences": 3,
                            "markdown_tables": False,
                        },
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
        )
        executed_tools: list[dict[str, Any]] = []
        usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

        for round_index in range(self.settings.kimi_max_tool_rounds + 1):
            response = self._completion(messages, request_id, round_index)
            self._add_usage(usage, getattr(response, "usage", None))
            message = response.choices[0].message
            tool_calls = list(getattr(message, "tool_calls", None) or [])
            messages.append(message.model_dump(exclude_none=True))

            if not tool_calls:
                answer = str(getattr(message, "content", "") or "").strip()
                if not answer:
                    raise RuntimeError("Kimi returned no answer")
                logger.info(
                    "coach_request_completed",
                    extra={
                        "request_id": request_id,
                        "model": self.settings.kimi_model,
                        "tool_call_count": len(executed_tools),
                        "duration_ms": round(
                            (perf_counter() - started) * 1000,
                            3,
                        ),
                        **usage,
                    },
                )
                return {
                    "request_id": request_id,
                    "model": self.settings.kimi_model,
                    "answer": answer,
                    "tool_calls": executed_tools,
                    "usage": usage,
                }

            if round_index >= self.settings.kimi_max_tool_rounds:
                raise CoachLoopLimitError("Kimi exceeded the tool-round limit")
            if len(executed_tools) + len(tool_calls) > self.settings.kimi_max_tool_calls:
                raise CoachLoopLimitError("Kimi exceeded the total tool-call limit")

            for tool_call in tool_calls:
                tool_record, tool_message = self._execute_tool_call(
                    tool_call,
                    request=request,
                    request_id=request_id,
                )
                executed_tools.append(tool_record)
                messages.append(tool_message)

        raise CoachLoopLimitError("Kimi did not finish within the tool-round limit")

    def _completion(
        self,
        messages: list[dict[str, Any]],
        request_id: str,
        round_index: int,
    ):
        started = perf_counter()
        response = self.client.chat.completions.create(
            model=self.settings.kimi_model,
            messages=messages,
            tools=available_tool_definitions(),
            tool_choice="auto",
            max_tokens=self.settings.kimi_max_output_tokens,
            extra_body={"thinking": {"type": "disabled"}},
        )
        logger.info(
            "coach_provider_call_completed",
            extra={
                "request_id": request_id,
                "model": self.settings.kimi_model,
                "round_index": round_index,
                "duration_ms": round((perf_counter() - started) * 1000, 3),
            },
        )
        return response

    @staticmethod
    def _execute_tool_call(
        tool_call: Any,
        *,
        request: CoachInput,
        request_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        name = str(tool_call.function.name)
        try:
            arguments = json.loads(tool_call.function.arguments or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be a JSON object")
            arguments = KimiCoachService._apply_application_context(
                name,
                arguments,
                request,
            )
            result = invoke_tool(name, arguments, request_id=request_id)
            record = {"name": name, "success": True, "result": result}
            content = {"success": True, "data": result}
        except (json.JSONDecodeError, ValueError, LookupError, FileNotFoundError) as exc:
            error = KimiCoachService._safe_tool_error(exc, tool_name=name)
            record = {"name": name, "success": False, "error": error}
            content = {"success": False, "error": error}
        return record, {
            "role": "tool",
            "tool_call_id": str(tool_call.id),
            "content": json.dumps(
                content,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        }

    @staticmethod
    def _apply_application_context(
        name: str,
        model_arguments: dict[str, Any],
        request: CoachInput,
    ) -> dict[str, Any]:
        """Make validated website context authoritative over model output."""
        option_names = DRAFT_TOOL_OPTIONS.get(name)
        if option_names is None:
            return {**model_arguments, "league_id": request.league_id}

        arguments = {
            key: model_arguments[key]
            for key in option_names
            if key in model_arguments
        }
        arguments["league_id"] = request.league_id
        if request.draft_state is not None:
            arguments.update(request.draft_state.model_dump(mode="json"))
        return arguments

    @staticmethod
    def _safe_tool_error(exc: Exception, *, tool_name: str = "") -> str:
        if isinstance(exc, FileNotFoundError):
            return "Required analysis data is unavailable for this season."
        if isinstance(exc, json.JSONDecodeError):
            return "The tool request contained invalid JSON arguments."
        if isinstance(exc, LookupError):
            return str(exc)
        if isinstance(exc, ValueError):
            if tool_name == "get_team_synergies":
                return (
                    "A specific team is required for team synergy analysis; "
                    "use league-wide hero relationships when no team is named."
                )
            return "The tool request arguments were invalid."
        return "The tool could not be completed."

    @staticmethod
    def _add_usage(target: dict[str, int], provider_usage: Any | None) -> None:
        if provider_usage is None:
            return
        input_tokens = int(getattr(provider_usage, "prompt_tokens", 0) or 0)
        output_tokens = int(
            getattr(provider_usage, "completion_tokens", 0) or 0
        )
        total_tokens = int(
            getattr(provider_usage, "total_tokens", input_tokens + output_tokens)
            or 0
        )
        target["input_tokens"] += input_tokens
        target["output_tokens"] += output_tokens
        target["total_tokens"] += total_tokens
