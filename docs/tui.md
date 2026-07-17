# The TUI

The fastest way to work through your repositories is the interactive table.
Start it any of these ways:

```bash
gman          # bare command, no subcommand
gman --tui
gman tui
gman-tui
```

## Reading the screen

- **Header** — the app title, and a subtitle showing your username,
  `visible/total` repo counts, the active filter, and how many repos you've
  selected.
- **The table** — one row per repo, eight columns:

    | Column | Meaning |
    | --- | --- |
    | ✓ | Selected for a bulk action |
    | Name | Repository name |
    | Vis. | Badges: 🔒 private / 🌐 public, ⑂ fork, ❌ archived, 📌 pinned to your profile |
    | Description | First 80 characters |
    | Lang | Primary language |
    | Stars | Star count |
    | Open | Open issues + pull requests |
    | Updated | Last update date |

- **Footer** — the keybindings available right now.

## Keys

### Looking around

| Key | Action |
| --- | --- |
| `↑` `↓` | Move through the table |
| `/` | Filter by substring (matches name and description; submit empty to clear) |
| `Enter` or `i` | Open the detail panel for the current repo |
| `v` *(in the detail panel)* | Read the repo's rendered README |
| `o` | Open the current repo in your browser |
| `r` | Refresh everything from GitHub |

The filter is instant and local — it narrows the table without touching the
network.

### Acting on one repo

| Key | Action |
| --- | --- |
| `c` | Change the description |
| `t` | Edit topics (comma-separated; validated before saving) |
| `h` | Edit the homepage URL |
| `a` | Archive — or unarchive, if it's already archived |
| `s` | Sync a fork with its upstream |
| `d` | Delete (with warnings, confirmation, and optional backup) |

### Acting on many repos

| Key | Action |
| --- | --- |
| `space` | Select / deselect the current repo |
| `ctrl+a` | Select everything visible (press again to deselect) |
| `b` | Open the bulk-action menu for the selection |

### Housekeeping

| Key | Action |
| --- | --- |
| `e` | Export the current list to `github_repos.xlsx` (in the directory you launched the TUI from) |
| `x` | Open that spreadsheet in your default app |
| `q` | Quit |

## The detail panel

Press `Enter` (or `i`) on any repo to open its dossier: description and
topics, stars/forks/size/license, creation and update dates, a language
breakdown, the latest release, the last CI run, the GitHub Pages URL,
14-day traffic, the open issue/PR split, security posture (Dependabot and
secret-scanning alert counts, vulnerability-alerts status), and Actions
storage (artifacts and cache size).

For forks there's an extra line — *⑂ fork of owner/repo — N ahead / M
behind* — so you can tell at a glance whether `s` (sync) is worth pressing.

Two things worth knowing:

- **Each row loads independently.** If your token can't see something (say,
  traffic), that row shows `—` with a hint naming the permission — the rest
  of the panel still fills in. See [Tokens & permissions](tokens.md).
- **Results are cached** for the session, so reopening a panel is instant.
  Press `r` to refresh from GitHub.

From the panel, `v` opens the repo's README rendered right in the terminal —
useful for answering "what *is* this repo?" before deleting it. `Esc` backs
out of any screen.

## Deleting, safely

Press `d` on a repo and gman opens a red confirmation modal that:

1. **Warns you** if the repo has forks or stars, is public, or is pinned to
   your profile — the situations where deleting is most likely to be a
   mistake.
2. Offers a **"Backup tarball first"** checkbox. When ticked, gman downloads
   `{name}-{branch}.tar.gz` to the current directory before deleting — and
   if that download fails, the deletion is **aborted**.
3. Requires you to **retype the repo's full name**. No match, no deletion.

`Esc` cancels at any point.

## Bulk actions

The multi-select workflow turns an afternoon of clicking into a minute of
key presses:

1. Select repos with `space` (or grab everything visible with `ctrl+a` —
   filter first with `/` to narrow the set). The ✓ column and the subtitle
   track your selection.
2. Press `b`. The menu offers:
    - Archive / Unarchive
    - Delete branch on merge → ON / OFF
    - Wiki, Issues, Projects → ON / OFF
    - Add topic… / Remove topic… (you'll be prompted for the topic)
    - Vulnerability alerts → ON / OFF
    - Automated security fixes → ON / OFF
    - Sync fork with upstream (skips non-forks)
    - Clear Actions artifacts / Clear Actions caches
3. A confirmation screen shows the operation and the target list — press
   `y` to proceed, `n` or `Esc` to back out.
4. Operations run one repo at a time; the subtitle shows progress, and a
   notification summarizes the outcome (`12 ok, 0 failed`). The table
   refreshes and your selection clears when it finishes.

If GitHub rate-limits mid-run, the remaining repos are skipped and reported
— nothing is left half-applied.
