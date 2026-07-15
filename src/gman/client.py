"""GitHub REST API client for the authenticated user's repositories."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from gman.capabilities import CapabilityCache, TokenInfo, classify_token

DEFAULT_API_URL = "https://api.github.com"


class GitHubError(Exception):
    """Raised when a request to the GitHub API cannot be completed."""


class RateLimitError(GitHubError):
    """Raised when the GitHub API rate limit has been exhausted."""


def _gh_cli_token() -> str | None:
    """Return the token stored by `gh auth login`, or `None` if unavailable."""
    if not shutil.which("gh"):
        return None
    try:
        r = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    return r.stdout.strip() or None


def _is_state_403(r: requests.Response) -> bool:
    """True when a 403 reflects resource state (archived/read-only), not authorization."""
    text = r.text[:500].lower()
    return "archived" in text or "read-only" in text


def _rate_limit_message(reset: str | None) -> str:
    if reset:
        try:
            when = datetime.fromtimestamp(int(reset), tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            pass
        else:
            return f"GitHub API rate limit exceeded; resets at {when:%Y-%m-%d %H:%M UTC}."
    return "GitHub API rate limit exceeded."


class GitHubClient:
    """Thin wrapper around the GitHub REST API for the authenticated user.

    The token is resolved in this order: constructor argument, `GITHUB_TOKEN`
    environment variable, then `gh auth token` from the GitHub CLI.

    The API base URL is resolved from the constructor argument, then the
    `GITHUB_API_URL` environment variable, then the public GitHub API. Set it
    to `https://<host>/api/v3` to talk to a GitHub Enterprise Server instance.
    """

    def __init__(
        self,
        token: str | None = None,
        api_url: str | None = None,
        max_retries: int = 3,
    ) -> None:
        if token:
            self.token = token
            self.token_source = "--token flag"
        elif env_token := os.getenv("GITHUB_TOKEN"):
            self.token = env_token
            self.token_source = "GITHUB_TOKEN env"
        elif cli_token := _gh_cli_token():
            self.token = cli_token
            self.token_source = "gh CLI"
        else:
            self.token = None
            self.token_source = "none"
        self.token_info = TokenInfo(kind=classify_token(self.token))
        self.capabilities = CapabilityCache(self.token_info)
        self._scopes_captured = False
        self.api_url = (api_url or os.getenv("GITHUB_API_URL") or DEFAULT_API_URL).rstrip("/")
        self.max_retries = max_retries
        self.session = requests.Session()
        if self.token:
            self.session.headers.update(
                {
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                }
            )

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        """Perform a request, retrying transient failures and surfacing rate limits.

        Raises `RateLimitError` when the rate limit is exhausted and
        `GitHubError` when the request cannot be completed after retries.
        """
        url = path if path.startswith("http") else f"{self.api_url}{path}"
        kwargs.setdefault("timeout", 30)
        for attempt in range(self.max_retries + 1):
            try:
                r = self.session.request(method, url, **kwargs)
            except requests.RequestException as e:
                if attempt < self.max_retries:
                    time.sleep(2**attempt)
                    continue
                raise GitHubError(f"Request to {url} failed: {e}") from e
            if r.status_code in (403, 429) and r.headers.get("X-RateLimit-Remaining") == "0":
                raise RateLimitError(_rate_limit_message(r.headers.get("X-RateLimit-Reset")))
            if r.status_code >= 500 and attempt < self.max_retries:
                time.sleep(2**attempt)
                continue
            if not self._scopes_captured and r.ok:
                self._scopes_captured = True
                self.token_info.apply_scopes_header(r.headers.get("X-OAuth-Scopes"))
            return r
        raise GitHubError(f"Request to {url} failed after {self.max_retries} retries")

    def _get_optional(
        self,
        family: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        mark_denied: bool = True,
    ) -> requests.Response | None:
        """GET a resource that may be permission-gated or absent.

        Returns the response on 2xx. Returns ``None`` on 403 (marks the
        capability family denied) and on 404 (marks it allowed — GitHub's
        fine-grained permission failures are 403, so a 404 means authz
        passed and the resource simply doesn't exist). Other statuses
        return ``None`` without marking.

        Pass `mark_denied=False` for endpoints whose 403 conflates a
        repo-level condition (e.g. traffic's push-access requirement) with
        the token permission.
        """
        r = self._request("GET", path, headers=headers, params=params)
        if r.status_code == 403:
            if mark_denied:
                self.capabilities.mark(family, False)
            return None
        if r.status_code == 404:
            self.capabilities.mark(family, True)
            return None
        if r.ok:
            self.capabilities.mark(family, True)
            return r
        return None

    def _mutate(
        self,
        method: str,
        path: str,
        *,
        ok_codes: tuple[int, ...],
        success_msg: str = "",
        family: str = "admin.write",
        json: dict[str, Any] | None = None,
        success_fn: Callable[[requests.Response], str] | None = None,
    ) -> tuple[bool, str]:
        """Perform a write request, feeding the capability cache.

        Returns `(ok, message)`. `RateLimitError` propagates so bulk runs can
        abort; other `GitHubError`s become `(False, message)`.
        """
        try:
            r = self._request(method, path, json=json)
        except RateLimitError:
            raise
        except GitHubError as e:
            return False, str(e)
        if r.status_code == 403 and not _is_state_403(r):
            self.capabilities.mark(family, False)
        if r.status_code in ok_codes:
            self.capabilities.mark(family, True)
            return True, success_fn(r) if success_fn is not None else success_msg
        return False, f"HTTP {r.status_code}: {r.text[:160]}"

    def update_repo(self, full_name: str, fields: dict[str, Any]) -> tuple[bool, str]:
        """Update repository settings via a single PATCH. Returns `(ok, message)`."""
        return self._mutate(
            "PATCH",
            f"/repos/{full_name}",
            ok_codes=(200,),
            success_msg=f"Updated {full_name}",
            json=fields,
        )

    def whoami(self) -> str | None:
        """Return the authenticated user's login, or `None` on failure."""
        try:
            r = self._request("GET", "/user")
        except GitHubError:
            return None
        if r.status_code == 200:
            return r.json().get("login")
        return None

    def list_repos(
        self,
        include_archived: bool = True,
        affiliation: str = "owner",
        progress: Callable[[int], None] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch repositories for the authenticated user.

        Pages through `/user/repos` 100 at a time. `affiliation` is passed
        straight to the API (e.g. `owner`, `collaborator`,
        `organization_member`, or a comma-separated combination). Repos are
        returned in `updated_at` descending order (the API default), with
        archived repos pushed to the end of the list. `progress`, if given, is
        called with the running repo count after each page.
        """
        repos: list[dict[str, Any]] = []
        page = 1
        while True:
            r = self._request(
                "GET",
                "/user/repos",
                params={
                    "per_page": 100,
                    "page": page,
                    "affiliation": affiliation,
                    "sort": "updated",
                    "direction": "desc",
                },
            )
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            for repo in batch:
                if not include_archived and repo.get("archived"):
                    continue
                repos.append(repo)
            if progress is not None:
                progress(len(repos))
            if len(batch) < 100:
                break
            page += 1
        repos.sort(key=lambda r: bool(r.get("archived")))
        return repos

    def delete_repo(self, full_name: str) -> tuple[bool, str]:
        """Delete a repository. Returns `(ok, message)`."""
        return self._mutate(
            "DELETE",
            f"/repos/{full_name}",
            ok_codes=(204,),
            success_msg=f"Deleted {full_name}",
            family="delete",
        )

    def set_archived(self, full_name: str, archived: bool) -> tuple[bool, str]:
        """Archive or unarchive a repository. Returns `(ok, message)`."""
        verb = "Archived" if archived else "Unarchived"
        ok, msg = self.update_repo(full_name, {"archived": archived})
        return (True, f"{verb} {full_name}") if ok else (False, msg)

    def set_description(self, full_name: str, description: str) -> tuple[bool, str]:
        """Update a repository's description. Returns `(ok, message)`."""
        ok, msg = self.update_repo(full_name, {"description": description})
        return (True, f"Updated description for {full_name}") if ok else (False, msg)

    def get_repo(self, full_name: str) -> dict[str, Any]:
        """Fetch a single repository object. Raises `GitHubError` on failure."""
        r = self._request("GET", f"/repos/{full_name}")
        if r.status_code != 200:
            raise GitHubError(f"Cannot fetch {full_name}: HTTP {r.status_code}: {r.text[:160]}")
        return r.json()

    def get_readme(self, full_name: str) -> str | None:
        """Return the repo README as raw markdown text, or `None`."""
        r = self._get_optional(
            "contents.read",
            f"/repos/{full_name}/readme",
            headers={"Accept": "application/vnd.github.raw+json"},
        )
        return r.text if r is not None else None

    def get_languages(self, full_name: str) -> dict[str, int] | None:
        """Return language → bytes for the repo, or `None`."""
        r = self._get_optional("metadata.read", f"/repos/{full_name}/languages")
        return r.json() if r is not None else None

    def get_latest_release(self, full_name: str) -> dict[str, Any] | None:
        """Return the latest published release, or `None` if there are none."""
        r = self._get_optional("contents.read", f"/repos/{full_name}/releases/latest")
        return r.json() if r is not None else None

    def get_latest_workflow_run(self, full_name: str) -> dict[str, Any] | None:
        """Return the most recent Actions workflow run, or `None`."""
        r = self._get_optional(
            "actions.read", f"/repos/{full_name}/actions/runs", params={"per_page": 1}
        )
        if r is None:
            return None
        runs = r.json().get("workflow_runs") or []
        return runs[0] if runs else None

    def get_pages_info(self, full_name: str) -> dict[str, Any] | None:
        """Return the GitHub Pages site object, or `None` if none exists."""
        r = self._get_optional("pages.read", f"/repos/{full_name}/pages")
        return r.json() if r is not None else None

    def get_traffic(self, full_name: str) -> dict[str, int] | None:
        """Return 14-day traffic counters, or `None` if unavailable.

        Requires push access to the repo in addition to Administration: read.
        A 403 here is push-access-confounded — it may reflect missing push
        access rather than missing admin.read — so it does NOT teach the
        capability cache (`mark_denied=False`).
        """
        views = self._get_optional(
            "admin.read", f"/repos/{full_name}/traffic/views", mark_denied=False
        )
        if views is None:
            return None
        clones = self._get_optional(
            "admin.read", f"/repos/{full_name}/traffic/clones", mark_denied=False
        )
        if clones is None:
            return None
        v, c = views.json(), clones.json()
        return {
            "views": v.get("count", 0),
            "unique_views": v.get("uniques", 0),
            "clones": c.get("count", 0),
            "unique_clones": c.get("uniques", 0),
        }

    def _open_count(self, family: str, path: str) -> int | None:
        """Count open items with one request via the Link-header pagination trick."""
        r = self._get_optional(family, path, params={"state": "open", "per_page": 1})
        if r is None:
            return None
        body = r.json()
        if not body:
            return 0
        m = re.search(r'[?&]page=(\d+)>;\s*rel="last"', r.headers.get("Link", ""))
        return int(m.group(1)) if m else len(body)

    def get_open_pr_count(self, full_name: str) -> int | None:
        """Count open PRs using the Link-header pagination trick (1 request)."""
        return self._open_count("pulls.read", f"/repos/{full_name}/pulls")

    def get_open_dependabot_alert_count(self, full_name: str) -> int | None:
        """Count open Dependabot alerts, or `None` when unavailable."""
        return self._open_count("dependabot.read", f"/repos/{full_name}/dependabot/alerts")

    def get_open_secret_alert_count(self, full_name: str) -> int | None:
        """Count open secret-scanning alerts; `None` when unavailable or scanning is off."""
        return self._open_count(
            "secret_scanning.read", f"/repos/{full_name}/secret-scanning/alerts"
        )

    def get_vulnerability_alerts_enabled(self, full_name: str) -> bool | None:
        """Return whether Dependabot vulnerability alerts are enabled, or `None`."""
        r = self._request("GET", f"/repos/{full_name}/vulnerability-alerts")
        if r.status_code == 204:
            self.capabilities.mark("admin.read", True)
            return True
        if r.status_code == 404:
            self.capabilities.mark("admin.read", True)
            return False
        if r.status_code == 403:
            self.capabilities.mark("admin.read", False)
        return None

    def download_tarball(self, full_name: str, ref: str, dest: Path) -> Path:
        """Stream a repo tarball to `dest`. Raises `GitHubError` on failure.

        The endpoint 302s to a short-lived codeload URL; requests follows it.
        Git contents only — no issues, wiki, or release assets.
        """
        r = self._request("GET", f"/repos/{full_name}/tarball/{ref}", stream=True, timeout=120)
        if r.status_code == 403:
            self.capabilities.mark("contents.read", False)
        if r.status_code != 200:
            raise GitHubError(f"Tarball download failed: HTTP {r.status_code}")
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
        return dest

    _PINNED_QUERY = (
        "query { viewer { pinnedItems(first: 6, types: REPOSITORY) "
        "{ nodes { ... on Repository { nameWithOwner } } } } }"
    )

    def get_pinned_repos(self) -> set[str]:
        """Return `nameWithOwner` for the user's pinned repos; empty set on any failure."""
        try:
            r = self._request("POST", "/graphql", json={"query": self._PINNED_QUERY})
            if r.status_code != 200:
                return set()
            body = r.json()
            if not isinstance(body, dict) or "errors" in body:
                return set()
            nodes = body["data"]["viewer"]["pinnedItems"]["nodes"]
            return {n["nameWithOwner"] for n in nodes if n and "nameWithOwner" in n}
        except (GitHubError, ValueError, KeyError, TypeError):
            return set()

    def set_topics(self, full_name: str, topics: list[str]) -> tuple[bool, str]:
        """Replace ALL topics on a repository. Returns `(ok, message)`."""
        return self._mutate(
            "PUT",
            f"/repos/{full_name}/topics",
            ok_codes=(200,),
            success_msg=f"Set {len(topics)} topics on {full_name}",
            json={"names": topics},
        )

    def set_vulnerability_alerts(self, full_name: str, enabled: bool) -> tuple[bool, str]:
        """Enable or disable Dependabot vulnerability alerts. Returns `(ok, message)`."""
        verb = "Enabled" if enabled else "Disabled"
        return self._mutate(
            "PUT" if enabled else "DELETE",
            f"/repos/{full_name}/vulnerability-alerts",
            ok_codes=(204,),
            success_msg=f"{verb} vulnerability alerts on {full_name}",
        )

    def set_automated_security_fixes(self, full_name: str, enabled: bool) -> tuple[bool, str]:
        """Enable or disable Dependabot automated security fixes. Returns `(ok, message)`."""
        verb = "Enabled" if enabled else "Disabled"
        return self._mutate(
            "PUT" if enabled else "DELETE",
            f"/repos/{full_name}/automated-security-fixes",
            ok_codes=(204,),
            success_msg=f"{verb} automated security fixes on {full_name}",
        )

    def _created_msg(self, r: requests.Response) -> str:
        body = r.json()
        return f"Created {body.get('full_name')} — {body.get('html_url')}"

    def create_repo(self, fields: dict[str, Any]) -> tuple[bool, str]:
        """Create a repository for the authenticated user. Returns `(ok, message)`."""
        return self._mutate(
            "POST", "/user/repos", ok_codes=(201,), json=fields, success_fn=self._created_msg
        )

    def create_from_template(self, template_full: str, fields: dict[str, Any]) -> tuple[bool, str]:
        """Generate a repository from a template repo. Returns `(ok, message)`."""
        return self._mutate(
            "POST",
            f"/repos/{template_full}/generate",
            ok_codes=(201,),
            json=fields,
            success_fn=self._created_msg,
        )

    def compare(self, full_name: str, basehead: str) -> dict[str, Any] | None:
        """Compare two refs (supports cross-fork `owner:branch...branch`), or `None`."""
        r = self._get_optional(
            "contents.read", f"/repos/{full_name}/compare/{basehead}", params={"per_page": 1}
        )
        return r.json() if r is not None else None

    def merge_upstream(self, full_name: str, branch: str) -> tuple[bool, str]:
        """Sync a fork's branch with its upstream. Returns `(ok, message)`.

        A 409 means merge conflicts that must be resolved locally.
        """
        return self._mutate(
            "POST",
            f"/repos/{full_name}/merge-upstream",
            ok_codes=(200,),
            success_msg=f"Synced {full_name} with upstream",
            family="contents.write",
            json={"branch": branch},
        )

    def get_actions_cache_usage(self, full_name: str) -> dict[str, Any] | None:
        """Actions cache usage for a repo (one call), or `None`."""
        r = self._get_optional("actions.read", f"/repos/{full_name}/actions/cache/usage")
        return r.json() if r is not None else None

    def get_artifact_count(self, full_name: str) -> int | None:
        """Total Actions artifact count (from the list body), or `None`."""
        r = self._get_optional(
            "actions.read", f"/repos/{full_name}/actions/artifacts", params={"per_page": 1}
        )
        return r.json().get("total_count", 0) if r is not None else None

    def _list_paginated(self, family: str, path: str, key: str) -> list[dict[str, Any]] | None:
        """Collect a paginated Actions listing; None if the first page is unavailable."""
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            r = self._get_optional(family, path, params={"per_page": 100, "page": page})
            if r is None:
                return None if page == 1 else items
            batch = r.json().get(key) or []
            items.extend(batch)
            if len(batch) < 100:
                return items
            page += 1

    def list_artifacts(self, full_name: str) -> list[dict[str, Any]] | None:
        """All Actions artifacts for a repo, or `None` when unavailable."""
        return self._list_paginated(
            "actions.read", f"/repos/{full_name}/actions/artifacts", "artifacts"
        )

    def list_caches(self, full_name: str) -> list[dict[str, Any]] | None:
        """All Actions caches for a repo, or `None` when unavailable."""
        return self._list_paginated(
            "actions.read", f"/repos/{full_name}/actions/caches", "actions_caches"
        )

    def list_recent_runs(self, full_name: str, limit: int = 5) -> list[dict[str, Any]] | None:
        """The most recent workflow runs, or `None`."""
        r = self._get_optional(
            "actions.read", f"/repos/{full_name}/actions/runs", params={"per_page": limit}
        )
        return (r.json().get("workflow_runs") or []) if r is not None else None

    def delete_artifact(self, full_name: str, artifact_id: int) -> tuple[bool, str]:
        """Delete one Actions artifact. Returns `(ok, message)`."""
        return self._mutate(
            "DELETE",
            f"/repos/{full_name}/actions/artifacts/{artifact_id}",
            ok_codes=(204,),
            success_msg=f"Deleted artifact {artifact_id}",
            family="actions.write",
        )

    def delete_cache(self, full_name: str, cache_id: int) -> tuple[bool, str]:
        """Delete one Actions cache entry. Returns `(ok, message)`."""
        return self._mutate(
            "DELETE",
            f"/repos/{full_name}/actions/caches/{cache_id}",
            ok_codes=(204,),
            success_msg=f"Deleted cache {cache_id}",
            family="actions.write",
        )

    def rerun_workflow(
        self, full_name: str, run_id: int, failed_only: bool = False
    ) -> tuple[bool, str]:
        """Re-run a workflow run (optionally only its failed jobs)."""
        suffix = "rerun-failed-jobs" if failed_only else "rerun"
        what = "failed jobs of run" if failed_only else "run"
        return self._mutate(
            "POST",
            f"/repos/{full_name}/actions/runs/{run_id}/{suffix}",
            ok_codes=(201,),
            success_msg=f"Re-ran {what} {run_id} on {full_name}",
            family="actions.write",
        )

    def cancel_workflow(self, full_name: str, run_id: int) -> tuple[bool, str]:
        """Cancel an in-progress workflow run."""
        return self._mutate(
            "POST",
            f"/repos/{full_name}/actions/runs/{run_id}/cancel",
            ok_codes=(202,),
            success_msg=f"Cancelled run {run_id} on {full_name}",
            family="actions.write",
        )

    def get_gitignore_templates(self) -> list[str] | None:
        """Available gitignore template names (no permissions required)."""
        r = self._get_optional("metadata.read", "/gitignore/templates")
        return r.json() if r is not None else None

    def get_license_templates(self) -> list[dict[str, Any]] | None:
        """Available license templates (no permissions required)."""
        r = self._get_optional("metadata.read", "/licenses")
        return r.json() if r is not None else None
