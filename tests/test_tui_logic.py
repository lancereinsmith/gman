"""Tests for pure TUI logic (no app instantiation)."""

from __future__ import annotations

from conftest import make_repo

from gman.tui import restored_cursor_row, row_for_repo, toggle_all


def test_row_for_repo_basic() -> None:
    repo = make_repo("alpha", stargazers_count=5, open_issues_count=2)
    row = row_for_repo(repo, pinned=set(), selected=set())
    assert row == ("", "alpha", "🌐", "desc for alpha", "Python", "5", "2", "2026-01-01")


def test_row_for_repo_badges() -> None:
    repo = make_repo("beta", private=True, archived=True)
    row = row_for_repo(repo, pinned={"octocat/beta"}, selected=set())
    assert row[2] == "🔒❌📌"


def test_row_for_repo_truncates_description() -> None:
    repo = make_repo("gamma", description="x" * 100)
    row = row_for_repo(repo, pinned=set(), selected=set())
    assert len(row[3]) == 78 and row[3].endswith("…")


def test_row_for_repo_escapes_markup_in_description() -> None:
    repo = make_repo("delta", description="see [/] notes")
    row = row_for_repo(repo, pinned=set(), selected=set())
    assert row[3] == r"see \[/] notes"


def test_row_for_repo_selection_marker() -> None:
    repo = make_repo("epsilon")
    assert row_for_repo(repo, pinned=set(), selected={"octocat/epsilon"})[0] == "✓"
    assert row_for_repo(repo, pinned=set(), selected=set())[0] == ""


def test_toggle_all_selects_then_deselects() -> None:
    visible = {"o/a", "o/b"}
    assert toggle_all(set(), visible) == {"o/a", "o/b"}
    assert toggle_all({"o/a"}, visible) == {"o/a", "o/b"}  # partial → select all
    assert toggle_all({"o/a", "o/b", "o/c"}, visible) == {"o/c"}  # all visible → drop them
    assert toggle_all(set(), set()) == set()


def test_restored_cursor_row_keeps_focused_key() -> None:
    keys = ["o/a", "o/b", "o/c"]
    # Cursor was on row 1 ("o/b"); a rebuild that keeps order must stay on it,
    # not jump to the top — this is the space-to-select bug.
    assert restored_cursor_row(keys, "o/b", 1) == 1


def test_restored_cursor_row_follows_key_when_order_changes() -> None:
    keys = ["o/c", "o/b", "o/a"]
    assert restored_cursor_row(keys, "o/b", 0) == 1


def test_restored_cursor_row_clamps_when_key_gone() -> None:
    keys = ["o/a", "o/b"]
    # Focused row was deleted; fall back to clamping the old index into range.
    assert restored_cursor_row(keys, "o/deleted", 5) == 1
    assert restored_cursor_row(keys, None, 0) == 0


def test_restored_cursor_row_empty_table() -> None:
    assert restored_cursor_row([], "o/a", 3) == 0


def test_row_for_repo_fork_badge() -> None:
    repo = make_repo("zeta", fork=True)
    row = row_for_repo(repo, pinned=set(), selected=set())
    assert row[2] == "🌐⑂"


def test_row_for_repo_fork_badge_order_with_all_badges() -> None:
    repo = make_repo("eta", private=True, archived=True, fork=True)
    row = row_for_repo(repo, pinned={"octocat/eta"}, selected=set())
    assert row[2] == "🔒⑂❌📌"
