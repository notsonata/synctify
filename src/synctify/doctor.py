from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
from typing import Callable

from .backup import validate_rclone_backup_target
from .config import Settings
from .db import SCHEMA_VERSION
from .spotify.auth import KeyringTokenStore, OAuthToken, SpotifyAuthError, SpotifyOAuthConfig
from .sync import SyncMode, SyncTarget


class CheckStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(slots=True, frozen=True)
class DoctorCheck:
    section: str
    name: str
    status: CheckStatus
    message: str


@dataclass(slots=True, frozen=True)
class DoctorReport:
    checks: tuple[DoctorCheck, ...]

    @property
    def passed(self) -> int:
        return sum(check.status is CheckStatus.PASS for check in self.checks)

    @property
    def warnings(self) -> int:
        return sum(check.status is CheckStatus.WARN for check in self.checks)

    @property
    def failures(self) -> int:
        return sum(check.status is CheckStatus.FAIL for check in self.checks)

    @property
    def ok(self) -> bool:
        return self.failures == 0


Which = Callable[[str], str | None]
Runner = Callable[..., subprocess.CompletedProcess[str]]
TokenLoader = Callable[[str], OAuthToken | None]


def _check_directory(name: str, path: Path) -> DoctorCheck:
    if not path.exists():
        return DoctorCheck("paths", name, CheckStatus.WARN, f"missing: {path}")
    if not path.is_dir():
        return DoctorCheck("paths", name, CheckStatus.FAIL, f"not a directory: {path}")
    if not os.access(path, os.R_OK | os.W_OK | os.X_OK):
        return DoctorCheck(
            "paths",
            name,
            CheckStatus.FAIL,
            f"directory is not readable/writable: {path}",
        )
    return DoctorCheck("paths", name, CheckStatus.PASS, str(path))


def _read_database(path: Path) -> tuple[list[DoctorCheck], tuple[SyncTarget, ...]]:
    checks: list[DoctorCheck] = []
    if not path.exists():
        checks.append(
            DoctorCheck(
                "database",
                "SQLite",
                CheckStatus.WARN,
                f"database not initialized: {path}",
            )
        )
        return checks, ()
    if not path.is_file():
        checks.append(
            DoctorCheck("database", "SQLite", CheckStatus.FAIL, f"not a file: {path}")
        )
        return checks, ()

    try:
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
    except (OSError, sqlite3.DatabaseError) as exc:
        checks.append(DoctorCheck("database", "SQLite", CheckStatus.FAIL, str(exc)))
        return checks, ()

    targets: tuple[SyncTarget, ...] = ()
    schema_is_older = False
    schema_is_current = False
    try:
        integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
        integrity = [str(row[0]) for row in integrity_rows]
        if integrity != ["ok"]:
            checks.append(
                DoctorCheck(
                    "database",
                    "integrity",
                    CheckStatus.FAIL,
                    "; ".join(integrity[:3]) or "integrity check failed",
                )
            )
        else:
            checks.append(DoctorCheck("database", "integrity", CheckStatus.PASS, "ok"))

        row = connection.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            checks.append(
                DoctorCheck(
                    "database",
                    "schema",
                    CheckStatus.FAIL,
                    "schema_version metadata is missing",
                )
            )
        else:
            actual = str(row["value"])
            if actual == SCHEMA_VERSION:
                schema_is_current = True
                checks.append(
                    DoctorCheck(
                        "database",
                        "schema",
                        CheckStatus.PASS,
                        f"version {actual}",
                    )
                )
            else:
                try:
                    actual_number = int(actual)
                    expected_number = int(SCHEMA_VERSION)
                except ValueError:
                    actual_number = expected_number = -1
                if 0 <= actual_number < expected_number:
                    schema_is_older = True
                    status = CheckStatus.WARN
                    message = (
                        f"version {actual}; current is {SCHEMA_VERSION}. "
                        "Run `synctify init` to apply migrations."
                    )
                else:
                    status = CheckStatus.FAIL
                    message = f"version {actual}; this Synctify expects {SCHEMA_VERSION}"
                checks.append(DoctorCheck("database", "schema", status, message))

        table_rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        tables = {str(row["name"]) for row in table_rows}
        core_required = {
            "metadata",
            "tracks",
            "playlists",
            "playlist_tracks",
            "track_resolutions",
            "sync_targets",
            "sync_runs",
            "playlist_backup_snapshots",
        }
        missing_core = sorted(core_required - tables)
        if missing_core:
            checks.append(
                DoctorCheck(
                    "database",
                    "tables",
                    CheckStatus.FAIL,
                    f"missing required table(s): {', '.join(missing_core)}",
                )
            )
        elif "generated_playlists" not in tables:
            if schema_is_older:
                checks.append(
                    DoctorCheck(
                        "database",
                        "tables",
                        CheckStatus.WARN,
                        "generated_playlists migration is pending; run `synctify init`",
                    )
                )
            else:
                checks.append(
                    DoctorCheck(
                        "database",
                        "tables",
                        CheckStatus.FAIL,
                        "current schema is missing generated_playlists",
                    )
                )
        else:
            message = "schema v5 tables present" if schema_is_current else "core tables present"
            checks.append(DoctorCheck("database", "tables", CheckStatus.PASS, message))

        if "sync_targets" in tables:
            rows = connection.execute(
                "SELECT id, name, kind, destination, mode FROM sync_targets ORDER BY name COLLATE NOCASE"
            ).fetchall()
            parsed: list[SyncTarget] = []
            for item in rows:
                try:
                    mode = SyncMode(item["mode"])
                except ValueError:
                    checks.append(
                        DoctorCheck(
                            "targets",
                            str(item["name"]),
                            CheckStatus.FAIL,
                            f"invalid sync mode: {item['mode']!r}",
                        )
                    )
                    continue
                parsed.append(
                    SyncTarget(
                        name=item["name"],
                        destination=item["destination"],
                        mode=mode,
                        kind=item["kind"],
                        id=item["id"],
                    )
                )
            targets = tuple(parsed)
    except sqlite3.DatabaseError as exc:
        checks.append(DoctorCheck("database", "SQLite", CheckStatus.FAIL, str(exc)))
    finally:
        connection.close()
    return checks, targets


def _check_spotify(
    settings: Settings,
    token_loader: TokenLoader,
) -> list[DoctorCheck]:
    try:
        config = SpotifyOAuthConfig.load(settings.spotify_config_path)
    except SpotifyAuthError as exc:
        status = CheckStatus.WARN if not settings.spotify_config_path.exists() else CheckStatus.FAIL
        return [DoctorCheck("spotify", "configuration", status, str(exc))]

    checks = [
        DoctorCheck(
            "spotify",
            "configuration",
            CheckStatus.PASS,
            f"client {config.client_id[:6]}...; redirect {config.redirect_uri}",
        )
    ]
    try:
        token = token_loader(config.client_id)
    except SpotifyAuthError as exc:
        checks.append(DoctorCheck("spotify", "keychain", CheckStatus.FAIL, str(exc)))
        return checks
    except Exception as exc:
        checks.append(
            DoctorCheck("spotify", "keychain", CheckStatus.FAIL, f"could not read token: {exc}")
        )
        return checks

    if token is None:
        checks.append(
            DoctorCheck(
                "spotify",
                "keychain",
                CheckStatus.WARN,
                "no stored token; run `synctify spotify login`",
            )
        )
    elif token.expires_at <= time.time():
        checks.append(
            DoctorCheck(
                "spotify",
                "keychain",
                CheckStatus.PASS,
                "refresh token present; access token will refresh on next use",
            )
        )
    else:
        checks.append(
            DoctorCheck("spotify", "keychain", CheckStatus.PASS, "stored token present")
        )
    return checks


def _check_executable(
    name: str,
    executable: str,
    which: Which,
) -> tuple[DoctorCheck, str | None]:
    resolved = which(executable)
    if resolved is None:
        return (
            DoctorCheck(
                "tools",
                name,
                CheckStatus.WARN,
                f"{executable!r} not found",
            ),
            None,
        )
    return DoctorCheck("tools", name, CheckStatus.PASS, resolved), resolved


def _list_rclone_remotes(
    executable: str,
    runner: Runner,
) -> tuple[set[str] | None, DoctorCheck]:
    try:
        result = runner(
            [executable, "listremotes"],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        return None, DoctorCheck("tools", "rclone remotes", CheckStatus.WARN, str(exc))
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "rclone listremotes failed").strip()
        return None, DoctorCheck("tools", "rclone remotes", CheckStatus.WARN, message)
    remotes = {
        line.strip().rstrip(":")
        for line in (result.stdout or "").splitlines()
        if line.strip()
    }
    return remotes, DoctorCheck(
        "tools",
        "rclone remotes",
        CheckStatus.PASS,
        f"{len(remotes)} configured",
    )


def _target_checks(
    targets: tuple[SyncTarget, ...],
    settings: Settings,
    remotes: set[str] | None,
) -> list[DoctorCheck]:
    if not targets:
        return [DoctorCheck("targets", "configured targets", CheckStatus.PASS, "none")]

    checks: list[DoctorCheck] = []
    home = settings.home.expanduser().resolve()
    for target in targets:
        if target.kind == "filesystem" and target.mode is SyncMode.MIRROR:
            destination = Path(target.destination).expanduser()
            if not destination.exists():
                checks.append(
                    DoctorCheck(
                        "targets",
                        target.name,
                        CheckStatus.WARN,
                        f"mirror destination is not mounted or does not exist: {destination}",
                    )
                )
                continue
            if not destination.is_dir():
                checks.append(
                    DoctorCheck(
                        "targets",
                        target.name,
                        CheckStatus.FAIL,
                        f"mirror destination is not a directory: {destination}",
                    )
                )
                continue
            try:
                resolved = destination.resolve()
            except OSError as exc:
                checks.append(DoctorCheck("targets", target.name, CheckStatus.FAIL, str(exc)))
                continue
            if (
                resolved == home
                or resolved.is_relative_to(home)
                or home.is_relative_to(resolved)
            ):
                checks.append(
                    DoctorCheck(
                        "targets",
                        target.name,
                        CheckStatus.FAIL,
                        "mirror destination overlaps Synctify application data",
                    )
                )
                continue
            checks.append(
                DoctorCheck("targets", target.name, CheckStatus.PASS, f"mirror: {resolved}")
            )
            continue

        if target.kind == "rclone" and target.mode is SyncMode.BACKUP:
            try:
                validate_rclone_backup_target(target)
            except ValueError as exc:
                checks.append(DoctorCheck("targets", target.name, CheckStatus.FAIL, str(exc)))
                continue
            remote = target.destination.partition(":")[0].strip()
            if remotes is None:
                checks.append(
                    DoctorCheck(
                        "targets",
                        target.name,
                        CheckStatus.WARN,
                        f"backup syntax is valid but remote {remote!r} was not verified",
                    )
                )
            elif remote not in remotes:
                checks.append(
                    DoctorCheck(
                        "targets",
                        target.name,
                        CheckStatus.WARN,
                        f"rclone remote {remote!r} is not configured",
                    )
                )
            else:
                checks.append(
                    DoctorCheck(
                        "targets",
                        target.name,
                        CheckStatus.PASS,
                        f"backup: {target.destination}",
                    )
                )
            continue

        checks.append(
            DoctorCheck(
                "targets",
                target.name,
                CheckStatus.FAIL,
                f"unsupported target kind/mode: {target.kind}/{target.mode.value}",
            )
        )
    return checks


def run_doctor(
    settings: Settings,
    *,
    qobuz_dl: str = "qobuz-dl",
    streamrip: str = "rip",
    rclone: str = "rclone",
    which: Which = shutil.which,
    runner: Runner = subprocess.run,
    token_loader: TokenLoader | None = None,
) -> DoctorReport:
    """Inspect local Synctify health without mutating configuration, state, or files."""
    checks: list[DoctorCheck] = [
        _check_directory("home", settings.home),
        _check_directory("library", settings.library_dir),
        _check_directory("playlists", settings.playlists_dir),
    ]

    database_checks, targets = _read_database(settings.database_path)
    checks.extend(database_checks)
    checks.extend(
        _check_spotify(
            settings,
            token_loader or KeyringTokenStore().load,
        )
    )

    qobuz_check, _ = _check_executable("qobuz-dl", qobuz_dl, which)
    streamrip_check, _ = _check_executable("Streamrip", streamrip, which)
    rclone_check, rclone_path = _check_executable("rclone", rclone, which)
    checks.extend((qobuz_check, streamrip_check, rclone_check))

    remotes: set[str] | None = None
    if rclone_path is not None:
        remotes, remote_check = _list_rclone_remotes(rclone_path, runner)
        checks.append(remote_check)
    checks.extend(_target_checks(targets, settings, remotes))
    return DoctorReport(tuple(checks))


def format_doctor_report(report: DoctorReport) -> str:
    lines = ["Synctify doctor"]
    current_section: str | None = None
    for check in report.checks:
        if check.section != current_section:
            current_section = check.section
            lines.append(f"\n{current_section.title()}")
        lines.append(f"  {check.status.value:<4} {check.name}: {check.message}")
    lines.extend(
        [
            "",
            f"Summary: {report.passed} passed, {report.warnings} warning(s), {report.failures} failure(s)",
        ]
    )
    return "\n".join(lines)
