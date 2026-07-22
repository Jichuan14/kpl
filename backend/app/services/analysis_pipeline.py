from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Literal

PipelineStep = Literal["export", "decisions", "statistics", "report", "all"]

REPO_ROOT = Path(__file__).resolve().parents[3]
ANALYSIS_DIR = REPO_ROOT / "analysis"
EXPORT_ROOT = ANALYSIS_DIR / "exports"
OUTPUT_ROOT = ANALYSIS_DIR / "outputs"


class AnalysisPipeline:
    """Run season-isolated JSONL and report generation."""

    def __init__(self, league_id: str):
        if not league_id or not all(
            character.isalnum() or character in "-_"
            for character in league_id
        ):
            raise ValueError("Invalid league_id")
        self.league_id = league_id
        self.export_dir = EXPORT_ROOT / league_id
        self.output_dir = OUTPUT_ROOT / league_id
        self.matches_path = self.export_dir / "matches.jsonl"
        self.decisions_path = self.export_dir / "bp_decisions.jsonl"
        self.report_path = self.output_dir / "bp_statistics_report.html"

    def run(self, step: PipelineStep) -> dict[str, Any]:
        steps = (
            ["export", "decisions", "statistics", "report"]
            if step == "all"
            else [step]
        )
        results = [self._run_step(current) for current in steps]
        return {
            "league_id": self.league_id,
            "requested_step": step,
            "steps": results,
            "exports_dir": str(self.export_dir.relative_to(REPO_ROOT)),
            "outputs_dir": str(self.output_dir.relative_to(REPO_ROOT)),
        }

    def _run_step(self, step: str) -> dict[str, Any]:
        self.export_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        command = self._command(step)
        started = time.monotonic()
        process = subprocess.run(
            command,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        duration = round(time.monotonic() - started, 3)
        if process.returncode != 0:
            detail = (process.stderr or process.stdout or "unknown error").strip()
            raise RuntimeError(f"{step} failed: {detail}")
        return {
            "step": step,
            "duration_seconds": duration,
            "output": process.stdout.strip(),
        }

    def _command(self, step: str) -> list[str]:
        python = sys.executable
        if step == "export":
            return [
                python,
                str(ANALYSIS_DIR / "export_match_data.py"),
                "--league-id",
                self.league_id,
                "--output",
                str(self.matches_path),
            ]
        if step == "decisions":
            return [
                python,
                str(ANALYSIS_DIR / "build_bp_decisions.py"),
                "--input",
                str(self.matches_path),
                "--output",
                str(self.decisions_path),
            ]
        if step == "statistics":
            return [
                python,
                str(ANALYSIS_DIR / "compute_bp_statistics.py"),
                "--input",
                str(self.decisions_path),
                "--output-dir",
                str(self.output_dir),
                "--min-selections",
                "2",
            ]
        if step == "report":
            return [
                python,
                str(ANALYSIS_DIR / "visualize_bp_statistics.py"),
                "--input-dir",
                str(self.output_dir),
                "--output",
                str(self.report_path),
                "--min-selections",
                "2",
            ]
        raise ValueError(f"Unknown pipeline step: {step}")
