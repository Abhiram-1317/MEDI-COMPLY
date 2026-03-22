"""MEDI-COMPLY package initialization."""

from __future__ import annotations

from dotenv import load_dotenv

# Load .env variables early so submodules can rely on os.environ values.
load_dotenv()
