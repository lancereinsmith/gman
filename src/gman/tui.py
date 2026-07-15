"""Textual TUI for browsing and managing repos."""

from __future__ import annotations

import os
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Any, ClassVar

from rich.markup import escape
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Markdown,
    OptionList,
    Static,
)
from textual.widgets.option_list import Option

from gman.bulk import TUI_BULK_MENU, BulkOp, build_menu_op, normalize_topics, run_bulk
from gman.client import GitHubClient, GitHubError
from gman.details import (
    RepoDetails,
    backup_repo,
    build_delete_warnings,
    fetch_details,
    render_details,
)
from gman.excel import DEFAULT_EXCEL_FILE, write_excel


def row_for_repo(
    repo: dict[str, Any], pinned: set[str], selected: set[str]
) -> tuple[str, str, str, str, str, str, str, str]:
    """Build one DataTable row; pure function for testability."""
    desc = (repo.get("description") or "").replace("\n", " ")
    if len(desc) > 80:
        desc = desc[:77] + "…"
    vis = "🔒" if repo["private"] else "🌐"
    if repo.get("fork"):
        vis += "⑂"
    if repo.get("archived"):
        vis += "❌"
    if repo.get("full_name") in pinned:
        vis += "📌"
    return (
        "✓" if repo.get("full_name") in selected else "",
        repo["name"],
        vis,
        escape(desc),
        repo.get("language") or "",
        str(repo.get("stargazers_count", 0)),
        str(repo.get("open_issues_count", 0)),
        (repo.get("updated_at") or "")[:10],
    )


def restored_cursor_row(new_keys: list[str], prev_key: str | None, prev_row: int) -> int:
    """Row the cursor should occupy after the table is rebuilt.

    Prefer the row still holding the previously focused key (so toggling a
    selection doesn't snap the cursor to the top); if that key is gone, clamp
    the old row index into the new range. Empty table → row 0.
    """
    if not new_keys:
        return 0
    if prev_key is not None:
        try:
            return new_keys.index(prev_key)
        except ValueError:
            pass
    return max(0, min(prev_row, len(new_keys) - 1))


def toggle_all(selected: set[str], visible: set[str]) -> set[str]:
    """All visible already selected → deselect them; otherwise select all visible."""
    if visible and visible <= selected:
        return selected - visible
    return selected | visible


class ConfirmDeleteScreen(ModalScreen[tuple[bool, bool] | None]):
    """Modal that requires retyping the full name; returns (confirmed, backup)."""

    DEFAULT_CSS = """
    ConfirmDeleteScreen { align: center middle; }
    #dialog {
        width: 70; height: auto;
        border: thick $error;
        background: $surface;
        padding: 1 2;
    }
    #title { color: $error; text-style: bold; }
    #warnings { color: $warning; }
    #hint  { color: $text-muted; margin-bottom: 1; }
    """

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, full_name: str, warnings: list[str] | None = None) -> None:
        super().__init__()
        self.full_name = full_name
        self.warnings = warnings or []

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Delete {self.full_name}?", id="title")
            if self.warnings:
                yield Label("\n".join(self.warnings), id="warnings")
            yield Checkbox("Backup tarball first (git contents only)", id="backup")
            yield Label("Type the full name to confirm (esc to cancel):", id="hint")
            yield Input(placeholder=self.full_name, id="confirm")

    def on_mount(self) -> None:
        self.query_one("#confirm", Input).focus()

    @on(Input.Submitted)
    def _submit(self, event: Input.Submitted) -> None:
        confirmed = event.value.strip() == self.full_name
        backup = self.query_one("#backup", Checkbox).value
        self.dismiss((confirmed, backup))

    def action_cancel(self) -> None:
        self.dismiss(None)


class FilterScreen(ModalScreen[str]):
    """Modal for entering a substring filter applied to name + description."""

    DEFAULT_CSS = """
    FilterScreen { align: center middle; }
    #fdialog {
        width: 60; height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, current: str = "") -> None:
        super().__init__()
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="fdialog"):
            yield Label("Filter (substring of name/description; empty to clear):")
            yield Input(value=self.current, id="filt")

    @on(Input.Submitted)
    def _submit(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(self.current)


class EditDescriptionScreen(ModalScreen[str | None]):
    """Modal for editing the description of a repo. Returns `None` if cancelled."""

    DEFAULT_CSS = """
    EditDescriptionScreen { align: center middle; }
    #edialog {
        width: 80; height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, full_name: str, current: str = "") -> None:
        super().__init__()
        self.full_name = full_name
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="edialog"):
            yield Label(f"Edit description for {self.full_name} (esc to cancel):")
            yield Input(value=self.current, id="desc")

    @on(Input.Submitted)
    def _submit(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class EditTopicsScreen(ModalScreen[str | None]):
    """Modal for editing a repo's topics (comma-separated). None = cancelled."""

    DEFAULT_CSS = """
    EditTopicsScreen { align: center middle; }
    #tdialog {
        width: 80; height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, full_name: str, current: str = "") -> None:
        super().__init__()
        self.full_name = full_name
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="tdialog"):
            yield Label(f"Edit topics for {self.full_name} (comma-separated; esc to cancel):")
            yield Input(value=self.current, id="topics")

    @on(Input.Submitted)
    def _submit(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class EditHomepageScreen(ModalScreen[str | None]):
    """Modal for editing a repo's homepage URL. None = cancelled."""

    DEFAULT_CSS = """
    EditHomepageScreen { align: center middle; }
    #hdialog {
        width: 80; height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, full_name: str, current: str = "") -> None:
        super().__init__()
        self.full_name = full_name
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="hdialog"):
            yield Label(f"Edit homepage for {self.full_name} (esc to cancel):")
            yield Input(value=self.current, id="homepage")

    @on(Input.Submitted)
    def _submit(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ReadmeScreen(ModalScreen[None]):
    """Scrollable rendered-markdown view of a repo's README."""

    DEFAULT_CSS = """
    ReadmeScreen { align: center middle; }
    #readme-box {
        width: 90%; height: 85%;
        border: thick $accent;
        background: $surface;
        padding: 0 1;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "close", "Close")]

    def __init__(self, client: GitHubClient, full_name: str) -> None:
        super().__init__()
        self.client = client
        self.full_name = full_name

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="readme-box"):
            yield Markdown("*Loading README…*", id="readme")

    def on_mount(self) -> None:
        self._load()

    @work(thread=True)
    def _load(self) -> None:
        text = self.client.get_readme(self.full_name)
        if text is None:
            hint = ""
            if self.client.capabilities.resolve("contents.read") is False:
                hint = f" — {self.client.capabilities.hint('contents.read')}"
            text = f"*No README available{hint}*"
        markdown = self.query_one("#readme", Markdown)
        self.app.call_from_thread(markdown.update, text)

    def action_close(self) -> None:
        self.dismiss(None)


class TopicInputScreen(ModalScreen[str | None]):
    """Prompt for a single topic name; returns the validated topic or None."""

    DEFAULT_CSS = """
    TopicInputScreen { align: center middle; }
    #topicdialog {
        width: 60; height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="topicdialog"):
            yield Label("Topic name (esc to cancel):")
            yield Input(placeholder="e.g. python", id="topic")

    @on(Input.Submitted)
    def _submit(self, event: Input.Submitted) -> None:
        valid, errors = normalize_topics(event.value)
        if errors or len(valid) != 1:
            self.app.notify("Enter exactly one valid topic.", severity="error")
            self.dismiss(None)
            return
        self.dismiss(valid[0])

    def action_cancel(self) -> None:
        self.dismiss(None)


class BulkMenuScreen(ModalScreen[tuple[str, str | None] | None]):
    """Pick a bulk operation; returns (menu key, topic arg) or None."""

    DEFAULT_CSS = """
    BulkMenuScreen { align: center middle; }
    #bulkmenu {
        width: 50; height: auto; max-height: 80%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, count: int) -> None:
        super().__init__()
        self.count = count

    def compose(self) -> ComposeResult:
        with Vertical(id="bulkmenu"):
            yield Label(f"Bulk action for {self.count} selected repos:")
            yield OptionList(
                *[Option(label, id=key) for key, label, _needs in TUI_BULK_MENU], id="ops"
            )

    @on(OptionList.OptionSelected)
    def _selected(self, event: OptionList.OptionSelected) -> None:
        key = event.option.id or ""
        needs_topic = next(needs for k, _label, needs in TUI_BULK_MENU if k == key)
        if not needs_topic:
            self.dismiss((key, None))
            return

        def after_topic(topic: str | None) -> None:
            self.dismiss(None if topic is None else (key, topic))

        self.app.push_screen(TopicInputScreen(), after_topic)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmBulkScreen(ModalScreen[bool]):
    """Confirm a bulk operation. No Input widget, so letter bindings are safe."""

    DEFAULT_CSS = """
    ConfirmBulkScreen { align: center middle; }
    #bulkconfirm {
        width: 70; height: auto;
        border: thick $warning;
        background: $surface;
        padding: 1 2;
    }
    #bctitle { text-style: bold; }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("y", "confirm", "Yes"),
        Binding("n", "cancel", "No"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, label: str, names: list[str]) -> None:
        super().__init__()
        self.label = label
        self.names = names

    def compose(self) -> ComposeResult:
        preview = ", ".join(self.names[:5])
        if len(self.names) > 5:
            preview += f" … +{len(self.names) - 5} more"
        with Vertical(id="bulkconfirm"):
            yield Label(escape(self.label), id="bctitle")
            yield Label(f"{len(self.names)} repos: {escape(preview)}")
            yield Label("Press y to proceed, n or esc to cancel.")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class RepoDetailScreen(ModalScreen[None]):
    """Lazy-loaded detail panel for one repo."""

    DEFAULT_CSS = """
    RepoDetailScreen { align: center middle; }
    #detail-box {
        width: 90%; height: 80%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Close"),
        Binding("v", "view_readme", "README"),
    ]

    def __init__(
        self,
        client: GitHubClient,
        repo: dict[str, Any],
        cache: dict[tuple[str, str], RepoDetails],
    ) -> None:
        super().__init__()
        self.client = client
        self.repo = repo
        self.cache = cache

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="detail-box"):
            yield Static(f"Loading {self.repo['full_name']}…", id="detail-content")

    def on_mount(self) -> None:
        self._load()

    @work(thread=True)
    def _load(self) -> None:
        key = (self.repo["full_name"], self.repo.get("updated_at") or "")
        details = self.cache.get(key)
        if details is None:
            details = fetch_details(self.client, self.repo)
            self.cache[key] = details
        content = self.query_one("#detail-content", Static)
        self.app.call_from_thread(content.update, render_details(details))

    def action_close(self) -> None:
        self.dismiss(None)

    def action_view_readme(self) -> None:
        self.app.push_screen(ReadmeScreen(self.client, self.repo["full_name"]))


class GitHubRepoApp(App[None]):
    """Interactive table of the user's repos with delete/export actions."""

    CSS = "DataTable { height: 1fr; }"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("e", "export_excel", "Excel"),
        Binding("x", "open_excel", "Open xlsx"),
        Binding("o", "open_browser", "Open"),
        Binding("i", "show_details", "Details"),
        Binding("a", "toggle_archive", "Archive/Unarchive"),
        Binding("c", "edit_description", "Change desc"),
        Binding("t", "edit_topics", "Topics"),
        Binding("h", "edit_homepage", "Homepage"),
        Binding("s", "sync_fork", "Sync fork"),
        Binding("d", "delete_repo", "Delete"),
        Binding("slash", "filter", "Filter"),
        Binding("space", "toggle_select", "Select"),
        Binding("ctrl+a", "toggle_select_all", "Select all"),
        Binding("b", "bulk_menu", "Bulk"),
    ]

    def __init__(self, client: GitHubClient) -> None:
        super().__init__()
        self.client = client
        self.all_repos: list[dict[str, Any]] = []
        self.filter_text: str = ""
        self.username: str = ""
        self.pinned: set[str] = set()
        self.selected: set[str] = set()
        self.details_cache: dict[tuple[str, str], RepoDetails] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield DataTable(zebra_stripes=True, cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "GitHub Repos"
        self.sub_title = "loading…"
        table = self.query_one(DataTable)
        table.add_columns("✓", "Name", "Vis.", "Description", "Lang", "Stars", "Open", "Updated")
        self.load_repos()

    @work(thread=True, exclusive=True)
    def load_repos(self) -> None:
        try:
            username = self.client.whoami() or ""
            repos = self.client.list_repos()
            pinned = self.client.get_pinned_repos()
        except Exception as e:
            self.call_from_thread(self.notify, f"Failed to load: {e}", severity="error")
            return
        self.call_from_thread(self._on_loaded, username, repos, pinned)

    def _on_loaded(self, username: str, repos: list[dict[str, Any]], pinned: set[str]) -> None:
        self.username = username
        self.all_repos = repos
        self.pinned = pinned
        self.selected.clear()
        self.details_cache.clear()
        self.refresh_table()

    def _visible_repos(self) -> list[dict[str, Any]]:
        ft = self.filter_text.lower()
        return [
            r
            for r in self.all_repos
            if not ft
            or ft in (r.get("name") or "").lower()
            or ft in (r.get("description") or "").lower()
        ]

    def refresh_table(self) -> None:
        table = self.query_one(DataTable)
        prev_row = table.cursor_row
        prev_key: str | None = None
        try:
            if table.row_count:
                prev_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        except Exception:
            prev_key = None
        table.clear()
        visible = self._visible_repos()
        keys = [repo["full_name"] for repo in visible]
        for repo in visible:
            table.add_row(*row_for_repo(repo, self.pinned, self.selected), key=repo["full_name"])
        if keys:
            table.move_cursor(row=restored_cursor_row(keys, prev_key, prev_row))
        suffix = f" — filter: {self.filter_text!r}" if self.filter_text else ""
        if self.selected:
            suffix += f" — {len(self.selected)} selected"
        self.sub_title = f"{self.username} — {len(visible)}/{len(self.all_repos)}{suffix}"

    def _selected_repo(self) -> dict[str, Any] | None:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return None
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        except Exception:
            return None
        for r in self.all_repos:
            if r["full_name"] == row_key:
                return r
        return None

    def action_refresh(self) -> None:
        self.sub_title = "loading…"
        self.load_repos()

    def action_export_excel(self) -> None:
        try:
            write_excel(self.all_repos, DEFAULT_EXCEL_FILE)
            self.notify(f"Wrote {len(self.all_repos)} repos to {DEFAULT_EXCEL_FILE}")
        except Exception as e:
            self.notify(f"Export failed: {e}", severity="error")

    def action_open_browser(self) -> None:
        repo = self._selected_repo()
        if repo and repo.get("html_url"):
            webbrowser.open(repo["html_url"])
            self.notify(f"Opened {repo['full_name']}")

    def action_open_excel(self) -> None:
        path = Path(DEFAULT_EXCEL_FILE).resolve()
        if not path.is_file():
            self.notify(
                f"No spreadsheet at {path} — press 'e' to export first.", severity="warning"
            )
            return
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", str(path)], check=True)
            elif sys.platform == "win32":
                os.startfile(str(path))
            else:
                subprocess.run(["xdg-open", str(path)], check=True)
        except (OSError, subprocess.CalledProcessError) as e:
            self.notify(f"Open failed: {e}", severity="error")
            return
        self.notify(f"Opened {path.name}")

    def action_delete_repo(self) -> None:
        repo = self._selected_repo()
        if not repo:
            return
        warnings = build_delete_warnings(repo, self.pinned)

        def after(result: tuple[bool, bool] | None) -> None:
            if not result or not result[0]:
                self.notify("Cancelled")
                return
            self._delete_worker(repo, backup=result[1])

        self.push_screen(ConfirmDeleteScreen(repo["full_name"], warnings), after)

    @work(thread=True)
    def _delete_worker(self, repo: dict[str, Any], backup: bool) -> None:
        full = repo["full_name"]
        if backup:
            try:
                path = backup_repo(self.client, repo, Path.cwd())
            except GitHubError as e:
                self.call_from_thread(
                    self.notify, f"Backup failed — deletion aborted: {e}", severity="error"
                )
                return
            self.call_from_thread(self.notify, f"Backed up to {path.name}")
        ok, msg = self.client.delete_repo(full)
        if ok:
            self.all_repos = [r for r in self.all_repos if r["full_name"] != full]
            self.call_from_thread(self.refresh_table)
            self.call_from_thread(self.notify, msg, severity="warning")
        else:
            self.call_from_thread(self.notify, f"Delete failed: {msg}", severity="error")

    def action_toggle_archive(self) -> None:
        repo = self._selected_repo()
        if not repo:
            return
        archived = bool(repo.get("archived"))
        target = not archived
        ok, msg = self.client.set_archived(repo["full_name"], archived=target)
        if not ok:
            self.notify(f"Archive failed: {msg}", severity="error")
            return
        repo["archived"] = target
        self.all_repos.sort(key=lambda r: bool(r.get("archived")))
        self.refresh_table()
        self.notify(msg, severity="warning")

    def action_filter(self) -> None:
        def after(text: str | None) -> None:
            self.filter_text = text or ""
            self.refresh_table()

        self.push_screen(FilterScreen(self.filter_text), after)

    def action_edit_description(self) -> None:
        repo = self._selected_repo()
        if not repo:
            return
        full = repo["full_name"]
        current = repo.get("description") or ""

        def after(new_desc: str | None) -> None:
            if new_desc is None:
                self.notify("Cancelled")
                return
            ok, msg = self.client.set_description(full, new_desc)
            if not ok:
                self.notify(f"Update failed: {msg}", severity="error")
                return
            repo["description"] = new_desc
            self.refresh_table()
            self.notify(msg)

        self.push_screen(EditDescriptionScreen(full, current), after)

    def action_edit_topics(self) -> None:
        repo = self._selected_repo()
        if not repo:
            return
        full = repo["full_name"]
        current = ", ".join(repo.get("topics") or [])

        def after(raw: str | None) -> None:
            if raw is None:
                self.notify("Cancelled")
                return
            topics, errors = normalize_topics(raw)
            if errors:
                self.notify(escape("; ".join(errors)), severity="error")
                return
            ok, msg = self.client.set_topics(full, topics)
            if ok:
                repo["topics"] = topics
                self.notify(msg)
            else:
                self.notify(f"Update failed: {escape(msg)}", severity="error")

        self.push_screen(EditTopicsScreen(full, current), after)

    def action_edit_homepage(self) -> None:
        repo = self._selected_repo()
        if not repo:
            return
        full = repo["full_name"]
        current = repo.get("homepage") or ""

        def after(url: str | None) -> None:
            if url is None:
                self.notify("Cancelled")
                return
            ok, msg = self.client.update_repo(full, {"homepage": url})
            if ok:
                repo["homepage"] = url
                self.notify(msg)
            else:
                self.notify(f"Update failed: {escape(msg)}", severity="error")

        self.push_screen(EditHomepageScreen(full, current), after)

    def action_sync_fork(self) -> None:
        repo = self._selected_repo()
        if not repo:
            return
        if not repo.get("fork"):
            self.notify("Not a fork — nothing to sync.", severity="warning")
            return
        self._sync_worker(repo)

    @work(thread=True)
    def _sync_worker(self, repo: dict[str, Any]) -> None:
        branch = repo.get("default_branch") or "HEAD"
        ok, msg = self.client.merge_upstream(repo["full_name"], branch)
        severity = "information" if ok else "error"
        self.call_from_thread(self.notify, escape(msg), severity=severity)
        if ok:
            self.call_from_thread(self.load_repos)

    def action_show_details(self) -> None:
        repo = self._selected_repo()
        if repo:
            self.push_screen(RepoDetailScreen(self.client, repo, self.details_cache))

    def action_toggle_select(self) -> None:
        repo = self._selected_repo()
        if not repo:
            return
        full = repo["full_name"]
        if full in self.selected:
            self.selected.discard(full)
        else:
            self.selected.add(full)
        self.refresh_table()

    def action_toggle_select_all(self) -> None:
        visible = {r["full_name"] for r in self._visible_repos()}
        self.selected = toggle_all(self.selected, visible)
        self.refresh_table()

    @on(DataTable.RowSelected)
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_show_details()

    def action_bulk_menu(self) -> None:
        if self.client.capabilities.resolve("admin.write") is False:
            hint = self.client.capabilities.hint("admin.write")
            self.notify(f"Token cannot write — {hint}", severity="error")
            return
        if not self.selected:
            self.notify("Nothing selected — press space to select repos.", severity="warning")
            return

        def after_menu(result: tuple[str, str | None] | None) -> None:
            if not result:
                return
            key, arg = result
            op = build_menu_op(key, arg)
            targets = [r for r in self.all_repos if r["full_name"] in self.selected]
            names = [r["full_name"] for r in targets]

            def after_confirm(confirmed: bool | None) -> None:
                if confirmed:
                    self._bulk_worker(targets, op)

            self.push_screen(ConfirmBulkScreen(op.label, names), after_confirm)

        self.push_screen(BulkMenuScreen(len(self.selected)), after_menu)

    @work(thread=True)
    def _bulk_worker(self, targets: list[dict[str, Any]], op: BulkOp) -> None:
        def progress(done: int, total: int) -> None:
            self.call_from_thread(setattr, self, "sub_title", f"bulk {done}/{total}…")

        results = run_bulk(self.client, targets, [op], progress=progress)
        ok = sum(1 for r in results if r.ok)
        failed = sum(1 for r in results if not r.ok and not r.skipped)
        skipped = sum(1 for r in results if r.skipped)
        summary = f"{op.label}: {ok} ok, {failed} failed"
        if skipped:
            summary += f", {skipped} skipped (rate limit)"
        severity = "error" if failed or skipped else "information"
        self.call_from_thread(self.notify, summary, severity=severity)
        self.call_from_thread(self.selected.clear)
        self.call_from_thread(self.load_repos)
