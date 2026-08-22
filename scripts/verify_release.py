from __future__ import annotations

import argparse
from pathlib import Path
import re
import tomllib


ROOT = Path(__file__).resolve().parents[1]
_STABLE_TAG = re.compile(r"^v(?P<version>(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))$")
_RUNTIME_VERSION = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)


class ReleaseValidationError(ValueError):
    pass


def _project_version(root: Path) -> str:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _runtime_version(root: Path) -> str:
    text = (root / "src" / "synctify" / "__init__.py").read_text(encoding="utf-8")
    match = _RUNTIME_VERSION.search(text)
    if match is None:
        raise ReleaseValidationError("could not find synctify.__version__")
    return match.group(1)


def _changelog_notes(root: Path, version: str) -> str:
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    heading = re.search(
        rf"^## {re.escape(version)}(?:\s+-\s+.+)?$",
        text,
        flags=re.MULTILINE,
    )
    if heading is None:
        raise ReleaseValidationError(f"CHANGELOG.md has no section for {version}")
    next_heading = re.search(r"^## ", text[heading.end() :], flags=re.MULTILINE)
    end = heading.end() + next_heading.start() if next_heading else len(text)
    notes = text[heading.end() : end].strip()
    if not notes:
        raise ReleaseValidationError(f"CHANGELOG.md section for {version} is empty")
    return notes + "\n"


def verify_release(root: Path, tag: str) -> tuple[str, str]:
    match = _STABLE_TAG.fullmatch(tag.strip())
    if match is None:
        raise ReleaseValidationError("release tag must use stable vX.Y.Z form")
    version = match.group("version")

    project_version = _project_version(root)
    if project_version != version:
        raise ReleaseValidationError(
            f"tag {tag} does not match pyproject version {project_version}"
        )

    runtime_version = _runtime_version(root)
    if runtime_version != version:
        raise ReleaseValidationError(
            f"runtime version {runtime_version} does not match tag {tag}"
        )

    readme = (root / "README.md").read_text(encoding="utf-8")
    if f"**Current version: {version}**" not in readme:
        raise ReleaseValidationError(f"README.md does not declare version {version}")

    notes = _changelog_notes(root, version)
    return version, notes


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Synctify release metadata.")
    parser.add_argument("tag", help="Git tag in stable vX.Y.Z form")
    parser.add_argument(
        "--notes-out",
        type=Path,
        help="Write the matching CHANGELOG section body to this file.",
    )
    args = parser.parse_args()

    try:
        version, notes = verify_release(ROOT, args.tag)
    except (OSError, KeyError, tomllib.TOMLDecodeError, ReleaseValidationError) as exc:
        parser.error(str(exc))

    if args.notes_out is not None:
        args.notes_out.write_text(notes, encoding="utf-8")
    print(f"release metadata verified for {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
