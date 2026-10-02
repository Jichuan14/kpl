from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "kpl-refresh"


class RefreshScriptTests(unittest.TestCase):
    def run_script(self, statuses: list[dict], override: str | None = None) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "statuses.json").write_text(json.dumps(statuses))
            (root / "auth.netrc").touch()
            fake_curl = root / "curl"
            fake_curl.write_text("""#!/usr/bin/env python3
import json, os, pathlib, sys
args = ' '.join(sys.argv[1:])
if '/api/jobs/scheduled' in args:
    root = pathlib.Path(os.environ['KPL_TEST_DIR'])
    options = sys.argv[1:]
    (root / 'posted.json').write_text(options[options.index('-d') + 1])
    print(json.dumps({'success': True, 'data': {'id': 'job-1', 'status': 'pending', 'status_url': '/api/jobs/job-1'}}))
else:
    root = pathlib.Path(os.environ['KPL_TEST_DIR'])
    count_file = root / 'count'
    count = int(count_file.read_text()) if count_file.exists() else 0
    count_file.write_text(str(count + 1))
    statuses = json.loads((root / 'statuses.json').read_text())
    print(json.dumps({'success': True, 'data': statuses[min(count, len(statuses) - 1)]}))
""")
            fake_curl.chmod(0o755)
            env = {**os.environ, "KPL_API_URL": "http://example.invalid", "KPL_AUTH_FILE": str(root / "auth.netrc"),
                   "KPL_POLL_SECONDS": "0", "KPL_MAX_POLLS": "3", "KPL_CURL_BIN": str(fake_curl),
                   "KPL_TEST_DIR": str(root)}
            env.pop("KPL_LEAGUE_ID", None)
            if override:
                env["KPL_LEAGUE_ID"] = override
            result = subprocess.run(["sh", str(SCRIPT)], env=env, capture_output=True,
                                    text=True, timeout=10)
            result.posted_body = json.loads((root / "posted.json").read_text())
            return result

    def test_logs_completion_after_running_status(self):
        result = self.run_script([
            {"status": "running", "stage": "analysis"},
            {"status": "completed", "stage": "completed"},
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("running:analysis", result.stdout)
        self.assertIn("refresh completed", result.stdout)
        self.assertEqual(result.posted_body, {})

    def test_returns_failure_with_job_error(self):
        result = self.run_script([{"status": "failed", "stage": "failed", "error": "model error"}])
        self.assertEqual(result.returncode, 1)
        self.assertIn("model error", result.stderr)

    def test_explicit_override_is_sent_without_changing_daily_trigger(self):
        result = self.run_script([{"status": "completed", "stage": "completed", "league_id": "20260003"}],
                                 override="20260003")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.posted_body, {"league_id": "20260003"})
        self.assertIn("pinned league 20260003", result.stdout)


if __name__ == "__main__":
    unittest.main()
