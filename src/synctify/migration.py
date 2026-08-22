from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .audit import LibraryAuditReport, audit_library
from .config import Settings
from .db import connect, initialize
from .doctor import DoctorReport, run_doctor
from .playlists import PlaylistBuildReport, build_playlists
from .portable import ImportPlan, PortableBundle, apply_import, plan_import, read_portable_bundle
from .relink import LibraryRelinkReport, relink_library
from .spotify.auth import (
    KeyringTokenStore,
    SpotifyAuth,
    SpotifyOAuthConfig,
    interactive_login,
)
from .spotify.client import SpotifyClient
from .spotify.ingest import SpotifySnapshot, fetch_spotify_snapshot
from .spotify.state import ChangePlan, apply_snapshot, plan_snapshot
from .user_config import (
    load_user_config,
    resolve_qobuz_dl,
    resolve_rclone,
    resolve_streamrip,
)


class MigrationError(RuntimeError):
    pass


SpotifyLogin = Callable[[Settings], None]
SnapshotFetcher = Callable[[Settings], SpotifySnapshot]
DoctorRunner = Callable[[Settings], DoctorReport]


@dataclass(slots=True, frozen=True)
class MigrationOptions:
    portable_file: Path
    library_source: Path | None = None
    spotify_login: bool = False
    allow_partial: bool = False
    relink_limit: int | None = None


@dataclass(slots=True, frozen=True)
class MigrationReport:
    import_plan: ImportPlan
    spotify_plan: ChangePlan | None = None
    relink: LibraryRelinkReport | None = None
    playlists: PlaylistBuildReport | None = None
    audit: LibraryAuditReport | None = None
    doctor: DoctorReport | None = None
    applied: bool = False

    @property
    def operational_failures(self) -> int:
        relink_failures = len(self.relink.failures) if self.relink is not None else 0
        audit_failures = len(self.audit.failures) if self.audit is not None else 0
        doctor_failures = self.doctor.failures if self.doctor is not None else 0
        return relink_failures + audit_failures + doctor_failures


def _default_spotify_login(settings: Settings) -> None:
    config = SpotifyOAuthConfig.load(settings.spotify_config_path)
    interactive_login(config, KeyringTokenStore())


def _default_fetch_snapshot(settings: Settings) -> SpotifySnapshot:
    config = SpotifyOAuthConfig.load(settings.spotify_config_path)
    auth = SpotifyAuth(config)
    with SpotifyClient(auth) as client:
        return fetch_spotify_snapshot(client)


def _default_doctor(settings: Settings) -> DoctorReport:
    config = load_user_config(settings.home)
    return run_doctor(
        settings,
        qobuz_dl=resolve_qobuz_dl(config),
        streamrip=resolve_streamrip(config),
        rclone=resolve_rclone(config),
    )


def _validate_library_source(source: Path | None) -> Path | None:
    if source is None:
        return None
    resolved = source.expanduser().resolve()
    if not resolved.exists():
        raise MigrationError(f"library source does not exist: {resolved}")
    if not resolved.is_dir():
        raise MigrationError(f"library source is not a directory: {resolved}")
    return resolved


def preview_migration(settings: Settings, options: MigrationOptions) -> MigrationReport:
    """Validate migration inputs and portable-state conflicts without changing local state."""
    try:
        bundle = read_portable_bundle(options.portable_file)
        import_plan = plan_import(settings, bundle)
    except (OSError, ValueError) as exc:
        raise MigrationError(str(exc)) from exc
    _validate_library_source(options.library_source)
    if options.relink_limit is not None and options.relink_limit < 1:
        raise MigrationError("relink limit must be at least 1")
    return MigrationReport(import_plan=import_plan, applied=False)


def run_migration(
    settings: Settings,
    options: MigrationOptions,
    *,
    spotify_login_fn: SpotifyLogin = _default_spotify_login,
    fetch_snapshot_fn: SnapshotFetcher = _default_fetch_snapshot,
    doctor_fn: DoctorRunner = _default_doctor,
) -> MigrationReport:
    """Apply portable setup, refresh desired state, relink audio, rebuild, and diagnose."""
    try:
        bundle: PortableBundle = read_portable_bundle(options.portable_file)
        # Preflight target/config compatibility before the first write.
        plan_import(settings, bundle)
        source = _validate_library_source(options.library_source)
        import_plan = apply_import(settings, bundle)
    except (OSError, ValueError) as exc:
        raise MigrationError(str(exc)) from exc

    try:
        if options.spotify_login:
            spotify_login_fn(settings)
        snapshot = fetch_snapshot_fn(settings)
    except Exception as exc:
        raise MigrationError(f"Spotify migration stage failed: {exc}") from exc

    settings.ensure_directories()
    initialize(settings.database_path)
    try:
        with connect(settings.database_path) as connection:
            spotify_plan = plan_snapshot(connection, snapshot)
            apply_snapshot(connection, snapshot)
    except Exception as exc:
        raise MigrationError(f"could not apply Spotify desired state: {exc}") from exc

    relink_report: LibraryRelinkReport | None = None
    if source is not None:
        try:
            with connect(settings.database_path) as connection:
                relink_report = relink_library(
                    connection,
                    source,
                    settings.library_dir,
                    apply=True,
                    limit=options.relink_limit,
                )
        except Exception as exc:
            raise MigrationError(f"library relink stage failed: {exc}") from exc

    try:
        with connect(settings.database_path) as connection:
            playlists_report = build_playlists(
                connection,
                settings.playlists_dir,
                allow_partial=options.allow_partial,
            )
            audit_report = audit_library(
                connection,
                settings.library_dir,
                repair=False,
            )
    except Exception as exc:
        raise MigrationError(f"post-migration library stage failed: {exc}") from exc

    try:
        doctor_report = doctor_fn(settings)
    except Exception as exc:
        raise MigrationError(f"doctor stage failed: {exc}") from exc

    return MigrationReport(
        import_plan=import_plan,
        spotify_plan=spotify_plan,
        relink=relink_report,
        playlists=playlists_report,
        audit=audit_report,
        doctor=doctor_report,
        applied=True,
    )


def format_migration_report(report: MigrationReport) -> str:
    lines = ["Synctify migration", f"  Mode: {'apply' if report.applied else 'preview'}"]
    lines.append(
        "  Portable config: "
        + (", ".join(report.import_plan.config_keys) if report.import_plan.config_keys else "no overrides")
    )
    lines.append(f"  Spotify config: {report.import_plan.spotify_action}")
    if report.import_plan.targets:
        lines.append(
            "  Targets: "
            + ", ".join(f"{item.action}:{item.target.name}" for item in report.import_plan.targets)
        )
    else:
        lines.append("  Targets: none")

    if not report.applied:
        lines.extend(
            [
                "",
                "Planned stages",
                "  1. Apply portable setup metadata",
                "  2. Optional Spotify browser login",
                "  3. Refresh Spotify desired state",
                "  4. Optional FLAC library relink/copy",
                "  5. Rebuild generated playlists",
                "  6. Run library audit",
                "  7. Run Synctify doctor",
                "",
                "No local state was changed. Re-run with --apply to migrate.",
            ]
        )
        return "\n".join(lines)

    if report.spotify_plan is not None:
        lines.extend(
            [
                "",
                "Spotify desired state",
                f"  Tracks added: {report.spotify_plan.tracks_added}",
                f"  Tracks removed: {report.spotify_plan.tracks_removed}",
                f"  Playlists added: {len(report.spotify_plan.playlists_added)}",
                f"  Playlists removed: {len(report.spotify_plan.playlists_removed)}",
            ]
        )

    if report.relink is not None:
        lines.extend(
            [
                "",
                "Library relink",
                f"  Matched: {report.relink.matched}",
                f"  Applied: {report.relink.applied_count}",
                f"  Unresolved: {report.relink.unresolved}",
                f"  Ambiguous: {report.relink.ambiguous}",
                f"  Failures: {len(report.relink.failures)}",
            ]
        )

    if report.playlists is not None:
        lines.extend(
            [
                "",
                "Playlists",
                f"  Written: {report.playlists.written}/{len(report.playlists.results)}",
                f"  Incomplete: {report.playlists.incomplete}",
            ]
        )

    if report.audit is not None:
        lines.extend(
            [
                "",
                "Audit",
                f"  Recorded issues: {len(report.audit.issues)}",
                f"  Untracked FLACs: {len(report.audit.untracked_files)}",
                f"  Failures: {len(report.audit.failures)}",
            ]
        )

    if report.doctor is not None:
        lines.extend(
            [
                "",
                "Doctor",
                f"  Passed: {report.doctor.passed}",
                f"  Warnings: {report.doctor.warnings}",
                f"  Failures: {report.doctor.failures}",
            ]
        )

    lines.extend(["", f"Operational failures: {report.operational_failures}"])
    return "\n".join(lines)
