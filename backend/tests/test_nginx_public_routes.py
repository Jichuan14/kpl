"""Guard public website APIs against accidental Nginx management auth."""

from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
NGINX_CONF = REPO_ROOT / "frontend" / "nginx.conf"
LOCATION_BLOCK = re.compile(
    r"^[ \t]*location\s+([^{]+)\{([^}]+)\}",
    re.MULTILINE,
)


class NginxPublicRouteTest(unittest.TestCase):
    def locations(self) -> list[tuple[str, str]]:
        text = NGINX_CONF.read_text(encoding="utf-8")
        return [
            (matcher.group(1).strip(), matcher.group(2))
            for matcher in LOCATION_BLOCK.finditer(text)
        ]

    def test_new_evidence_routes_are_not_behind_management_auth(self) -> None:
        blocks = {matcher: body for matcher, body in self.locations()}
        public_routes = {
            "^~ /api/simulations/",
            "^~ /api/visualization/draft-evidence/",
        }
        for matcher in public_routes:
            self.assertIn(matcher, blocks)
            self.assertNotIn("auth_basic", blocks[matcher], matcher)

        public_api = [
            body
            for matcher, body in blocks.items()
            if matcher in {"/api/", "/api/"} or matcher.endswith("/api/")
        ]
        self.assertTrue(public_api)
        for body in public_api:
            self.assertNotIn("auth_basic", body)

    def test_management_routes_still_require_basic_auth(self) -> None:
        protected = {
            "= /management",
            "^~ /api/sync/",
            "^~ /api/pipeline/",
            "^~ /api/visualization/",
            "= /api/coach/usage",
            "= /api/coach/limits",
        }
        blocks = {matcher: body for matcher, body in self.locations()}
        for matcher in protected:
            self.assertIn("auth_basic", blocks[matcher], matcher)


if __name__ == "__main__":
    unittest.main()
