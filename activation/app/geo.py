"""Approximate location of an IP, from DB-IP's free IP to City Lite database.

The database (CC BY 4.0, attributed on the admin page) is downloaded into the data
volume and refreshed monthly in the background, so it neither bloats the image nor slows
CI. Lookups are local: IPs are never sent to anyone. Until the first download finishes,
locations are simply unknown; they are resolved when the admin page asks, so earlier
requests gain theirs once it is in place.
"""

import gzip
import logging
import os
import re
import shutil
import threading
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

import maxminddb

from app import config

logger = logging.getLogger(__name__)

SOURCE = "https://download.db-ip.com/free/dbip-city-lite-{month}.mmdb.gz"
# DB-IP publishes monthly; a copy older than this is replaced.
MAX_AGE = timedelta(days=32)
DOWNLOAD_TIMEOUT = 60

_lock = threading.Lock()
_reader: Any = None


def _months() -> list[str]:
    """This month's database, and last month's in case this one is not out yet."""
    today = datetime.now(UTC).date()
    previous = today.replace(day=1) - timedelta(days=1)
    return [today.strftime("%Y-%m"), previous.strftime("%Y-%m")]


def _stale() -> bool:
    try:
        age = time.time() - config.GEOIP_PATH.stat().st_mtime
    except OSError:
        return True
    return age > MAX_AGE.total_seconds()


def _download() -> None:
    part = config.GEOIP_PATH.with_suffix(".part")
    for month in _months():
        try:
            request = urllib.request.Request(
                SOURCE.format(month=month),
                headers={"User-Agent": "renfe-enjoyer-activation"},
            )
            with (
                urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response,
                gzip.GzipFile(fileobj=response) as unzipped,
                part.open("wb") as target,
            ):
                shutil.copyfileobj(unzipped, target, 1024 * 1024)
            maxminddb.open_database(str(part)).close()
            os.replace(part, config.GEOIP_PATH)
            logger.info("GeoIP database updated to %s", month)
            return
        except (OSError, ValueError, maxminddb.InvalidDatabaseError) as exc:
            logger.warning("GeoIP download for %s failed: %s", month, exc)
            part.unlink(missing_ok=True)


def _open() -> None:
    global _reader
    try:
        reader = maxminddb.open_database(str(config.GEOIP_PATH))
    except (OSError, ValueError, maxminddb.InvalidDatabaseError):
        return
    with _lock:
        previous, _reader = _reader, reader
    locate.cache_clear()
    if previous is not None:
        previous.close()


def _refresh() -> None:
    if _stale():
        config.GEOIP_PATH.parent.mkdir(parents=True, exist_ok=True)
        _download()
    _open()


def start() -> None:
    """Load the database, fetching or renewing it first if needed, without blocking."""
    if config.GEOIP_ENABLED:
        threading.Thread(target=_refresh, name="geoip", daemon=True).start()


def _name(entry: dict[str, Any] | None) -> str | None:
    names = (entry or {}).get("names") or {}
    name = names.get("es") or names.get("en")
    # The Spanish names carry ISO-style articles: "Estados Unidos de América (los)".
    return re.sub(r"\s*\([^)]*\)$", "", name) if name else None


@lru_cache(maxsize=4096)
def locate(ip: str | None) -> str | None:
    """Like "Coslada, Comunidad de Madrid, España"; None if unknown."""
    with _lock:
        reader = _reader
    if not ip or reader is None:
        return None
    try:
        record = reader.get(ip)
    except ValueError:
        return None
    if not record:
        return None
    subdivisions = record.get("subdivisions") or [None]
    parts = [
        _name(record.get("city")),
        _name(subdivisions[0]),
        _name(record.get("country")),
    ]
    unique = [part for i, part in enumerate(parts) if part and part not in parts[:i]]
    return ", ".join(unique) or None
