"""Configuration and environment management for Coldwire Telegram Review Bot."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent.parent


def load_env(env_path: Path = None):
    """Load environment variables from a .env file if it exists."""
    candidates = [
        env_path,
        ROOT / ".env",
        PROJECT_ROOT / ".env",
    ]
    for p in candidates:
        if p and p.is_file():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k and k not in os.environ:
                            os.environ[k] = v
                break
            except Exception:
                pass


# Load local .env if present
load_env()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

# Authorized user ID must be an integer
_raw_user_id = os.environ.get("AUTHORIZED_USER_ID", "0").strip()
try:
    AUTHORIZED_USER_ID = int(_raw_user_id)
except ValueError:
    AUTHORIZED_USER_ID = 0

# Database path - default to user directory or discovery service default
_default_db = Path.home() / ".local" / "share" / "coldcall" / "leads.db"
DB_PATH = Path(os.environ.get("COLDCALL_DB", _default_db))


def validate_config(strict=True):
    """Validate that required secrets and identifiers are provided.

    Returns (is_valid, error_message).
    """
    if not TELEGRAM_BOT_TOKEN:
        msg = "TELEGRAM_BOT_TOKEN is missing or empty. Provide it via environment or .env."
        if strict:
            raise ValueError(msg)
        return False, msg

    if AUTHORIZED_USER_ID <= 0:
        msg = "AUTHORIZED_USER_ID is missing or invalid. It must be a positive integer Telegram user ID."
        if strict:
            raise ValueError(msg)
        return False, msg

    return True, "Config valid"
