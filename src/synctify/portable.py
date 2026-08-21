from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any

from . import __version__
from .config import Settings
from .db import connect, initialize
from .setup import SetupError, _ensure_backup_target, _ensure_mirror_target
from .spotify.auth import SpotifyOAuthConfig
from .sync import SyncMode, SyncTarget
from .user_config import (
    UserConfigError,
    _read_overrides,
    _write_overrides,
    config_path,
    validate_value,
)

FORMAT_NAME = "synctify-portable"
FORMAT_VERSION = 1


class PortableStateError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class PortableTarget:
    name: str
    kind: str
    destination: str
    mode: str


@dataclass(slots=True, frozen=True)
class PortableBundle:
    config: dict[str, object]
    spotify: dict[str, str] | None
    targets: tuple[PortableTarget, ...]


@dataclass(slots=True, frozen=True)
class ImportTargetPlan:
    target: PortableTarget
    action: str


@dataclass(slots=True, frozen=True)
class ImportPlan:
    config_keys: tuple[str, ...]
    spotify_action: str
    targets: tuple[ImportTargetPlan, ...]
    apply: bool = False


_ALLOWED_TOP_LEVEL = frozenset(
    {"format", "format_version", "synctify_version", "config", "spotify", "targets"}
)


def _read_targets(path: Path) -> tuple[PortableTarget, ...]:
    if not path.exists():
        return ()
    if not path.is_file():
        raise PortableStateError(f"database path is not a file: {path}")
    try:
        connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
    except sqlite3.DatabaseError as exc:
        raise PortableStateError(f"could not open Synctify database: {exc}") from exc
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sync_targets'"
        ).fetchone()
        if table is None:
            return ()
        rows = connection.execute(
            "SELECT name, kind, destination, mode FROM sync_targets ORDER BY name COLLATE NOCASE"
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        raise PortableStateError(f"could not read sync targets: {exc}") from exc
    finally:
        connection.close()
    return tuple(
        PortableTarget(
            name=str(row["name"]),
            kind=str(row["kind"]),
            destination=str(row["destination"]),
            mode=str(row["mode"]),
        )
        for row in rows
    )


def build_portable_bundle(settings: Settings) -> PortableBundle:
    """Collect portable setup metadata without keychain tokens or library state."""
    try:
        overrides = _read_overrides(config_path(settings.home))
    except UserConfigError as exc:
        raise PortableStateError(str(exc)) from exc

    spotify: dict[str, str] | None = None
    if settings.spotify_config_path.exists():
        try:
            config = SpotifyOAuthConfig.load(settings.spotify_config_path)
        except Exception as exc:
            raise PortableStateError(str(exc)) from exc
        spotify = {
            "client_id": config.client_id,
            "redirect_uri": config.redirect_uri,
        }

    return PortableBundle(
        config=overrides,
        spotify=spotify,
        targets=_read_targets(settings.database_path),
    )


def bundle_to_dict(bundle: PortableBundle) -> dict[str, object]:
    return {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "synctify_version": __version__,
        "config": {
            key: list(value) if isinstance(value, tuple) else value
            for key, value in sorted(bundle.config.items())
        },
        "spotify": bundle.spotify,
        "targets": [
            {
                "name": target.name,
                "kind": target.kind,
                "destination": target.destination,
                "mode": target.mode,
            }
            for target in bundle.targets
        ],
    }


def write_portable_bundle(
    settings: Settings,
    destination: Path,
    *,
    overwrite: bool = False,
) -> PortableBundle:
    path = destination.expanduser()
    if path.exists() and not overwrite:
        raise PortableStateError(f"export destination already exists: {path}; use --force to replace it")
    if path.exists() and not path.is_file():
        raise PortableStateError(f"export destination is not a file: {path}")
    bundle = build_portable_bundle(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(bundle_to_dict(bundle), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise PortableStateError(f"could not write export: {exc}") from exc
    return bundle


def _parse_config(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise PortableStateError("portable config must be an object")
    parsed: dict[str, object] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise PortableStateError("portable config keys must be strings")
        normalized = key.strip().lower().replace("-", "_")
        try:
            parsed[normalized] = validate_value(normalized, value)
        except UserConfigError as exc:
            raise PortableStateError(str(exc)) from exc
    return parsed


def _parse_spotify(raw: object) -> dict[str, str] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != {"client_id", "redirect_uri"}:
        raise PortableStateError("spotify must contain exactly client_id and redirect_uri")
    client_id = raw.get("client_id")
    redirect_uri = raw.get("redirect_uri")
    if not isinstance(client_id, str) or not client_id.strip():
        raise PortableStateError("spotify client_id cannot be empty")
    if not isinstance(redirect_uri, str) or not redirect_uri.strip():
        raise PortableStateError("spotify redirect_uri cannot be empty")
    candidate = SpotifyOAuthConfig(client_id.strip(), redirect_uri.strip())
    # Reuse save/load validation semantics without writing anything.
    if not candidate.redirect_uri.startswith("http://127.0.0.1:"):
        raise PortableStateError("Spotify redirect URI must use the 127.0.0.1 loopback host")
    return {"client_id": candidate.client_id, "redirect_uri": candidate.redirect_uri}


def _parse_target(raw: object) -> PortableTarget:
    if not isinstance(raw, dict):
        raise PortableStateError("each portable target must be an object")
    if set(raw) != {"name", "kind", "destination", "mode"}:
        raise PortableStateError("target must contain exactly name, kind, destination, and mode")
    if not all(isinstance(raw.get(key), str) for key in ("name", "kind", "destination", "mode")):
        raise PortableStateError("target fields must be strings")
    target = PortableTarget(
        name=str(raw["name"]).strip(),
        kind=str(raw["kind"]).strip(),
        destination=str(raw["destination"]).strip(),
        mode=str(raw["mode"]).strip(),
    )
    if not target.name or not target.destination:
        raise PortableStateError("target name and destination cannot be empty")
    supported = (
        (target.kind == "filesystem" and target.mode == SyncMode.MIRROR.value)
        or (target.kind == "rclone" and target.mode == SyncMode.BACKUP.value)
    )
    if not supported:
        raise PortableStateError(
            f"unsupported portable target {target.name!r}: {target.kind}/{target.mode}"
        )
    return target


def parse_portable_dict(raw: object) -> PortableBundle:
    if not isinstance(raw, dict):
        raise PortableStateError("portable state file must contain a JSON object")
    unknown = sorted(set(raw) - _ALLOWED_TOP_LEVEL)
    if unknown:
        raise PortableStateError(f"unknown portable state field(s): {', '.join(unknown)}")
    if raw.get("format") != FORMAT_NAME:
        raise PortableStateError(f"unsupported portable state format: {raw.get('format')!r}")
    if raw.get("format_version") != FORMAT_VERSION:
        raise PortableStateError(
            f"unsupported portable format version: {raw.get('format_version')!r}; expected {FORMAT_VERSION}"
        )
    targets_raw = raw.get("targets", [])
    if not isinstance(targets_raw, list):
        raise PortableStateError("targets must be a list")
    targets = tuple(_parse_target(item) for item in targets_raw)
    names = [target.name for target in targets]
    if len(set(names)) != len(names):
        raise PortableStateError("portable target names must be unique")
    return PortableBundle(
        config=_parse_config(raw.get("config", {})),
        spotify=_parse_spotify(raw.get("spotify")),
        targets=targets,
    )


def read_portable_bundle(path: Path) -> PortableBundle:
    source = path.expanduser()
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PortableStateError(f"portable state file not found: {source}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise PortableStateError(f"could not read portable state file {source}: {exc}") from exc
    return parse_portable_dict(raw)


def _existing_targets(settings: Settings) -> dict[str, SyncTarget]:
    if not settings.database_path.exists():
        return {}
    try:
        connection = sqlite3.connect(
            f"file:{settings.database_path.resolve().as_posix()}?mode=ro", uri=True
        )
        connection.row_factory = sqlite3.Row
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sync_targets'"
        ).fetchone()
        if table is None:
            return {}
        rows = connection.execute(
            "SELECT id, name, kind, destination, mode FROM sync_targets"
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        raise PortableStateError(f"could not inspect existing targets: {exc}") from exc
    finally:
        try:
            connection.close()
        except UnboundLocalError:
            pass
    result: dict[str, SyncTarget] = {}
    for row in rows:
        try:
            mode = SyncMode(str(row["mode"]))
        except ValueError as exc:
            raise PortableStateError(
                f"existing target {row['name']!r} has invalid mode {row['mode']!r}"
            ) from exc
        result[str(row["name"])] = SyncTarget(
            name=str(row["name"]),
            destination=str(row["destination"]),
            mode=mode,
            kind=str(row["kind"]),
            id=int(row["id"]),
        )
    return result


def _target_action(target: PortableTarget, existing: SyncTarget | None) -> str:
    if existing is None:
        return "create"
    same = (
        existing.kind == target.kind
        and existing.mode.value == target.mode
        and existing.destination.rstrip("/") == target.destination.rstrip("/")
    )
    if same:
        return "unchanged"
    raise PortableStateError(
        f"target {target.name!r} conflicts with existing settings; remove it explicitly before import"
    )


def plan_import(settings: Settings, bundle: PortableBundle) -> ImportPlan:
    existing_targets = _existing_targets(settings)
    target_plans = tuple(
        ImportTargetPlan(target, _target_action(target, existing_targets.get(target.name)))
        for target in bundle.targets
    )

    if bundle.spotify is None:
        spotify_action = "none"
    elif not settings.spotify_config_path.exists():
        spotify_action = "create"
    else:
        try:
            current = SpotifyOAuthConfig.load(settings.spotify_config_path)
        except Exception as exc:
            raise PortableStateError(str(exc)) from exc
        spotify_action = (
            "unchanged"
            if current.client_id == bundle.spotify["client_id"]
            and current.redirect_uri == bundle.spotify["redirect_uri"]
            else "replace"
        )

    return ImportPlan(
        config_keys=tuple(sorted(bundle.config)),
        spotify_action=spotify_action,
        targets=target_plans,
        apply=False,
    )


def apply_import(settings: Settings, bundle: PortableBundle) -> ImportPlan:
    # Preflight all conflicts before the first write.
    plan = plan_import(settings, bundle)

    settings.ensure_directories()
    initialize(settings.database_path)

    try:
        existing_overrides = _read_overrides(config_path(settings.home))
        merged = dict(existing_overrides)
        merged.update(bundle.config)
        _write_overrides(config_path(settings.home), merged)
    except UserConfigError as exc:
        raise PortableStateError(str(exc)) from exc

    if bundle.spotify is not None:
        SpotifyOAuthConfig(
            bundle.spotify["client_id"], bundle.spotify["redirect_uri"]
        ).save(settings.spotify_config_path)

    try:
        with connect(settings.database_path) as connection:
            for item in bundle.targets:
                if item.kind == "filesystem":
                    _ensure_mirror_target(connection, item.name, Path(item.destination))
                else:
                    _ensure_backup_target(connection, item.name, item.destination)
    except (SetupError, ValueError) as exc:
        raise PortableStateError(str(exc)) from exc

    return ImportPlan(
        config_keys=plan.config_keys,
        spotify_action=plan.spotify_action,
        targets=plan.targets,
        apply=True,
    )


def format_import_plan(plan: ImportPlan) -> str:
    lines = ["Synctify portable import", f"  Mode: {'apply' if plan.apply else 'preview'}"]
    lines.append(
        "  Config: " + (", ".join(plan.config_keys) if plan.config_keys else "no saved overrides")
    )
    lines.append(f"  Spotify config: {plan.spotify_action}")
    if plan.targets:
        lines.append("  Targets:")
        for item in plan.targets:
            lines.append(
                f"    {item.action}: {item.target.name} ({item.target.kind}/{item.target.mode}) -> {item.target.destination}"
            )
    else:
        lines.append("  Targets: none")
    if not plan.apply:
        lines.append("  No local state was changed. Re-run with --apply to import.")
    return "\n".join(lines)
