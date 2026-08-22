from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
from typing import Callable
from zipfile import BadZipFile, ZipFile

import httpx


REPOSITORY = "notsonata/synctify"
API_ROOT = "https://api.github.com"
_STABLE_TAG = re.compile(r"^v(?P<version>(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))$")


class UpdateError(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class ReleaseInfo:
    version: str
    tag: str
    asset_name: str
    asset_api_url: str
    asset_size: int | None = None
    asset_digest: str | None = None


@dataclass(slots=True, frozen=True)
class AutomaticUpdateResult:
    checked: bool = False
    release: ReleaseInfo | None = None
    installed: bool = False
    error: str | None = None


def _version_tuple(value: str) -> tuple[int, int, int]:
    text = value.strip()
    match = _STABLE_TAG.fullmatch(text if text.startswith("v") else f"v{text}")
    if match is None:
        raise UpdateError(f"invalid stable Synctify version: {value!r}")
    parts = tuple(int(part) for part in match.group("version").split("."))
    return parts[0], parts[1], parts[2]


def _repository() -> str:
    return os.getenv("SYNCTIFY_UPDATE_REPOSITORY", REPOSITORY).strip() or REPOSITORY


def resolve_github_token(
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> str | None:
    for key in ("SYNCTIFY_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        value = os.getenv(key)
        if value and value.strip():
            return value.strip()

    gh = which("gh")
    if gh is None:
        return None
    try:
        completed = run(
            [gh, "auth", "token", "--hostname", "github.com"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    token = completed.stdout.strip()
    return token or None


def github_client(token: str | None = None) -> httpx.Client:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "synctify-self-update",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(headers=headers, timeout=8.0, follow_redirects=True)


def fetch_latest_release(
    client: httpx.Client,
    *,
    repository: str | None = None,
) -> ReleaseInfo:
    repo = repository or _repository()
    try:
        response = client.get(f"{API_ROOT}/repos/{repo}/releases/latest")
    except httpx.HTTPError as exc:
        raise UpdateError(f"could not contact GitHub for updates: {exc}") from exc

    if response.status_code == 404:
        raise UpdateError(
            "Synctify releases are not accessible. If the repository is private, "
            "authenticate GitHub CLI with `gh auth login` or set SYNCTIFY_GITHUB_TOKEN."
        )
    try:
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise UpdateError(f"GitHub update check failed: {exc}") from exc

    if not isinstance(data, dict):
        raise UpdateError("GitHub returned an invalid release response")
    tag = str(data.get("tag_name") or "").strip()
    match = _STABLE_TAG.fullmatch(tag)
    if match is None:
        raise UpdateError(f"latest GitHub release has an invalid stable tag: {tag!r}")
    version = match.group("version")
    expected_asset = f"synctify-{version}-macos.zip"
    assets = data.get("assets")
    if not isinstance(assets, list):
        raise UpdateError(f"GitHub release {tag} has no asset list")

    matching = [
        asset
        for asset in assets
        if isinstance(asset, dict) and str(asset.get("name") or "") == expected_asset
    ]
    if len(matching) != 1:
        raise UpdateError(
            f"GitHub release {tag} must contain exactly one {expected_asset} asset"
        )
    asset = matching[0]
    api_url = str(asset.get("url") or "").strip()
    if not api_url:
        raise UpdateError(f"GitHub release asset {expected_asset} has no API URL")

    raw_size = asset.get("size")
    size = raw_size if isinstance(raw_size, int) and raw_size >= 0 else None
    raw_digest = asset.get("digest")
    digest = str(raw_digest).strip() if isinstance(raw_digest, str) and raw_digest.strip() else None
    return ReleaseInfo(
        version=version,
        tag=tag,
        asset_name=expected_asset,
        asset_api_url=api_url,
        asset_size=size,
        asset_digest=digest,
    )


def find_update(
    current_version: str,
    client: httpx.Client,
    *,
    repository: str | None = None,
) -> ReleaseInfo | None:
    release = fetch_latest_release(client, repository=repository)
    if _version_tuple(release.version) <= _version_tuple(current_version):
        return None
    return release


def _download_release_asset(
    release: ReleaseInfo,
    destination: Path,
    client: httpx.Client,
) -> Path:
    target = destination / release.asset_name
    hasher = hashlib.sha256()
    total = 0
    try:
        with client.stream(
            "GET",
            release.asset_api_url,
            headers={"Accept": "application/octet-stream"},
        ) as response:
            response.raise_for_status()
            with target.open("wb") as handle:
                for chunk in response.iter_bytes():
                    if not chunk:
                        continue
                    handle.write(chunk)
                    hasher.update(chunk)
                    total += len(chunk)
    except (OSError, httpx.HTTPError) as exc:
        raise UpdateError(f"failed to download {release.asset_name}: {exc}") from exc

    if release.asset_size is not None and total != release.asset_size:
        raise UpdateError(
            f"downloaded {release.asset_name} has size {total}, expected {release.asset_size}"
        )
    if release.asset_digest:
        algorithm, separator, expected = release.asset_digest.partition(":")
        if separator and algorithm.lower() == "sha256" and expected:
            actual = hasher.hexdigest()
            if actual.lower() != expected.lower():
                raise UpdateError(
                    f"downloaded {release.asset_name} failed SHA-256 verification"
                )
    return target


def _safe_extract_bundle(zip_path: Path, destination: Path, version: str) -> Path:
    root_name = f"synctify-{version}-macos"
    try:
        with ZipFile(zip_path) as archive:
            members = archive.infolist()
            if not members:
                raise UpdateError(f"{zip_path.name} is empty")
            for info in members:
                path = PurePosixPath(info.filename)
                if path.is_absolute() or ".." in path.parts or not path.parts:
                    raise UpdateError(f"unsafe path in update archive: {info.filename}")
                if path.parts[0] != root_name:
                    raise UpdateError(
                        f"unexpected update archive root {path.parts[0]!r}; expected {root_name!r}"
                    )
                file_type = (info.external_attr >> 16) & 0o170000
                if file_type == 0o120000:
                    raise UpdateError(f"symlinks are not allowed in update archive: {info.filename}")
            archive.extractall(destination)
    except BadZipFile as exc:
        raise UpdateError(f"downloaded update is not a valid ZIP archive: {exc}") from exc

    bundle = destination / root_name
    required = [
        bundle / "VERSION",
        bundle / "README.txt",
        bundle / "install.sh",
        bundle / "synctify.sh",
    ]
    missing = [path.name for path in required if not path.is_file()]
    wheels = list(bundle.glob(f"synctify-{version}-*.whl"))
    if missing or len(wheels) != 1:
        detail = ", ".join(missing) if missing else f"{len(wheels)} matching wheels"
        raise UpdateError(f"update bundle is incomplete: {detail}")
    installed_version = (bundle / "VERSION").read_text(encoding="utf-8").strip()
    if installed_version != version:
        raise UpdateError(
            f"update bundle VERSION is {installed_version!r}, expected {version!r}"
        )
    (bundle / "install.sh").chmod(0o755)
    (bundle / "synctify.sh").chmod(0o755)
    return bundle


def install_release(
    release: ReleaseInfo,
    client: httpx.Client,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    with tempfile.TemporaryDirectory(prefix="synctify-update-") as temporary:
        root = Path(temporary)
        archive = _download_release_asset(release, root, client)
        bundle = _safe_extract_bundle(archive, root / "extracted", release.version)
        try:
            completed = run(
                [str(bundle / "install.sh")],
                env=os.environ.copy(),
                text=True,
                check=False,
            )
        except OSError as exc:
            raise UpdateError(f"could not run the Synctify installer: {exc}") from exc
        if completed.returncode != 0:
            raise UpdateError(
                f"Synctify {release.version} installer exited with code {completed.returncode}"
            )


def run_automatic_update(
    mode: str,
    current_version: str,
    *,
    token: str | None = None,
    client_factory: Callable[[str | None], httpx.Client] = github_client,
    installer: Callable[[ReleaseInfo, httpx.Client], None] = install_release,
) -> AutomaticUpdateResult:
    """Check on every installed invocation; optionally install without prompting."""
    normalized = mode.strip().lower()
    if normalized == "off":
        return AutomaticUpdateResult()
    if normalized not in {"check", "prompt", "install"}:
        return AutomaticUpdateResult(error=f"invalid automatic update mode: {mode}")
    if os.getenv("SYNCTIFY_INSTALLED") != "1" or os.getenv("SYNCTIFY_SKIP_AUTO_UPDATE") == "1":
        return AutomaticUpdateResult()

    resolved_token = token if token is not None else resolve_github_token()
    release: ReleaseInfo | None = None
    error: str | None = None
    try:
        with client_factory(resolved_token) as client:
            release = find_update(current_version, client)
            if release is not None and normalized == "install":
                installer(release, client)
    except UpdateError as exc:
        error = str(exc)
    except (OSError, httpx.HTTPError) as exc:
        error = str(exc)

    return AutomaticUpdateResult(
        checked=True,
        release=release,
        installed=release is not None and normalized == "install" and error is None,
        error=error,
    )
