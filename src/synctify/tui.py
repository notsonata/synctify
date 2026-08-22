from __future__ import annotations

from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Static,
    TabbedContent,
    TabPane,
)

from . import __version__
from .audit import LibraryAuditReport
from .config import Settings
from .doctor import DoctorReport
from .tui_backend import (
    DashboardState,
    list_unresolved_tracks,
    read_audit_report,
    read_dashboard,
    read_doctor_report,
    set_manual_resolution,
)


class SynctifyTUI(App[None]):
    """Interactive terminal frontend for Synctify's local state and diagnostics."""

    TITLE = "Synctify"
    SUB_TITLE = f"v{__version__}"

    CSS = """
    Screen {
        background: $surface;
    }

    Header {
        background: $boost;
    }

    #tabs {
        height: 1fr;
    }

    TabPane {
        padding: 1 2;
    }

    .panel {
        border: round $primary;
        padding: 1 2;
        margin-bottom: 1;
    }

    .actions {
        height: auto;
        margin-bottom: 1;
    }

    .actions Button {
        margin-right: 1;
    }

    #dashboard-summary {
        height: auto;
    }

    #resolver-layout {
        height: 1fr;
    }

    #unresolved-table,
    #doctor-table,
    #audit-table {
        height: 1fr;
        border: round $secondary;
    }

    #resolver-form {
        height: auto;
        border: round $primary;
        padding: 1;
        margin-top: 1;
    }

    #resolver-form Input {
        margin-bottom: 1;
    }

    #selected-track,
    #doctor-summary,
    #audit-summary,
    #status-line {
        height: auto;
    }

    #status-line {
        dock: bottom;
        padding: 0 2;
        background: $boost;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
        ("1", "show_tab('dashboard')", "Dashboard"),
        ("2", "show_tab('resolver')", "Resolve"),
        ("3", "show_tab('doctor')", "Doctor"),
        ("4", "show_tab('audit')", "Audit"),
    ]

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__()
        self.settings = settings or Settings.default()
        self._selected_spotify_id: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="dashboard", id="tabs"):
            with TabPane("Dashboard", id="dashboard"):
                with Horizontal(classes="actions"):
                    yield Button("Refresh", id="refresh-dashboard", variant="primary")
                    yield Button("Run Doctor", id="open-doctor")
                    yield Button("Run Audit", id="open-audit")
                yield Static(id="dashboard-summary", classes="panel")

            with TabPane("Unresolved", id="resolver"):
                with Vertical(id="resolver-layout"):
                    yield DataTable(id="unresolved-table")
                    with Vertical(id="resolver-form"):
                        yield Static(
                            "Select an unresolved track, then enter a supported provider and provider track ID.",
                            id="selected-track",
                        )
                        yield Input(
                            placeholder="Provider: qobuz, tidal, or deezer",
                            id="provider-input",
                        )
                        yield Input(
                            placeholder="Provider track ID",
                            id="provider-track-id-input",
                        )
                        with Horizontal(classes="actions"):
                            yield Button("Save Mapping", id="save-mapping", variant="primary")
                            yield Button("Refresh", id="refresh-resolver")

            with TabPane("Doctor", id="doctor"):
                with Horizontal(classes="actions"):
                    yield Button("Run Doctor", id="run-doctor", variant="primary")
                yield Static("Doctor has not been run yet.", id="doctor-summary", classes="panel")
                yield DataTable(id="doctor-table")

            with TabPane("Audit", id="audit"):
                with Horizontal(classes="actions"):
                    yield Button("Run Audit", id="run-audit", variant="primary")
                yield Static("Audit has not been run yet.", id="audit-summary", classes="panel")
                yield DataTable(id="audit-table")

        yield Static("Ready", id="status-line")
        yield Footer()

    def on_mount(self) -> None:
        self._configure_tables()
        self.refresh_core()

    def _configure_tables(self) -> None:
        unresolved = self.query_one("#unresolved-table", DataTable)
        unresolved.cursor_type = "row"
        unresolved.zebra_stripes = True
        unresolved.add_columns("Artist", "Title", "Album", "ISRC")

        doctor = self.query_one("#doctor-table", DataTable)
        doctor.cursor_type = "row"
        doctor.zebra_stripes = True
        doctor.add_columns("Status", "Section", "Check", "Message")

        audit = self.query_one("#audit-table", DataTable)
        audit.cursor_type = "row"
        audit.zebra_stripes = True
        audit.add_columns("Type", "Artist", "Track", "Path")

    def _set_status(self, message: str) -> None:
        self.query_one("#status-line", Static).update(message)

    def _render_dashboard(self, state: DashboardState) -> None:
        if not state.initialized:
            body = (
                "[b]Synctify is not initialized.[/b]\n\n"
                f"Home: {self.settings.home}\n"
                "Run `synctify setup` or `synctify init` before using stateful TUI actions."
            )
        else:
            body = "\n".join(
                [
                    "[b]Library[/b]",
                    f"Tracks: {state.tracks}",
                    f"Local FLACs: {state.local_tracks}",
                    f"Unresolved: {state.unresolved}",
                    f"Stored resolutions: {state.resolutions}",
                    f"Pending downloads: {state.pending_downloads}",
                    "",
                    "[b]Playlists and targets[/b]",
                    f"Playlists: {state.playlists}",
                    f"Sync/backup targets: {state.targets}",
                    "",
                    "[b]Spotify[/b]",
                    f"Last pull: {state.last_pull or 'never pulled'}",
                    "",
                    f"Library: {state.library_dir}",
                ]
            )
        self.query_one("#dashboard-summary", Static).update(body)

    def _refresh_unresolved(self) -> None:
        table = self.query_one("#unresolved-table", DataTable)
        table.clear()
        tracks = list_unresolved_tracks(self.settings)
        for track in tracks:
            table.add_row(
                track.artist,
                track.title,
                track.album or "",
                track.isrc or "",
                key=track.spotify_id,
            )
        self._selected_spotify_id = None
        self.query_one("#selected-track", Static).update(
            f"{len(tracks)} unresolved desired track(s). Select a row to create a manual mapping."
        )

    def refresh_core(self) -> None:
        try:
            state = read_dashboard(self.settings)
            self._render_dashboard(state)
            self._refresh_unresolved()
        except Exception as exc:
            self._set_status(f"Refresh failed: {exc}")
        else:
            self._set_status("Refreshed local state")

    def action_refresh(self) -> None:
        self.refresh_core()

    def action_show_tab(self, tab: str) -> None:
        self.query_one("#tabs", TabbedContent).active = tab

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id != "unresolved-table":
            return
        key = event.row_key.value
        self._selected_spotify_id = key
        if key is None:
            return
        table = event.data_table
        artist = table.get_cell(key, table.columns.keys().__iter__().__next__())
        self.query_one("#selected-track", Static).update(
            f"Selected Spotify track: {key}\nArtist: {artist}"
        )

    def _save_mapping(self) -> None:
        spotify_id = self._selected_spotify_id
        if spotify_id is None:
            self.notify("Select an unresolved track first.", severity="warning")
            return
        provider = self.query_one("#provider-input", Input).value.strip().lower()
        provider_track_id = self.query_one("#provider-track-id-input", Input).value.strip()
        if not provider or not provider_track_id:
            self.notify("Provider and provider track ID are required.", severity="warning")
            return
        try:
            set_manual_resolution(
                self.settings,
                spotify_id,
                provider,
                provider_track_id,
            )
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            self.notify(str(exc), severity="error", timeout=5)
            self._set_status(f"Mapping failed: {exc}")
            return

        self.query_one("#provider-track-id-input", Input).value = ""
        self.notify(f"Mapped {spotify_id} to {provider}:{provider_track_id}")
        self.refresh_core()

    @work(thread=True, exclusive=True, group="doctor", exit_on_error=False)
    def run_doctor_worker(self) -> None:
        self.call_from_thread(self._set_status, "Running doctor…")
        try:
            report = read_doctor_report(self.settings)
        except Exception as exc:
            self.call_from_thread(self._doctor_failed, exc)
            return
        self.call_from_thread(self._render_doctor, report)

    def _doctor_failed(self, exc: Exception) -> None:
        self._set_status(f"Doctor failed: {exc}")
        self.notify(str(exc), severity="error", timeout=5)

    def _render_doctor(self, report: DoctorReport) -> None:
        table = self.query_one("#doctor-table", DataTable)
        table.clear()
        for index, check in enumerate(report.checks):
            table.add_row(
                check.status.value,
                check.section,
                check.name,
                check.message,
                key=f"doctor-{index}",
            )
        self.query_one("#doctor-summary", Static).update(
            f"[b]{report.passed} passed[/b] · {report.warnings} warning(s) · {report.failures} failure(s)"
        )
        self._set_status("Doctor complete")

    @work(thread=True, exclusive=True, group="audit", exit_on_error=False)
    def run_audit_worker(self) -> None:
        self.call_from_thread(self._set_status, "Running read-only audit…")
        try:
            report = read_audit_report(self.settings)
        except Exception as exc:
            self.call_from_thread(self._audit_failed, exc)
            return
        self.call_from_thread(self._render_audit, report)

    def _audit_failed(self, exc: Exception) -> None:
        self._set_status(f"Audit failed: {exc}")
        self.notify(str(exc), severity="error", timeout=5)

    def _render_audit(self, report: LibraryAuditReport) -> None:
        table = self.query_one("#audit-table", DataTable)
        table.clear()
        for index, issue in enumerate(report.issues):
            table.add_row(
                issue.kind.value,
                issue.artist,
                issue.title,
                str(issue.path),
                key=f"issue-{index}",
            )
        self.query_one("#audit-summary", Static).update(
            " · ".join(
                [
                    f"{len(report.issues)} recorded issue(s)",
                    f"{report.missing} missing",
                    f"{report.hash_problems} hash problem(s)",
                    f"{report.unsafe_paths} unsafe path(s)",
                    f"{len(report.untracked_files)} untracked FLAC(s)",
                    f"{len(report.proposed_adoptions)} safe adoption(s)",
                ]
            )
        )
        self._set_status("Audit complete")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id in {"refresh-dashboard", "refresh-resolver"}:
            self.refresh_core()
        elif button_id == "save-mapping":
            self._save_mapping()
        elif button_id in {"open-doctor", "run-doctor"}:
            self.action_show_tab("doctor")
            self.run_doctor_worker()
        elif button_id in {"open-audit", "run-audit"}:
            self.action_show_tab("audit")
            self.run_audit_worker()


def run_tui(settings: Settings | None = None) -> None:
    """Launch the Synctify terminal UI."""
    SynctifyTUI(settings=settings).run()


__all__ = ["SynctifyTUI", "run_tui"]
