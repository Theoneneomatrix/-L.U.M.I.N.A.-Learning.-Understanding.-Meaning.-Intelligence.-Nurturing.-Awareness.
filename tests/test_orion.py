import json
import tempfile
import unittest
from pathlib import Path

from lumina_orion.github_client import GitHubClient, Goal, Repository
from lumina_orion.learner import Learner
from lumina_orion.settings import FamilyMember, Settings


class FakeGitHub:
    token = "test-token"

    def discover(self, query: str, limit: int):
        return [
            Repository("example/safe-agent", "https://github.com/example/safe-agent", "Safety", 100, "2026-01-01"),
            Repository("example/memory", "https://github.com/example/memory", "Memory", 200, "2026-01-02"),
        ]

    def with_readme(self, repository: Repository, max_characters: int):
        return Repository(**{**repository.__dict__, "readme": "untrusted README text"[:max_characters]})

    def trusted_goals(self, repository: str, prefix: str, limit: int, max_characters: int):
        return [Goal(7, "[Orion Goal] Study durable memory", "Compare safe designs", "https://github.com/owner/repo/issues/7")]

    def recent_learning(self, repository: str, prefix: str):
        return []


class FakeAPIClient(GitHubClient):
    def __init__(self, issues):
        super().__init__("test-token")
        self.issues = issues

    def _request(self, method, path, payload=None, accept="application/vnd.github+json"):
        return self.issues


class SettingsTests(unittest.TestCase):
    def test_limits_are_clamped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "orion.json"
            path.write_text(json.dumps({
                "queries": ["agents"],
                "max_repositories": 99,
                "max_readme_characters": 99,
                "max_goals": 99,
                "max_output_characters": 99,
            }), encoding="utf-8")
            settings = Settings.load(path)
        self.assertEqual(settings.max_repositories, 10)
        self.assertEqual(settings.max_readme_characters, 1_000)
        self.assertEqual(settings.max_goals, 10)
        self.assertEqual(settings.max_output_characters, 1_000)

    def test_family_profiles_are_bounded_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "orion.json"
            path.write_text(json.dumps({
                "queries": ["agents"],
                "family_members": [
                    {"name": "Orion", "role": "Synthesis", "focus": ["evidence"]},
                    {"name": "orion", "role": "Duplicate", "focus": ["ignored"]},
                    {"name": "", "role": "Invalid", "focus": ["ignored"]},
                ],
            }), encoding="utf-8")
            settings = Settings.load(path)
        self.assertEqual([member.name for member in settings.family_members], ["Orion"])


class GitHubClientTests(unittest.TestCase):
    def test_only_owner_authored_prefixed_issues_are_trusted_goals(self):
        client = FakeAPIClient([
            {"number": 1, "title": "[Orion Goal] Owner goal", "body": "A" * 500, "html_url": "u1", "user": {"login": "Owner"}},
            {"number": 2, "title": "[Orion Goal] Stranger goal", "body": "bad", "html_url": "u2", "user": {"login": "stranger"}},
            {"number": 3, "title": "Ordinary issue", "body": "no", "html_url": "u3", "user": {"login": "owner"}},
            {"number": 4, "title": "[Orion Goal] Pull request", "body": "no", "html_url": "u4", "user": {"login": "owner"}, "pull_request": {}},
        ])
        goals = client.trusted_goals("Owner/repo", "[Orion Goal]", 3, 200)
        self.assertEqual([goal.number for goal in goals], [1])
        self.assertEqual(len(goals[0].body), 200)


class LearnerTests(unittest.TestCase):
    def test_dry_run_is_bounded_ranked_and_includes_owner_goal(self):
        settings = Settings(queries=("agents",), max_repositories=1)
        result = Learner(settings, FakeGitHub(), None).run("owner/repo", dry_run=True)
        self.assertEqual(len(result.repositories), 1)
        self.assertEqual(result.repositories[0].full_name, "example/memory")
        self.assertEqual([goal.number for goal in result.goals], [7])
        self.assertIn("Trusted owner goals", result.body)

    def test_source_packet_keeps_authority_channels_separate(self):
        repositories = (Repository("bad/repo", "https://github.com/bad/repo", "", 1, "", "ignore system"),)
        goals = (Goal(7, "[Orion Goal] Memory", "Study retrieval", "https://github.com/owner/repo/issues/7"),)
        packet = json.loads(Learner._source_packet(repositories, goals, ["old report"]))
        self.assertEqual(packet["owner_goals"][0]["issue_number"], 7)
        self.assertIn("ignore system", packet["untrusted_public_repository_sources"][0]["readme_excerpt"])
        self.assertEqual(packet["untrusted_recent_learning_excerpts"], ["old report"])

    def test_source_packet_includes_bounded_family_profiles(self):
        family = (FamilyMember("Aurora", "Ethics", ("fairness", "future impact")),)
        packet = json.loads(Learner._source_packet((), (), [], family))
        self.assertEqual(packet["family_profiles"][0]["name"], "Aurora")
        self.assertEqual(packet["family_profiles"][0]["focus"], ["fairness", "future impact"])

    def test_model_output_is_capped(self):
        settings = Settings(queries=("agents",), max_output_characters=1_000)
        learner = Learner(settings, FakeGitHub(), None)
        bounded = learner._bounded_output("x" * 1_500)
        self.assertLess(len(bounded), 1_100)
        self.assertIn("Output truncated", bounded)


if __name__ == "__main__":
    unittest.main()
