from __future__ import annotations

from pathlib import Path
import re
import tomllib

from synctify import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_project_package_and_readme_versions_match() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project_version = project["project"]["version"]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    match = re.search(r"\*\*Current version: ([^*]+)\*\*", readme)

    assert match is not None, "README must contain a Current version line"
    assert __version__ == project_version
    assert match.group(1).strip() == project_version
