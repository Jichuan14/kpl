from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.schemas import AnalysisRunRequest
from app.services import analysis_pipeline


class AnalysisPipelineTests(unittest.TestCase):
    def test_full_pipeline_trains_sequence_model_after_learnable_model(self) -> None:
        pipeline = analysis_pipeline.AnalysisPipeline("20260003")
        completed: list[str] = []

        with patch.object(
            pipeline,
            "_run_step",
            side_effect=lambda step: completed.append(step) or {"step": step},
        ):
            result = pipeline.run("all")

        self.assertEqual(completed[-2:], ["learnable_draft_model", "sequence_draft_model"])
        self.assertEqual(result["steps"][-1]["step"], "sequence_draft_model")

    def test_sequence_step_builds_features_then_trains_with_extended_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            analysis_dir = root / "analysis"
            with (
                patch.object(analysis_pipeline, "REPO_ROOT", root),
                patch.object(analysis_pipeline, "ANALYSIS_DIR", analysis_dir),
                patch.object(analysis_pipeline, "EXPORT_ROOT", analysis_dir / "exports"),
                patch.object(analysis_pipeline, "OUTPUT_ROOT", analysis_dir / "outputs"),
                patch.object(
                    analysis_pipeline.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0, "complete", ""),
                ) as run,
            ):
                result = analysis_pipeline.AnalysisPipeline("20260003").run(
                    "sequence_draft_model"
                )

        self.assertEqual(result["steps"][0]["step"], "sequence_draft_model")
        self.assertEqual(run.call_count, 3)
        feature_command = run.call_args_list[0].args[0]
        lane_profile_command = run.call_args_list[1].args[0]
        training_command = run.call_args_list[2].args[0]
        self.assertTrue(feature_command[1].endswith("build_hero_draft_feature_vectors.py"))
        self.assertTrue(lane_profile_command[1].endswith("build_hero_lane_profiles.py"))
        self.assertEqual(lane_profile_command[-2:], ["--through-season", "20260003"])
        self.assertTrue(training_command[1].endswith("train_sequence_draft_choice_model.py"))
        self.assertEqual(training_command[-2:], ["--league-id", "20260003"])
        self.assertNotIn("poc", str(training_command))
        self.assertTrue(all(call.kwargs["timeout"] == 900 for call in run.call_args_list))

    def test_sequence_step_is_accepted_by_request_schema(self) -> None:
        request = AnalysisRunRequest(
            league_id="20260003",
            step="sequence_draft_model",
        )
        self.assertEqual(request.step, "sequence_draft_model")

    def test_sequence_timeout_is_reported_as_pipeline_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            analysis_dir = root / "analysis"
            with (
                patch.object(analysis_pipeline, "REPO_ROOT", root),
                patch.object(analysis_pipeline, "ANALYSIS_DIR", analysis_dir),
                patch.object(analysis_pipeline, "EXPORT_ROOT", analysis_dir / "exports"),
                patch.object(analysis_pipeline, "OUTPUT_ROOT", analysis_dir / "outputs"),
                patch.object(
                    analysis_pipeline.subprocess,
                    "run",
                    side_effect=subprocess.TimeoutExpired(["python"], 900),
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "sequence_draft_model timed out after 900 seconds",
                ):
                    analysis_pipeline.AnalysisPipeline("20260003").run(
                        "sequence_draft_model"
                    )


if __name__ == "__main__":
    unittest.main()
