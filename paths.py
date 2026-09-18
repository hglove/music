"""Locate data files both when running from source and from a PyInstaller bundle.

A one-file PyInstaller build unpacks everything it was told to carry into a temp
directory and points ``sys._MEIPASS`` at it. Code that resolves resources relative
to ``__file__`` keeps working for *imported modules*, but the data files themselves
(``smtc.ps1``, ``frontend/dist``) only exist under that temp directory — so every
path that names one has to come through here or the frozen build ships without them.
"""

from __future__ import annotations

import sys
from pathlib import Path


def resource_root() -> Path:
    """Directory holding the bundled data files."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))
