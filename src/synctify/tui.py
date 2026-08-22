from __future__ import annotations

import re
import shlex
import threading

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    LoadingIndicator,
    ProgressBar,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)

from . import __version__
from .audit import LibraryAuditReport
from .config import Settings
from .doctor import DoctorReport
from .spotify.selection import PlaylistCatalogEntry, SpotifySelectionCancelled
from .tui_backend import (
    CommandResult,
    DashboardState,
    apply_spotify_playlist,
    choose_spotify_item,
    confirm_spotify_playlist,
    fetch_spotify_playlist_items,
    fetch_spotify_playlists,
    fetch_tracked_spotify_updates,
    list_unresolved_tracks,
    read_audit_report,
    read_dashboard,
    read_doctor_report,
    read_spotify_playlist_items,
    read_spotify_playlists,
    run_cli_command,
    set_manual_resolution,
    unimport_spotify_playlist,
)


_UPDATE_PROGRESS_RE = re.compile(
    r"^\[update\]\s+Resolving via (?P<source>\S+) "
    r"\[(?P<current>\d+)/(?P<total>\d+)\]\s+(?P<label>.+)$"
)


class SynctifyTUI(App[None]):
    """Interactive terminal frontend for Synctify state and staged Spotify selection."""

    TITLE = "Synctify"
    SUB_TITLE = f"v{__version__}"

    CSS = """
    Screen { background: $surface; }
    Header { background: $boost; }
    #tabs { height: 1fr; }
    TabPane { padding: 1 2; }
    .panel { border: round $primary; padding: 1 2; margin-bottom: 1; }
    .actions { height: auto; margin-bottom: 1; }
    .actions Button { margin-right: 1; }
    #dashboard-summary, #spotify-sidebar, #command-help { height: auto; }
    #resolver-layout, #spotify-layout, #spotify-detail { height: 1fr; }
    #spotify-playlists { width: 38%; height: 1fr; border: round $secondary; margin-right: 1; }
    #spotify-detail { width: 62%; }
    #spotify-tracks, #unresolved-table, #doctor-table, #audit-table, #command-log {
        height: 1fr;
        border: round $secondary;
    }
    #spotify-sidebar { min-height: 7; }
    #resolver-form { height: auto; border: round $primary; padding: 1; margin-top: 1; }
    #resolver-form Input { margin-bottom: 1; }
    #command-row { height: auto; margin-bottom: 1; }
    #command-input { width: 1fr; margin-right: 1; }
    #busy-row { dock: bottom; height: 4; padding: 0 2; background: $boost; }
    #busy-indicator { width: 6; display: none; }
    #busy-stack { width: 1fr; height: 3; }
    #busy-label { height: 1; content-align: left middle; }
    #busy-progress { height: 1; display: none; margin-top: 1; }
    #cancel-action { width: 14; }
    #selected-track, #doctor-summary, #audit-summary { height: auto; }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("escape", "cancel_current", "Cancel"),
        ("r", "refresh", "Refresh"),
        ("i", "show_tab('spotify')", "Spotify"),
        ("u", "start_update", "Library Update"),
        ("1", "show_tab('dashboard')", "Dashboard"),
        ("2", "show_tab('spotify')", "Spotify"),
        ("3", "show_tab('resolver')", "Resolve"),
        ("4", "show_tab('doctor')", "Doctor"),
        ("5", "show_tab('audit')", "Audit"),
        ("6", "show_tab('commands')", "Commands"),
    ]

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__()
        self._settings_explicit = settings is not None
        self.settings = settings or Settings.default()
        self._selected_spotify_id: str | None = None
        self._selected_playlist_id: str | None = None
        self._selected_playlist_item_key: str | None = None
        self._playlist_cache: dict[str, PlaylistCatalogEntry] = {}
        self._operation_running = False
        self._cancel_event = threading.Event()

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="dashboard", id="tabs"):
            with TabPane("Dashboard", id="dashboard"):
                with Horizontal(classes="actions"):
                    yield Button("Spotify Playlists", id="open-spotify", variant="primary")
                    yield Button("Library Update", id="run-update")
                    yield Button("Refresh", id="refresh-dashboard")
                    yield Button("Run Doctor", id="open-doctor")
                    yield Button("Run Audit", id="open-audit")
                yield Static(id="dashboard-summary", classes="panel")

            with TabPane("Spotify", id="spotify"):
                yield Static(
                    "Fetch the playlist list first. Opening a playlist fetches only that playlist's tracks. "
                    "Imported playlists keep included/excluded choices; new Spotify changes stay pending until reviewed.",
                    classes="panel",
                )
                with Horizontal(classes="actions"):
                    yield Button("Fetch Playlists", id="spotify-fetch", variant="primary")
                    yield Button("Update Tracked", id="spotify-update-tracked")
                    yield Button("Load / Review", id="spotify-load")
                    yield Button("Confirm Playlist", id="spotify-confirm")
                    yield Button("Apply Choices", id="spotify-apply")
                    yield Button("Unimport", id="spotify-unimport", variant="error")
                with Horizontal(id="spotify-layout"):
                    yield DataTable(id="spotify-playlists")
                    with Vertical(id="spotify-detail"):
                        yield Static(
                            "Select a playlist to review it.",
                            id="spotify-sidebar",
                            classes="panel",
                        )
                        with Horizontal(classes="actions"):
                            yield Button("Include Track", id="spotify-include-track")
                            yield Button("Exclude Track", id="spotify-exclude-track")
                        yield DataTable(id="spotify-tracks")

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

            with TabPane("Commands", id="commands"):
                yield Static(
                    "[b]Run non-interactive Synctify workflows without leaving the TUI.[/b]\n"
                    "Spotify selection is managed in the Spotify tab. The command box accepts arguments after "
                    "`synctify`, for example `acquire --source tidal`, `sync phone`, `backup cloud`, or `config show`.",
                    id="command-help",
                    classes="panel",
                )
                with Horizontal(classes="actions"):
                    yield Button("Fetch Playlists", id="command-fetch-playlists", variant="primary")
                    yield Button("Update Tracked", id="command-update-tracked")
                    yield Button("Library Update", id="command-update")
                    yield Button("Auto Resolve", id="command-resolve")
                    yield Button("Build Playlists", id="command-playlists")
                    yield Button("List Targets", id="command-targets")
                with Horizontal(id="command-row"):
                    yield Input(
                        placeholder="Command, e.g. sync phone or acquire --source deezer",
                        id="command-input",
                    )
                    yield Button("Run Command", id="run-command", variant="primary")
                yield RichLog(id="command-log", wrap=True, markup=False, highlight=True)

        with Horizontal(id="busy-row"):
            yield LoadingIndicator(id="busy-indicator")
            with Vertical(id="busy-stack"):
                yield Static("Ready", id="busy-label")
                yield ProgressBar(
                    total=100,
                    show_eta=False,
                    show_percentage=True,
                    id="busy-progress",
                )
            yield Button("Cancel (Esc)", id="cancel-action", variant="error", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        self._configure_tables()
        self.refresh_core()

    def _configure_tables(self) -> None:
        playlists = self.query_one("#spotify-playlists", DataTable)
        playlists.cursor_type = "row"
        playlists.zebra_stripes = True
        playlists.add_columns("Use", "Playlist", "Included", "Excluded", "Pending")

        spotify_tracks = self.query_one("#spotify-tracks", DataTable)
        spotify_tracks.cursor_type = "row"
        spotify_tracks.zebra_stripes = True
        spotify_tracks.add_columns("State", "Artist", "Title", "Album")

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
        self.query_one("#busy-label", Static).update(message)

    def _show_indeterminate_status(self, message: str) -> None:
        progress = self.query_one("#busy-progress", ProgressBar)
        progress.display = False
        self.query_one("#busy-indicator", LoadingIndicator).display = self._operation_running
        self._set_status(message)

    def _show_resolution_progress(
        self,
        source: str,
        current: int,
        total: int,
        label: str,
    ) -> None:
        self.query_one("#busy-indicator", LoadingIndicator).display = False
        progress = self.query_one("#busy-progress", ProgressBar)
        progress.display = True
        progress.update(total=total, progress=current)
        self._set_status(
            f"{source.title()} resolution {current}/{total} · {label} · Esc cancels"
        )

    def _set_busy(self, label: str) -> bool:
        if self._operation_running:
            self.notify("Another Synctify action is already running.", severity="warning")
            return False
        self._operation_running = True
        self._cancel_event.clear()
        progress = self.query_one("#busy-progress", ProgressBar)
        progress.update(total=100, progress=0)
        progress.display = False
        self.query_one("#busy-indicator", LoadingIndicator).display = True
        self.query_one("#cancel-action", Button).disabled = False
        for button in self.query(Button):
            if button.id != "cancel-action":
                button.disabled = True
        self._set_status(f"{label}…  Esc cancels")
        return True

    def _clear_busy(self, message: str) -> None:
        self._operation_running = False
        self.query_one("#busy-indicator", LoadingIndicator).display = False
        progress = self.query_one("#busy-progress", ProgressBar)
        progress.display = False
        progress.update(total=100, progress=0)
        self.query_one("#cancel-action", Button).disabled = True
        for button in self.query(Button):
            if button.id != "cancel-action":
                button.disabled = False
        self._set_status(message)

    def action_cancel_current(self) -> None:
        if not self._operation_running:
            return
        self._cancel_event.set()
        self.query_one("#cancel-action", Button).disabled = True
        self._show_indeterminate_status("Cancelling current action…")

    def _render_dashboard(self, state: DashboardState) -> None:
        if not state.initialized:
            body = (
                "[b]Synctify is not initialized.[/b]\n\n"
                f"Home: {self.settings.home}\n"
                "Run `synctify setup` or fetch Spotify playlists before using stateful actions."
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
                    f"Imported playlists: {state.playlists}",
                    f"Sync/backup targets: {state.targets}",
                    "",
                    "[b]Spotify[/b]",
                    f"Last catalog/tracked refresh: {state.last_pull or 'never fetched'}",
                    "",
                    f"Library: {state.library_dir}",
                ]
            )
        self.query_one("#dashboard-summary", Static).update(body)

    def _refresh_spotify(self) -> None:
        table = self.query_one("#spotify-playlists", DataTable)
        table.clear()
        playlists = read_spotify_playlists(self.settings)
        self._playlist_cache = {item.spotify_id: item for item in playlists}
        for item in playlists:
            pending = item.pending_add + item.pending_remove
            marker = "☑" if item.tracked else "☐"
            if not item.available:
                marker += " !"
            table.add_row(
                marker,
                item.name,
                str(item.included),
                str(item.excluded),
                str(pending),
                key=item.spotify_id,
            )
        if self._selected_playlist_id not in self._playlist_cache:
            self._selected_playlist_id = None
            self._selected_playlist_item_key = None
            self.query_one("#spotify-tracks", DataTable).clear()
            self.query_one("#spotify-sidebar", Static).update(
                f"{len(playlists)} Spotify playlist(s) cached. Select one to review."
            )
        elif self._selected_playlist_id is not None:
            self._render_spotify_detail(self._selected_playlist_id)

    def _render_spotify_detail(self, playlist_id: str) -> None:
        item = self._playlist_cache.get(playlist_id)
        if item is None:
            return
        tracks = read_spotify_playlist_items(self.settings, playlist_id)
        table = self.query_one("#spotify-tracks", DataTable)
        table.clear()
        state_labels = {
            "included": "☑ included",
            "excluded": "☐ excluded",
            "pending_add": "? new",
            "pending_remove": "? removed",
        }
        for track in tracks:
            table.add_row(
                state_labels.get(track.state, track.state),
                track.artist,
                track.title,
                track.album or "",
                key=track.item_key,
            )
        self._selected_playlist_item_key = None
        total = item.track_count if item.track_count is not None else len(tracks)
        self.query_one("#spotify-sidebar", Static).update(
            "\n".join(
                [
                    f"[b]{item.name}[/b]",
                    f"Status: {'Imported' if item.tracked else 'Available'}",
                    f"Spotify tracks: {total}",
                    f"Included: {item.included}",
                    f"Excluded: {item.excluded}",
                    f"Pending additions: {item.pending_add}",
                    f"Pending removals: {item.pending_remove}",
                    "Pending changes do not enter the download queue until you confirm/apply them.",
                ]
            )
        )

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
            if not self._settings_explicit:
                self.settings = Settings.default()
            self._render_dashboard(read_dashboard(self.settings))
            self._refresh_spotify()
            self._refresh_unresolved()
        except Exception as exc:
            if not self._operation_running:
                self._set_status(f"Refresh failed: {exc}")

    def action_refresh(self) -> None:
        self.refresh_core()
        if not self._operation_running:
            self._set_status("Refreshed local state")

    def action_show_tab(self, tab: str) -> None:
        self.query_one("#tabs", TabbedContent).active = tab

    def action_start_update(self) -> None:
        self._start_command(("update",), "Library update", show_commands=False)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        key = event.row_key.value
        if event.data_table.id == "unresolved-table":
            self._selected_spotify_id = key
            if key is not None:
                self.query_one("#selected-track", Static).update(
                    f"Selected Spotify track: {key}"
                )
            return
        if event.data_table.id == "spotify-playlists":
            self._selected_playlist_id = key
            if key is not None:
                self._render_spotify_detail(key)
            return
        if event.data_table.id == "spotify-tracks":
            self._selected_playlist_item_key = key

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
            set_manual_resolution(self.settings, spotify_id, provider, provider_track_id)
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            self.notify(str(exc), severity="error", timeout=5)
            self._set_status(f"Mapping failed: {exc}")
            return
        self.query_one("#provider-track-id-input", Input).value = ""
        self.notify(f"Mapped {spotify_id} to {provider}:{provider_track_id}")
        self.refresh_core()

    def _selected_playlist(self) -> PlaylistCatalogEntry | None:
        if self._selected_playlist_id is None:
            self.notify("Select a Spotify playlist first.", severity="warning")
            return None
        item = self._playlist_cache.get(self._selected_playlist_id)
        if item is None:
            self.notify("Refresh the Spotify playlist list first.", severity="warning")
            return None
        return item

    def _choose_selected_track(self, included: bool) -> None:
        playlist = self._selected_playlist()
        key = self._selected_playlist_item_key
        if playlist is None:
            return
        if key is None:
            self.notify("Select a track first.", severity="warning")
            return
        try:
            choose_spotify_item(self.settings, playlist.spotify_id, key, included)
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            self.notify(str(exc), severity="error")
            return
        self._refresh_spotify()
        self._render_spotify_detail(playlist.spotify_id)

    def _confirm_selected_playlist(self) -> None:
        playlist = self._selected_playlist()
        if playlist is None:
            return
        try:
            confirm_spotify_playlist(self.settings, playlist.spotify_id)
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            self.notify(str(exc), severity="error", timeout=5)
            return
        self.notify(f"Imported {playlist.name}. Configure the next playlist when ready.")
        self._refresh_spotify()
        self.refresh_core()

    def _apply_selected_playlist(self) -> None:
        playlist = self._selected_playlist()
        if playlist is None:
            return
        try:
            apply_spotify_playlist(self.settings, playlist.spotify_id)
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            self.notify(str(exc), severity="error", timeout=5)
            return
        self.notify(f"Applied reviewed choices for {playlist.name}.")
        self.refresh_core()

    def _unimport_selected_playlist(self) -> None:
        playlist = self._selected_playlist()
        if playlist is None:
            return
        if not playlist.tracked:
            self.notify("That playlist is not currently imported.", severity="warning")
            return
        try:
            unimport_spotify_playlist(self.settings, playlist.spotify_id)
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            self.notify(str(exc), severity="error", timeout=5)
            return
        self.notify(
            f"Unimported {playlist.name}. Unreferenced local FLACs were removed; cloud backups were not touched."
        )
        self.refresh_core()

    def _progress_from_thread(self, message: str) -> None:
        self.call_from_thread(
            self._show_indeterminate_status,
            f"{message}  Esc cancels",
        )

    @work(thread=True, exclusive=True, group="spotify", exit_on_error=False)
    def fetch_spotify_catalog_worker(self) -> None:
        try:
            count = fetch_spotify_playlists(
                self.settings,
                cancelled=self._cancel_event.is_set,
                progress=self._progress_from_thread,
            )
        except Exception as exc:
            self.call_from_thread(self._operation_failed, "Spotify playlist fetch", exc)
            return
        self.call_from_thread(self._operation_complete, f"Fetched {count} Spotify playlist(s)")

    @work(thread=True, exclusive=True, group="spotify", exit_on_error=False)
    def fetch_spotify_items_worker(self, playlist_id: str, name: str) -> None:
        try:
            count = fetch_spotify_playlist_items(
                self.settings,
                playlist_id,
                cancelled=self._cancel_event.is_set,
                progress=self._progress_from_thread,
            )
        except Exception as exc:
            self.call_from_thread(self._operation_failed, f"Load {name}", exc)
            return
        self.call_from_thread(self._operation_complete, f"Loaded {count} track(s) from {name}")

    @work(thread=True, exclusive=True, group="spotify", exit_on_error=False)
    def update_tracked_worker(self) -> None:
        try:
            count = fetch_tracked_spotify_updates(
                self.settings,
                cancelled=self._cancel_event.is_set,
                progress=self._progress_from_thread,
            )
        except Exception as exc:
            self.call_from_thread(self._operation_failed, "Tracked playlist update", exc)
            return
        self.call_from_thread(self._operation_complete, f"Checked {count} tracked playlist(s)")

    def _operation_failed(self, label: str, exc: Exception) -> None:
        cancelled = isinstance(exc, SpotifySelectionCancelled) or self._cancel_event.is_set()
        self.refresh_core()
        if cancelled:
            self._clear_busy(f"{label} cancelled")
            self.notify(f"{label} cancelled")
        else:
            self._clear_busy(f"{label} failed")
            self.notify(str(exc), severity="error", timeout=6)

    def _operation_complete(self, message: str) -> None:
        self.refresh_core()
        self._clear_busy(message)
        self.notify(message)

    def _append_command_output(self, line: str) -> None:
        self.query_one("#command-log", RichLog).write(line)
        progress_match = _UPDATE_PROGRESS_RE.match(line)
        if progress_match is not None and self._operation_running:
            self._show_resolution_progress(
                progress_match.group("source"),
                int(progress_match.group("current")),
                int(progress_match.group("total")),
                progress_match.group("label"),
            )
            return
        if line.startswith("[update] ") and self._operation_running:
            self._show_indeterminate_status(f"{line[len('[update] '):]}  Esc cancels")

    def _run_custom_command(self) -> None:
        raw = self.query_one("#command-input", Input).value.strip()
        if not raw:
            self.notify("Enter a Synctify command first.", severity="warning")
            return
        try:
            args = tuple(shlex.split(raw))
        except ValueError as exc:
            self.notify(f"Invalid command: {exc}", severity="error")
            return
        self._start_command(args, "Command")

    def _start_command(
        self,
        args: tuple[str, ...],
        label: str,
        *,
        show_commands: bool = True,
    ) -> None:
        if not args:
            self.notify("Command arguments are required.", severity="warning")
            return
        if args[0] == "tui":
            self.notify("A nested TUI cannot be launched from the TUI.", severity="warning")
            return
        if not self._set_busy(label):
            return
        if show_commands:
            self.action_show_tab("commands")
        log = self.query_one("#command-log", RichLog)
        log.clear()
        log.write(f"$ synctify {shlex.join(args)}")
        self.run_command_worker(args, label)

    @work(thread=True, exclusive=True, group="command", exit_on_error=False)
    def run_command_worker(self, args: tuple[str, ...], label: str) -> None:
        try:
            result = run_cli_command(
                self.settings,
                args,
                on_output=lambda line: self.call_from_thread(self._append_command_output, line),
                cancelled=self._cancel_event.is_set,
            )
        except Exception as exc:
            self.call_from_thread(self._command_failed, label, exc)
            return
        self.call_from_thread(self._command_finished, label, result)

    def _command_failed(self, label: str, exc: Exception) -> None:
        self._append_command_output(f"{label} failed: {exc}")
        self.refresh_core()
        self._clear_busy(f"{label} failed")
        self.notify(str(exc), severity="error", timeout=6)

    def _command_finished(self, label: str, result: CommandResult) -> None:
        if result.cancelled:
            self._append_command_output(f"{label} cancelled.")
            self.refresh_core()
            self._clear_busy(f"{label} cancelled")
            return
        if result.returncode == 0:
            self._append_command_output(f"{label} complete.")
            message = f"{label} complete"
        else:
            self._append_command_output(f"{label} exited with status {result.returncode}.")
            message = f"{label} failed with status {result.returncode}"
        self.refresh_core()
        self._clear_busy(message)
        if result.returncode == 0:
            self.notify(message)
        else:
            self.notify(message, severity="error", timeout=6)

    @work(thread=True, exclusive=True, group="doctor", exit_on_error=False)
    def run_doctor_worker(self) -> None:
        try:
            report = read_doctor_report(self.settings)
        except Exception as exc:
            self.call_from_thread(self._doctor_failed, exc)
            return
        self.call_from_thread(self._render_doctor, report)

    def _doctor_failed(self, exc: Exception) -> None:
        self._clear_busy(f"Doctor failed: {exc}")
        self.notify(str(exc), severity="error", timeout=5)

    def _render_doctor(self, report: DoctorReport) -> None:
        table = self.query_one("#doctor-table", DataTable)
        table.clear()
        for index, check in enumerate(report.checks):
            table.add_row(check.status.value, check.section, check.name, check.message, key=f"doctor-{index}")
        self.query_one("#doctor-summary", Static).update(
            f"[b]{report.passed} passed[/b] · {report.warnings} warning(s) · {report.failures} failure(s)"
        )
        self._clear_busy("Doctor complete")

    @work(thread=True, exclusive=True, group="audit", exit_on_error=False)
    def run_audit_worker(self) -> None:
        try:
            report = read_audit_report(self.settings)
        except Exception as exc:
            self.call_from_thread(self._audit_failed, exc)
            return
        self.call_from_thread(self._render_audit, report)

    def _audit_failed(self, exc: Exception) -> None:
        self._clear_busy(f"Audit failed: {exc}")
        self.notify(str(exc), severity="error", timeout=5)

    def _render_audit(self, report: LibraryAuditReport) -> None:
        table = self.query_one("#audit-table", DataTable)
        table.clear()
        for index, issue in enumerate(report.issues):
            table.add_row(issue.kind.value, issue.artist, issue.title, str(issue.path), key=f"issue-{index}")
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
        self._clear_busy("Audit complete")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "command-input":
            self._run_custom_command()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "cancel-action":
            self.action_cancel_current()
        elif button_id in {"refresh-dashboard", "refresh-resolver"}:
            self.action_refresh()
        elif button_id == "open-spotify":
            self.action_show_tab("spotify")
        elif button_id == "save-mapping":
            self._save_mapping()
        elif button_id in {"run-update", "command-update"}:
            self.action_start_update()
        elif button_id in {"spotify-fetch", "command-fetch-playlists"}:
            if self._set_busy("Fetching Spotify playlists"):
                self.action_show_tab("spotify")
                self.fetch_spotify_catalog_worker()
        elif button_id in {"spotify-update-tracked", "command-update-tracked"}:
            if self._set_busy("Checking tracked Spotify playlists"):
                self.action_show_tab("spotify")
                self.update_tracked_worker()
        elif button_id == "spotify-load":
            playlist = self._selected_playlist()
            if playlist is not None and self._set_busy(f"Loading {playlist.name}"):
                self.fetch_spotify_items_worker(playlist.spotify_id, playlist.name)
        elif button_id == "spotify-confirm":
            self._confirm_selected_playlist()
        elif button_id == "spotify-apply":
            self._apply_selected_playlist()
        elif button_id == "spotify-unimport":
            self._unimport_selected_playlist()
        elif button_id == "spotify-include-track":
            self._choose_selected_track(True)
        elif button_id == "spotify-exclude-track":
            self._choose_selected_track(False)
        elif button_id == "command-resolve":
            self._start_command(("resolve", "auto"), "Automatic resolution")
        elif button_id == "command-playlists":
            self._start_command(("playlists", "build"), "Playlist build")
        elif button_id == "command-targets":
            self._start_command(("targets", "list"), "Target listing")
        elif button_id == "run-command":
            self._run_custom_command()
        elif button_id in {"open-doctor", "run-doctor"}:
            if self._set_busy("Running doctor"):
                self.action_show_tab("doctor")
                self.run_doctor_worker()
        elif button_id in {"open-audit", "run-audit"}:
            if self._set_busy("Running read-only audit"):
                self.action_show_tab("audit")
                self.run_audit_worker()


def run_tui(settings: Settings | None = None) -> None:
    """Launch the Synctify terminal UI."""
    SynctifyTUI(settings=settings).run()


__all__ = ["SynctifyTUI", "run_tui"]
