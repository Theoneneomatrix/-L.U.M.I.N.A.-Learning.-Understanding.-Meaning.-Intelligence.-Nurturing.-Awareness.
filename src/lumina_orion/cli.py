from __future__ import annotations

import argparse
import os
import sys

from .github_client import GitHubClient
from .learner import Learner
from .settings import Settings


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Orion's bounded autonomous learning cycle")
    parser.add_argument("--config", default=None, help="Path to Orion JSON configuration")
    parser.add_argument("--dry-run", action="store_true", help="Discover sources and goals without calling a model or creating an issue")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    token = os.getenv("GITHUB_TOKEN", "").strip() or None
    if not repository or "/" not in repository:
        print("GITHUB_REPOSITORY must be set to owner/name", file=sys.stderr)
        return 2

    settings = Settings.load(args.config)
    github = GitHubClient(token)
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    openai_client = None
    if api_key and not args.dry_run:
        from openai import OpenAI

        openai_client = OpenAI(api_key=api_key)
    learner = Learner(settings, github, openai_client)
    result = learner.run(repository, dry_run=args.dry_run)

    if args.dry_run:
        print(result.body)
        return 0
    if not token:
        print("GITHUB_TOKEN is required to publish the learning issue", file=sys.stderr)
        return 2

    existing = github.find_issue(repository, result.title)
    if existing:
        print(f"Today's learning issue already exists: {existing}")
        return 0
    url = github.create_issue(repository, result.title, result.body)
    print(f"Orion spoke through: {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
