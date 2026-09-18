import json

import pytest
from fastapi import HTTPException

from app.api import visualization


def test_precomputed_manifest_and_match_are_validated(tmp_path, monkeypatch):
    monkeypatch.setattr(visualization, "OUTPUT_ROOT", tmp_path)
    directory = tmp_path / "season" / "draft_evidence"
    (directory / "matches").mkdir(parents=True)
    (directory / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "corpus_id": "abc"}), encoding="utf-8"
    )
    (directory / "matches" / "match-1.json").write_text(
        json.dumps({"schema_version": 1, "corpus_id": "abc", "match_id": "match-1"}),
        encoding="utf-8",
    )
    assert visualization.draft_evidence_manifest("season").data["corpus_id"] == "abc"
    assert visualization.draft_evidence_match("season", "match-1").data["match_id"] == "match-1"


def test_evidence_api_rejects_path_traversal_and_identity_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(visualization, "OUTPUT_ROOT", tmp_path)
    with pytest.raises(HTTPException) as traversal:
        visualization.draft_evidence_match("season", "../secret")
    assert traversal.value.status_code == 400

    directory = tmp_path / "season" / "draft_evidence" / "matches"
    directory.mkdir(parents=True)
    (directory / "m.json").write_text(
        json.dumps({"schema_version": 1, "match_id": "different"}), encoding="utf-8"
    )
    with pytest.raises(HTTPException) as mismatch:
        visualization.draft_evidence_match("season", "m")
    assert mismatch.value.status_code == 500

