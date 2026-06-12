"""Vercel entrypoint for the FastAPI backend.

This keeps the existing backend package untouched and exposes the ASGI app
for /api/* routes on Vercel.
"""

from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app  # noqa: E402
