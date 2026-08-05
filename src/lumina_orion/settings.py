from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    queries: tuple[str, ...]
    max_repositories: int = 5
    max_readme_characters: int = 12_000
    max_goals: int = 3
    max_goal_characters: int = 4_000
    max_output_characters: int = 12_000
    model: str = "gpt-5.6"
    issue_prefix: str = "[Orion Learning]"
    goal_prefix: str = "[Orion Goal]"

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Settings":
        config_path = Path(path or os.getenv("ORION_CONFIG", "config/orion.json"))
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        queries = tuple(str(item).strip() for item in raw.get("queries", []) if str(item).strip())
        if not queries:
            raise ValueError("At least one learning query is required")

        return cls(
            queries=queries,
            max_repositories=max(1, min(int(raw.get("max_repositories", 5)), 10)),
            max_readme_characters=max(1_000, min(int(raw.get("max_readme_characters", 12_000)), 50_000)),
            max_goals=max(0, min(int(raw.get("max_goals", 3)), 10)),
            max_goal_characters=max(200, min(int(raw.get("max_goal_characters", 4_000)), 10_000)),
            max_output_characters=max(1_000, min(int(raw.get("max_output_characters", 12_000)), 50_000)),
            model=os.getenv("ORION_MODEL", str(raw.get("model", "gpt-5.6"))),
            issue_prefix=str(raw.get("issue_prefix", "[Orion Learning]")),
            goal_prefix=str(raw.get("goal_prefix", "[Orion Goal]")),
        )
