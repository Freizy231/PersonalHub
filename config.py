"""Shared configuration; importing it never creates a database."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=False)
configured_path = Path(os.getenv("DB_PATH", "data/intel.db")).expanduser()
DB_PATH = (configured_path if configured_path.is_absolute() else BASE_DIR / configured_path).resolve()


def env_int(name, default, minimum, maximum):
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        raise ValueError(f"{name} 必须是整数") from None
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} 必须在 {minimum}–{maximum} 之间")
    return value
