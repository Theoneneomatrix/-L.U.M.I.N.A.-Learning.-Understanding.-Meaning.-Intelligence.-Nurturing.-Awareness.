from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class Repository:
    full_name: str
    html_url: str
    description: str
    stars: int
    updated_at: str
    readme: str = ""


@dataclass(frozen=True)
class Goal:
    number: int
    title: str
    body: str
    html_url: str


class GitHubClient:
    api_root = "https://api.github.com"

    def __init__(self, token: str | None = None) -> None:
        self.token = token

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None, accept: str = "application/vnd.github+json") -> Any:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Accept": accept,
            "User-Agent": "lumina-orion-autonomy/0.2",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(f"{self.api_root}{path}", data=body, headers=headers, method=method)
        with urlopen(request, timeout=30) as response:
            data = response.read(1_000_000)
            if accept == "application/vnd.github.raw+json":
                return data.decode("utf-8", errors="replace")
            return json.loads(data or b"null")

    def discover(self, query: str, limit: int) -> list[Repository]:
        params = urlencode({
            "q": f"{query} archived:false fork:false",
            "sort": "stars",
            "order": "desc",
            "per_page": min(limit, 10),
        })
        result = self._request("GET", f"/search/repositories?{params}")
        repositories: list[Repository] = []
        for item in result.get("items", []):
            repositories.append(Repository(
                full_name=item["full_name"],
                html_url=item["html_url"],
                description=item.get("description") or "",
                stars=int(item.get("stargazers_count", 0)),
                updated_at=item.get("updated_at") or "",
            ))
        return repositories

    def with_readme(self, repository: Repository, max_characters: int) -> Repository:
        try:
            readme = self._request(
                "GET",
                f"/repos/{quote(repository.full_name, safe='/')}/readme",
                accept="application/vnd.github.raw+json",
            )[:max_characters]
        except HTTPError as error:
            if error.code != 404:
                raise
            readme = ""
        return Repository(**{**repository.__dict__, "readme": readme})

    def trusted_goals(self, repository: str, prefix: str, limit: int, max_characters: int) -> list[Goal]:
        """Return open goal issues authored by the repository owner only."""
        owner = repository.split("/", 1)[0].casefold()
        path = f"/repos/{quote(repository, safe='/')}/issues?state=open&per_page=50&sort=updated&direction=desc"
        issues = self._request("GET", path)
        goals: list[Goal] = []
        for issue in issues:
            author = str((issue.get("user") or {}).get("login") or "").casefold()
            title = str(issue.get("title") or "")
            if "pull_request" in issue or author != owner or not title.startswith(prefix):
                continue
            goals.append(Goal(
                number=int(issue["number"]),
                title=title,
                body=str(issue.get("body") or "")[:max_characters],
                html_url=str(issue.get("html_url") or ""),
            ))
            if len(goals) >= limit:
                break
        return goals

    def recent_learning(self, repository: str, prefix: str, limit: int = 5) -> list[str]:
        path = f"/repos/{quote(repository, safe='/')}/issues?state=all&per_page=30&sort=created&direction=desc"
        issues = self._request("GET", path)
        return [str(issue.get("body") or "")[:8_000] for issue in issues if str(issue.get("title", "")).startswith(prefix)][:limit]

    def find_issue(self, repository: str, title: str) -> str | None:
        params = urlencode({"q": f'repo:{repository} is:issue in:title "{title}"'})
        result = self._request("GET", f"/search/issues?{params}")
        for issue in result.get("items", []):
            if issue.get("title") == title:
                return issue.get("html_url")
        return None

    def create_issue(self, repository: str, title: str, body: str) -> str:
        result = self._request(
            "POST",
            f"/repos/{quote(repository, safe='/')}/issues",
            {"title": title, "body": body},
        )
        return str(result["html_url"])
