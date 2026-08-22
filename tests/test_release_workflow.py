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

    version, notes = release.verify_release(ROOT, "v1.2.3")

    assert version == "1.2.3"
    assert "Active library scope" in notes
    assert "Local FLAC identity safety" in notes


def test_mismatched_release_tag_is_rejected() -> None:
    release = _release_module()

    with pytest.raises(release.ReleaseValidationError, match="does not match pyproject"):
        release.verify_release(ROOT, "v1.2.4")


def test_non_stable_release_tag_is_rejected() -> None:
    release = _release_module()

    with pytest.raises(release.ReleaseValidationError, match="stable vX.Y.Z"):
        release.verify_release(ROOT, "1.2.3")


def test_auto_tag_workflow_releases_successful_main_version_once() -> None:
    workflow = (ROOT / ".github" / "workflows" / "auto-tag-release.yml").read_text(
        encoding="utf-8"
    )

    assert "workflow_run:" in workflow
    assert 'workflows: ["tests"]' in workflow
    assert "types: [completed]" in workflow
    assert "workflow_dispatch:" in workflow
    assert "contents: write" in workflow
    assert "actions: write" in workflow
    assert "github.event.workflow_run.conclusion == 'success'" in workflow
    assert "github.event.workflow_run.event == 'push'" in workflow
    assert "github.event.workflow_run.head_branch == 'main'" in workflow
    assert "ref: ${{ github.event.workflow_run.head_sha }}" in workflow
    assert 'python scripts/verify_release.py "$TAG"' in workflow
    assert 'git ls-remote --exit-code --tags origin "refs/tags/$TAG"' in workflow
    assert 'git tag "$TAG" "$(git rev-parse HEAD)"' in workflow
    assert 'git push origin "refs/tags/$TAG"' in workflow
    assert 'gh workflow run release.yml --ref main --field tag="$TAG"' in workflow


def test_release_workflow_builds_and_publishes_verified_artifacts() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert '      - "v*.*.*"' in workflow
    assert "workflow_dispatch:" in workflow
    assert "Stable release tag to publish (vX.Y.Z)" in workflow
    assert "contents: write" in workflow
    assert "RELEASE_TAG:" in workflow
    assert "ref: ${{ env.RELEASE_TAG }}" in workflow
    assert 'TAG_SHA="$(git rev-parse "$RELEASE_TAG^{commit}")"' in workflow
    assert 'git merge-base --is-ancestor "$TAG_SHA" origin/main' in workflow
    assert 'python scripts/verify_release.py "$RELEASE_TAG" --notes-out release-notes.md' in workflow
    assert "python -m build" in workflow
    assert "python -m twine check dist/*" in workflow
    assert ".venv-release/bin/synctify --help" in workflow
    assert ".venv-release/bin/synctify tui --help" in workflow
    assert ".venv-release/bin/synctify upgrade --help" in workflow
    assert "self-update --help" not in workflow
    assert "python scripts/build_distribution.py --dist-dir dist" in workflow
    assert 'BUNDLE="synctify-${VERSION}-macos"' in workflow
    assert '"dist/${BUNDLE}.zip"' in workflow
    assert '".dist-release/${BUNDLE}/synctify.sh" --help' in workflow
    assert '".dist-release/${BUNDLE}/synctify.sh" install' in workflow
    assert '".dist-home/.local/bin/synctify" --help' in workflow
    assert '".dist-home/.local/bin/synctify" upgrade --help' in workflow
    assert '"dist/synctify-${VERSION}.tar.gz"' in workflow
    assert 'gh release create "$RELEASE_TAG" \\' in workflow
    assert 'gh release create "$RELEASE_TAG" dist/*' not in workflow
    assert "SHA256SUMS" not in workflow
    assert '"dist/synctify-${VERSION}-py3-none-any.whl"' not in workflow
    assert "--notes-file release-notes.md" in workflow
