"""Bulk write operations: topic validation, op registry, sequential runner."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from gman.client import GitHubClient, RateLimitError

_TOPIC_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,49}$")
MAX_TOPICS = 20


def normalize_topics(raw: str) -> tuple[list[str], list[str]]:
    """Parse a comma/space separated topic string into `(valid, errors)`.

    Lowercases, dedupes preserving order, and validates GitHub's topic rules
    (start alphanumeric; lowercase letters/numbers/hyphens; ≤50 chars; ≤20
    topics per repo).
    """
    valid: list[str] = []
    errors: list[str] = []
    for part in re.split(r"[,\s]+", raw.strip()):
        if not part:
            continue
        topic = part.lower()
        if topic in valid:
            continue
        if not _TOPIC_RE.match(topic):
            errors.append(
                f"invalid topic {topic!r} (lowercase letters, numbers, hyphens; max 50 chars)"
            )
            continue
        valid.append(topic)
    if len(valid) > MAX_TOPICS:
        errors.append(f"too many topics ({len(valid)}); GitHub allows at most {MAX_TOPICS}")
    return valid, errors


@dataclass(frozen=True)
class BulkOp:
    """One bulk operation: a label plus an apply(client, repo) callable."""

    key: str
    label: str
    apply: Callable[[GitHubClient, dict[str, Any]], tuple[bool, str]]


@dataclass
class BulkResult:
    """Outcome of one op applied to one repo."""

    full_name: str
    op_label: str
    ok: bool
    msg: str
    skipped: bool = False


def fields_op(fields: dict[str, Any], label: str, key: str = "fields") -> BulkOp:
    """An op that PATCHes the same settings fields onto each repo."""
    return BulkOp(key, label, lambda client, repo: client.update_repo(repo["full_name"], fields))


def add_topic_op(topic: str) -> BulkOp:
    """Add one topic (read-modify-write; no-op success when already present)."""

    def apply(client: GitHubClient, repo: dict[str, Any]) -> tuple[bool, str]:
        current = list(repo.get("topics") or [])
        if topic in current:
            return True, f"{repo['full_name']} already has topic {topic!r}"
        return client.set_topics(repo["full_name"], [*current, topic])

    return BulkOp("add_topic", f"Add topic {topic!r}", apply)


def remove_topic_op(topic: str) -> BulkOp:
    """Remove one topic (no-op success when absent)."""

    def apply(client: GitHubClient, repo: dict[str, Any]) -> tuple[bool, str]:
        current = list(repo.get("topics") or [])
        if topic not in current:
            return True, f"{repo['full_name']} does not have topic {topic!r}"
        return client.set_topics(repo["full_name"], [t for t in current if t != topic])

    return BulkOp("remove_topic", f"Remove topic {topic!r}", apply)


def vulnerability_alerts_op(enabled: bool) -> BulkOp:
    state = "ON" if enabled else "OFF"
    return BulkOp(
        "vulnerability_alerts",
        f"Vulnerability alerts → {state}",
        lambda client, repo: client.set_vulnerability_alerts(repo["full_name"], enabled),
    )


def security_fixes_op(enabled: bool) -> BulkOp:
    state = "ON" if enabled else "OFF"
    return BulkOp(
        "security_fixes",
        f"Automated security fixes → {state}",
        lambda client, repo: client.set_automated_security_fixes(repo["full_name"], enabled),
    )


def sync_fork_op() -> BulkOp:
    """Sync forks with their upstream default branch; non-forks are skipped."""

    def apply(client: GitHubClient, repo: dict[str, Any]) -> tuple[bool, str]:
        if not repo.get("fork"):
            return True, f"{repo['full_name']} is not a fork — skipped"
        branch = repo.get("default_branch") or "HEAD"
        return client.merge_upstream(repo["full_name"], branch)

    return BulkOp("sync_fork", "Sync fork with upstream", apply)


def _clear_items_op(
    key: str,
    label: str,
    list_fn_name: str,
    delete_fn_name: str,
    noun: str,
) -> BulkOp:
    """Shared shape for artifact/cache cleanup: list, then delete one at a time."""

    def apply(client: GitHubClient, repo: dict[str, Any]) -> tuple[bool, str]:
        full = repo["full_name"]
        items = getattr(client, list_fn_name)(full)
        if items is None:
            return False, f"{full}: {noun}s unavailable (permission?)"
        if not items:
            return True, f"{full}: no {noun}s"
        deleted = 0
        freed = 0
        for item in items:
            ok, msg = getattr(client, delete_fn_name)(full, item["id"])
            if not ok:
                return False, f"{full}: deleted {deleted}, then failed: {msg}"
            deleted += 1
            freed += item.get("size_in_bytes") or 0
        return True, f"{full}: deleted {deleted} {noun}s ({freed / 1_000_000:.0f} MB)"

    return BulkOp(key, label, apply)


def clear_artifacts_op() -> BulkOp:
    """Delete every Actions artifact on each repo."""
    return _clear_items_op(
        "clear_artifacts",
        "Clear Actions artifacts",
        "list_artifacts",
        "delete_artifact",
        "artifact",
    )


def clear_caches_op() -> BulkOp:
    """Delete every Actions cache entry on each repo."""
    return _clear_items_op(
        "clear_caches", "Clear Actions caches", "list_caches", "delete_cache", "cache"
    )


# (key, label, needs_topic) — display order for the TUI bulk menu.
TUI_BULK_MENU: list[tuple[str, str, bool]] = [
    ("archive", "Archive", False),
    ("unarchive", "Unarchive", False),
    ("dbom_on", "Delete branch on merge → ON", False),
    ("dbom_off", "Delete branch on merge → OFF", False),
    ("wiki_off", "Wiki → OFF", False),
    ("wiki_on", "Wiki → ON", False),
    ("issues_on", "Issues → ON", False),
    ("issues_off", "Issues → OFF", False),
    ("projects_off", "Projects → OFF", False),
    ("projects_on", "Projects → ON", False),
    ("add_topic", "Add topic…", True),
    ("remove_topic", "Remove topic…", True),
    ("vuln_on", "Vulnerability alerts → ON", False),
    ("vuln_off", "Vulnerability alerts → OFF", False),
    ("secfix_on", "Automated security fixes → ON", False),
    ("secfix_off", "Automated security fixes → OFF", False),
    ("sync_fork", "Sync fork with upstream", False),
    ("clear_artifacts", "Clear Actions artifacts", False),
    ("clear_caches", "Clear Actions caches", False),
]


def build_menu_op(key: str, arg: str | None = None) -> BulkOp:
    """Resolve a TUI menu key (plus topic arg where needed) to a BulkOp."""
    simple: dict[str, BulkOp] = {
        "archive": fields_op({"archived": True}, "Archive", key="archive"),
        "unarchive": fields_op({"archived": False}, "Unarchive", key="unarchive"),
        "dbom_on": fields_op(
            {"delete_branch_on_merge": True}, "Delete branch on merge → ON", key="dbom_on"
        ),
        "dbom_off": fields_op(
            {"delete_branch_on_merge": False}, "Delete branch on merge → OFF", key="dbom_off"
        ),
        "wiki_on": fields_op({"has_wiki": True}, "Wiki → ON", key="wiki_on"),
        "wiki_off": fields_op({"has_wiki": False}, "Wiki → OFF", key="wiki_off"),
        "issues_on": fields_op({"has_issues": True}, "Issues → ON", key="issues_on"),
        "issues_off": fields_op({"has_issues": False}, "Issues → OFF", key="issues_off"),
        "projects_on": fields_op({"has_projects": True}, "Projects → ON", key="projects_on"),
        "projects_off": fields_op({"has_projects": False}, "Projects → OFF", key="projects_off"),
        "vuln_on": vulnerability_alerts_op(True),
        "vuln_off": vulnerability_alerts_op(False),
        "secfix_on": security_fixes_op(True),
        "secfix_off": security_fixes_op(False),
        "sync_fork": sync_fork_op(),
        "clear_artifacts": clear_artifacts_op(),
        "clear_caches": clear_caches_op(),
    }
    if key in simple:
        return simple[key]
    if key in ("add_topic", "remove_topic"):
        if not arg:
            raise ValueError(f"{key} requires a topic argument")
        return add_topic_op(arg) if key == "add_topic" else remove_topic_op(arg)
    raise ValueError(f"unknown bulk op {key!r}")


def run_bulk(
    client: GitHubClient,
    repos: list[dict[str, Any]],
    ops: list[BulkOp],
    progress: Callable[[int, int], None] | None = None,
) -> list[BulkResult]:
    """Apply each op to each repo, strictly sequentially.

    GitHub's secondary rate limits punish concurrent writes — never
    parallelize this. Per-op failures are recorded and the run continues; a
    `RateLimitError` aborts the run and the remaining pairs are recorded as
    `skipped=True` with the rate-limit message. `progress` fires for every repo,
    including skipped ones, so callers' progress displays always reach `total`.
    """
    if not repos or not ops:
        return []
    results: list[BulkResult] = []
    aborted_msg: str | None = None
    total = len(repos)
    for done, repo in enumerate(repos, start=1):
        for op in ops:
            if aborted_msg is not None:
                results.append(
                    BulkResult(repo["full_name"], op.label, False, aborted_msg, skipped=True)
                )
                continue
            try:
                ok, msg = op.apply(client, repo)
            except RateLimitError as e:
                aborted_msg = str(e)
                results.append(
                    BulkResult(repo["full_name"], op.label, False, aborted_msg, skipped=True)
                )
                continue
            results.append(BulkResult(repo["full_name"], op.label, ok, msg))
        if progress is not None:
            progress(done, total)
    return results
