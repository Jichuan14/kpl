from __future__ import annotations

import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.schemas import AnalysisRunRequest
from app.services import analysis_pipeline


class AnalysisPipelineTests(unittest.TestCase):
    def test_full_pipeline_trains_lineup_value_after_draft_models(self) -> None:
        pipeline = analysis_pipeline.AnalysisPipeline("20260003")
        completed: list[str] = []

        with patch.object(
            pipeline,
            "_run_step",
            side_effect=lambda step: completed.append(step) or {"step": step},
        ):
            result = pipeline.run("all")

        self.assertEqual(
            completed[-4:],
            [
                "learnable_draft_model",
                "sequence_draft_model",
                "ban_value_model",
                "lineup_value_model",
            ],
        )
        self.assertEqual(result["steps"][-1]["step"], "lineup_value_model")

    def test_display_pipeline_skips_all_draft_models(self) -> None:
        pipeline = analysis_pipeline.AnalysisPipeline("20250004")
        completed: list[str] = []

        with patch.object(
            pipeline,
            "_run_step",
            side_effect=lambda step: completed.append(step) or {"step": step},
        ):
            result = pipeline.run("display")

        self.assertEqual(
            completed,
            [
                "export",
                "decisions",
                "statistics",
                "meta",
                "team_synergy",
                "team_profiles",
                "power_rankings",
            ],
        )
        self.assertEqual(result["requested_step"], "display")

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
                    analysis_pipeline,
                    "_run_command",
                    return_value=subprocess.CompletedProcess([], 0, "complete", ""),
                ) as run,
            ):
                result = analysis_pipeline.AnalysisPipeline("20260003").run(
                    "sequence_draft_model"
                )

        self.assertEqual(result["steps"][0]["step"], "sequence_draft_model")
        self.assertEqual(run.call_count, 2)
        feature_command = run.call_args_list[0].args[0]
        training_command = run.call_args_list[1].args[0]
        self.assertTrue(feature_command[1].endswith("build_hero_draft_feature_vectors.py"))
        self.assertTrue(training_command[1].endswith("train_sequence_draft_choice_model.py"))
        self.assertEqual(
            training_command[-4:],
            [
                "--league-id",
                "20260003",
                "--use-series-context",
                "--train-on-all-data",
            ],
        )
        self.assertEqual(training_command.count("--use-series-context"), 1)
        self.assertEqual(training_command.count("--train-on-all-data"), 1)
        self.assertNotIn("poc", str(training_command))
        self.assertTrue(
            all(call.kwargs["timeout_seconds"] == 900 for call in run.call_args_list)
        )

    def test_sequence_step_is_accepted_by_request_schema(self) -> None:
        request = AnalysisRunRequest(
            league_id="20260003",
            step="sequence_draft_model",
        )
        self.assertEqual(request.step, "sequence_draft_model")

    def test_lineup_value_step_uses_managed_trainer_and_extended_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            analysis_dir = root / "analysis"
            with (
                patch.object(analysis_pipeline, "REPO_ROOT", root),
                patch.object(analysis_pipeline, "ANALYSIS_DIR", analysis_dir),
                patch.object(analysis_pipeline, "EXPORT_ROOT", analysis_dir / "exports"),
                patch.object(analysis_pipeline, "OUTPUT_ROOT", analysis_dir / "outputs"),
                patch.object(
                    analysis_pipeline,
                    "_run_command",
                    return_value=subprocess.CompletedProcess([], 0, "complete", ""),
                ) as run,
            ):
                result = analysis_pipeline.AnalysisPipeline("20260003").run(
                    "lineup_value_model"
                )

        self.assertEqual(result["steps"][0]["step"], "lineup_value_model")
        command = run.call_args.args[0]
        self.assertTrue(command[1].endswith("train_lineup_value_model.py"))
        self.assertEqual(command[-4:], ["--league-id", "20260003", "--output-dir", str(analysis_dir / "outputs" / "20260003")])
        self.assertEqual(run.call_args.kwargs["timeout_seconds"], 900)

    def test_lineup_value_step_is_accepted_by_request_schema(self) -> None:
        request = AnalysisRunRequest(
            league_id="20260003",
            step="lineup_value_model",
        )
        self.assertEqual(request.step, "lineup_value_model")

    def test_ban_value_step_uses_season_scoped_trainer(self) -> None:
        pipeline = analysis_pipeline.AnalysisPipeline("20260003")
        command = pipeline._command("ban_value_model")

        self.assertTrue(command[1].endswith("train_ban_value_model.py"))
        self.assertIn("--league-id", command)
        self.assertIn("20260003", command)
        self.assertTrue(command[-3].endswith("ban_value_model.json"))
        self.assertTrue(command[-1].endswith("ban_value_validation.json"))

    def test_ban_value_step_is_accepted_by_request_schema(self) -> None:
        request = AnalysisRunRequest(
            league_id="20260003",
            step="ban_value_model",
        )
        self.assertEqual(request.step, "ban_value_model")

    def test_display_step_is_accepted_by_request_schema(self) -> None:
        request = AnalysisRunRequest(league_id="20250004", step="display")
        self.assertEqual(request.step, "display")

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
                    analysis_pipeline,
                    "_run_command",
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

    def test_concurrent_pipeline_run_is_rejected(self) -> None:
        self.assertTrue(analysis_pipeline._PIPELINE_RUN_LOCK.acquire(blocking=False))
        try:
            with self.assertRaisesRegex(
                analysis_pipeline.PipelineBusyError,
                "already running",
            ):
                analysis_pipeline.AnalysisPipeline("20260003").run("display")
        finally:
            analysis_pipeline._PIPELINE_RUN_LOCK.release()

    def test_timeout_terminates_the_entire_process_group(self) -> None:
        process = unittest.mock.Mock()
        process.pid = 1234
        process.communicate.side_effect = [subprocess.TimeoutExpired(["python"], 5), ("", "")]

        with patch.object(analysis_pipeline.os, "killpg") as killpg:
            analysis_pipeline._terminate_process_tree(process)

        self.assertEqual(
            killpg.call_args_list,
            [unittest.mock.call(1234, signal.SIGTERM), unittest.mock.call(1234, signal.SIGKILL)],
        )


if __name__ == "__main__":
    unittest.main()
