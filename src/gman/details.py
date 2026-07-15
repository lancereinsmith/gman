"""Per-repo detail fetching, delete warnings, backup, and shared rendering."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.markup import escape
from rich.table import Table

from gman.client import GitHubClient, GitHubError

# detail field -> capability family (for hint lookup on denial)
FIELD_FAMILIES: dict[str, str] = {
    "languages": "metadata.read",
    "latest_release": "contents.read",
    "latest_run": "actions.read",
    "pages": "pages.read",
    "traffic": "admin.read",
    "open_prs": "pulls.read",
    "fork_status": "contents.read",
    "dependabot_alerts": "dependabot.read",
    "secret_alerts": "secret_scanning.read",
    "vulnerability_alerts_enabled": "admin.read",
    "actions_storage": "actions.read",
}


@dataclass
class RepoDetails:
    """Everything the detail panel / `gman info` shows for one repo."""

    repo: dict[str, Any]
    languages: dict[str, int] | None = None
    latest_release: dict[str, Any] | None = None
    latest_run: dict[str, Any] | None = None
    pages: dict[str, Any] | None = None
    traffic: dict[str, int] | None = None
    open_prs: int | None = None
    fork_status: dict[str, Any] | None = None
    dependabot_alerts: int | None = None
    secret_alerts: int | None = None
    vulnerability_alerts_enabled: bool | None = None
    actions_storage: dict[str, Any] | None = None
    hints: dict[str, str] = field(default_factory=dict)  # field name -> denial hint

    @property
    def open_issues(self) -> int | None:
        """Open issues = repo's combined open count minus open PRs."""
        if self.open_prs is None:
            return None
        return max(0, (self.repo.get("open_issues_count") or 0) - self.open_prs)


def _fork_status(client: GitHubClient, repo: dict[str, Any]) -> dict[str, Any] | None:
    """Ahead/behind counts for a fork vs its upstream default branch."""
    full = repo["full_name"]
    parent = repo.get("parent")
    if not parent:
        parent = client.get_repo(full).get("parent")
    if not parent:
        return None
    base = f"{parent['owner']['login']}:{parent.get('default_branch') or 'HEAD'}"
    head = repo.get("default_branch") or "HEAD"
    cmp = client.compare(full, f"{base}...{head}")
    if cmp is None:
        return None
    return {
        "parent": parent["full_name"],
        "ahead_by": cmp.get("ahead_by", 0),
        "behind_by": cmp.get("behind_by", 0),
        "status": cmp.get("status", ""),
    }


def _actions_storage(client: GitHubClient, full_name: str) -> dict[str, Any] | None:
    """Artifact count + cache usage; None when Actions data is unavailable."""
    count = client.get_artifact_count(full_name)
    usage = client.get_actions_cache_usage(full_name)
    if count is None or usage is None:
        return None
    return {
        "artifact_count": count,
        "cache_bytes": usage.get("active_caches_size_in_bytes", 0),
        "cache_count": usage.get("active_caches_count", 0),
    }


def fetch_details(client: GitHubClient, repo: dict[str, Any]) -> RepoDetails:
    """Fetch all detail fields concurrently; each degrades independently."""
    full = repo["full_name"]
    tasks: dict[str, Callable[[], Any]] = {
        "languages": lambda: client.get_languages(full),
        "latest_release": lambda: client.get_latest_release(full),
        "latest_run": lambda: client.get_latest_workflow_run(full),
        "pages": lambda: client.get_pages_info(full),
        "traffic": lambda: client.get_traffic(full),
        "open_prs": lambda: client.get_open_pr_count(full),
        "dependabot_alerts": lambda: client.get_open_dependabot_alert_count(full),
        "secret_alerts": lambda: client.get_open_secret_alert_count(full),
        "vulnerability_alerts_enabled": lambda: client.get_vulnerability_alerts_enabled(full),
        "actions_storage": lambda: _actions_storage(client, full),
    }
    if repo.get("fork"):
        tasks["fork_status"] = lambda: _fork_status(client, repo)
    details = RepoDetails(repo=repo)
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {name: pool.submit(fn) for name, fn in tasks.items()}
        values: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for name, fut in futures.items():
            try:
                values[name] = fut.result()
            except GitHubError as e:
                errors[name] = f"error: {e}"
    # Executor context exit joins all threads, so every capability mark has
    # landed before any hint check below — no intra-fetch ordering sensitivity.
    for name, err in errors.items():
        details.hints[name] = err
    for name, value in values.items():
        setattr(details, name, value)
        if value is None and client.capabilities.resolve(FIELD_FAMILIES[name]) is False:
            details.hints[name] = client.capabilities.hint(FIELD_FAMILIES[name])
    return details


def build_delete_warnings(repo: dict[str, Any], pinned: set[str]) -> list[str]:
    """Compose human warnings shown before a repo is deleted."""
    warnings: list[str] = []
    forks = repo.get("forks_count") or 0
    if forks:
        s = "s" if forks != 1 else ""
        warnings.append(f"⚠ has {forks} fork{s} (they survive, but lose their upstream)")
    stars = repo.get("stargazers_count") or 0
    if stars:
        s = "s" if stars != 1 else ""
        warnings.append(f"★ {stars} star{s}")
    if repo.get("full_name") in pinned:
        warnings.append("📌 pinned on your profile")
    if not repo.get("private"):
        warnings.append("🌐 public repo")
    return warnings


def unique_path(path: Path) -> Path:
    """Return `path`, or the first `-N`-suffixed variant that doesn't exist."""
    if not path.exists():
        return path
    stem = path.name.removesuffix(".tar.gz")
    n = 1
    while True:
        candidate = path.with_name(f"{stem}-{n}.tar.gz")
        if not candidate.exists():
            return candidate
        n += 1


def backup_repo(client: GitHubClient, repo: dict[str, Any], dest_dir: Path) -> Path:
    """Download `{name}-{default_branch}.tar.gz` into `dest_dir`; returns the path."""
    branch = repo.get("default_branch") or "HEAD"
    dest = unique_path(dest_dir / f"{repo['name']}-{branch}.tar.gz")
    dest_dir.mkdir(parents=True, exist_ok=True)
    return client.download_tarball(repo["full_name"], branch, dest)


def probe_capabilities(client: GitHubClient) -> None:
    """Resolve unknown READ families with one cheap call each against the newest repo.

    Write families cannot be probed non-destructively and stay unknown.
    """
    try:
        r = client._request(
            "GET",
            "/user/repos",
            params={"per_page": 1, "sort": "updated", "affiliation": "owner"},
        )
    except GitHubError:
        return
    if r.status_code != 200:
        return
    batch = r.json()
    if not batch:
        return
    full = batch[0]["full_name"]
    client.get_readme(full)
    client.get_latest_workflow_run(full)
    client.get_pages_info(full)
    # admin.read is probed via the vulnerability-alerts check, not traffic:
    # traffic 403s conflate missing push access and never teach the cache.
    client.get_vulnerability_alerts_enabled(full)
    client.get_open_pr_count(full)
    client.get_open_dependabot_alert_count(full)
    client.get_open_secret_alert_count(full)


def _fmt_date(value: str | None) -> str:
    return (value or "")[:10] or "—"


def render_details(details: RepoDetails) -> Table:
    """Two-column Rich grid shared by `gman info` and the TUI detail screen."""
    repo = details.repo

    def dash(field_name: str) -> str:
        hint = details.hints.get(field_name)
        return f"— [dim]({hint})[/dim]" if hint else "—"

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold", no_wrap=True)
    grid.add_column(overflow="fold")

    badges = []
    if repo.get("archived"):
        badges.append("📦 archived")
    vis = "🔒 private" if repo.get("private") else "🌐 public"
    grid.add_row("Repository", f"{escape(repo.get('full_name', ''))}  {vis} {' '.join(badges)}")
    grid.add_row("Description", escape(repo.get("description") or "") or "—")
    topics = repo.get("topics") or []
    grid.add_row("Topics", escape(", ".join(topics)) if topics else "—")
    lic = repo.get("license") or {}
    grid.add_row(
        "Facts",
        f"★ {repo.get('stargazers_count', 0)}  ⑂ {repo.get('forks_count', 0)}  "
        f"size {repo.get('size', 0)} KB  license {escape(lic.get('spdx_id') or '') or '—'}  "
        f"branch {escape(repo.get('default_branch') or '') or '—'}",
    )
    grid.add_row(
        "Dates",
        f"created {_fmt_date(repo.get('created_at'))}  "
        f"updated {_fmt_date(repo.get('updated_at'))}  "
        f"pushed {_fmt_date(repo.get('pushed_at'))}",
    )

    if details.languages:
        total = sum(details.languages.values()) or 1
        top = sorted(details.languages.items(), key=lambda kv: -kv[1])[:5]
        grid.add_row("Languages", "  ".join(f"{k} {v * 100 // total}%" for k, v in top))
    else:
        grid.add_row("Languages", dash("languages"))

    lr = details.latest_release
    grid.add_row(
        "Latest release",
        f"{escape(lr.get('tag_name') or '')} — {_fmt_date(lr.get('published_at'))}"
        if lr
        else dash("latest_release"),
    )

    run = details.latest_run
    if run:
        glyph = {"success": "✅", "failure": "❌"}.get(run.get("conclusion") or "", "…")
        grid.add_row(
            "Latest CI run",
            f"{glyph} {escape(run.get('name') or 'workflow')} "
            f"({run.get('conclusion') or run.get('status')}) {_fmt_date(run.get('created_at'))}",
        )
    else:
        grid.add_row("Latest CI run", dash("latest_run"))

    grid.add_row(
        "Pages",
        details.pages.get("html_url", "—") if details.pages else dash("pages"),
    )

    t = details.traffic
    grid.add_row(
        "Traffic (14d)",
        f"{t['views']} views ({t['unique_views']} unique), "
        f"{t['clones']} clones ({t['unique_clones']} unique)"
        if t
        else dash("traffic"),
    )

    if details.open_prs is not None:
        grid.add_row("Open items", f"{details.open_issues} issues / {details.open_prs} PRs")
    else:
        grid.add_row("Open items", dash("open_prs"))

    if repo.get("fork"):
        fs = details.fork_status
        if fs:
            grid.add_row(
                "Fork",
                f"⑂ fork of {escape(fs['parent'])} — "
                f"{fs['ahead_by']} ahead / {fs['behind_by']} behind",
            )
        else:
            grid.add_row("Fork", dash("fork_status"))

    dep = (
        str(details.dependabot_alerts)
        if details.dependabot_alerts is not None
        else dash("dependabot_alerts")
    )
    sec = str(details.secret_alerts) if details.secret_alerts is not None else dash("secret_alerts")
    if details.vulnerability_alerts_enabled is None:
        va = dash("vulnerability_alerts_enabled")
    else:
        va = "ON" if details.vulnerability_alerts_enabled else "OFF"
    grid.add_row(
        "Security",
        f"Dependabot alerts: {dep} · secret-scanning: {sec} · vulnerability alerts: {va}",
    )

    st = details.actions_storage
    if st is not None:
        mb = st["cache_bytes"] / 1_000_000
        grid.add_row(
            "Actions storage",
            f"{st['artifact_count']} artifacts · {mb:.0f} MB cache ({st['cache_count']} entries)",
        )
    else:
        grid.add_row("Actions storage", dash("actions_storage"))
    return grid


def details_to_dict(details: RepoDetails) -> dict[str, Any]:
    """JSON-safe projection for `gman info --json` (unavailable fields are null)."""
    repo = details.repo
    lr = details.latest_release
    run = details.latest_run
    return {
        "name": repo.get("name"),
        "full_name": repo.get("full_name"),
        "description": repo.get("description"),
        "private": repo.get("private"),
        "archived": repo.get("archived"),
        "visibility": repo.get("visibility"),
        "default_branch": repo.get("default_branch"),
        "stargazers_count": repo.get("stargazers_count"),
        "forks_count": repo.get("forks_count"),
        "size": repo.get("size"),
        "topics": repo.get("topics") or [],
        "created_at": repo.get("created_at"),
        "updated_at": repo.get("updated_at"),
        "pushed_at": repo.get("pushed_at"),
        "html_url": repo.get("html_url"),
        "languages": details.languages,
        "latest_release": (
            {
                "tag": lr.get("tag_name"),
                "name": lr.get("name"),
                "published_at": lr.get("published_at"),
            }
            if lr
            else None
        ),
        "latest_run": (
            {
                "name": run.get("name"),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "created_at": run.get("created_at"),
            }
            if run
            else None
        ),
        "pages_url": details.pages.get("html_url") if details.pages else None,
        "traffic": details.traffic,
        "open_prs": details.open_prs,
        "open_issues": details.open_issues,
        "fork_status": details.fork_status,
        "dependabot_alerts": details.dependabot_alerts,
        "secret_alerts": details.secret_alerts,
        "vulnerability_alerts_enabled": details.vulnerability_alerts_enabled,
        "actions_storage": details.actions_storage,
    }
