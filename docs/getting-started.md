# Getting started

Five minutes from install to a full inventory of your repositories.

## 1. Install gman

```bash
uv tool install gman     # or: pipx install gman  /  pip install gman
```

## 2. Connect your GitHub account

gman talks to GitHub with a token. It looks for one in this order:

1. The `--token` flag on any command
2. The `GITHUB_TOKEN` environment variable
3. The [GitHub CLI](https://cli.github.com/) — if you've ever run
   `gh auth login`, gman uses that token automatically

**Already use the `gh` CLI?** You're done — skip to step 3.

**Otherwise**, the quickest route is:

```bash
gh auth login            # guided browser sign-in, stores a token for you
```

…or create a personal access token at
[github.com/settings/tokens](https://github.com/settings/tokens) and export
it:

```bash
export GITHUB_TOKEN=ghp_xxxx
```

!!! tip "Least privilege"
    If you'd rather grant gman only what it needs — say, read-only access —
    create a fine-grained token instead. [Tokens & permissions](tokens.md)
    has copy-paste permission recipes for read-only, dashboard, and full
    management tiers.

## 3. Take it for a spin

```bash
gman auth        # what can my token do?
gman list        # every repo you own, newest first
gman tui         # the interactive table
```

`gman auth` prints a feature-availability table so you know up front which
gman features your token unlocks. Anything unavailable shows a hint telling
you exactly which permission it needs.

## 4. A first housekeeping pass

A typical first session in the TUI:

1. `gman tui`
2. Type `/` and filter for something like `test` or `demo`.
3. Move to a suspicious repo and press `Enter` — check its description,
   last CI run, and open items. Press `v` to skim its README.
4. Press `d` to delete it. gman warns you if it has stars, forks, or is
   pinned to your profile, and offers to download a backup tarball first.
5. Press `Esc`, clear the filter, and press `space` on a few repos that just
   need tidying — then `b` → *Delete branch on merge → ON* to fix them all
   in one pass.

## Working with GitHub Enterprise

Point gman at a GitHub Enterprise Server instance with `--api-url` or the
`GITHUB_API_URL` environment variable:

```bash
gman --api-url https://ghe.example.com/api/v3 list
```

## Where next?

- [Commands](usage.md) — the full CLI reference
- [The TUI](tui.md) — every key, screen, and workflow
- [Tokens & permissions](tokens.md) — least-privilege token recipes
