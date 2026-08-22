from __future__ import annotations

import importlib.util
from pathlib import Path
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_distribution.py"


def _distribution_module():
    spec = importlib.util.spec_from_file_location("synctify_build_distribution", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_distribution_zip_contains_launcher_wheel_and_instructions(tmp_path: Path) -> None:
    distribution = _distribution_module()
    version = distribution.project_version(ROOT)
    wheel_name = f"synctify-{version}-py3-none-any.whl"
    wheel_bytes = b"fake-wheel-for-layout-test"
    (tmp_path / wheel_name).write_bytes(wheel_bytes)

    output = distribution.build_distribution(ROOT, tmp_path)

    assert output.name == f"synctify-{version}-macos.zip"
    prefix = f"synctify-{version}-macos/"
    with ZipFile(output) as archive:
        assert set(archive.namelist()) == {
            prefix + "synctify.sh",
            prefix + "README.txt",
            prefix + "VERSION",
            prefix + wheel_name,
        }
        assert archive.read(prefix + "VERSION") == f"{version}\n".encode()
        assert archive.read(prefix + wheel_name) == wheel_bytes
        launcher_info = archive.getinfo(prefix + "synctify.sh")
        assert (launcher_info.external_attr >> 16) & 0o111 == 0o111


def test_launcher_bootstraps_private_environment_and_defaults_to_tui() -> None:
    launcher = (ROOT / "distribution" / "macos" / "synctify.sh").read_text(
        encoding="utf-8"
    )

    assert "Python 3.12 or newer is required" in launcher
    assert "SYNCTIFY_PYTHON" in launcher
    assert 'VENV_DIR="$ROOT_DIR/.venv"' in launcher
    assert 'install --upgrade "$WHEEL"' in launcher
    assert 'exec "$VENV_DIR/bin/synctify" tui' in launcher
    assert 'exec "$VENV_DIR/bin/synctify" "$@"' in launcher


def test_bundle_readme_explains_state_and_external_tool_boundaries() -> None:
    text = (ROOT / "distribution" / "macos" / "README.txt").read_text(
        encoding="utf-8"
    )

    assert "./synctify.sh" in text
    assert "Python 3.12 or newer" in text
    assert "not bundled with Synctify" in text
    assert "database, configuration, canonical music library, and playlists are NOT stored" in text
