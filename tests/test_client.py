"""Tests for the GitHub API client."""

from __future__ import annotations

import json as jsonlib
from pathlib import Path

import pytest
import responses
from conftest import make_repo

from gman.client import DEFAULT_API_URL, GitHubClient, GitHubError, RateLimitError


@pytest.fixture
def client() -> GitHubClient:
    return GitHubClient(token="fake-token")


@responses.activate
def test_list_repos_paginates_and_sorts_archived_last(client: GitHubClient) -> None:
    page1 = [make_repo(f"repo{i}") for i in range(100)]
    page2 = [make_repo("active"), make_repo("old", archived=True)]
    responses.add(responses.GET, f"{DEFAULT_API_URL}/user/repos", json=page1, status=200)
    responses.add(responses.GET, f"{DEFAULT_API_URL}/user/repos", json=page2, status=200)

    repos = client.list_repos()

    assert len(repos) == 102
    # A second page was fetched because page 1 was full.
    assert len(responses.calls) == 2
    # Archived repos are pushed to the end.
    assert repos[-1]["name"] == "old"
    assert all(not r["archived"] for r in repos[:-1])


@responses.activate
def test_list_repos_can_exclude_archived(client: GitHubClient) -> None:
    batch = [make_repo("active"), make_repo("old", archived=True)]
    responses.add(responses.GET, f"{DEFAULT_API_URL}/user/repos", json=batch, status=200)

    repos = client.list_repos(include_archived=False)

    assert [r["name"] for r in repos] == ["active"]


@responses.activate
def test_list_repos_reports_progress(client: GitHubClient) -> None:
    responses.add(responses.GET, f"{DEFAULT_API_URL}/user/repos", json=[make_repo("a")], status=200)
    seen: list[int] = []

    client.list_repos(progress=seen.append)

    assert seen == [1]


@responses.activate
def test_list_repos_passes_affiliation(client: GitHubClient) -> None:
    responses.add(responses.GET, f"{DEFAULT_API_URL}/user/repos", json=[], status=200)

    client.list_repos(affiliation="owner,organization_member")

    assert "affiliation=owner%2Corganization_member" in str(responses.calls[0].request.url)


@responses.activate
def test_rate_limit_raises(client: GitHubClient) -> None:
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/user/repos",
        json={"message": "rate limited"},
        status=403,
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1893456000"},
    )

    with pytest.raises(RateLimitError, match="rate limit exceeded"):
        client.list_repos()


@responses.activate
def test_retries_transient_5xx(monkeypatch: pytest.MonkeyPatch, client: GitHubClient) -> None:
    monkeypatch.setattr("gman.client.time.sleep", lambda _s: None)
    responses.add(responses.GET, f"{DEFAULT_API_URL}/user", status=502)
    responses.add(responses.GET, f"{DEFAULT_API_URL}/user", json={"login": "octocat"}, status=200)

    assert client.whoami() == "octocat"
    assert len(responses.calls) == 2


@responses.activate
def test_delete_repo_success(client: GitHubClient) -> None:
    responses.add(responses.DELETE, f"{DEFAULT_API_URL}/repos/octocat/x", status=204)

    ok, msg = client.delete_repo("octocat/x")

    assert ok
    assert "Deleted" in msg


@responses.activate
def test_delete_repo_failure_returns_tuple(client: GitHubClient) -> None:
    responses.add(
        responses.DELETE,
        f"{DEFAULT_API_URL}/repos/octocat/x",
        json={"message": "Not Found"},
        status=404,
    )

    ok, msg = client.delete_repo("octocat/x")

    assert not ok
    assert "404" in msg


@responses.activate
def test_set_archived_and_description(client: GitHubClient) -> None:
    responses.add(responses.PATCH, f"{DEFAULT_API_URL}/repos/octocat/x", json={}, status=200)
    responses.add(responses.PATCH, f"{DEFAULT_API_URL}/repos/octocat/x", json={}, status=200)

    ok1, _ = client.set_archived("octocat/x", archived=True)
    ok2, msg2 = client.set_description("octocat/x", "new")

    assert ok1 and ok2
    assert "description" in msg2


@responses.activate
def test_enterprise_api_url() -> None:
    enterprise = "https://ghe.example.com/api/v3"
    client = GitHubClient(token="t", api_url=enterprise + "/")  # trailing slash trimmed
    responses.add(responses.GET, f"{enterprise}/user", json={"login": "me"}, status=200)

    assert client.whoami() == "me"
    assert client.api_url == enterprise


def test_api_url_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_API_URL", "https://ghe.example.com/api/v3")
    monkeypatch.setenv("GITHUB_TOKEN", "t")

    assert GitHubClient().api_url == "https://ghe.example.com/api/v3"


@responses.activate
def test_scopes_captured_from_first_response(client: GitHubClient) -> None:
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/user",
        json={"login": "octocat"},
        status=200,
        headers={"X-OAuth-Scopes": "repo, delete_repo"},
    )
    client.whoami()
    assert client.token_info.scopes == {"repo", "delete_repo"}
    assert client.token_info.kind == "classic"
    assert client.capabilities.resolve("delete") is True


@responses.activate
def test_no_scopes_header_leaves_scopes_none(client: GitHubClient) -> None:
    responses.add(responses.GET, f"{DEFAULT_API_URL}/user", json={"login": "o"}, status=200)
    client.whoami()
    assert client.token_info.scopes is None


@responses.activate
def test_get_optional_403_marks_denied(client: GitHubClient) -> None:
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/o/r/actions/runs",
        json={"message": "Resource not accessible by personal access token"},
        status=403,
    )
    r = client._get_optional("actions.read", "/repos/o/r/actions/runs")
    assert r is None
    assert client.capabilities.resolve("actions.read") is False


@responses.activate
def test_get_optional_404_marks_allowed(client: GitHubClient) -> None:
    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/o/r/pages", status=404)
    r = client._get_optional("pages.read", "/repos/o/r/pages")
    assert r is None
    assert client.capabilities.resolve("pages.read") is True


def test_token_source_flag() -> None:
    assert GitHubClient(token="t").token_source == "--token flag"


def test_token_source_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    assert GitHubClient().token_source == "GITHUB_TOKEN env"


@responses.activate
def test_scopes_still_captured_after_failed_first_request(client: GitHubClient) -> None:
    """A dead first response must not latch the capture flag."""
    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/o/r", status=404)
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/user",
        json={"login": "o"},
        status=200,
        headers={"X-OAuth-Scopes": "repo"},
    )
    client._request("GET", "/repos/o/r")  # 404: not ok, must not latch
    client.whoami()
    assert client.token_info.scopes == {"repo"}


@responses.activate
def test_get_repo_success_and_failure(client: GitHubClient) -> None:
    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/o/r", json=make_repo("r"), status=200)
    assert client.get_repo("o/r")["name"] == "r"

    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/o/gone", status=404)
    with pytest.raises(GitHubError, match="404"):
        client.get_repo("o/gone")


@responses.activate
def test_get_readme_raw(client: GitHubClient) -> None:
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/o/r/readme",
        body="# Hello\n",
        status=200,
    )
    assert client.get_readme("o/r") == "# Hello\n"
    # raw media type requested
    assert responses.calls[0].request.headers["Accept"] == "application/vnd.github.raw+json"


@responses.activate
def test_get_readme_missing_returns_none(client: GitHubClient) -> None:
    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/o/r/readme", status=404)
    assert client.get_readme("o/r") is None


@responses.activate
def test_get_languages(client: GitHubClient) -> None:
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/o/r/languages",
        json={"Python": 1000, "Shell": 50},
        status=200,
    )
    assert client.get_languages("o/r") == {"Python": 1000, "Shell": 50}


@responses.activate
def test_get_latest_release_none_when_absent(client: GitHubClient) -> None:
    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/o/r/releases/latest", status=404)
    assert client.get_latest_release("o/r") is None


@responses.activate
def test_latest_workflow_run(client: GitHubClient) -> None:
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/o/r/actions/runs",
        json={"workflow_runs": [{"name": "CI", "status": "completed", "conclusion": "success"}]},
        status=200,
    )
    run = client.get_latest_workflow_run("o/r")
    assert run is not None and run["conclusion"] == "success"


@responses.activate
def test_latest_workflow_run_empty(client: GitHubClient) -> None:
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/o/r/actions/runs",
        json={"workflow_runs": []},
        status=200,
    )
    assert client.get_latest_workflow_run("o/r") is None


@responses.activate
def test_traffic_combines_views_and_clones(client: GitHubClient) -> None:
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/o/r/traffic/views",
        json={"count": 100, "uniques": 40},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/o/r/traffic/clones",
        json={"count": 7, "uniques": 5},
        status=200,
    )
    assert client.get_traffic("o/r") == {
        "views": 100,
        "unique_views": 40,
        "clones": 7,
        "unique_clones": 5,
    }


@responses.activate
def test_traffic_denied_returns_none(client: GitHubClient) -> None:
    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/o/r/traffic/views", status=403)
    assert client.get_traffic("o/r") is None
    # Traffic 403s are push-access-confounded, so they must not teach the
    # capability cache (sanctioned semantic change — see test below).
    assert client.capabilities.resolve("admin.read") is None


@responses.activate
def test_traffic_403_does_not_mark_admin_read(client: GitHubClient) -> None:
    """Traffic 403s conflate push access — they must not teach the capability cache."""
    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/o/r/traffic/views", status=403)
    assert client.get_traffic("o/r") is None
    assert client.capabilities.resolve("admin.read") is None  # NOT False


@responses.activate
def test_pr_count_via_link_header(client: GitHubClient) -> None:
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/o/r/pulls",
        json=[{"number": 1}],
        status=200,
        headers={
            "Link": (
                f'<{DEFAULT_API_URL}/repos/o/r/pulls?state=open&per_page=1&page=2>; rel="next", '
                f'<{DEFAULT_API_URL}/repos/o/r/pulls?state=open&per_page=1&page=57>; rel="last"'
            )
        },
    )
    assert client.get_open_pr_count("o/r") == 57


@responses.activate
def test_pr_count_no_link_header(client: GitHubClient) -> None:
    responses.add(
        responses.GET, f"{DEFAULT_API_URL}/repos/o/r/pulls", json=[{"number": 1}], status=200
    )
    assert client.get_open_pr_count("o/r") == 1


@responses.activate
def test_pr_count_zero(client: GitHubClient) -> None:
    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/o/r/pulls", json=[], status=200)
    assert client.get_open_pr_count("o/r") == 0


@responses.activate
def test_download_tarball_follows_redirect(client: GitHubClient, tmp_path: Path) -> None:
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/o/r/tarball/main",
        status=302,
        headers={"Location": "https://codeload.github.com/o/r/legacy.tar.gz/main"},
    )
    responses.add(
        responses.GET,
        "https://codeload.github.com/o/r/legacy.tar.gz/main",
        body=b"tarball-bytes",
        status=200,
    )
    dest = tmp_path / "r-main.tar.gz"
    assert client.download_tarball("o/r", "main", dest) == dest
    assert dest.read_bytes() == b"tarball-bytes"


@responses.activate
def test_download_tarball_failure_raises(client: GitHubClient, tmp_path: Path) -> None:
    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/o/r/tarball/main", status=404)
    with pytest.raises(GitHubError, match="404"):
        client.download_tarball("o/r", "main", tmp_path / "x.tar.gz")


@responses.activate
def test_pinned_repos_happy_path(client: GitHubClient) -> None:
    responses.add(
        responses.POST,
        f"{DEFAULT_API_URL}/graphql",
        json={
            "data": {
                "viewer": {
                    "pinnedItems": {"nodes": [{"nameWithOwner": "o/a"}, {"nameWithOwner": "o/b"}]}
                }
            }
        },
        status=200,
    )
    assert client.get_pinned_repos() == {"o/a", "o/b"}


@responses.activate
def test_pinned_repos_failure_is_empty_set(client: GitHubClient) -> None:
    responses.add(
        responses.POST,
        f"{DEFAULT_API_URL}/graphql",
        json={"errors": [{"message": "nope"}]},
        status=200,
    )
    assert client.get_pinned_repos() == set()

    responses.add(responses.POST, f"{DEFAULT_API_URL}/graphql", status=403)
    assert client.get_pinned_repos() == set()


@responses.activate
def test_pinned_repos_null_nodes_is_empty_set(client: GitHubClient) -> None:
    responses.add(
        responses.POST,
        f"{DEFAULT_API_URL}/graphql",
        json={"data": {"viewer": {"pinnedItems": {"nodes": None}}}},
        status=200,
    )
    assert client.get_pinned_repos() == set()


@responses.activate
def test_pinned_repos_non_json_body_is_empty_set(client: GitHubClient) -> None:
    responses.add(responses.POST, f"{DEFAULT_API_URL}/graphql", body="not json", status=200)
    assert client.get_pinned_repos() == set()


@responses.activate
def test_pinned_repos_rate_limit_is_empty_set(client: GitHubClient) -> None:
    responses.add(
        responses.POST,
        f"{DEFAULT_API_URL}/graphql",
        json={"message": "rate limited"},
        status=403,
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1893456000"},
    )
    assert client.get_pinned_repos() == set()


@responses.activate
def test_update_repo_patches_fields(client: GitHubClient) -> None:
    responses.add(responses.PATCH, f"{DEFAULT_API_URL}/repos/o/r", json={}, status=200)

    ok, msg = client.update_repo("o/r", {"homepage": "https://x.example", "has_wiki": False})

    assert ok and msg == "Updated o/r"
    body = jsonlib.loads(responses.calls[0].request.body)
    assert body == {"homepage": "https://x.example", "has_wiki": False}
    assert client.capabilities.resolve("admin.write") is True


@responses.activate
def test_update_repo_403_marks_admin_write_denied(client: GitHubClient) -> None:
    responses.add(
        responses.PATCH,
        f"{DEFAULT_API_URL}/repos/o/r",
        json={"message": "forbidden"},
        status=403,
    )

    ok, msg = client.update_repo("o/r", {"has_wiki": False})

    assert not ok and "403" in msg
    assert client.capabilities.resolve("admin.write") is False


@responses.activate
def test_archived_403_does_not_mark_denied(client: GitHubClient) -> None:
    """State-based 403s (archived repo) must not poison the capability cache."""
    responses.add(
        responses.PATCH,
        f"{DEFAULT_API_URL}/repos/o/r",
        json={"message": "Repository was archived so is read-only."},
        status=403,
    )
    ok, msg = client.update_repo("o/r", {"has_wiki": False})
    assert not ok and "403" in msg
    assert client.capabilities.resolve("admin.write") is None  # NOT False


@responses.activate
def test_mutate_rate_limit_propagates(client: GitHubClient) -> None:
    responses.add(
        responses.PATCH,
        f"{DEFAULT_API_URL}/repos/o/r",
        json={"message": "rate limited"},
        status=403,
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1893456000"},
    )

    with pytest.raises(RateLimitError):
        client.update_repo("o/r", {"has_wiki": False})


@responses.activate
def test_delete_repo_marks_delete_family(client: GitHubClient) -> None:
    responses.add(responses.DELETE, f"{DEFAULT_API_URL}/repos/octocat/x", status=204)

    ok, _ = client.delete_repo("octocat/x")

    assert ok
    assert client.capabilities.resolve("delete") is True


@responses.activate
def test_set_topics_puts_names(client: GitHubClient) -> None:
    responses.add(responses.PUT, f"{DEFAULT_API_URL}/repos/o/r/topics", json={}, status=200)

    ok, msg = client.set_topics("o/r", ["cli", "github"])

    assert ok and msg == "Set 2 topics on o/r"
    assert jsonlib.loads(responses.calls[0].request.body) == {"names": ["cli", "github"]}


@responses.activate
def test_vulnerability_alerts_on_off(client: GitHubClient) -> None:
    responses.add(responses.PUT, f"{DEFAULT_API_URL}/repos/o/r/vulnerability-alerts", status=204)
    responses.add(responses.DELETE, f"{DEFAULT_API_URL}/repos/o/r/vulnerability-alerts", status=204)

    ok_on, msg_on = client.set_vulnerability_alerts("o/r", True)
    ok_off, msg_off = client.set_vulnerability_alerts("o/r", False)

    assert ok_on and "Enabled" in msg_on
    assert ok_off and "Disabled" in msg_off


@responses.activate
def test_security_fixes_failure_tuple(client: GitHubClient) -> None:
    responses.add(
        responses.PUT,
        f"{DEFAULT_API_URL}/repos/o/r/automated-security-fixes",
        json={"message": "vulnerability alerts must be enabled"},
        status=422,
    )

    ok, msg = client.set_automated_security_fixes("o/r", True)

    assert not ok and "422" in msg


@responses.activate
def test_dependabot_alert_count_via_link(client: GitHubClient) -> None:
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/o/r/dependabot/alerts",
        json=[{"number": 1}],
        status=200,
        headers={
            "Link": (
                f"<{DEFAULT_API_URL}/repos/o/r/dependabot/alerts?state=open&per_page=1&page=7>; "
                'rel="last"'
            )
        },
    )
    assert client.get_open_dependabot_alert_count("o/r") == 7


@responses.activate
def test_dependabot_count_403_marks_family(client: GitHubClient) -> None:
    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/o/r/dependabot/alerts", status=403)
    assert client.get_open_dependabot_alert_count("o/r") is None
    assert client.capabilities.resolve("dependabot.read") is False


@responses.activate
def test_secret_alert_count_zero_and_disabled(client: GitHubClient) -> None:
    responses.add(
        responses.GET, f"{DEFAULT_API_URL}/repos/o/r/secret-scanning/alerts", json=[], status=200
    )
    assert client.get_open_secret_alert_count("o/r") == 0

    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/o/x/secret-scanning/alerts", status=404)
    # 404 = secret scanning not enabled on the repo → absent, and authz passed
    assert client.get_open_secret_alert_count("o/x") is None
    assert client.capabilities.resolve("secret_scanning.read") is True


@responses.activate
def test_vulnerability_alerts_enabled_tristate(client: GitHubClient) -> None:
    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/o/on/vulnerability-alerts", status=204)
    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/o/off/vulnerability-alerts", status=404)
    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/o/no/vulnerability-alerts", status=403)

    assert client.get_vulnerability_alerts_enabled("o/on") is True
    assert client.get_vulnerability_alerts_enabled("o/off") is False
    assert client.get_vulnerability_alerts_enabled("o/no") is None
    assert client.capabilities.resolve("admin.read") is False


@responses.activate
def test_compare_returns_counts(client: GitHubClient) -> None:
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/o/fork/compare/upstream:main...main",
        json={"ahead_by": 2, "behind_by": 14, "status": "diverged"},
        status=200,
    )
    cmp = client.compare("o/fork", "upstream:main...main")
    assert cmp is not None and cmp["behind_by"] == 14


@responses.activate
def test_merge_upstream_success_and_conflict(client: GitHubClient) -> None:
    responses.add(
        responses.POST,
        f"{DEFAULT_API_URL}/repos/o/fork/merge-upstream",
        json={"message": "Successfully fetched and fast-forwarded", "merge_type": "fast-forward"},
        status=200,
    )
    ok, msg = client.merge_upstream("o/fork", "main")
    assert ok and msg == "Synced o/fork with upstream"
    assert jsonlib.loads(responses.calls[0].request.body) == {"branch": "main"}
    assert client.capabilities.resolve("contents.write") is True

    responses.add(
        responses.POST,
        f"{DEFAULT_API_URL}/repos/o/fork/merge-upstream",
        json={"message": "There are merge conflicts"},
        status=409,
    )
    ok2, msg2 = client.merge_upstream("o/fork", "main")
    assert not ok2 and "409" in msg2


@responses.activate
def test_actions_cache_usage(client: GitHubClient) -> None:
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/o/r/actions/cache/usage",
        json={"active_caches_size_in_bytes": 480_000_000, "active_caches_count": 3},
        status=200,
    )
    usage = client.get_actions_cache_usage("o/r")
    assert usage is not None and usage["active_caches_count"] == 3


@responses.activate
def test_artifact_count_from_body(client: GitHubClient) -> None:
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/o/r/actions/artifacts",
        json={"total_count": 12, "artifacts": [{"id": 1}]},
        status=200,
    )
    assert client.get_artifact_count("o/r") == 12


@responses.activate
def test_list_artifacts_paginates(client: GitHubClient) -> None:
    page1 = {"total_count": 101, "artifacts": [{"id": i} for i in range(100)]}
    page2 = {"total_count": 101, "artifacts": [{"id": 100}]}
    responses.add(
        responses.GET, f"{DEFAULT_API_URL}/repos/o/r/actions/artifacts", json=page1, status=200
    )
    responses.add(
        responses.GET, f"{DEFAULT_API_URL}/repos/o/r/actions/artifacts", json=page2, status=200
    )
    arts = client.list_artifacts("o/r")
    assert arts is not None and len(arts) == 101


@responses.activate
def test_list_artifacts_denied_first_page(client: GitHubClient) -> None:
    responses.add(responses.GET, f"{DEFAULT_API_URL}/repos/o/r/actions/artifacts", status=403)
    assert client.list_artifacts("o/r") is None
    assert client.capabilities.resolve("actions.read") is False


@responses.activate
def test_list_caches_and_recent_runs(client: GitHubClient) -> None:
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/o/r/actions/caches",
        json={"total_count": 1, "actions_caches": [{"id": 5, "key": "k", "size_in_bytes": 9}]},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/repos/o/r/actions/runs",
        json={"workflow_runs": [{"id": 1, "name": "CI"}]},
        status=200,
    )
    caches = client.list_caches("o/r")
    runs = client.list_recent_runs("o/r", limit=5)
    assert caches is not None and caches[0]["key"] == "k"
    assert runs is not None and runs[0]["name"] == "CI"
    assert "per_page=5" in str(responses.calls[1].request.url)


@responses.activate
def test_template_pickers(client: GitHubClient) -> None:
    responses.add(
        responses.GET, f"{DEFAULT_API_URL}/gitignore/templates", json=["Python", "Go"], status=200
    )
    responses.add(
        responses.GET,
        f"{DEFAULT_API_URL}/licenses",
        json=[{"key": "mit", "name": "MIT License"}],
        status=200,
    )
    assert client.get_gitignore_templates() == ["Python", "Go"]
    licenses = client.get_license_templates()
    assert licenses is not None and licenses[0]["key"] == "mit"


@responses.activate
def test_delete_artifact_and_cache(client: GitHubClient) -> None:
    responses.add(responses.DELETE, f"{DEFAULT_API_URL}/repos/o/r/actions/artifacts/7", status=204)
    responses.add(responses.DELETE, f"{DEFAULT_API_URL}/repos/o/r/actions/caches/9", status=204)

    ok1, _ = client.delete_artifact("o/r", 7)
    ok2, _ = client.delete_cache("o/r", 9)

    assert ok1 and ok2
    assert client.capabilities.resolve("actions.write") is True


@responses.activate
def test_rerun_workflow_variants(client: GitHubClient) -> None:
    responses.add(responses.POST, f"{DEFAULT_API_URL}/repos/o/r/actions/runs/1/rerun", status=201)
    responses.add(
        responses.POST,
        f"{DEFAULT_API_URL}/repos/o/r/actions/runs/2/rerun-failed-jobs",
        status=201,
    )

    ok1, msg1 = client.rerun_workflow("o/r", 1)
    ok2, _ = client.rerun_workflow("o/r", 2, failed_only=True)

    assert ok1 and ok2 and "Re-ran" in msg1


@responses.activate
def test_cancel_workflow(client: GitHubClient) -> None:
    responses.add(responses.POST, f"{DEFAULT_API_URL}/repos/o/r/actions/runs/3/cancel", status=202)
    ok, msg = client.cancel_workflow("o/r", 3)
    assert ok and "Cancelled" in msg


@responses.activate
def test_create_repo_success_message_from_response(client: GitHubClient) -> None:
    responses.add(
        responses.POST,
        f"{DEFAULT_API_URL}/user/repos",
        json={"full_name": "octocat/new", "html_url": "https://github.com/octocat/new"},
        status=201,
    )
    ok, msg = client.create_repo({"name": "new", "private": True})
    assert ok and msg == "Created octocat/new — https://github.com/octocat/new"
    assert jsonlib.loads(responses.calls[0].request.body) == {"name": "new", "private": True}


@responses.activate
def test_create_repo_name_taken(client: GitHubClient) -> None:
    responses.add(
        responses.POST,
        f"{DEFAULT_API_URL}/user/repos",
        json={"message": "name already exists on this account"},
        status=422,
    )
    ok, msg = client.create_repo({"name": "dupe"})
    assert not ok and "422" in msg


@responses.activate
def test_create_from_template(client: GitHubClient) -> None:
    responses.add(
        responses.POST,
        f"{DEFAULT_API_URL}/repos/tpl/base/generate",
        json={"full_name": "octocat/gen", "html_url": "https://github.com/octocat/gen"},
        status=201,
    )
    ok, msg = client.create_from_template("tpl/base", {"name": "gen"})
    assert ok and "octocat/gen" in msg
