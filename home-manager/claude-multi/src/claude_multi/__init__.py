"""claude-multi composition compiler (Python standard library only).

The version comes from ``version.json`` at the package root — the same file
the Nix package reads — so every ``--version`` surface agrees by
construction.
"""

from __future__ import annotations

import json
from pathlib import Path


def _load_version() -> str:
    try:
        document = json.loads(
            (Path(__file__).resolve().parents[2] / "version.json").read_text(
                encoding="utf-8"
            )
        )
        return str(document["launcher_version"])
    except (OSError, ValueError, KeyError):
        return "0.0.0-unknown"


__version__ = _load_version()
