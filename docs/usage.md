# Commands

Everything gman can do from the command line. Global options come first, then
each command grouped by what you're trying to accomplish.

```text
gman [--token TOKEN] [--api-url URL] COMMAND [options]
```

| Global option | What it does |
| --- | --- |
| `--token`, `-t` | Use this token instead of `$GITHUB_TOKEN` / the gh CLI |
| `--api-url` | Talk to a GitHub Enterprise Server (e.g. `https://ghe.example.com/api/v3`); `$GITHUB_API_URL` works too |

Exit codes follow the usual convention: `0` on success, `1` when an operation
fails, `2` when the command line itself is invalid.

---

## Browsing your repositories

### `list`

Print a table of every repo you own, newest activity first.

```bash
gman list
gman list --detailed          # adds Language, Stars, and Forks columns
gman list --json              # machine-readable, pipe it anywhere
gman list --include-orgs      # also show org and collaborator repos
```

`--json` keeps stdout clean — progress messages go to stderr — so you can
pipe it straight into `jq`. `--affiliation` accepts a raw API filter if you
need something more specific than `--include-orgs`.

### `info`

A dossier on a single repo: description, topics, languages, latest release,
last CI run, GitHub Pages URL, 14-day traffic, the open issue/PR split, fork
status, security alerts, and Actions storage.

```bash
gman info username/project
gman info username/project --json
```

Anything your token can't see prints as `—` with a hint naming the missing
permission. In `--json` mode those hints go to stderr so the JSON stays
clean.

### `excel`

Export your inventory to a formatted spreadsheet — handy for sharing or
printing.

```bash
gman excel
gman excel --output ~/Desktop/repos.xlsx
gman excel --include-orgs
```

See [Excel export](excel.md) for exactly what the file looks like.

### `auth`

Ask gman what your token can do.

```bash
gman auth
gman auth --probe    # resolve unknowns with one cheap read per permission
```

Prints the token's source, its type (classic or fine-grained), any announced
scopes, and a feature-availability table. Fine-grained tokens can't announce
their permissions up front, so some rows start as *unknown* — `--probe`
resolves the read permissions, and write permissions resolve the first time
you use them.

---

## Editing a repository

### `describe`

Set a repo's description. An empty string clears it.

```bash
gman describe username/project "A short, useful tagline"
gman describe username/project ""
```

### `edit`

Change any combination of settings and metadata in one go — gman batches all
the setting flags into a single API call.

```bash
gman edit username/project --homepage https://example.com --wiki off
gman edit username/project --rename new-name --visibility private
gman edit username/project --topics python,cli               # replace all topics
gman edit username/project --add-topic tui --remove-topic wip
gman edit username/project --delete-branch-on-merge on --allow-rebase off
```

| Flag | Changes |
| --- | --- |
| `--description TEXT` | Description |
| `--homepage URL` | Homepage URL |
| `--rename NAME` | Repository name (GitHub redirects the old URLs) |
| `--visibility {public,private}` | Visibility |
| `--topics a,b,c` | Replace **all** topics |
| `--add-topic X` / `--remove-topic X` | Adjust topics individually (repeatable) |
| `--wiki` / `--issues` / `--projects {on,off}` | Feature tabs |
| `--delete-branch-on-merge {on,off}` | Auto-delete merged branches |
| `--allow-squash` / `--allow-merge-commit` / `--allow-rebase` / `--allow-update-branch {on,off}` | Merge methods |
| `--squash-commit-title` / `--squash-commit-message` / `--merge-commit-title` / `--merge-commit-message` | Default commit messages |

Topics are validated before anything is written — if a topic is invalid, the
command exits with code 2 and **nothing changes**.

### `archive`

Archive a repo (or bring one back). Reversible, unlike delete.

```bash
gman archive username/old-project
gman archive username/old-project --unarchive
gman archive username/old-project --force      # skip the y/N prompt
```

---

## Changing many repos at once

### `bulk`

Apply the same change to a set of repositories. Pick targets one of three
ways — explicit names, a filter, or everything:

```bash
gman bulk --all --delete-branch-on-merge on --dry-run   # see what would change
gman bulk --filter experiment --archive --yes
gman bulk o/r1 o/r2 --add-topic archived-candidate
gman bulk --all --vulnerability-alerts on
gman bulk --all --sync-fork                             # non-forks are skipped
gman bulk --all --clear-artifacts --clear-caches        # free Actions storage
```

Most `edit` flags work here, plus bulk-only ones: `--archive`/`--unarchive`,
`--vulnerability-alerts {on,off}`, `--security-fixes {on,off}`,
`--sync-fork`, `--clear-artifacts`, `--clear-caches`. (`--rename`,
`--description`, and `--topics` replace-all are deliberately unavailable —
applying them uniformly is never what you want.)

How a run works:

1. gman lists the operations and every target repo.
2. `--dry-run` stops here. Otherwise it asks `Proceed? [y/N]` — `--yes`
   skips the question.
3. Changes apply **one repo at a time** (GitHub throttles concurrent
   writes), with a progress line as it goes.
4. You get a per-repo ✅/❌ report. If GitHub rate-limits mid-run, the
   remaining repos are marked `⏭ skipped` rather than half-applied.

Exit code is `0` only if every operation succeeded.

---

## Forks

### `sync`

Bring a fork up to date with its upstream — the same thing as GitHub's
**Sync fork** button.

```bash
gman sync username/my-fork
gman sync username/my-fork --branch release
```

Syncs the default branch unless you say otherwise. If the branches have
conflicting changes, gman reports the conflict and leaves everything
untouched — resolve it locally with git.

!!! tip
    The TUI shows how far each fork has drifted (press `Enter` on a fork to
    see *N ahead / M behind*), and `gman bulk --all --sync-fork` syncs every
    fork you own in one pass.

---

## GitHub Actions housekeeping

### `actions`

Inspect and clean up a repo's Actions footprint. With no flags you get an
overview; with a flag you act.

```bash
gman actions username/repo                              # overview
gman actions username/repo --clear-artifacts
gman actions username/repo --clear-artifacts --older-than 30
gman actions username/repo --clear-caches
gman actions username/repo --rerun RUN_ID
gman actions username/repo --rerun RUN_ID --failed-only
gman actions username/repo --cancel RUN_ID
```

The overview shows the five most recent workflow runs (with pass/fail
status), the artifact count and total size, and the cache count and size.

| Flag | What it does |
| --- | --- |
| `--clear-artifacts` | Delete artifacts (all of them, or older than `--older-than DAYS`) |
| `--clear-caches` | Delete every Actions cache entry |
| `--rerun RUN_ID` | Re-run a workflow run (`--failed-only` re-runs just its failed jobs) |
| `--cancel RUN_ID` | Cancel an in-progress run |

Artifacts and caches count against your account-wide Actions storage quota —
`gman bulk --all --clear-artifacts --clear-caches` reclaims it everywhere at
once.

---

## Creating a repository

### `new`

Create a repo without opening a browser.

```bash
gman new my-repo
gman new my-repo --private --description "A short description"
gman new my-repo --auto-init --gitignore Python --license mit
gman new my-repo --template owner/template-repo --private
gman new --list-gitignores        # what .gitignore templates exist?
gman new --list-licenses          # what license templates exist?
```

**Direct creation** accepts `--private`, `--description`, `--homepage`,
`--auto-init` (start with a README), `--gitignore TEMPLATE`, and
`--license TEMPLATE`.

**Template mode** (`--template owner/repo`) generates the new repo from a
template repository. It accepts `--private`, `--description`, and
`--include-all-branches`; the direct-creation flags don't apply and are
rejected.

On success gman prints the new repo's URL and a ready-to-paste
`git clone` command. If the name is taken, GitHub's own error message tells
you so — nothing is created.

---

## Deleting a repository

### `delete`

Delete a repo by full name. gman makes you retype the name to confirm — and
looks out for you first.

```bash
gman delete username/old-project
gman delete username/old-project --backup --backup-dir ~/Backups
gman delete username/old-project --force        # no confirmation prompt
```

Before the prompt, gman warns you if the repo:

- has forks (they survive, but lose their upstream)
- has stars
- is public
- is pinned to your profile

`--backup` downloads a `{name}-{branch}.tar.gz` snapshot before deleting.
If the download fails, **the deletion is aborted** — you never lose a repo
whose backup didn't land. The tarball contains the git contents only (no
issues, wiki, or releases).

!!! warning
    Deletion is permanent. Even with `--force` there is no recycle bin.

!!! note "Token scope: `delete_repo`"
    Deletion requires the `delete_repo` scope, which `gh auth login` does
    **not** request by default. If GitHub returns "Must have admin rights
    to Repository", run `gh auth refresh -h github.com -s delete_repo`.
    A fine-grained token needs `Administration: write` instead — see
    [Tokens & permissions](tokens.md).

---

## The interactive table

### `tui`

Launch the TUI (`gman-tui` is a direct shortcut).

```bash
gman tui
```

The [TUI guide](tui.md) walks through every key and workflow.
