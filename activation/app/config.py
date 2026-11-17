import os
from pathlib import Path

# Both secrets are required: without them the server would either issue licenses nobody
# can verify or leave the admin page open, so it refuses to start instead.
# ADMIN_PASSWORD is only the initial password: once the admin changes it, the hash in the
# database wins.
PRIVATE_KEY = os.getenv("ACTIVATION_PRIVATE_KEY", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

DATABASE_PATH = Path(os.getenv("ACTIVATION_DB_PATH", "/data/activation.db"))

# How long an approved computer keeps working without reaching this server. It is also
# the longest a revocation can take to bite on a computer that stays offline.
LICENSE_DAYS = int(os.getenv("LICENSE_DAYS", "7"))

# Caps how many unanswered requests anyone can pile up in the database.
MAX_PENDING = int(os.getenv("MAX_PENDING", "500"))

# Where CI publishes the Windows executable, and the asset's name in each release.
GITHUB_REPO = os.getenv("GITHUB_REPO", "RafitolMG/RenfeEnjoyer")
RELEASE_ASSET = "RenfeEnjoyer.exe"
# Optional: only raises GitHub's rate limit (60 requests an hour without it).
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
