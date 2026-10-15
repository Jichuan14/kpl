"""File-configured, season-independent public code board."""
import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.config import get_settings

logger = logging.getLogger(__name__)


class BoardConfig(BaseModel):
    model_config = {"extra": "forbid"}

    enabled: bool = False
    title_zh: str = Field(default="活动互助码", min_length=1, max_length=100)
    title_en: str = Field(default="Event code board", min_length=1, max_length=100)
    instructions_zh: str = Field(default="", max_length=1000)
    instructions_en: str = Field(default="", max_length=1000)
    placeholder_zh: str = Field(default="输入活动码", max_length=100)
    placeholder_en: str = Field(default="Enter your code", max_length=100)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    max_code_length: int = Field(default=500, ge=1, le=500)
    code_format: Literal["plain_text", "birthday_standee"] = "plain_text"

    @model_validator(mode="after")
    def valid_window(self):
        for value in (self.starts_at, self.ends_at):
            if value is not None and value.utcoffset() is None:
                raise ValueError("Board dates must include a timezone")
        if self.starts_at and self.ends_at and self.starts_at >= self.ends_at:
            raise ValueError("ends_at must follow starts_at")
        return self

    def is_active(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return (self.enabled and (self.starts_at is None or now >= self.starts_at)
                and (self.ends_at is None or now < self.ends_at))


# The game message is matched in full. Names/rarities and point values vary;
# the redemption token is exactly six ASCII letters/digits in the supplied format.
STANDEE_SHARE = re.compile(
    r"王者荣耀【(?P<item>[\u3400-\u9fff·]{1,32}立牌)(?P<token>[A-Za-z0-9]{6})】"
    r"价值(?P<points>[1-9][0-9]{0,5})分[,，]复制口令获取立牌[,，]"
    r"来小王生日会免费换周年庆限定传说皮肤[!！]"
)


class InvalidBoardCode(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def sanitize_board_code(value: str, config: BoardConfig) -> tuple[str, str]:
    """Return safe canonical copy text and its duplicate-detection identity.

    Format validation cannot establish that the game issued or can redeem a code.
    Keep the format selection in configuration so future activities can reuse the board.
    """
    if config.code_format == "birthday_standee":
        # Clipboard artifacts and full-width ASCII are harmless to normalize.
        value = unicodedata.normalize("NFKC", value).replace("\u200b", "").replace("\ufeff", "")
    else:
        value = unicodedata.normalize("NFC", value)
    if any(unicodedata.category(char).startswith("C") and char not in "\r\n\t" for char in value):
        raise InvalidBoardCode("invalid_code", "Enter a valid plain-text code.")
    code = " ".join(value.split())
    if not code or len(code) > config.max_code_length:
        raise InvalidBoardCode("invalid_code", "Enter a code within the allowed length.")
    if config.code_format == "plain_text":
        return code, code

    # Chinese game messages have no significant spaces. Rebuild the output
    # from validated fields rather than retaining arbitrary surrounding text.
    compact = "".join(value.split())
    if compact.count("王者荣耀【") > 1:
        raise InvalidBoardCode("multiple_share_messages", "Paste one share message at a time.")
    match = STANDEE_SHARE.fullmatch(compact)
    if match is None:
        raise InvalidBoardCode("invalid_share_message", "Paste a complete birthday standee share message from the game.")
    token = match["token"].upper()
    code = (f"王者荣耀【{match['item']}{token}】价值{match['points']}分，"
            "复制口令获取立牌，来小王生日会免费换周年庆限定传说皮肤！")
    return code, f"birthday_standee:{token}"


def load_board_config() -> BoardConfig:
    # Read on each request so the on/off switch does not require an API restart.
    # Invalid/missing configuration closes both reads and writes.
    try:
        return BoardConfig.model_validate(json.loads(Path(get_settings().event_board_config_path).read_text()))
    except (OSError, ValueError):
        logger.warning("Event code board disabled: configuration unavailable or invalid")
        return BoardConfig()
