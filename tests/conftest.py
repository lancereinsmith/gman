"""Shared test fixtures."""

from __future__ import annotations

from typing import Any


def make_repo(name: str, **overrides: Any) -> dict[str, Any]:
    """Build a repo dict resembling a GitHub API payload."""
    repo = {
        "name": name,
        "full_name": f"octocat/{name}",
        "private": False,
        "archived": False,
        "fork": False,
        "visibility": "public",
        "description": f"desc for {name}",
        "language": "Python",
        "stargazers_count": 0,
        "forks_count": 0,
        "updated_at": "2026-01-01T00:00:00Z",
        "html_url": f"https://github.com/octocat/{name}",
        "open_issues_count": 0,
        "default_branch": "main",
        "size": 128,
        "watchers_count": 0,
        "topics": [],
        "license": None,
        "created_at": "2025-01-01T00:00:00Z",
        "pushed_at": "2026-01-01T00:00:00Z",
    }
    repo.update(overrides)
    return repo
