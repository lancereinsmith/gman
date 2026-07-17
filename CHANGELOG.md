# Changelog

All notable changes to this project are documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.2] - 2026-07-16

### Added

- Launch the TUI with a bare `gman` (no subcommand) or `gman --tui`, in
  addition to the existing `gman tui` and `gman-tui`.

## [0.1.1] - 2026-07-16

### Fixed

- Corrected project URLs to the `lancereinsmith` GitHub account in
  `pyproject.toml` (Homepage, Repository, Issues, Changelog) and the docs
  site (`site_url`, `repo_url`).

## [0.1.0] - 2026-07-15

Initial release.

### Added

- `list` command with a Rich-formatted table and `--json` for machine-readable
  output.
- `excel` command exporting every repo to a landscape `.xlsx` (banded rows,
  frozen header, autofilter, sorted by Last Updated descending).
- `describe` command to set a repository's description.
- `delete` command to remove a repo by `owner/name` with a confirmation prompt.
- `archive` command (with `--unarchive`) to toggle a repo's archived state.
- `edit` command: homepage, rename, visibility, feature toggles,
  delete-branch-on-merge, merge-strategy defaults, and topics
  (`--topics` / `--add-topic` / `--remove-topic`) in one call.
- `new` command to create repos directly or from templates, with
  `--list-gitignores` / `--list-licenses` pickers and template mode
  (`--template owner/repo`) for cloning repos.
- `bulk` command to apply settings, archive/unarchive, topics, and Dependabot
  toggles to many repos sequentially, with dry-run and confirmation.
- `sync` command to sync a fork with its upstream.
- `actions` command to manage workflow runs, artifacts, and caches:
  `--clear-artifacts` / `--older-than DAYS`, `--clear-caches`, `--rerun RUN_ID` /
  `--failed-only`, `--cancel RUN_ID`; bulk variant for clearing artifacts/caches.
- `info` command: languages, latest release, CI status, Pages URL, 14-day
  traffic, open issue/PR split, Actions storage, and security posture (open
  Dependabot and secret-scanning alert counts plus vulnerability-alerts
  status) — each field degrades gracefully when the token lacks its permission.
- `auth` command showing token type, classic scopes, and per-feature
  availability (`--probe` resolves unknowns for fine-grained tokens).
- `tui` command: an interactive Textual table with filter, open-in-browser,
  archive, edit-description, delete, and Excel export; plus a detail panel
  (`i`/Enter), README viewer, multi-select (`space`, `ctrl+a`, `b` menu) for
  bulk operations, `t` (topics) and `h` (homepage) editors, `s` (sync fork)
  binding, an `Open` column (open issues + PRs), 📌 pinned and `⑂` fork
  badges, and an Actions storage row (artifact count, cache count/size). The
  row cursor stays in place when toggling a selection or refreshing the table.
- Deletion safety net: warnings for forks/stars/public/pinned repos and
  `delete --backup` / a TUI backup checkbox that downloads a tarball first and
  aborts deletion if the download fails.
- Fork triage: ahead/behind status in the detail panel and a "Sync fork with
  upstream" bulk operation.
- Capability model: classic-token scope introspection and fine-grained-token
  403 learning with graceful degradation everywhere, surfaced in `gman auth`.
- `--affiliation` and `--include-orgs` flags on `list` and `excel` to include
  collaborator and organization repositories.
- `--api-url` flag and `GITHUB_API_URL` environment variable for GitHub
  Enterprise Server support.
- Progress spinner while paginating repositories.
- Automatic retries for transient (5xx / network) failures and a clear
  `RateLimitError` when the API rate limit is exhausted.
- User-manual documentation site (Getting started, Commands, TUI guide,
  Choosing a token, token recipes) deployed to GitHub Pages on every push to
  `main`.
- Supports Python 3.10 and newer.

### Security

- Excel export escapes cell values that begin with a formula character
  (`= + - @`), preventing spreadsheet formula injection from repo metadata.
