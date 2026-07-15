<div align="center">

<img src="https://raw.githubusercontent.com/lancereinsmith/github-repo-manager/main/assets/logo.svg" alt="gman logo" width="140" height="140">

# gman

**Your repos. Under investigation.**

List, manage, and export your GitHub repositories from the terminal.

</div>

The G-man for your GitHub account: `gman` puts every repo you own on the record
— list it, archive it, redescribe it, delete it, or export the whole file to
Excel — from a fast CLI or an interactive TUI.

## Features

- **CLI** — `list`, `delete`, `archive`, `describe`, `excel` subcommands
- **TUI** — interactive [Textual](https://textual.textualize.io/) table with
  filter, open-in-browser, archive, edit-description, delete, and Excel export
- **Excel export** — landscape `.xlsx` with banded rows, frozen header, and
  autofilter, sorted by Last Updated descending
- **Works with GitHub Enterprise** via `--api-url` / `GITHUB_API_URL`

## Install

```bash
uv tool install gman        # or: pipx install gman  /  pip install gman
```

## Quick start

```bash
gh auth login                  # or: export GITHUB_TOKEN=ghp_xxx
gman tui                       # interactive UI
gman list --detailed           # or: list, excel, describe, delete, archive
gman-tui                       # shortcut straight into the TUI
```

The token is resolved from `--token`, then `$GITHUB_TOKEN`, then
`gh auth token`. For `delete`, the gh CLI token needs the extra
`delete_repo` scope: `gh auth refresh -h github.com -s delete_repo`.

## Examples

```bash
gman list --json                       # machine-readable output
gman list --include-orgs               # include org & collaborator repos
gman describe owner/repo "New tagline"
gman excel --output ~/Desktop/repos.xlsx
gman --api-url https://ghe.example.com/api/v3 list   # GitHub Enterprise
```

See the [user manual](https://lancereinsmith.github.io/gman/) for full usage and configuration.

## License

[MIT](LICENSE)
