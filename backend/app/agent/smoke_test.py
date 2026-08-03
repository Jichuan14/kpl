"""Minimal live connectivity test for the configured Kimi backend client."""

from __future__ import annotations

import sys

from app.agent.service import (
    CoachInput,
    CoachLoopLimitError,
    KimiCoachService,
    KimiConfigurationError,
)
from app.config import get_settings


def main() -> int:
    settings = get_settings()
    configured = settings.moonshot_api_key is not None and bool(
        settings.moonshot_api_key.get_secret_value().strip()
    )
    if not configured:
        print("FAILED: MOONSHOT_API_KEY is missing from backend/.env")
        print("Add it as MOONSHOT_API_KEY=your-key, then run this command again.")
        return 2

    # Keep this live check inexpensive even if the normal application limit is
    # larger. The secret remains a SecretStr and is never displayed.
    smoke_settings = settings.model_copy(
        update={
            "kimi_max_output_tokens": min(
                settings.kimi_max_output_tokens,
                64,
            ),
            "kimi_max_tool_rounds": 1,
            "kimi_max_tool_calls": 1,
        }
    )

    print(f"Testing Kimi model: {smoke_settings.kimi_model}")
    print(f"Endpoint: {smoke_settings.kimi_base_url}")
    print("API key loaded: yes (value hidden)")

    try:
        service = KimiCoachService(settings=smoke_settings)
        result = service.ask(
            CoachInput(
                message=(
                    "This is a backend connectivity check. Do not call tools. "
                    "Reply exactly: Kimi connection OK"
                ),
                league_id="smoke_test",
            ),
            request_id="local-kimi-smoke-test",
        )
    except KimiConfigurationError as exc:
        print(f"FAILED: {exc}")
        return 2
    except CoachLoopLimitError:
        print("FAILED: Kimi unexpectedly requested too many tools.")
        return 3
    except Exception as exc:  # Provider exceptions vary by SDK version.
        status_code = getattr(exc, "status_code", None)
        if status_code == 401:
            print("FAILED: Kimi rejected the API key (HTTP 401).")
            if "moonshot.ai" in smoke_settings.kimi_base_url:
                print(
                    "If the key came from platform.kimi.com, add "
                    "KIMI_BASE_URL=https://api.moonshot.cn/v1 to backend/.env."
                )
                print(
                    "Otherwise, create a fresh key at platform.kimi.ai and "
                    "replace MOONSHOT_API_KEY."
                )
            elif "moonshot.cn" in smoke_settings.kimi_base_url:
                print(
                    "If the key came from platform.kimi.ai, add "
                    "KIMI_BASE_URL=https://api.moonshot.ai/v1 to backend/.env."
                )
                print(
                    "Otherwise, create a fresh key at platform.kimi.com and "
                    "replace MOONSHOT_API_KEY."
                )
            else:
                print("Check that this endpoint matches the key's Kimi platform.")
        elif status_code == 429:
            print("FAILED: Kimi rate or account quota was exceeded (HTTP 429).")
        elif status_code is not None:
            print(f"FAILED: Kimi returned HTTP {status_code}.")
        else:
            print(f"FAILED: {type(exc).__name__} while contacting Kimi.")
        return 4

    print("SUCCESS: the backend authenticated and received a Kimi response.")
    print(f"Reply: {result['answer']}")
    print(f"Total tokens: {result['usage']['total_tokens']}")
    print(f"Tool calls: {len(result['tool_calls'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
