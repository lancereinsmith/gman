"""Tests for detail fetching, delete warnings, backup helpers, and rendering."""

from __future__ import annotations

from pathlib import Path

import pytest
import responses
from conftest import make_repo

from gman.client import DEFAULT_API_URL, GitHubClient
from gman.details import (
    RepoDetails,
    backup_repo,
    build_delete_warnings,
    details_to_dict,
    fetch_details,
    probe_capabilities,
    render_details,
    unique_path,
)


@pytest.fixture
def client() -> GitHubClient:
    return GitHubClient(token="fake-token")


def test_build_delete_warnings() -> None:
    repo = make_repo("r", forks_count=2, stargazers_count=1, private=False)
    warnings = build_delete_warnings(repo, pinned={"octocat/r"})
    joined = "\n".join(warnings)
    assert "2 forks" in joined
    assert "1 star" in joined
    assert "pinned" in joined
    assert "public" in joined


def test_build_delete_warnings_quiet_for_boring_private_repo() -> None:
    repo = make_repo("r", private=True)
    assert build_delete_warnings(repo, pinned=set()) == []


def test_unique_path_suffixes(tmp_path: Path) -> None:
    p = tmp_path / "r-main.tar.gz"
    assert unique_path(p) == p
    p.write_bytes(b"x")
    assert unique_path(p) == tmp_path / "r-main-1.tar.gz"
    (tmp_path / "r-main-1.tar.gz").write_bytes(b"x")
    assert unique_path(p) == tmp_path / "r-main-2.tar.gz"


def test_open_issues_derived() -> None:
    d = RepoDetails(repo=make_repo("r", open_issues_count=10), open_prs=3)
    assert d.open_issues == 7
    d2 = RepoDetails(repo=make_repo("r"), open_prs=None)
    assert d2.open_issues is None


@responses.activate
def test_fetch_details_degrades_per_field(client: GitHubClient) -> None:
    """One denied family must not poison the other fields."""
    full = "octocat/r"
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/{full}/languages",
        json={"Python": 100},
        status=200,
    )
    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/{full}/releases/latest", status=404)
    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/{full}/actions/runs", status=403)
    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/{full}/pages", status=404)
    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/{full}/traffic/views", status=403)
    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/{full}/traffic/clones", status=403)
    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/{full}/pulls", json=[], status=200)
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/{full}/dependabot/alerts",
        json=[],
        status=200,
    )
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/{full}/secret-scanning/alerts",
        status=404,
    )
    # 403 (not 204): a 204 would mark admin.read allowed and race against
    # traffic's 403 denial mark, making the traffic-hint assertion flaky.
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/{full}/vulnerability-alerts",
        status=403,
    )
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/{full}/actions/artifacts",
        status=403,
    )
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/{full}/actions/cache/usage",
        status=403,
    )

    details = fetch_details(client, make_repo("r"))

    assert details.languages == {"Python": 100}
    assert details.latest_release is None and "latest_release" not in details.hints
    assert details.latest_run is None and "latest_run" in details.hints  # denied → hinted
    assert details.open_prs == 0
    assert details.open_issues == 0
    # traffic denied (admin.read) → None + hinted
    assert details.traffic is None and "traffic" in details.hints
    assert details.vulnerability_alerts_enabled is None
    assert details.actions_storage is None and "actions_storage" in details.hints
    # pages absent (404 = true absence) → None WITHOUT hint
    assert details.pages is None and "pages" not in details.hints


@responses.activate
def test_traffic_denied_with_vuln_enabled_is_deterministic(client: GitHubClient) -> None:
    """Token with admin.read but no push: vuln-check succeeds, traffic 403s.

    admin.read must deterministically resolve True (vuln is definitive; traffic
    is push-confounded), and traffic renders unhinted.
    """
    full = "octocat/r"
    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/{full}/languages", json={}, status=200)
    for path in ("releases/latest", "actions/runs", "pages"):
        responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/{full}/{path}", status=404)
    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/{full}/traffic/views", status=403)
    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/{full}/pulls", json=[], status=200)
    responses.add(
        responses.GET, f"{DEFAULT_API_URL}/repos/{full}/dependabot/alerts", json=[], status=200
    )
    responses.add(
        responses.GET, f"{DEFAULT_API_URL}/repos/{full}/secret-scanning/alerts", status=404
    )
    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/{full}/vulnerability-alerts", status=204)
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/{full}/actions/artifacts",
        json={"total_count": 0, "artifacts": []},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/{full}/actions/cache/usage",
        json={"active_caches_size_in_bytes": 0, "active_caches_count": 0},
        status=200,
    )

    details = fetch_details(client, make_repo("r"))

    assert details.vulnerability_alerts_enabled is True
    assert details.traffic is None and "traffic" not in details.hints
    assert client.capabilities.resolve("admin.read") is True


@responses.activate
def test_backup_repo_names_file(client: GitHubClient, tmp_path: Path) -> None:
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/octocat/r/tarball/main",
        body=b"bytes",
        status=200,
    )
    path = backup_repo(client, make_repo("r"), tmp_path)
    assert path == tmp_path / "r-main.tar.gz"
    assert path.read_bytes() == b"bytes"


def test_render_and_dict_shapes() -> None:
    details = RepoDetails(
        repo=make_repo("r"),
        languages={"Python": 75, "Shell": 25},
        open_prs=1,
        hints={"traffic": "needs Administration: read"},
    )
    table = render_details(details)  # must not raise
    assert table.row_count > 0
    d = details_to_dict(details)
    assert d["full_name"] == "octocat/r"
    assert d["traffic"] is None
    assert d["open_prs"] == 1


@responses.activate
def test_probe_capabilities_marks_read_families(client: GitHubClient) -> None:
    full = "octocat/newest"
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/user/repos",
        json=[make_repo("newest")],
        status=200,
    )
    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/{full}/readme", body="# x", status=200)
    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/{full}/actions/runs", status=403)
    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/{full}/pages", status=404)
    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/{full}/vulnerability-alerts", status=403)
    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/{full}/pulls", json=[], status=200)
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/{full}/dependabot/alerts",
        json=[],
        status=200,
    )
    responses.add(
        responses.GET, f"{DEFAULT_API_URL}/repos/{full}/secret-scanning/alerts", status=404
    )

    probe_capabilities(client)

    caps = client.capabilities
    assert caps.resolve("contents.read") is True
    assert caps.resolve("actions.read") is False
    assert caps.resolve("pages.read") is True  # 404 = authz passed
    # admin.read is probed via the vulnerability-alerts check (definitive),
    # since traffic 403s are push-access-confounded and never teach the cache.
    assert caps.resolve("admin.read") is False
    assert caps.resolve("pulls.read") is True
    assert caps.resolve("dependabot.read") is True
    assert caps.resolve("secret_scanning.read") is True  # 404 = authz passed


@responses.activate
def test_probe_capabilities_silent_on_failure(client: GitHubClient) -> None:
    responses.add(responses.GET, f"{DEFAULT_API_URL}/user/repos", status=401)
    probe_capabilities(client)  # must not raise


def test_render_details_survives_markup_in_metadata() -> None:
    from io import StringIO

    from rich.console import Console

    details = RepoDetails(
        repo=make_repo("r", description="see [/] notes [WIP]", topics=["a[b]c"]),
        open_prs=0,
    )
    console = Console(file=StringIO(), width=200)
    console.print(render_details(details))  # must not raise MarkupError
    out = console.file.getvalue()
    assert "[WIP]" in out
    assert "a[b]c" in out


@responses.activate
def test_backup_repo_creates_missing_dir(client: GitHubClient, tmp_path: Path) -> None:
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/octocat/r/tarball/main",
        body=b"bytes",
        status=200,
    )
    dest = tmp_path / "does" / "not" / "exist"
    path = backup_repo(client, make_repo("r"), dest)
    assert path == dest / "r-main.tar.gz"
    assert path.read_bytes() == b"bytes"


@responses.activate
def test_probe_capabilities_silent_on_rate_limit(client: GitHubClient) -> None:
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/user/repos",
        json={"message": "rate limited"},
        status=403,
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1893456000"},
    )
    probe_capabilities(client)  # must not raise


@responses.activate
def test_fork_status_uses_parent_passthrough(client: GitHubClient) -> None:
    """When the repo dict already carries `parent`, no extra get_repo call happens."""
    full = "octocat/fork"
    repo = make_repo(
        "fork",
        fork=True,
        parent={
            "full_name": "upstream/orig",
            "default_branch": "main",
            "owner": {"login": "upstream"},
        },
    )
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/{full}/compare/upstream:main...main",
        json={"ahead_by": 1, "behind_by": 3, "status": "diverged"},
        status=200,
    )
    from gman.details import _fork_status

    status = _fork_status(client, repo)

    assert status == {
        "parent": "upstream/orig",
        "ahead_by": 1,
        "behind_by": 3,
        "status": "diverged",
    }
    assert len(responses.calls) == 1  # compare only — no get_repo


@responses.activate
def test_fork_status_fetches_parent_when_missing(client: GitHubClient) -> None:
    full = "octocat/fork"
    repo = make_repo("fork", fork=True)
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/{full}",
        json=make_repo(
            "fork",
            fork=True,
            parent={
                "full_name": "upstream/orig",
                "default_branch": "dev",
                "owner": {"login": "upstream"},
            },
        ),
        status=200,
    )
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/{full}/compare/upstream:dev...main",
        json={"ahead_by": 0, "behind_by": 5, "status": "behind"},
        status=200,
    )
    from gman.details import _fork_status

    status = _fork_status(client, repo)
    assert status is not None and status["behind_by"] == 5


@responses.activate
def test_fetch_details_fork_task_only_for_forks(client: GitHubClient) -> None:
    full = "octocat/r"
    for path in ("languages", "releases/latest", "actions/runs", "pages", "pulls"):
        responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/{full}/{path}", json={}, status=404)
    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/{full}/traffic/views", status=403)
    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/{full}/traffic/clones", status=403)
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/{full}/dependabot/alerts",
        json=[],
        status=200,
    )
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/{full}/secret-scanning/alerts",
        status=404,
    )
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/{full}/vulnerability-alerts",
        status=204,
    )
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/{full}/actions/artifacts",
        json={"total_count": 0, "artifacts": []},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/{full}/actions/cache/usage",
        json={"active_caches_size_in_bytes": 0, "active_caches_count": 0},
        status=200,
    )

    details = fetch_details(client, make_repo("r"))  # fork=False

    assert details.fork_status is None and "fork_status" not in details.hints
    assert details.dependabot_alerts == 0
    assert details.secret_alerts is None
    assert details.vulnerability_alerts_enabled is True


def test_render_and_dict_include_security_and_fork() -> None:
    details = RepoDetails(
        repo=make_repo(
            "f",
            fork=True,
        ),
        open_prs=0,
        fork_status={"parent": "up/orig", "ahead_by": 1, "behind_by": 2, "status": "diverged"},
        dependabot_alerts=3,
        secret_alerts=None,
        vulnerability_alerts_enabled=False,
        hints={"secret_alerts": "needs Secret scanning alerts: read"},
    )
    from io import StringIO

    from rich.console import Console

    console = Console(file=StringIO(), width=200)
    console.print(render_details(details))
    out = console.file.getvalue()
    assert "up/orig" in out and "1 ahead" in out and "2 behind" in out
    assert "Dependabot alerts: 3" in out
    assert "OFF" in out

    d = details_to_dict(details)
    assert d["fork_status"]["behind_by"] == 2
    assert d["dependabot_alerts"] == 3
    assert d["secret_alerts"] is None
    assert d["vulnerability_alerts_enabled"] is False


@responses.activate
def test_actions_storage_field(client: GitHubClient) -> None:
    full = "octocat/r"
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/{full}/actions/artifacts",
        json={"total_count": 12, "artifacts": []},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/{full}/actions/cache/usage",
        json={"active_caches_size_in_bytes": 480_000_000, "active_caches_count": 3},
        status=200,
    )
    from gman.details import _actions_storage

    storage = _actions_storage(client, full)
    assert storage == {"artifact_count": 12, "cache_bytes": 480_000_000, "cache_count": 3}


def test_render_actions_storage_row() -> None:
    details = RepoDetails(
        repo=make_repo("r"),
        open_prs=0,
        actions_storage={"artifact_count": 12, "cache_bytes": 480_000_000, "cache_count": 3},
    )
    from io import StringIO

    from rich.console import Console

    console = Console(file=StringIO(), width=200)
    console.print(render_details(details))
    out = console.file.getvalue()
    assert "12 artifacts" in out and "480 MB" in out and "(3 entries)" in out
    assert details_to_dict(details)["actions_storage"]["cache_count"] == 3
