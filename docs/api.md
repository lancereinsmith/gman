# Scripting with Python

Everything the CLI does is available as a small Python API — useful for
one-off scripts and automation that outgrows `gman list --json`. The package
re-exports its top-level objects:

```python
from gman import GitHubClient, GitHubError, RateLimitError, write_excel
```

## `GitHubClient`

A thin wrapper around the GitHub REST API for the authenticated user.

```python
client = GitHubClient(token="ghp_...")   # falls back to $GITHUB_TOKEN, then `gh auth token`
# Talk to GitHub Enterprise (or set $GITHUB_API_URL):
client = GitHubClient(api_url="https://ghe.example.com/api/v3")

client.whoami()                           # -> "username" or None
repos = client.list_repos()               # list[dict]
ok, message = client.delete_repo("username/repo")
ok, message = client.set_archived("username/repo", archived=True)
ok, message = client.set_description("username/repo", "New description")
```

`list_repos(include_archived=True, affiliation="owner", progress=None)` pages
through `/user/repos` 100 at a time and returns the raw API objects sorted by
`updated_at` descending, with archived repos pushed to the end. Pass
`include_archived=False` to drop archived repos, `affiliation` to widen the
scope (e.g. `"owner,collaborator,organization_member"`), and a `progress`
callback that receives the running repo count after each page.

`delete_repo`, `set_archived`, and `set_description` each return a
`(bool, str)` tuple — useful for surfacing the HTTP error text if the call
fails.

## Errors and retries

Transient failures (network errors and `5xx` responses) are retried
automatically with exponential backoff (`max_retries`, default 3). Two
exceptions may be raised by `list_repos` / `whoami`:

- `RateLimitError` — the GitHub API rate limit is exhausted (the message
  includes the reset time when the API provides it).
- `GitHubError` — the request could not be completed after retries.

`RateLimitError` is a subclass of `GitHubError`, so a single `except
GitHubError` catches both.

## `write_excel`

```python
write_excel(repos, "github_repos.xlsx")
```

Takes a list of repo dicts (the shape returned by `list_repos`) and writes
the formatted spreadsheet described on the [Excel export](excel.md) page.
Values that begin with a formula character (`= + - @`) are escaped so
spreadsheet applications treat them as text, not formulas.

## Detail helpers

```python
client.get_repo("o/r")               # repo object (raises GitHubError)
client.get_readme("o/r")             # raw markdown or None
client.get_languages("o/r")          # {"Python": 12345} or None
client.get_latest_release("o/r")     # release object or None
client.get_latest_workflow_run("o/r")
client.get_pages_info("o/r")
client.get_traffic("o/r")            # needs push access + admin read
client.get_open_pr_count("o/r")      # Link-header trick: 1 request
client.get_pinned_repos()            # set of "owner/name"; empty on failure
client.download_tarball("o/r", "main", Path("backup.tar.gz"))
```

Read helpers return `None` when the resource is absent (404) **or** the token
lacks the permission (403). Check `client.capabilities.resolve(family)` to
distinguish: `False` means denied, `True` means it was truly absent.

```python
from gman.details import fetch_details

details = fetch_details(client, repo)   # concurrent, per-field degradation
details.open_issues, details.open_prs, details.hints
```
