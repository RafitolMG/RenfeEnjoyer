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
class GitHubRelease:
    version: str
    url: str
    sha256: str
    size: int
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
    """The release's executable, if it is a final build that carries one with a digest."""
    if release.get("draft") or release.get("prerelease"):
        return None
    version = parse_version(str(release.get("tag_name", "")))
    if version is None:
        return None
    for asset in release.get("assets") or []:
        digest = str(asset.get("digest") or "")
        if asset.get("name") == config.RELEASE_ASSET and digest.startswith("sha256:"):
            return GitHubRelease(
                version=".".join(map(str, version)),
                url=str(asset["browser_download_url"]),
                sha256=digest.removeprefix("sha256:"),
                size=int(asset.get("size") or 0),
                notes=str(release.get("body") or ""),
                created_at=str(release.get("published_at") or ""),
            )
    return None


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


def signed_update(session: Session, key: Ed25519PrivateKey) -> dict[str, str] | None:
    """The newest published version, signed for the app to verify; None if none."""
    releases = published(session)
    if not releases:
        return None
    latest = releases[0]
    payload = json.dumps(
        {
            # Keeps an update from passing for a license, which the same key signs.
            "kind": "update",
            "version": latest.version,
            "url": latest.url,
            "sha256": latest.sha256,
            "size": latest.size,
            "min_version": policy(session).min_version,
        },
        separators=(",", ":"),
    ).encode()
    return {"payload": b64encode(payload), "signature": b64encode(key.sign(payload))}
