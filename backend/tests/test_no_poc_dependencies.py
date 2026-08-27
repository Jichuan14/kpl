import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


class ProductionDependencyGuardTest(unittest.TestCase):
    def test_lineup_runtime_and_management_trainer_do_not_depend_on_poc(self) -> None:
        maintained_files = [
            REPO_ROOT / "analysis" / "train_lineup_value_model.py",
            REPO_ROOT / "analysis" / "lineup_value" / "history.py",
            REPO_ROOT / "analysis" / "lineup_value" / "training.py",
            REPO_ROOT / "backend" / "app" / "services" / "lineup_value.py",
            REPO_ROOT / "backend" / "Dockerfile",
        ]
        forbidden = ("/poc/", '"poc"', "'poc'", "team-advantage-poc")

        for path in maintained_files:
            contents = path.read_text(encoding="utf-8")
            for marker in forbidden:
                self.assertNotIn(marker, contents, f"{path} contains {marker!r}")


if __name__ == "__main__":
    unittest.main()
