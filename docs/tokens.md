# Tokens & permissions

gman works with any GitHub token and **degrades gracefully**: features your
token can't perform show a dash and a hint (TUI) or a message naming the
missing permission (CLI) — never an error wall. Run `gman auth` at any time
to see exactly what your current token unlocks.

You don't need to read this page to use gman — `gh auth login` and go.
Read it when you want to grant gman *only* what it needs.

## Fine-grained token recipes (least privilege)

Create at <https://github.com/settings/personal-access-tokens/new>, choose
repository access (all repos or selected), and grant one of these permission
sets:

| Tier | Permissions | What works |
| --- | --- | --- |
| Read-only inventory | Metadata: read (automatic) + Contents: read | list, excel, info (partial), README preview, backup download |
| Dashboard | + Actions: read, Pages: read, Pull requests: read, Administration: read, Dependabot alerts: read, Secret scanning alerts: read | full detail panel incl. CI status, Pages, traffic, issue/PR split, alert counts, and security status |
| Manager | + Administration: write, Contents: write, Actions: write | archive, describe, `edit`, `bulk`, topics, Dependabot alert/fix toggles, `sync` fork with upstream, `actions` cleanup/re-run/cancel, **delete** — fine-grained tokens bundle delete under Administration: write |

Fine-grained tokens cannot be introspected (GitHub sends no scope header), so
`gman auth` shows `unknown` until a feature is used or you run
`gman auth --probe`.

## Classic PATs and the gh CLI

Classic tokens announce their scopes, so `gman auth` reports availability
immediately.

- `repo` scope covers every read feature and all writes **except delete**.
- `delete_repo` is a separate scope required for `gman delete`.
- `gh auth token` (gman's fallback) is always a classic token with
  `repo, read:org, gist` — add delete with
  `gh auth refresh -h github.com -s delete_repo`.

!!! note
    The `gman new` template/license pickers (`--list-gitignores`,
    `--list-licenses`) work with any token — they access public APIs that
    require no special permissions.

## Known gaps

Repo **transfer** and the **notifications** API do not work with fine-grained
tokens at all (GitHub limitation) — they are not part of gman today.
Traffic stats additionally require push access to the repo.
