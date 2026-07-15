<p align="center">
  <img src="assets/logo.svg" alt="gman logo" width="140" height="140">
</p>

# gman

<p align="center"><strong>Your repos. Under investigation.</strong></p>

gman is a terminal tool for managing the GitHub repositories you own. It puts
your whole account on one screen — then lets you prune, retag, archive, sync,
and clean up from right there, without a browser tab in sight.

Use it two ways:

- **The TUI** — an interactive table of every repo you own. Filter, inspect,
  multi-select, and act. Start it with `gman tui` (or `gman-tui`).
- **The CLI** — scriptable commands for everything the TUI does and more:
  `gman list --json`, `gman bulk --all --delete-branch-on-merge on`,
  `gman actions myrepo --clear-artifacts`, …

## What can it do?

| I want to… | Reach for |
| --- | --- |
| See every repo I own, at a glance | [`gman tui`](tui.md) or [`gman list`](usage.md#list) |
| Dig into one repo — CI status, traffic, alerts, releases | [`gman info`](usage.md#info) or press `Enter` in the TUI |
| Fix descriptions, topics, homepages, settings | [`gman edit`](usage.md#edit) or the TUI's edit keys |
| Apply one change to *many* repos | [`gman bulk`](usage.md#bulk) or the TUI's bulk menu |
| Delete dead repos — safely | [`gman delete --backup`](usage.md#delete) or `d` in the TUI |
| Keep forks in step with upstream | [`gman sync`](usage.md#sync) or `s` in the TUI |
| Free GitHub Actions storage | [`gman actions`](usage.md#actions) |
| Start a new repository | [`gman new`](usage.md#new) |
| Hand a spreadsheet to someone | [`gman excel`](excel.md) |

Every feature degrades gracefully to what your token allows — fields you
can't see show a dash and a hint instead of an error. See
[Tokens & permissions](tokens.md).

## Install

```bash
uv tool install gman     # or: pipx install gman  /  pip install gman
```

That installs two commands:

- `gman` — the full CLI (`gman --help` lists every command)
- `gman-tui` — jumps straight into the interactive table

Then head to [Getting started](getting-started.md) to connect your GitHub
account — it takes about a minute.
