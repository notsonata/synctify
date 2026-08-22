from __future__ import annotations

import argparse
from pathlib import Path
import tomllib
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


ROOT = Path(__file__).resolve().parents[1]
FIXED_TIMESTAMP = (2026, 1, 1, 0, 0, 0)


def project_version(root: Path) -> str:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _write_bytes(
    archive: ZipFile,
    name: str,
    data: bytes,
    *,
    mode: int = 0o644,
) -> None:
    info = ZipInfo(name, date_time=FIXED_TIMESTAMP)
    info.create_system = 3
    info.compress_type = ZIP_DEFLATED
    info.external_attr = (mode & 0xFFFF) << 16
    archive.writestr(info, data)


def build_distribution(root: Path, dist_dir: Path) -> Path:
    version = project_version(root)
    wheels = sorted(dist_dir.glob(f"synctify-{version}-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(
            f"expected exactly one Synctify {version} wheel in {dist_dir}, found {len(wheels)}"
        )

    launcher = root / "distribution" / "macos" / "synctify.sh"
    instructions = root / "distribution" / "macos" / "README.txt"
    if not launcher.is_file() or not instructions.is_file():
        raise RuntimeError("macOS distribution templates are missing")

    dist_dir.mkdir(parents=True, exist_ok=True)
    bundle_name = f"synctify-{version}-macos"
    output = dist_dir / f"{bundle_name}.zip"
    wheel = wheels[0]

    with ZipFile(output, "w") as archive:
        prefix = f"{bundle_name}/"
        _write_bytes(
            archive,
            prefix + "synctify.sh",
            launcher.read_bytes(),
            mode=0o755,
        )
        _write_bytes(
            archive,
            prefix + "README.txt",
            instructions.read_bytes(),
        )
        _write_bytes(
            archive,
            prefix + "VERSION",
            f"{version}\n".encode("utf-8"),
        )
        _write_bytes(
            archive,
            prefix + wheel.name,
            wheel.read_bytes(),
        )

    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Synctify macOS launcher ZIP.")
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=ROOT / "dist",
        help="Directory containing the already-built Synctify wheel.",
    )
    args = parser.parse_args()

    try:
        output = build_distribution(ROOT, args.dist_dir.resolve())
    except (OSError, KeyError, tomllib.TOMLDecodeError, RuntimeError) as exc:
        parser.error(str(exc))

    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
