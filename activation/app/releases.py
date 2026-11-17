"""Updates for the desktop app: which versions are on GitHub and which ones are offered.

CI publishes every tagged build as a GitHub release, but a release only reaches the apps
once the admin publishes it here. Publishing pins the SHA-256 GitHub reports for the
executable at that moment, and every check hands the newest published version to the
app signed with the same Ed25519 key as the licenses. A file swapped on GitHub after
publishing, or a release nobody reviewed, never gets installed.

The admin can also set a minimum version: below it the app refuses to search until it
updates, for when Renfe changes its site and older versions stop working.
"""

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlmodel import Session, select

from app import config
from app.models import PublishedRelease, UpdatePolicy
from app.signing import b64encode

VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
GITHUB_TIMEOUT = 10


class GitHubError(Exception):
    """Raised when GitHub cannot be reached or answers something unexpected."""


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = VERSION_PATTERN.match(text.strip())
    if match is None:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


@dataclass(frozen=True)
class Asset:
    url: str
    sha256: str
    size: int


@dataclass(frozen=True)
class GitHubRelease:
    version: str
    # By platform ("windows", "linux"); only builds GitHub reports a digest for.
    assets: dict[str, Asset]
    notes: str
    created_at: str


def _github(path: str) -> Any:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{config.GITHUB_REPO}{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "renfe-enjoyer-activation",
            **(
                {"Authorization": f"Bearer {config.GITHUB_TOKEN}"}
                if config.GITHUB_TOKEN
                else {}
            ),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=GITHUB_TIMEOUT) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise GitHubError(f"GitHub respondió {exc.code}") from exc
    except (OSError, ValueError) as exc:
        raise GitHubError("No se pudo consultar GitHub") from exc


def _from_github(release: dict[str, Any]) -> GitHubRelease | None:
    """The release's builds, if it is final and at least one carries a digest."""
    if release.get("draft") or release.get("prerelease"):
        return None
    version = parse_version(str(release.get("tag_name", "")))
    if version is None:
        return None
    names = {name: platform for platform, name in config.RELEASE_ASSETS.items()}
    assets: dict[str, Asset] = {}
    for asset in release.get("assets") or []:
        digest = str(asset.get("digest") or "")
        platform = names.get(str(asset.get("name")))
        if platform and digest.startswith("sha256:"):
            assets[platform] = Asset(
                url=str(asset["browser_download_url"]),
                sha256=digest.removeprefix("sha256:"),
                size=int(asset.get("size") or 0),
            )
    if not assets:
        return None
    return GitHubRelease(
        version=".".join(map(str, version)),
        assets=assets,
        notes=str(release.get("body") or ""),
        created_at=str(release.get("published_at") or ""),
    )


def fetch_releases() -> list[GitHubRelease]:
    """The executables on GitHub, newest first."""
    found = [
        parsed
        for release in _github("/releases?per_page=30")
        if (parsed := _from_github(release)) is not None
    ]
    return sorted(
        found, key=lambda r: parse_version(r.version) or (0, 0, 0), reverse=True
    )


# --- what the apps are offered ---------------------------------------------------------


def published(session: Session) -> list[PublishedRelease]:
    rows = session.exec(select(PublishedRelease)).all()
    return sorted(rows, key=lambda r: parse_version(r.version) or (0, 0, 0), reverse=True)


def policy(session: Session) -> UpdatePolicy:
    return session.get(UpdatePolicy, 1) or UpdatePolicy()


def asset_for(release: PublishedRelease, platform: str) -> Asset | None:
    if platform == "windows":
        return Asset(url=release.url, sha256=release.sha256, size=release.size)
    if platform == "linux" and release.linux_url and release.linux_sha256:
        return Asset(release.linux_url, release.linux_sha256, release.linux_size or 0)
    return None


def signed_update(
    session: Session, key: Ed25519PrivateKey, platform: str = "windows"
) -> dict[str, str] | None:
    """The newest published build for this platform, signed; None if there is none."""
    found = next(
        (
            (release, asset)
            for release in published(session)
            if (asset := asset_for(release, platform)) is not None
        ),
        None,
    )
    if found is None:
        return None
    release, asset = found
    payload = json.dumps(
        {
            # Keeps an update from passing for a license, which the same key signs.
            "kind": "update",
            "version": release.version,
            "url": asset.url,
            "sha256": asset.sha256,
            "size": asset.size,
            "min_version": policy(session).min_version,
        },
        separators=(",", ":"),
    ).encode()
    return {"payload": b64encode(payload), "signature": b64encode(key.sign(payload))}
