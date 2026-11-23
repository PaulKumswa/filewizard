"""User preferences, stored as JSON in %LOCALAPPDATA%\\FileWizard\\settings.json."""

from __future__ import annotations

import json

from .util import app_dir

DEFAULTS = {
    "notify": True,          # show the "Done — Open / Show in folder" toast
    "output": "beside",
    "menu": "auto",
    "paper": "letter",       # "letter" or "a4" for pages FileWizard creates          # "auto" (full native menu when available) or "classic" (registry only)      # "beside" the original, or "subfolder" (a FileWizard folder next to it)
}


def path():
    return app_dir() / "settings.json"


def load() -> dict:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
        return {**DEFAULTS, **(data if isinstance(data, dict) else {})}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def save(values: dict) -> None:
    current = load()
    current.update(values)
    path().write_text(json.dumps(current, indent=2), encoding="utf-8")


def hidden_actions() -> set[str]:
    """Actions the user switched off in Settings > Customize menu (read by the native menu too)."""
    try:
        return set((app_dir() / "hidden.txt").read_text(encoding="utf-8").split())
    except OSError:
        return set()


def set_hidden_actions(ids: set[str]) -> None:
    (app_dir() / "hidden.txt").write_text("\n".join(sorted(ids)) + "\n", encoding="utf-8")
