"""Guard public website APIs against accidental Nginx management auth."""

from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
NGINX_CONF = REPO_ROOT / "frontend" / "nginx.conf"
LOCATION_BLOCK = re.compile(
    r"location\s+([^{]+)\{([^}]+)\}",
    re.MULTILINE,
)


class NginxPublicRouteTest(unittest.TestCase):
    def locations(self) -> list[tuple[str, str]]:
        text = NGINX_CONF.read_text(encoding="utf-8")
        return [
            (matcher.group(1).strip(), matcher.group(2))
            for matcher in LOCATION_BLOCK.finditer(text)
        ]

    def test_simulator_lineup_routes_are_not_behind_management_auth(self) -> None:
        blocks = self.locations()
        simulation = [
            body
            for matcher, body in blocks
            if "/api/simulations" in matcher
        ]
        self.assertTrue(simulation, "expected an explicit /api/simulations/ location")
        for body in simulation:
            self.assertNotIn("auth_basic", body)

        public_api = [
            body
            for matcher, body in blocks
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
            "= /api/coach/usage",
            "= /api/coach/limits",
        }
        blocks = {matcher: body for matcher, body in self.locations()}
        for matcher in protected:
            self.assertIn("auth_basic", blocks[matcher], matcher)


if __name__ == "__main__":
    unittest.main()
