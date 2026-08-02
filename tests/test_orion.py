import json
import tempfile
import unittest
from pathlib import Path

from lumina_orion.github_client import Repository
from lumina_orion.learner import Learner
from lumina_orion.settings import Settings


class FakeGitHub:
    token = None

    def discover(self, query: str, limit: int):
        return [
            Repository("example/safe-agent", "https://github.com/example/safe-agent", "Safety", 100, "2026-01-01"),
            Repository("example/memory", "https://github.com/example/memory", "Memory", 200, "2026-01-02"),
        ]

    def with_readme(self, repository: Repository, max_characters: int):
        return Repository(**{**repository.__dict__, "readme": "untrusted README text"[:max_characters]})

    def recent_learning(self, repository: str, prefix: str):
        return []


class SettingsTests(unittest.TestCase):
    def test_limits_are_clamped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "orion.json"
            path.write_text(json.dumps({"queries": ["agents"], "max_repositories": 99, "max_readme_characters": 99}), encoding="utf-8")
            settings = Settings.load(path)
        self.assertEqual(settings.max_repositories, 10)
        self.assertEqual(settings.max_readme_characters, 1_000)


class LearnerTests(unittest.TestCase):
    def test_dry_run_is_bounded_and_ranked(self):
        settings = Settings(queries=("agents",), max_repositories=1)
        result = Learner(settings, FakeGitHub(), None).run("owner/repo", dry_run=True)
        self.assertEqual(len(result.repositories), 1)
        self.assertEqual(result.repositories[0].full_name, "example/memory")
        self.assertIn("Dry run", result.body)


if __name__ == "__main__":
    unittest.main()

