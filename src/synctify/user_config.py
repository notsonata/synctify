from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path

DEFAULT_SOURCE_PRIORITY = ("qobuz", "tidal", "deezer")
_LEGACY_SOURCE_PRIORITY = (*DEFAULT_SOURCE_PRIORITY, "soundcloud")
QOBUZ_QUALITIES = frozenset({6, 7, 27})
_LEGACY_QOBUZ_QUALITIES = frozenset({5, 6, 7, 27})
STREAMRIP_QUALITIES = frozenset({0, 1, 2, 3, 4})


class UserConfigError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class UserConfig:
    source_priority: tuple[str, ...] = DEFAULT_SOURCE_PRIORITY
    qobuz_dl: str = "qobuz-dl"
    streamrip: str = "rip"
    rclone: str = "rclone"
    qobuz_quality: int = 27
    streamrip_qobuz_quality: int = 4
    streamrip_tidal_quality: int = 3
    streamrip_deezer_quality: int = 2
    # Kept so existing config/portable files remain readable. SoundCloud itself
    # is not an automatic/acquisition source for Synctify's canonical FLAC library.
    streamrip_soundcloud_quality: int = 2
    # None keeps the library relative to SYNCTIFY_HOME. Explicit values are
    # stored as absolute paths so runtime behavior never depends on cwd.
    library_dir: str | None = None

    def streamrip_quality_for(self, source: str) -> int:
        values = {
            "qobuz": self.streamrip_qobuz_quality,
            "tidal": self.streamrip_tidal_quality,
            "deezer": self.streamrip_deezer_quality,
            "soundcloud": self.streamrip_soundcloud_quality,
        }
        return values.get(source.strip().lower(), 2)

    def as_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["source_priority"] = list(self.source_priority)
        return data


_KEYS = frozenset(UserConfig.__dataclass_fields__)


def config_path(home: Path) -> Path:
    return home / "config.json"


def _normalize_priority(
    value: object,
    *,
    allow_legacy: bool = False,
) -> tuple[str, ...]:
    if isinstance(value, str):
        raw = value.split(",")
    elif isinstance(value, (list, tuple)):
        raw = list(value)
    else:
        raise UserConfigError("source_priority must be a comma-separated string or list")
    values = tuple(str(item).strip().lower() for item in raw if str(item).strip())
    if not values:
        raise UserConfigError("source_priority cannot be empty")
    allowed = _LEGACY_SOURCE_PRIORITY if allow_legacy else DEFAULT_SOURCE_PRIORITY
    unknown = [item for item in values if item not in allowed]
    if unknown:
        raise UserConfigError(f"unsupported source(s): {', '.join(unknown)}")
    if len(set(values)) != len(values):
        raise UserConfigError("source_priority cannot contain duplicates")
    return values


def _quality(value: object, allowed: frozenset[int], label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise UserConfigError(f"invalid {label}: {value!r}") from exc
    if parsed not in allowed:
        choices = ", ".join(str(item) for item in sorted(allowed))
        raise UserConfigError(f"{label} must be one of: {choices}")
    return parsed


def _library_dir(value: object) -> str:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise UserConfigError("library_dir cannot be empty")
    path = Path(str(value).strip()).expanduser()
    if not path.is_absolute():
        raise UserConfigError("library_dir must be an absolute path")
    return str(path)


def validate_value(key: str, value: object) -> object:
    """Parse stored/portable values, including legacy lossy settings."""
    if key == "source_priority":
        return _normalize_priority(value, allow_legacy=True)
    if key in {"qobuz_dl", "streamrip", "rclone"}:
        if not isinstance(value, str) or not value.strip():
            raise UserConfigError(f"{key} cannot be empty")
        return value.strip()
    if key == "library_dir":
        return _library_dir(value)
    if key == "qobuz_quality":
        return _quality(value, _LEGACY_QOBUZ_QUALITIES, key)
    if key.startswith("streamrip_") and key.endswith("_quality"):
        return _quality(value, STREAMRIP_QUALITIES, key)
    raise UserConfigError(f"unknown config key: {key}")


def _validate_runtime_value(key: str, value: object) -> object:
    if key == "source_priority":
        return _normalize_priority(value)
    if key == "qobuz_quality":
        return _quality(value, QOBUZ_QUALITIES, key)
    return validate_value(key, value)


def _read_overrides(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UserConfigError(f"invalid Synctify config at {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise UserConfigError(f"invalid Synctify config at {path}: expected an object")
    unknown = sorted(set(raw) - _KEYS)
    if unknown:
        raise UserConfigError(f"unknown config key(s): {', '.join(unknown)}")
    return {key: validate_value(key, value) for key, value in raw.items()}


def load_user_config(home: Path) -> UserConfig:
    path = config_path(home)
    overrides = _read_overrides(path)
    values = UserConfig().as_dict()
    values.update(overrides)

    # Old releases allowed SoundCloud in fallback order and Qobuz quality 5
    # (MP3 320). Keep those files readable, but make the effective runtime
    # configuration lossless without rewriting user state implicitly.
    priority = _normalize_priority(values["source_priority"], allow_legacy=True)
    values["source_priority"] = tuple(
        source for source in priority if source in DEFAULT_SOURCE_PRIORITY
    )
    if not values["source_priority"]:
        values["source_priority"] = DEFAULT_SOURCE_PRIORITY
    if values["qobuz_quality"] == 5:
        values["qobuz_quality"] = 6
    return UserConfig(**values)


def _write_overrides(path: Path, overrides: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serializable: dict[str, object] = {}
    for key in sorted(overrides):
        value = overrides[key]
        serializable[key] = list(value) if key == "source_priority" else value
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(serializable, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def set_user_config(home: Path, key: str, value: object) -> UserConfig:
    normalized = key.strip().lower().replace("-", "_")
    path = config_path(home)
    overrides = _read_overrides(path)
    overrides[normalized] = _validate_runtime_value(normalized, value)
    _write_overrides(path, overrides)
    return load_user_config(home)


def unset_user_config(home: Path, key: str) -> tuple[UserConfig, bool]:
    normalized = key.strip().lower().replace("-", "_")
    if normalized not in _KEYS:
        raise UserConfigError(f"unknown config key: {normalized}")
    path = config_path(home)
    overrides = _read_overrides(path)
    existed = normalized in overrides
    overrides.pop(normalized, None)
    if overrides:
        _write_overrides(path, overrides)
    elif path.exists():
        path.unlink()
    return load_user_config(home), existed


def format_user_config(config: UserConfig, home: Path) -> str:
    lines = [f"Config: {config_path(home)}"]
    for key, value in config.as_dict().items():
        if key == "source_priority":
            shown = ",".join(value)
        elif key == "library_dir":
            shown = value or str(home / "library")
        else:
            shown = value
        lines.append(f"  {key.replace('_', '-')}: {shown}")
    return "\n".join(lines)


def resolve_qobuz_dl(config: UserConfig, cli_value: str | None = None) -> str:
    return cli_value or os.getenv("SYNCTIFY_QOBUZ_DL") or config.qobuz_dl


def resolve_streamrip(config: UserConfig, cli_value: str | None = None) -> str:
    return cli_value or os.getenv("SYNCTIFY_STREAMRIP") or config.streamrip


def resolve_rclone(config: UserConfig, cli_value: str | None = None) -> str:
    return cli_value or os.getenv("SYNCTIFY_RCLONE") or config.rclone


def resolve_library_dir(
    config: UserConfig,
    home: Path,
    cli_value: str | Path | None = None,
    *,
    default: Path | None = None,
) -> Path:
    raw = cli_value or os.getenv("SYNCTIFY_LIBRARY_DIR") or config.library_dir
    if raw is None:
        return (default or (home / "library")).expanduser()
    return Path(_library_dir(raw))


def resolve_source_priority(config: UserConfig, cli_value: str | None = None) -> tuple[str, ...]:
    raw = cli_value or os.getenv("SYNCTIFY_SOURCES")
    return _normalize_priority(raw) if raw else config.source_priority


def resolve_qobuz_quality(config: UserConfig, cli_value: int | None = None) -> int:
    if cli_value is not None:
        return _quality(cli_value, QOBUZ_QUALITIES, "qobuz_quality")
    env = os.getenv("SYNCTIFY_QOBUZ_QUALITY")
    return _quality(env, QOBUZ_QUALITIES, "qobuz_quality") if env is not None else config.qobuz_quality


def resolve_streamrip_quality(config: UserConfig, source: str, cli_value: int | None = None) -> int:
    if cli_value is not None:
        return _quality(cli_value, STREAMRIP_QUALITIES, "streamrip quality")
    normalized = source.strip().lower()
    env = os.getenv(f"SYNCTIFY_STREAMRIP_{normalized.upper()}_QUALITY")
    if env is not None:
        return _quality(env, STREAMRIP_QUALITIES, "streamrip quality")
    return config.streamrip_quality_for(normalized)


def effective_user_config(config: UserConfig) -> UserConfig:
    """Resolve environment-overridden values using normal runtime precedence."""
    library_override = os.getenv("SYNCTIFY_LIBRARY_DIR")
    return UserConfig(
        source_priority=resolve_source_priority(config),
        qobuz_dl=resolve_qobuz_dl(config),
        streamrip=resolve_streamrip(config),
        rclone=resolve_rclone(config),
        qobuz_quality=resolve_qobuz_quality(config),
        streamrip_qobuz_quality=resolve_streamrip_quality(config, "qobuz"),
        streamrip_tidal_quality=resolve_streamrip_quality(config, "tidal"),
        streamrip_deezer_quality=resolve_streamrip_quality(config, "deezer"),
        streamrip_soundcloud_quality=resolve_streamrip_quality(config, "soundcloud"),
        library_dir=_library_dir(library_override) if library_override else config.library_dir,
    )
