from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .audit import LibraryAuditReport, audit_library
from .config import Settings
from .db import connect, initialize
from .doctor import DoctorReport, run_doctor
from .migration_checkpoint import (
    STAGE_AUDIT,
    STAGE_DOCTOR,
    STAGE_LIBRARY_RELINK,
    STAGE_PLAYLISTS,
    STAGE_PORTABLE_IMPORT,
    STAGE_SPOTIFY_LOGIN,
    STAGE_SPOTIFY_REFRESH,
    MigrationCheckpoint,
    MigrationCheckpointError,
    checkpoint_path,
    load_checkpoint,
    mark_stage_completed,
    migration_identity,
    new_checkpoint,
    record_stage_error,
    refresh_complete,
    save_checkpoint,
)
from .playlists import PlaylistBuildReport, build_playlists
from .portable import ImportPlan, PortableBundle, apply_import, plan_import, read_portable_bundle
from .relink import RelinkReport, relink_library
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
    resume: bool = False
    restart: bool = False


@dataclass(slots=True, frozen=True)
class MigrationReport:
    import_plan: ImportPlan
    spotify_plan: ChangePlan | None = None
    relink: RelinkReport | None = None
    playlists: PlaylistBuildReport | None = None
    audit: LibraryAuditReport | None = None
    doctor: DoctorReport | None = None
    applied: bool = False
    checkpoint: MigrationCheckpoint | None = None
    checkpoint_file: Path | None = None
    resumed_stages: tuple[str, ...] = ()

    @property
    def operational_failures(self) -> int:
        relink_failures = len(self.relink.failures) if self.relink is not None else 0
        audit_failures = len(self.audit.failures) if self.audit is not None else 0
        doctor_failures = self.doctor.failures if self.doctor is not None else 0
        resumed_failures = (
            self.checkpoint.completed_failures(self.resumed_stages)
            if self.checkpoint is not None
            else 0
        )
        return relink_failures + audit_failures + doctor_failures + resumed_failures


@dataclass(slots=True, frozen=True)
class _MigrationPreflight:
    bundle: PortableBundle
    import_plan: ImportPlan
    library_source: Path | None
    checkpoint: MigrationCheckpoint | None
    checkpoint_file: Path


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


def _preflight(settings: Settings, options: MigrationOptions) -> _MigrationPreflight:
    if options.resume and options.restart:
        raise MigrationError("--resume and --restart cannot be used together")
    if options.relink_limit is not None and options.relink_limit < 1:
        raise MigrationError("relink limit must be at least 1")

    try:
        bundle = read_portable_bundle(options.portable_file)
        import_plan = plan_import(settings, bundle)
        source = _validate_library_source(options.library_source)
        identity = migration_identity(
            options.portable_file,
            library_source=source,
            relink_limit=options.relink_limit,
            allow_partial=options.allow_partial,
        )
        path = checkpoint_path(settings.home)
        existing = load_checkpoint(path)
    except (OSError, ValueError, MigrationCheckpointError) as exc:
        raise MigrationError(str(exc)) from exc

    if options.resume:
        if existing is None:
            raise MigrationError(f"no migration checkpoint exists at {path}; start with --apply first")
        if existing.fingerprint != identity.fingerprint:
            raise MigrationError(
                "migration checkpoint does not match these inputs; use the original portable file/library/options "
                "or start over with --restart"
            )
        checkpoint = existing
    elif options.restart:
        checkpoint = new_checkpoint(identity)
    else:
        checkpoint = existing

    return _MigrationPreflight(bundle, import_plan, source, checkpoint, path)


def preview_migration(settings: Settings, options: MigrationOptions) -> MigrationReport:
    """Validate migration inputs/checkpoint state without changing local state."""
    preflight = _preflight(settings, options)
    resumed = (
        preflight.checkpoint.completed_stages()
        if options.resume and preflight.checkpoint is not None
        else ()
    )
    return MigrationReport(
        import_plan=preflight.import_plan,
        applied=False,
        checkpoint=preflight.checkpoint,
        checkpoint_file=preflight.checkpoint_file,
        resumed_stages=resumed,
    )


def _prepare_apply_checkpoint(
    preflight: _MigrationPreflight,
    options: MigrationOptions,
) -> MigrationCheckpoint:
    existing = preflight.checkpoint
    try:
        identity = migration_identity(
            options.portable_file,
            library_source=preflight.library_source,
            relink_limit=options.relink_limit,
            allow_partial=options.allow_partial,
        )
    except MigrationCheckpointError as exc:
        raise MigrationError(str(exc)) from exc

    if options.resume:
        assert existing is not None
        return existing
    if options.restart:
        checkpoint = new_checkpoint(identity)
        save_checkpoint(preflight.checkpoint_file, checkpoint)
        return checkpoint
    if existing is not None:
        if existing.fingerprint != identity.fingerprint:
            raise MigrationError(
                f"a migration checkpoint for different inputs exists at {preflight.checkpoint_file}; "
                "use --restart to replace it"
            )
        if existing.complete:
            raise MigrationError(
                f"this migration checkpoint is already complete at {preflight.checkpoint_file}; "
                "use --resume to inspect it or --restart to run the migration again"
            )
        raise MigrationError(
            f"an incomplete migration checkpoint exists at {preflight.checkpoint_file}; "
            "use --resume to continue it or --restart to start over"
        )

    checkpoint = new_checkpoint(identity)
    try:
        save_checkpoint(preflight.checkpoint_file, checkpoint)
    except MigrationCheckpointError as exc:
        raise MigrationError(str(exc)) from exc
    return checkpoint


def _save_checkpoint(
    path: Path,
    checkpoint: MigrationCheckpoint,
    options: MigrationOptions,
) -> None:
    refresh_complete(
        checkpoint,
        has_library=options.library_source is not None,
        spotify_login=options.spotify_login or checkpoint.is_completed(STAGE_SPOTIFY_LOGIN),
    )
    try:
        save_checkpoint(path, checkpoint)
    except MigrationCheckpointError as exc:
        raise MigrationError(str(exc)) from exc


def _record_error(
    path: Path,
    checkpoint: MigrationCheckpoint,
    options: MigrationOptions,
    stage: str,
    exc: Exception,
) -> None:
    record_stage_error(checkpoint, stage, str(exc))
    _save_checkpoint(path, checkpoint, options)


def _resumed(checkpoint: MigrationCheckpoint, stage: str, resumed: list[str]) -> bool:
    if not checkpoint.is_completed(stage):
        return False
    resumed.append(stage)
    return True


def run_migration(
    settings: Settings,
    options: MigrationOptions,
    *,
    spotify_login_fn: SpotifyLogin = _default_spotify_login,
    fetch_snapshot_fn: SnapshotFetcher = _default_fetch_snapshot,
    doctor_fn: DoctorRunner = _default_doctor,
) -> MigrationReport:
    """Apply or resume portable setup, desired state, relink, rebuild, and diagnostics."""
    preflight = _preflight(settings, options)
    checkpoint = _prepare_apply_checkpoint(preflight, options)
    resumed: list[str] = []

    import_plan = preflight.import_plan
    if not _resumed(checkpoint, STAGE_PORTABLE_IMPORT, resumed):
        try:
            import_plan = apply_import(settings, preflight.bundle)
            mark_stage_completed(
                checkpoint,
                STAGE_PORTABLE_IMPORT,
                summary={
                    "config_keys": len(import_plan.config_keys),
                    "spotify_action": import_plan.spotify_action,
                    "targets": len(import_plan.targets),
                },
            )
            _save_checkpoint(preflight.checkpoint_file, checkpoint, options)
        except Exception as exc:
            _record_error(
                preflight.checkpoint_file,
                checkpoint,
                options,
                STAGE_PORTABLE_IMPORT,
                exc,
            )
            raise MigrationError(f"portable import stage failed: {exc}") from exc

    if options.spotify_login:
        if not _resumed(checkpoint, STAGE_SPOTIFY_LOGIN, resumed):
            try:
                spotify_login_fn(settings)
                mark_stage_completed(
                    checkpoint,
                    STAGE_SPOTIFY_LOGIN,
                    summary={"status": "authenticated"},
                )
                _save_checkpoint(preflight.checkpoint_file, checkpoint, options)
            except Exception as exc:
                _record_error(
                    preflight.checkpoint_file,
                    checkpoint,
                    options,
                    STAGE_SPOTIFY_LOGIN,
                    exc,
                )
                raise MigrationError(f"Spotify login stage failed: {exc}") from exc

    spotify_plan: ChangePlan | None = None
    if not _resumed(checkpoint, STAGE_SPOTIFY_REFRESH, resumed):
        try:
            snapshot = fetch_snapshot_fn(settings)
            settings.ensure_directories()
            initialize(settings.database_path)
            with connect(settings.database_path) as connection:
                spotify_plan = plan_snapshot(connection, snapshot)
                apply_snapshot(connection, snapshot)
            mark_stage_completed(
                checkpoint,
                STAGE_SPOTIFY_REFRESH,
                summary={
                    "tracks_added": spotify_plan.tracks_added,
                    "tracks_removed": spotify_plan.tracks_removed,
                    "playlists_added": len(spotify_plan.playlists_added),
                    "playlists_removed": len(spotify_plan.playlists_removed),
                },
            )
            _save_checkpoint(preflight.checkpoint_file, checkpoint, options)
        except Exception as exc:
            _record_error(
                preflight.checkpoint_file,
                checkpoint,
                options,
                STAGE_SPOTIFY_REFRESH,
                exc,
            )
            raise MigrationError(f"Spotify migration stage failed: {exc}") from exc
    elif not settings.database_path.exists():
        raise MigrationError(
            "checkpoint says Spotify desired state was restored, but the local database is missing; use --restart"
        )

    relink_report: RelinkReport | None = None
    if preflight.library_source is not None:
        if not _resumed(checkpoint, STAGE_LIBRARY_RELINK, resumed):
            try:
                with connect(settings.database_path) as connection:
                    relink_report = relink_library(
                        connection,
                        preflight.library_source,
                        settings.library_dir,
                        apply=True,
                        limit=options.relink_limit,
                    )
                mark_stage_completed(
                    checkpoint,
                    STAGE_LIBRARY_RELINK,
                    operational_failures=len(relink_report.failures),
                    summary={
                        "safe_matches": len(relink_report.matches),
                        "copied": relink_report.copied,
                        "adopted": relink_report.adopted,
                        "reused": relink_report.reused,
                        "unmatched": len(relink_report.unmatched),
                        "failures": len(relink_report.failures),
                    },
                )
                _save_checkpoint(preflight.checkpoint_file, checkpoint, options)
            except Exception as exc:
                _record_error(
                    preflight.checkpoint_file,
                    checkpoint,
                    options,
                    STAGE_LIBRARY_RELINK,
                    exc,
                )
                raise MigrationError(f"library relink stage failed: {exc}") from exc

    playlists_report: PlaylistBuildReport | None = None
    if not _resumed(checkpoint, STAGE_PLAYLISTS, resumed):
        try:
            with connect(settings.database_path) as connection:
                playlists_report = build_playlists(
                    connection,
                    settings.playlists_dir,
                    allow_partial=options.allow_partial,
                )
            mark_stage_completed(
                checkpoint,
                STAGE_PLAYLISTS,
                summary={
                    "written": playlists_report.written,
                    "incomplete": playlists_report.incomplete,
                },
            )
            _save_checkpoint(preflight.checkpoint_file, checkpoint, options)
        except Exception as exc:
            _record_error(
                preflight.checkpoint_file,
                checkpoint,
                options,
                STAGE_PLAYLISTS,
                exc,
            )
            raise MigrationError(f"playlist rebuild stage failed: {exc}") from exc

    audit_report: LibraryAuditReport | None = None
    if not _resumed(checkpoint, STAGE_AUDIT, resumed):
        try:
            with connect(settings.database_path) as connection:
                audit_report = audit_library(
                    connection,
                    settings.library_dir,
                    repair=False,
                )
            if audit_report.failures:
                record_stage_error(
                    checkpoint,
                    STAGE_AUDIT,
                    f"audit reported {len(audit_report.failures)} operational failure(s)",
                )
            else:
                mark_stage_completed(
                    checkpoint,
                    STAGE_AUDIT,
                    summary={
                        "issues": len(audit_report.issues),
                        "untracked_flacs": len(audit_report.untracked_files),
                        "failures": 0,
                    },
                )
            _save_checkpoint(preflight.checkpoint_file, checkpoint, options)
        except Exception as exc:
            _record_error(preflight.checkpoint_file, checkpoint, options, STAGE_AUDIT, exc)
            raise MigrationError(f"library audit stage failed: {exc}") from exc

    doctor_report: DoctorReport | None = None
    if not _resumed(checkpoint, STAGE_DOCTOR, resumed):
        try:
            doctor_report = doctor_fn(settings)
            if doctor_report.failures:
                record_stage_error(
                    checkpoint,
                    STAGE_DOCTOR,
                    f"doctor reported {doctor_report.failures} failure(s)",
                )
            else:
                mark_stage_completed(
                    checkpoint,
                    STAGE_DOCTOR,
                    summary={
                        "passed": doctor_report.passed,
                        "warnings": doctor_report.warnings,
                        "failures": 0,
                    },
                )
            _save_checkpoint(preflight.checkpoint_file, checkpoint, options)
        except Exception as exc:
            _record_error(preflight.checkpoint_file, checkpoint, options, STAGE_DOCTOR, exc)
            raise MigrationError(f"doctor stage failed: {exc}") from exc

    _save_checkpoint(preflight.checkpoint_file, checkpoint, options)
    return MigrationReport(
        import_plan=import_plan,
        spotify_plan=spotify_plan,
        relink=relink_report,
        playlists=playlists_report,
        audit=audit_report,
        doctor=doctor_report,
        applied=True,
        checkpoint=checkpoint,
        checkpoint_file=preflight.checkpoint_file,
        resumed_stages=tuple(resumed),
    )


def _checkpoint_lines(report: MigrationReport) -> list[str]:
    checkpoint = report.checkpoint
    if checkpoint is None or report.checkpoint_file is None:
        return []
    completed = checkpoint.completed_stages()
    lines = [
        "",
        "Checkpoint",
        f"  File: {report.checkpoint_file}",
        f"  Complete: {'yes' if checkpoint.complete else 'no'}",
        "  Completed stages: " + (", ".join(completed) if completed else "none"),
    ]
    if report.resumed_stages:
        lines.append("  Resumed/skipped: " + ", ".join(report.resumed_stages))
    if checkpoint.last_error_stage is not None:
        lines.append(
            f"  Last error: {checkpoint.last_error_stage}: {checkpoint.last_error or 'unknown error'}"
        )
    return lines


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
    lines.extend(_checkpoint_lines(report))

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
            ]
        )
        if report.resumed_stages:
            lines.append("  Completed checkpoint stages above will be skipped on --apply --resume.")
        lines.extend(["", "No local state was changed. Re-run with --apply to migrate."])
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
                f"  Safe matches: {len(report.relink.matches)}",
                f"  Copied: {report.relink.copied}",
                f"  Adopted: {report.relink.adopted}",
                f"  Reused: {report.relink.reused}",
                f"  Unmatched: {len(report.relink.unmatched)}",
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
