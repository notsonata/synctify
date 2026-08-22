from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import sqlite3
from typing import Callable

from .backup import add_rclone_backup_target, validate_rclone_backup_target
from .config import Settings
from .db import connect, initialize
from .spotify.auth import DEFAULT_REDIRECT_URI, SpotifyOAuthConfig, resolve_config
from .sync import SyncMode, SyncTarget, add_filesystem_target
from .user_config import UserConfig, load_user_config, set_user_config

Which = Callable[[str], str | None]


class SetupError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class SetupOptions:
    source_priority: str | None = None
    qobuz_dl: str | None = None
    streamrip: str | None = None
    rclone: str | None = None
    qobuz_quality: int | None = None
    streamrip_qobuz_quality: int | None = None
    streamrip_tidal_quality: int | None = None
    streamrip_deezer_quality: int | None = None
    streamrip_soundcloud_quality: int | None = None
    spotify_client_id: str | None = None
    spotify_redirect_uri: str | None = None
    mirror_name: str | None = None
    mirror_destination: Path | None = None
    backup_name: str | None = None
    backup_destination: str | None = None


@dataclass(slots=True, frozen=True)
class SetupToolCheck:
    name: str
    executable: str
    available: bool
    resolved_path: str | None


@dataclass(slots=True, frozen=True)
class SetupTargetResult:
    name: str
    kind: str
    destination: str
    created: bool


@dataclass(slots=True, frozen=True)
class SetupReport:
    settings: Settings
    config: UserConfig
    spotify_configured: bool
    tools: tuple[SetupToolCheck, ...]
    targets: tuple[SetupTargetResult, ...]

    @property
    def missing_tools(self) -> int:
        return sum(not item.available for item in self.tools)


def _apply_config(settings: Settings, options: SetupOptions) -> UserConfig:
    values: tuple[tuple[str, object | None], ...] = (
        ("source_priority", options.source_priority),
        ("qobuz_dl", options.qobuz_dl),
        ("streamrip", options.streamrip),
        ("rclone", options.rclone),
        ("qobuz_quality", options.qobuz_quality),
        ("streamrip_qobuz_quality", options.streamrip_qobuz_quality),
        ("streamrip_tidal_quality", options.streamrip_tidal_quality),
        ("streamrip_deezer_quality", options.streamrip_deezer_quality),
        ("streamrip_soundcloud_quality", options.streamrip_soundcloud_quality),
    )
    for key, value in values:
        if value is not None:
            set_user_config(settings.home, key, value)
    return load_user_config(settings.home)


def _configure_spotify(settings: Settings, options: SetupOptions) -> bool:
    if options.spotify_client_id is None and options.spotify_redirect_uri is None:
        return settings.spotify_config_path.exists()
    resolve_config(
        settings.spotify_config_path,
        client_id=options.spotify_client_id,
        redirect_uri=options.spotify_redirect_uri,
    )
    return True


def _existing_target(connection: sqlite3.Connection, name: str) -> SyncTarget | None:
    row = connection.execute(
        "SELECT id, name, kind, destination, mode FROM sync_targets WHERE name = ?",
        (name,),
    ).fetchone()
    if row is None:
        return None
    try:
        mode = SyncMode(row["mode"])
    except ValueError as exc:
        raise SetupError(f"target {name!r} has an invalid stored mode: {row['mode']!r}") from exc
    return SyncTarget(
        name=row["name"],
        destination=row["destination"],
        mode=mode,
        kind=row["kind"],
        id=row["id"],
    )


def _validate_target_options(options: SetupOptions) -> None:
    if (options.mirror_name is None) != (options.mirror_destination is None):
        raise SetupError("mirror target requires both name and destination")
    if (options.backup_name is None) != (options.backup_destination is None):
        raise SetupError("backup target requires both name and destination")

    if options.mirror_name is not None:
        if not options.mirror_name.strip():
            raise SetupError("mirror target name cannot be empty")

    if options.backup_name is not None and options.backup_destination is not None:
        cleaned_name = options.backup_name.strip()
        if not cleaned_name:
            raise SetupError("backup target name cannot be empty")
        candidate = SyncTarget(
            cleaned_name,
            options.backup_destination.strip().rstrip("/"),
            SyncMode.BACKUP,
            kind="rclone",
        )
        try:
            validate_rclone_backup_target(candidate)
        except ValueError as exc:
            raise SetupError(str(exc)) from exc


def _ensure_mirror_target(
    connection: sqlite3.Connection,
    name: str,
    destination: Path,
) -> SetupTargetResult:
    cleaned = name.strip()
    if not cleaned:
        raise SetupError("mirror target name cannot be empty")
    requested = str(destination.expanduser().resolve())
    existing = _existing_target(connection, cleaned)
    if existing is not None:
        if (
            existing.kind == "filesystem"
            and existing.mode is SyncMode.MIRROR
            and Path(existing.destination).expanduser().resolve()
            == Path(requested).expanduser().resolve()
        ):
            return SetupTargetResult(cleaned, "mirror", requested, False)
        raise SetupError(
            f"target {cleaned!r} already exists with different settings; "
            "remove it explicitly before replacing it"
        )
    try:
        target = add_filesystem_target(connection, cleaned, destination)
    except ValueError as exc:
        raise SetupError(str(exc)) from exc
    return SetupTargetResult(target.name, "mirror", target.destination, True)


def _ensure_backup_target(
    connection: sqlite3.Connection,
    name: str,
    destination: str,
) -> SetupTargetResult:
    cleaned = name.strip()
    if not cleaned:
        raise SetupError("backup target name cannot be empty")
    requested = destination.strip().rstrip("/")
    existing = _existing_target(connection, cleaned)
    if existing is not None:
        if (
            existing.kind == "rclone"
            and existing.mode is SyncMode.BACKUP
            and existing.destination.rstrip("/") == requested
        ):
            return SetupTargetResult(cleaned, "backup", requested, False)
        raise SetupError(
            f"target {cleaned!r} already exists with different settings; "
            "remove it explicitly before replacing it"
        )
    try:
        target = add_rclone_backup_target(connection, cleaned, requested)
    except ValueError as exc:
        raise SetupError(str(exc)) from exc
    return SetupTargetResult(target.name, "backup", target.destination, True)


def _configure_targets(
    settings: Settings,
    options: SetupOptions,
) -> tuple[SetupTargetResult, ...]:
    _validate_target_options(options)

    results: list[SetupTargetResult] = []
    with connect(settings.database_path) as connection:
        if options.mirror_name is not None and options.mirror_destination is not None:
            results.append(
                _ensure_mirror_target(
                    connection,
                    options.mirror_name,
                    options.mirror_destination,
                )
            )
        if options.backup_name is not None and options.backup_destination is not None:
            results.append(
                _ensure_backup_target(
                    connection,
                    options.backup_name,
                    options.backup_destination,
                )
            )
    return tuple(results)


def _tool_checks(config: UserConfig, which: Which) -> tuple[SetupToolCheck, ...]:
    values = (
        ("qobuz-dl", config.qobuz_dl),
        ("Streamrip", config.streamrip),
        ("rclone", config.rclone),
    )
    checks: list[SetupToolCheck] = []
    for name, executable in values:
        resolved = which(executable)
        checks.append(
            SetupToolCheck(
                name=name,
                executable=executable,
                available=resolved is not None,
                resolved_path=resolved,
            )
        )
    return tuple(checks)


def run_setup(
    settings: Settings,
    options: SetupOptions,
    *,
    which: Which = shutil.which,
) -> SetupReport:
    """Initialize Synctify and persist first-run configuration without network access."""
    # Validate target syntax before creating the home directory, database, or config.
    _validate_target_options(options)
    settings.ensure_directories()
    initialize(settings.database_path)
    config = _apply_config(settings, options)
    spotify_configured = _configure_spotify(settings, options)
    targets = _configure_targets(settings, options)
    tools = _tool_checks(config, which)
    return SetupReport(
        settings=settings,
        config=config,
        spotify_configured=spotify_configured,
        tools=tools,
        targets=targets,
    )


def load_spotify_config(settings: Settings) -> SpotifyOAuthConfig | None:
    if not settings.spotify_config_path.exists():
        return None
    return SpotifyOAuthConfig.load(settings.spotify_config_path)


def format_setup_report(report: SetupReport) -> str:
    lines = [
        "Synctify setup",
        f"  Home:      {report.settings.home}",
        f"  Library:   {report.settings.library_dir}",
        f"  Playlists: {report.settings.playlists_dir}",
        f"  Database:  {report.settings.database_path}",
        f"  Spotify:   {'configured' if report.spotify_configured else 'not configured'}",
        f"  Sources:   {','.join(report.config.source_priority)}",
        "",
        "Tools",
    ]
    for item in report.tools:
        if item.available:
            lines.append(f"  PASS {item.name}: {item.resolved_path}")
        else:
            lines.append(f"  WARN {item.name}: {item.executable!r} not found")
    if report.targets:
        lines.append("")
        lines.append("Targets")
        for target in report.targets:
            state = "created" if target.created else "already configured"
            lines.append(
                f"  {target.kind} {target.name}: {target.destination} ({state})"
            )
    lines.append("")
    lines.append(
        "Setup complete. Run `synctify doctor` for a full read-only health check."
    )
    return "\n".join(lines)
