"""A light check for requests to reproduce a specific existing game (ADR 0089).

"Make Super Mario Bros", "recreate the first level of Celeste", "copy Stardew Valley's
art". Journeyman's shared instructions already say what to do then: ask once whether
the user wants their own take or a twist, and offer an original game in the same
genre, instead of rebuilding someone else's levels, characters or look. This module
spots such a request in the user's words and adds a short reminder to that turn, so
the instruction is fresh where it matters, for every agent alike.

It only ever adds a reminder: it never blocks, never stops the turn and never refuses.
Genre, mechanics and feel are free to borrow ("a platformer with a dash like
Celeste's", "a roguelike inspired by Spelunky"), and those do not trigger it.
"""

from __future__ import annotations

import re

#: Verbs that ask for a reproduction rather than inspiration.
_VERBS = (
    r"clone|clones|cloning|copy|copies|copying|recreate|recreating|remake|remaking|"
    r"replicate|replicating|reproduce|reproducing|rip|ripping|port|porting|rebuild|"
    r"rebuilding|duplicate|duplicating|make|build|do"
)
_VERB = re.compile(rf"\b(?:{_VERBS})\b", re.IGNORECASE)
#: "make" alone is everyday; it counts only as "make an exact copy / a clone of".
_WEAK = frozenset(
    {"make", "build", "do", "duplicate", "duplicating", "port", "porting"}
)
_EXACT = re.compile(
    r"\b(?:exact|identical|faithful|1:1|one[- ]to[- ]one|pixel[- ]perfect|"
    r"carbon)\b[^.?!]{0,24}?\b(?:copy|clone|replica|recreation|remake|version|port)\b",
    re.IGNORECASE,
)
_SAME = re.compile(
    r"\b(?:the\s+)?same\s+(?:levels?|maps?|characters?|story|art|art\s+style|look|"
    r"sprites?|bosses|world|music|soundtrack|ui|hud|names?)\s+as\s+",
    re.IGNORECASE,
)
_CLONE_OF = re.compile(
    r"\b(?:a\s+)?(?:clone|copy|replica|knock-?off)\s+of\s+", re.IGNORECASE
)
#: What a game's own content is called after "<Name>'s".
_CONTENT = (
    r"levels?|maps?|worlds?|characters?|cast|heroes|bosses|art|art\s+style|look|"
    r"sprites?|story|music|soundtrack|ui|hud|menus?|logo|title\s+screen|stages?"
)
_POSSESSIVE = re.compile(
    rf"\b([A-Z][\w:!-]*(?:\s+(?:[A-Z][\w:!-]*|\d+|of|the))*)['’]s\s+(?:{_CONTENT})\b"
)
#: Words after a capitalised name that mean a thing in this project, not a game.
_PROJECT_THINGS = frozenset(
    {
        "node",
        "nodes",
        "scene",
        "scenes",
        "script",
        "scripts",
        "file",
        "files",
        "material",
        "materials",
        "mesh",
        "resource",
        "folder",
        "asset",
        "object",
        "button",
        "panel",
        "level",
        "room",
        "texture",
        "shader",
        "animation",
        "sprite",
        "model",
        "layer",
    }
)
#: Capitalised words that aren't another game: tools, engines, Joinery's own names,
#: words that start sentences or name directions.
_NOT_GAMES = frozenset(
    {
        "i",
        "a",
        "an",
        "the",
        "this",
        "that",
        "it",
        "my",
        "our",
        "please",
        "godot",
        "joinery",
        "journeyman",
        "blender",
        "krita",
        "inkscape",
        "audacity",
        "surge",
        "supercollider",
        "material",
        "maker",
        "gdscript",
        "hud",
        "ui",
        "menu",
        "left",
        "right",
        "up",
        "down",
    }
)
_NAME = re.compile(
    r"[\"“']([^\"”']{2,60})[\"”']|([A-Z0-9][\w:!-]*(?:\s+(?:[A-Z0-9][\w:!-]*|of|the))*)"
)
_SKIP_WORDS = re.compile(
    r"^(?:\s*(?:the|a|an|its|their|whole|entire|exact|exactly|first|second|third|last|"
    r"opening|every|all|level|levels|stage|world|map|game|of|from|in|for|me|us|"
    r"sprites?|art|music|characters?|assets?|look|style|bosses|enemies|story|"
    r"\d+(?:-\d+)?))*\s*",
    re.IGNORECASE,
)

REMINDER = (
    "Clone check: this request may ask to reproduce a specific existing game "
    "({names}). Before building, ask the user once whether they want their own take "
    "or a twist, and offer an original game in the same genre with its own name, "
    "levels, characters and look. Borrowing a genre, a mechanic or a feel is fine; "
    "reproducing another game's levels, characters, art, music, names or UI is not. "
    "If they only want the genre or a mechanic, go ahead without asking."
)


def _game_name(text: str) -> str:
    """The first name in ``text`` that reads as another game's: a quoted title or
    capitalised words that aren't a tool, a sentence word or a project thing."""
    rest = _SKIP_WORDS.sub("", text, count=1)
    match = _NAME.match(rest)
    if match is None:
        return ""
    if match.group(1):
        return match.group(1).strip()
    words = match.group(2).split()
    while words and words[-1].casefold() in {"of", "the"}:
        words.pop()
    if not words or words[0].casefold() in _NOT_GAMES or words[0].startswith("@"):
        return ""
    after = rest[match.end() :].split()
    if after and after[0].casefold().strip(".,!?") in _PROJECT_THINGS:
        return ""
    if len(words) == 1 and words[0].isdigit():
        return ""
    return " ".join(words)


def named_games(request: str) -> list[str]:
    """The existing games ``request`` seems to ask to reproduce (empty for none)."""
    found: list[str] = []
    text = " ".join(request.split())
    for pattern in (_CLONE_OF, _SAME):
        for match in pattern.finditer(text):
            name = _game_name(text[match.end() : match.end() + 80])
            if name:
                found.append(name)
    for match in _POSSESSIVE.finditer(text):
        before = text[max(0, match.start() - 40) : match.start()]
        if (
            _VERB.search(before)
            and match.group(1).split()[0].casefold() not in _NOT_GAMES
        ):
            found.append(match.group(1))
    exact = _EXACT.search(text)
    for match in _VERB.finditer(text):
        verb = match.group(0).casefold()
        if verb in _WEAK and not exact:
            continue
        window = text[match.end() : match.end() + 80]
        if exact:
            window = text[exact.end() : exact.end() + 80] if verb in _WEAK else window
            window = re.sub(r"^\s*of\b", "", window)
        name = _game_name(window)
        if name:
            found.append(name)
    return list(dict.fromkeys(found))


def reminder(request: str) -> str:
    """The reminder for this turn, or "" when the request doesn't ask for a copy."""
    names = named_games(request)
    if not names:
        return ""
    shown = ", ".join(f'"{n}"' for n in names[:3])
    return REMINDER.format(names=shown)
