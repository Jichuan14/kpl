import argparse
from contextlib import redirect_stdout
import importlib.util
import io
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
MODULE_SPEC = importlib.util.spec_from_file_location("build_production", ROOT / "deploy/build-production.py")
builder = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(builder)


class ProductionBuildTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="kpl-build-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "backend").mkdir()
        for name in ("Dockerfile", "requirements.txt", "requirements-training.txt"):
            shutil.copyfile(ROOT / "backend" / name, self.root / "backend" / name)
        self.spec = builder.dependency_spec(self.root, "linux/amd64")
        self.calls = []
        self.generated = []
        self.options = argparse.Namespace(api_only=False, require_reuse=False,
                                          rebuild_dependencies=False, bootstrap_image="kpl-api:latest")

    def command(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if args[:3] == ["docker", "buildx", "build"]:
            self.generated.append(Path(args[args.index("-f") + 1]).read_text())
        return subprocess.CompletedProcess(args, 0, stdout="linux/amd64\n", stderr="")

    def run_build(self, image_lookup, command=None):
        with patch.object(builder, "inspect_image", side_effect=image_lookup), \
             patch.object(builder, "command", side_effect=command or self.command), \
             patch.object(builder.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), \
             redirect_stdout(io.StringIO()):
            builder.build(self.root, self.options)

    def api_image(self):
        return {"Id": "sha256:" + "a" * 64, "Architecture": "amd64", "Os": "linux",
                "RootFS": {"Layers": builder.MIGRATION_BASE_LAYERS + ["app-layer"]}, "Config": {}}

    def test_key_ignores_source_and_runtime_but_changes_for_dependencies_and_platform(self):
        (self.root / "backend/main.py").write_text("new application code")
        dockerfile = self.root / "backend/Dockerfile"
        dockerfile.write_text(dockerfile.read_text() + "\nENV RUNTIME_ONLY=changed\n")
        self.assertEqual(self.spec["key"], builder.dependency_spec(self.root, "linux/amd64")["key"])
        self.assertNotEqual(self.spec["key"], builder.dependency_spec(self.root, "linux/arm64")["key"])
        for name in ("requirements.txt", "requirements-training.txt"):
            path = self.root / "backend" / name
            original = path.read_bytes()
            path.write_bytes(original + b"\nnew-package==1\n")
            self.assertNotEqual(self.spec["key"], builder.dependency_spec(self.root, "linux/amd64")["key"])
            path.write_bytes(original)
        dockerfile.write_text(dockerfile.read_text().replace("PYTHONUNBUFFERED=1", "PYTHONUNBUFFERED=0"))
        self.assertNotEqual(self.spec["key"], builder.dependency_spec(self.root, "linux/amd64")["key"])

    def test_migration_recipe_is_the_exact_audited_recipe(self):
        self.assertEqual(self.spec["recipe"], builder.MIGRATION_RECIPE)

    def test_cached_image_skips_installation_even_without_build_cache(self):
        cached = {"Config": {"Labels": {builder.DEPENDENCY_LABEL: self.spec["key"]}}}
        self.run_build(lambda _: cached)
        self.assertFalse(any(args[1] in ("run", "buildx") for args, _ in self.calls))
        args, kwargs = self.calls[-1]
        self.assertEqual(args[-2:], ["api", "web"])
        self.assertEqual(kwargs["env"]["KPL_API_DEPENDENCY_IMAGE"], "kpl-api-deps:" + self.spec["key"])

    def test_migration_copies_packages_without_application_or_installation_layers(self):
        self.options.require_reuse = True
        self.run_build(lambda name: self.api_image() if name == "kpl-api:latest" else None)
        dockerfile = self.generated[0]
        self.assertIn("COPY --from=installed /usr/local /usr/local", dockerfile)
        self.assertNotIn("pip install", dockerfile)
        self.assertNotIn("COPY backend /app/backend", dockerfile)
        self.assertIn(self.spec["recipe"], dockerfile)
        run = next(args for args, _ in self.calls if args[1] == "run")
        self.assertIn("--network=none", run)
        self.assertIn("--pull=never", run)
        self.assertEqual(run[-2:], [self.spec["api"], self.spec["training"]])
        self.assertFalse(any("--target" in args for args, _ in self.calls))

    def test_require_reuse_does_not_install_after_package_mismatch(self):
        self.options.require_reuse = True
        def reject_packages(args, **kwargs):
            self.command(args, **kwargs)
            if args[1] == "run":
                raise subprocess.CalledProcessError(1, args)
            return subprocess.CompletedProcess(args, 0, stdout="linux/amd64\n")
        with self.assertRaisesRegex(RuntimeError, "refusing package installation"):
            self.run_build(lambda name: self.api_image() if name == "kpl-api:latest" else None,
                           command=reject_packages)
        self.assertFalse(any(args[1] in ("buildx", "compose") for args, _ in self.calls))

    def test_different_base_cannot_seed_dependencies(self):
        self.options.require_reuse = True
        image = self.api_image()
        image["RootFS"]["Layers"][0] = "different-base"
        with self.assertRaisesRegex(RuntimeError, "refusing package installation"):
            self.run_build(lambda name: image if name == "kpl-api:latest" else None)
        self.assertFalse(any(args[1] in ("run", "buildx") for args, _ in self.calls))

    def test_recipe_changes_disable_old_image_migration(self):
        self.options.require_reuse = True
        path = self.root / "backend/Dockerfile"
        path.write_text(path.read_text().replace("PYTHONUNBUFFERED=1", "PYTHONUNBUFFERED=0"))
        with self.assertRaisesRegex(RuntimeError, "refusing package installation"):
            self.run_build(lambda name: self.api_image() if name == "kpl-api:latest" else None)
        self.assertFalse(any(args[1] in ("run", "buildx") for args, _ in self.calls))

    def test_missing_dependencies_build_before_application_images(self):
        self.run_build(lambda _: None)
        dependency_call = next(args for args, _ in self.calls if args[1] == "buildx")
        self.assertEqual(dependency_call[dependency_call.index("--target") + 1], "dependencies")
        self.assertIn("DEPENDENCY_KEY=" + self.spec["key"], dependency_call)
        self.assertEqual(self.calls[-1][0][1], "compose")

    def test_cached_image_with_wrong_label_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "unexpected dependency label"):
            self.run_build(lambda _: {"Config": {"Labels": {builder.DEPENDENCY_LABEL: "wrong"}}})
        self.assertFalse(any(args[1] == "compose" for args, _ in self.calls))

    def test_dependency_failure_never_tags_current_or_builds_application(self):
        def fail_installation(args, **kwargs):
            self.command(args, **kwargs)
            if args[1] == "buildx":
                raise subprocess.CalledProcessError(1, args)
            return subprocess.CompletedProcess(args, 0, stdout="linux/amd64\n")
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_build(lambda _: None, command=fail_installation)
        self.assertFalse(any(args[1] in ("compose", "image") for args, _ in self.calls))

    def test_explicit_refresh_bypasses_migration_and_cache(self):
        self.options.rebuild_dependencies = True
        self.options.api_only = True
        self.run_build(lambda _: None)
        dependency_call = next(args for args, _ in self.calls if args[1] == "buildx")
        self.assertIn("--no-cache", dependency_call)
        self.assertFalse(any(args[1] == "run" for args, _ in self.calls))
        self.assertEqual(self.calls[-1][0][-1], "api")

    def test_migration_checks_real_manifest_bytes_and_runs_offline_pip_check(self):
        original_path = Path
        expected = [self.spec["api"], self.spec["training"]]
        for mismatch in (False, True):
            if mismatch:
                (self.root / "backend/requirements.txt").write_text("different==1\n")
            with patch("pathlib.Path", side_effect=lambda value: self.root / "backend"
                       if value == "/app/backend" else original_path(value)), \
                 patch.object(sys, "argv", ["-", *expected]), \
                 patch("subprocess.run") as pip_check, redirect_stdout(io.StringIO()):
                if mismatch:
                    with self.assertRaisesRegex(SystemExit, "differs"):
                        exec(builder.MIGRATION_CHECK, {})
                    pip_check.assert_not_called()
                else:
                    exec(builder.MIGRATION_CHECK, {})
                    pip_check.assert_called_once_with([sys.executable, "-m", "pip", "check"], check=True)

    def test_runtime_guard_rejects_stale_requirements_recipe_and_missing_receipt(self):
        code = re.findall(r'RUN --network=none python -c "([^\n]+)"',
                          (self.root / "backend/Dockerfile").read_text())[-1]
        required = self.root / "required"
        required.mkdir()
        for name in ("Dockerfile", "requirements.txt", "requirements-training.txt"):
            shutil.copyfile(self.root / "backend" / name, required / name)
        receipt = self.root / "recipe.sha256"
        receipt.write_text(self.spec["recipe"])
        code = code.replace("'/tmp/kpl-required'", repr(str(required)))
        code = code.replace("'/tmp/kpl-required/Dockerfile'", repr(str(required / "Dockerfile")))
        code = code.replace("'/app/backend'", repr(str(self.root / "backend")))
        code = code.replace("'/opt/kpl-dependencies/recipe.sha256'", repr(str(receipt)))
        def check(success):
            result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
            self.assertEqual(result.returncode == 0, success, result.stderr)
            if not success:
                self.assertIn("Dependency image is outdated", result.stderr)
        check(True)
        receipt.unlink()
        check(False)
        receipt.write_text("different-recipe")
        check(False)
        receipt.write_text(self.spec["recipe"])
        for name in ("requirements.txt", "requirements-training.txt"):
            path = required / name
            original = path.read_bytes()
            path.write_text("different==1\n")
            check(False)
            path.write_bytes(original)
        path = required / "Dockerfile"
        path.write_text(path.read_text().replace("PYTHONUNBUFFERED=1", "PYTHONUNBUFFERED=0"))
        check(False)


if __name__ == "__main__":
    unittest.main()
