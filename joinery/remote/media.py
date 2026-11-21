"""Review pictures and sounds for a device: by change and index, never by path."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

#: What a device may be shown from a change's folder.
MEDIA_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".wav", ".ogg"})
MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
}
NOT_FOUND = "No such picture."


def checked_media(project: Path, transaction_id: str, named: Path) -> Path:
    """``named`` when it is a picture or sound inside the change's own folder under
    ``.joinery/transactions``, reached through no link; ValueError otherwise."""
    if (
        not transaction_id
        or transaction_id in {".", ".."}
        or any(sep in transaction_id for sep in "/\\\0")
    ):
        raise ValueError(NOT_FOUND)
    root = project / ".joinery" / "transactions" / transaction_id
    if named.suffix.lower() not in MEDIA_SUFFIXES or not named.is_absolute():
        raise ValueError(NOT_FOUND)
    try:
        relative = named.relative_to(root)
    except ValueError:
        raise ValueError(NOT_FOUND) from None
    if ".." in relative.parts:
        raise ValueError(NOT_FOUND)
    step = project
    for part in (".joinery", "transactions", transaction_id, *relative.parts):
        step = step / part
        if step.is_symlink():
            raise ValueError(NOT_FOUND)
    if not step.is_file():
        raise ValueError("That picture isn't ready.")
    return step


def scrub(value: Any, project: str) -> Any:
    """``value`` with no file path left as a whole string: one in the project becomes
    project-relative, any other absolute one is dropped. Words that merely start with
    a slash ("/variants 3 …") stay as they are."""
    if isinstance(value, dict):
        return {k: scrub(v, project) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v, project) for v in value]
    if isinstance(value, str):
        prefix = project.rstrip("/\\") + os.sep if project else ""
        if prefix and value.startswith(prefix) and "\n" not in value:
            return value[len(prefix) :].replace(os.sep, "/")
        if _absolute(value):
            return ""
    return value


def _absolute(text: str) -> bool:
    if not text or any(ch.isspace() for ch in text):
        return False
    if len(text) > 2 and text[1] == ":" and text[2] in "/\\":
        return True
    return text.startswith(("/", "\\")) and len(text) > 1 and text[1:].count("/") >= 1
