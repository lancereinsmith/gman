"""Tests for topic validation and the bulk-op registry."""

from __future__ import annotations

import json as jsonlib

import pytest
import responses
from conftest import make_repo

from gman.bulk import (
    TUI_BULK_MENU,
    BulkOp,
    add_topic_op,
    build_menu_op,
    fields_op,
    normalize_topics,
    remove_topic_op,
    run_bulk,
)
from gman.client import DEFAULT_API_URL, GitHubClient


@pytest.fixture
def client() -> GitHubClient:
    return GitHubClient(token="fake-token")


def test_normalize_topics_parses_and_dedupes() -> None:
    valid, errors = normalize_topics("CLI, github cli,  tui")
    assert valid == ["cli", "github", "tui"]
    assert errors == []


def test_normalize_topics_rejects_bad_charset_and_length() -> None:
    valid, errors = normalize_topics("good, Bad_Topic!, -leading, " + "x" * 51)
    assert valid == ["good"]
    assert len(errors) == 3


def test_normalize_topics_rejects_too_many() -> None:
    raw = ",".join(f"t{i}" for i in range(21))
    valid, errors = normalize_topics(raw)
    assert len(valid) == 21  # parsed, but flagged
    assert any("at most 20" in e for e in errors)


@responses.activate
def test_add_topic_op_appends_to_current(client: GitHubClient) -> None:
    responses.add(responses.PUT, f"{DEFAULT_API_URL}/repos/octocat/r/topics", json={}, status=200)
    repo = make_repo("r", topics=["existing"])

    ok, _ = add_topic_op("new").apply(client, repo)

    assert ok
    assert jsonlib.loads(responses.calls[0].request.body) == {"names": ["existing", "new"]}


def test_add_topic_op_noop_when_present(client: GitHubClient) -> None:
    repo = make_repo("r", topics=["existing"])
    ok, msg = add_topic_op("existing").apply(client, repo)
    assert ok and "already" in msg  # no HTTP call registered — would error if attempted


def test_remove_topic_op_noop_when_absent(client: GitHubClient) -> None:
    repo = make_repo("r", topics=["other"])
    ok, msg = remove_topic_op("gone").apply(client, repo)
    assert ok and "does not have" in msg


@responses.activate
def test_fields_op_patches(client: GitHubClient) -> None:
    responses.add(responses.PATCH, f"{DEFAULT_API_URL}/repos/octocat/r", json={}, status=200)
    op = fields_op({"delete_branch_on_merge": True}, "DBOM on")

    ok, _ = op.apply(client, make_repo("r"))

    assert ok and op.label == "DBOM on"


def test_build_menu_op_covers_every_menu_key() -> None:
    for key, _label, needs_topic in TUI_BULK_MENU:
        op = build_menu_op(key, "sometopic" if needs_topic else None)
        assert op.label


def test_build_menu_op_errors() -> None:
    with pytest.raises(ValueError):
        build_menu_op("nonsense")
    with pytest.raises(ValueError):
        build_menu_op("add_topic", None)  # missing required topic


def _op(label: str, results: dict[str, tuple[bool, str]]) -> BulkOp:
    """Test op whose outcome per full_name is table-driven."""
    return BulkOp("test", label, lambda client, repo: results[repo["full_name"]])


def test_run_bulk_continues_on_failure(client: GitHubClient) -> None:
    repos = [make_repo("a"), make_repo("b")]
    op = _op("Op", {"octocat/a": (False, "boom"), "octocat/b": (True, "fine")})
    seen: list[tuple[int, int]] = []

    results = run_bulk(client, repos, [op], progress=lambda d, t: seen.append((d, t)))

    assert [(r.full_name, r.ok, r.skipped) for r in results] == [
        ("octocat/a", False, False),
        ("octocat/b", True, False),
    ]
    assert seen == [(1, 2), (2, 2)]


def test_run_bulk_rate_limit_aborts_remainder(client: GitHubClient) -> None:
    from gman.client import RateLimitError

    repos = [make_repo("a"), make_repo("b"), make_repo("c")]

    def apply(cl: GitHubClient, repo: dict) -> tuple[bool, str]:
        if repo["full_name"] == "octocat/b":
            raise RateLimitError("rate limit exceeded")
        return True, "ok"

    results = run_bulk(client, repos, [BulkOp("t", "Op", apply)])

    assert [(r.full_name, r.ok, r.skipped) for r in results] == [
        ("octocat/a", True, False),
        ("octocat/b", False, True),
        ("octocat/c", False, True),
    ]
    assert "rate limit" in results[2].msg


def test_run_bulk_progress_still_fires_after_abort(client: GitHubClient) -> None:
    """Progress reaches total even when an abort skips the tail (UIs rely on this)."""
    from gman.client import RateLimitError

    repos = [make_repo("a"), make_repo("b"), make_repo("c")]

    def apply(cl: GitHubClient, repo: dict) -> tuple[bool, str]:
        if repo["full_name"] == "octocat/a":
            raise RateLimitError("rate limit exceeded")
        return True, "ok"

    seen: list[tuple[int, int]] = []
    run_bulk(client, repos, [BulkOp("t", "Op", apply)], progress=lambda d, t: seen.append((d, t)))

    assert seen == [(1, 3), (2, 3), (3, 3)]


def test_run_bulk_empty_inputs(client: GitHubClient) -> None:
    assert run_bulk(client, [], [_op("x", {})]) == []
    assert run_bulk(client, [make_repo("a")], []) == []


def test_sync_fork_op_skips_non_forks(client: GitHubClient) -> None:
    from gman.bulk import sync_fork_op

    ok, msg = sync_fork_op().apply(client, make_repo("r"))  # fork=False, no mock registered
    assert ok and "skipped" in msg


@responses.activate
def test_sync_fork_op_merges_forks(client: GitHubClient) -> None:
    from gman.bulk import sync_fork_op

    responses.add(
        responses.POST,
        f"{DEFAULT_API_URL}/repos/octocat/f/merge-upstream",
        json={"merge_type": "fast-forward"},
        status=200,
    )
    ok, msg = sync_fork_op().apply(client, make_repo("f", fork=True))
    assert ok and "Synced" in msg
    assert jsonlib.loads(responses.calls[0].request.body) == {"branch": "main"}


def test_menu_includes_sync_fork() -> None:
    from gman.bulk import TUI_BULK_MENU, build_menu_op

    assert ("sync_fork", "Sync fork with upstream", False) in TUI_BULK_MENU
    assert build_menu_op("sync_fork").label == "Sync fork with upstream"


@responses.activate
def test_clear_artifacts_op_deletes_all(client: GitHubClient) -> None:
    from gman.bulk import clear_artifacts_op

    full = "octocat/r"
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/{full}/actions/artifacts",
        json={
            "total_count": 2,
            "artifacts": [
                {"id": 1, "size_in_bytes": 1_000_000},
                {"id": 2, "size_in_bytes": 2_000_000},
            ],
        },
        status=200,
    )
    responses.add(
        responses.DELETE, f"{DEFAULT_API_URL}/repos/{full}/actions/artifacts/1", status=204
    )
    responses.add(
        responses.DELETE, f"{DEFAULT_API_URL}/repos/{full}/actions/artifacts/2", status=204
    )

    ok, msg = clear_artifacts_op().apply(client, make_repo("r"))
    assert ok and "2 artifacts" in msg and "3 MB" in msg


def test_clear_artifacts_op_empty_and_unavailable(client: GitHubClient) -> None:
    from gman.bulk import clear_artifacts_op

    class FakeEmpty:
        def list_artifacts(self, full):
            return []

    class FakeDenied:
        def list_artifacts(self, full):
            return None

    ok1, msg1 = clear_artifacts_op().apply(FakeEmpty(), make_repo("r"))
    ok2, msg2 = clear_artifacts_op().apply(FakeDenied(), make_repo("r"))
    assert ok1 and "no artifacts" in msg1
    assert not ok2 and "unavailable" in msg2


def test_clear_caches_op_partial_failure(client: GitHubClient) -> None:
    from gman.bulk import clear_caches_op

    class FakePartial:
        def list_caches(self, full):
            return [
                {"id": 1, "size_in_bytes": 10},
                {"id": 2, "size_in_bytes": 10},
            ]

        def delete_cache(self, full, cache_id):
            if cache_id == 2:
                return False, "HTTP 500: boom"
            return True, "ok"

    ok, msg = clear_caches_op().apply(FakePartial(), make_repo("r"))
    assert not ok and "deleted 1" in msg and "failed" in msg


def test_menu_includes_cleanup_ops() -> None:
    from gman.bulk import TUI_BULK_MENU, build_menu_op

    assert ("clear_artifacts", "Clear Actions artifacts", False) in TUI_BULK_MENU
    assert ("clear_caches", "Clear Actions caches", False) in TUI_BULK_MENU
    assert build_menu_op("clear_artifacts").label == "Clear Actions artifacts"
    assert build_menu_op("clear_caches").label == "Clear Actions caches"
