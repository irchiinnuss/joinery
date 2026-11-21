"""Review pictures and sounds for a device: by change and index, never by path."""

from __future__ import annotations

from pathlib import Path

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
