# Excel export

`gman excel` (or `e` in the TUI) writes your repository inventory to a
spreadsheet you can hand to anyone. The layout is intentionally simple —
four columns, banded rows, optimised for landscape printing.

```bash
gman excel                              # writes github_repos.xlsx
gman excel --output ~/Desktop/repos.xlsx
```

!!! note "Where does the file go?"
    By default the file is `github_repos.xlsx` **in the directory you run
    the command from** — installation location doesn't matter. Pass
    `--output` with any path to put it somewhere specific. The TUI's `e`
    key likewise writes to the directory you launched `gman tui` from,
    and `x` opens that same file.

## Columns

| # | Header | Source field |
| - | --- | --- |
| 1 | Repository | `name` |
| 2 | Description | `description` |
| 3 | Visibility | `visibility` (capitalised) |
| 4 | Last Updated | `updated_at`, parsed as a real datetime |

Rows are sorted by Last Updated descending — the same default the GitHub
API gives back when querying `/user/repos?sort=updated&direction=desc`.

## Formatting

- Header row: white text on dark blue (`#305496`), bold
- Even rows: light gray fill (`#F2F2F2`)
- Description column has wrap-text enabled
- `Last Updated` cells are real datetimes formatted `yyyy-mm-dd hh:mm`
- Header pane is frozen and an autofilter is applied to the table
- Values starting with `= + - @` are prefixed with `'` so spreadsheet apps
  render them as text rather than executing them as formulas

## Page setup

- Orientation: **landscape**
- Fit-to-width: 1 page wide, unlimited pages tall
- Gridlines off, repeating header row when printed
- 0.4″ left/right and 0.5″ top/bottom margins

## Programmatic use

```python
from gman import GitHubClient, write_excel

client = GitHubClient()  # reads GITHUB_TOKEN
write_excel(client.list_repos(), "repos.xlsx")
```
