from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
from zipfile import ZipFile

import pytest


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
            prefix + "install.sh",
            prefix + "README.txt",
            prefix + "VERSION",
            prefix + wheel_name,
        }
        assert archive.read(prefix + "VERSION") == f"{version}\n".encode()
        assert archive.read(prefix + wheel_name) == wheel_bytes
        launcher_info = archive.getinfo(prefix + "synctify.sh")
        installer_info = archive.getinfo(prefix + "install.sh")
        assert (launcher_info.external_attr >> 16) & 0o111 == 0o111
        assert (installer_info.external_attr >> 16) & 0o111 == 0o111


def test_launcher_bootstraps_private_environment_and_defaults_to_tui() -> None:
    launcher = (ROOT / "distribution" / "macos" / "synctify.sh").read_text(
        encoding="utf-8"
    )

    assert "Python 3.12 or newer is required" in launcher
    assert "SYNCTIFY_PYTHON" in launcher
    assert 'VENV_DIR="$ROOT_DIR/.venv"' in launcher
    assert 'INSTALLER="$ROOT_DIR/install.sh"' in launcher
    assert '"${1:-}" == "install"' in launcher
    assert 'install --upgrade "$WHEEL"' in launcher
    assert 'exec "$VENV_DIR/bin/synctify" tui' in launcher
    assert 'exec "$VENV_DIR/bin/synctify" "$@"' in launcher


def test_installer_creates_stable_command_and_versioned_app_layout(tmp_path: Path) -> None:
    if sys.platform != "darwin":
        pytest.skip("macOS installer test")

    version = "9.9.9"
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    shutil.copy2(ROOT / "distribution" / "macos" / "install.sh", bundle / "install.sh")
    (bundle / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    (bundle / "README.txt").write_text("test bundle\n", encoding="utf-8")
    (bundle / f"synctify-{version}-py3-none-any.whl").write_bytes(b"wheel")
    (bundle / "synctify.sh").write_text(
        '#!/bin/bash\nprintf "launcher:%s\\n" "$*"\n',
        encoding="utf-8",
    )
    os.chmod(bundle / "install.sh", 0o755)
    os.chmod(bundle / "synctify.sh", 0o755)

    home = tmp_path / "home"
    profile = home / ".zprofile"
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "SHELL": "/bin/zsh",
            "SYNCTIFY_PROFILE": str(profile),
        }
    )

    result = subprocess.run(
        [str(bundle / "install.sh")],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    app_root = home / "Library" / "Application Support" / "Synctify" / "app"
    release = app_root / "releases" / version
    current = app_root / "current"
    command = home / ".local" / "bin" / "synctify"

    assert release.is_dir()
    assert (release / "install.sh").is_file()
    assert (release / "synctify.sh").is_file()
    assert current.is_symlink()
    assert os.readlink(current) == f"releases/{version}"
    assert command.is_file()
    assert os.access(command, os.X_OK)
    assert 'export PATH="$HOME/.local/bin:$PATH"' in profile.read_text(encoding="utf-8")

    launched = subprocess.run(
        [str(command), "doctor", "--example"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert launched.returncode == 0, launched.stderr
    assert launched.stdout == "launcher:doctor --example\n"


def test_bundle_readme_explains_state_and_external_tool_boundaries() -> None:
    text = (ROOT / "distribution" / "macos" / "README.txt").read_text(
        encoding="utf-8"
    )

    assert "./synctify.sh" in text
    assert "./synctify.sh install" in text
    assert "~/.local/bin/synctify" in text
    assert "Python 3.12 or newer" in text
    assert "not bundled with Synctify" in text
    assert "database, configuration, canonical music library, and playlists are NOT stored" in text
