"""Task 1 skeleton: contracts for a future patch-document retrieval tool.

This module is intentionally not registered in ``tool_registry.py`` yet.
Complete the Pydantic models as directed in
``agent/course/TASK_01_PATCH_TOOL_CONTRACT.md``. Do not add filesystem access,
SQLite queries, HTTP requests, or an LLM call in this task.
"""

from datetime import date
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator, model_validator


class SearchPatchNotesArguments(BaseModel):
    """Validated, bounded request for future patch-document retrieval."""

    model_config = {"extra": "forbid"}
    query: str = Field(min_length=2, max_length=300)
    hero_name: str | None = Field(default=None, min_length=1, max_length=100)
    from_date: date | None = None
    to_date: date | None = None
    limit: int = Field(default=3, ge=1, le=5)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("query must not be blank")
        return normalized

    @field_validator("hero_name")
    @classmethod
    def normalize_hero_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("hero_name must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_date_window(self) -> "SearchPatchNotesArguments":
        if self.from_date and self.to_date and self.from_date > self.to_date:
            raise ValueError("from_date must not be after to_date")
        return self


class PatchEvidenceCard(BaseModel):
    """One compact, source-attributed patch-document excerpt."""

    model_config = {"extra": "forbid"}

    announcement_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=300)
    published_at: date
    entity_type: Literal["hero", "equipment", "system"] = "hero"
    hero_names: list[str] = Field(default_factory=list)
    equipment_names: list[str] = Field(default_factory=list)
    heading_path: list[str] = Field(min_length=1)
    excerpt: str = Field(min_length=1, max_length=1600)
    source_url: AnyHttpUrl
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("announcement_id", "title", "excerpt")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("source text fields must not be blank")
        return normalized

    @field_validator("hero_names", "equipment_names", "heading_path")
    @classmethod
    def normalize_required_text_list(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("source text lists must not contain blank values")
        return normalized

    @field_validator("source_url")
    @classmethod
    def require_https_source(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.scheme != "https":
            raise ValueError("source_url must use HTTPS")
        return value

    @model_validator(mode="after")
    def validate_entity_evidence(self) -> "PatchEvidenceCard":
        if self.entity_type == "hero" and not self.hero_names:
            raise ValueError("hero evidence requires hero_names")
        if self.entity_type == "equipment" and not self.equipment_names:
            raise ValueError("equipment evidence requires equipment_names")
        return self


class PatchSearchResponse(BaseModel):
    """Bounded, evidence-only result of a future patch search."""

    model_config = {"extra": "forbid"}

    source_type: Literal["tencent_patch_notes"] = "tencent_patch_notes"
    index_version: str = Field(min_length=1, max_length=200)
    result_count: int = Field(ge=0)
    results: list[PatchEvidenceCard]
    warnings: list[str] = Field(default_factory=list)

    @field_validator("index_version")
    @classmethod
    def reject_blank_index_version(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("index_version must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_result_count(self) -> "PatchSearchResponse":
        if self.result_count != len(self.results):
            raise ValueError("result_count must equal the number of results")
        return self


def search_patch_notes(arguments: SearchPatchNotesArguments) -> dict[str, object]:
    """Return read-only, official patch-note evidence from the local index.

    The import stays inside the handler to keep this contract module independent
    of the index implementation while Task 3 imports these Pydantic models.
    """
    from app.knowledge.patch_retrieval import PatchRetriever

    return PatchRetriever().search(arguments).model_dump(mode="json")
