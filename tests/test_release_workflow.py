from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_release.py"


def _release_module():
    spec = importlib.util.spec_from_file_location("synctify_verify_release", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_current_stable_release_metadata_is_self_consistent() -> None:
    release = _release_module()

    version, notes = release.verify_release(ROOT, "v1.0.5")

    assert version == "1.0.5"
    assert "Self-update" in notes
    assert "Update now?" in notes


def test_mismatched_release_tag_is_rejected() -> None:
    release = _release_module()

    with pytest.raises(release.ReleaseValidationError, match="does not match pyproject"):
        release.verify_release(ROOT, "v1.0.6")


def test_non_stable_release_tag_is_rejected() -> None:
    release = _release_module()

    with pytest.raises(release.ReleaseValidationError, match="stable vX.Y.Z"):
        release.verify_release(ROOT, "1.0.5")


def test_release_workflow_builds_and_publishes_verified_artifacts() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert '      - "v*.*.*"' in workflow
    assert "contents: write" in workflow
    assert 'git merge-base --is-ancestor "$GITHUB_SHA" origin/main' in workflow
    assert 'python scripts/verify_release.py "$GITHUB_REF_NAME" --notes-out release-notes.md' in workflow
    assert "python -m build" in workflow
    assert "python -m twine check dist/*" in workflow
    assert ".venv-release/bin/synctify --help" in workflow
    assert ".venv-release/bin/synctify tui --help" in workflow
    assert ".venv-release/bin/synctify self-update --help" in workflow
    assert "python scripts/build_distribution.py --dist-dir dist" in workflow
    assert 'BUNDLE="synctify-${VERSION}-macos"' in workflow
    assert '"dist/${BUNDLE}.zip"' in workflow
    assert '".dist-release/${BUNDLE}/synctify.sh" --help' in workflow
    assert '".dist-release/${BUNDLE}/synctify.sh" install' in workflow
    assert '".dist-home/.local/bin/synctify" --help' in workflow
    assert '".dist-home/.local/bin/synctify" self-update --help' in workflow
    assert '"dist/synctify-${VERSION}.tar.gz"' in workflow
    assert 'gh release create "$GITHUB_REF_NAME" \\' in workflow
    assert 'gh release create "$GITHUB_REF_NAME" dist/*' not in workflow
    assert "SHA256SUMS" not in workflow
    assert '"dist/synctify-${VERSION}-py3-none-any.whl"' not in workflow
    assert "--notes-file release-notes.md" in workflow
