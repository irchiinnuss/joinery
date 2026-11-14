"""SuperCollider: complete NRT patches rendered offline; live audition via its MCP.

sclang does not exit on its own. A patch that fails to parse, raises, or never
calls `exit` would otherwise sit idle until the worker's own timeout, so the
recipe script exits with a clear `JOINERY_ERROR` / `JOINERY_TIMEOUT` line, and
replay runs under `timeout` as a last resort.
"""

from __future__ import annotations

import re
import zlib

from joinery.tools.audio_chain import sc_text
from joinery.tools.base import (
    CREATIVE_WORKER,
    MCP_WORKER,
    Feature,
    ImageCheck,
    InstallCheck,
    McpProbe,
    ToolModule,
    WorkerExec,
)
from joinery.tools.surge_xt import AUDIO_EDIT_CHECK
from joinery.tools.surge_xt import MODULE as SURGE

#: Seconds a patch may run before the recipe script stops it. An NRT render is
#: much faster than real time, so this only trips on a patch that never exits.
RENDER_LIMIT = 240
#: Hard stop for replay, above RENDER_LIMIT so the script's own message wins.
REPLAY_TIMEOUT = 300
#: A patch ends sclang itself (usually `0.exit` in recordNRT's action).
_EXITS = re.compile(r"\bexit\b")
#: A patch that seeds the server's or the language's random generators.
_SEEDED = re.compile(r"\b[Rr]andSeed\b")
#: Random sources, on the server and in the language. Broad on purpose: a
#: false hit only means the render is compared by sound instead of bytes.
_RANDOM = re.compile(r"Noise|Dust|Gendy|CoinGate|[Rr]and|choose|scramble|coin")

NO_EXIT = (
    "JOINERY_ERROR: the patch never exits. End the render with 0.exit, for "
    "example in Score.recordNRT's action: { 0.exit }."
)
PARSE_ERROR = (
    "JOINERY_ERROR: the patch does not parse; see the SuperCollider error above."
)
RUN_ERROR = (
    "JOINERY_ERROR: the patch raised an error; see the SuperCollider error above."
)
TIMEOUT = (
    f"JOINERY_TIMEOUT: the patch did not exit within {RENDER_LIMIT} s. End the "
    "render with 0.exit in Score.recordNRT's action."
)


#: The seed a patch's RandSeed sets (the first one, as a recipe records it).
_SEED_VALUE = re.compile(r"\bRandSeed\.ir\(\s*[^,()]+,\s*(\d+)\s*\)")
#: A SynthDef, whose graph function the seed goes into.
_SYNTHDEF = re.compile(r"\bSynthDef(?:\.new)?\s*\(")
#: Seeds a patch may take: a UGen input is a 32-bit float, exact below 2**24,
#: so a derived seed stays there; a given one may use the kit's whole range.
SEED_LIMIT = 2**31
DERIVED_SEED_LIMIT = 2**24


def seed_line(seed: int) -> str:
    """The statements that fix a synth's random generators to `seed`.

    RandID picks generator 0 and RandSeed resets it at the synth's start, so
    WhiteNoise, Dust, Rand and the rest draw the same numbers every render.
    """
    return f"RandID.ir(0); RandSeed.ir(1, {seed});"


def patch_seed(text: str) -> int | None:
    """The seed a patch (or a recipe script holding one) sets, if any."""
    found = _SEED_VALUE.search(text)
    return int(found.group(1)) if found else None


def derived_seed(source: str) -> int:
    """A stable seed for a patch given none: from its source path, so every
    edit of the same sound keeps the same randomness."""
    return zlib.crc32(source.encode()) % DERIVED_SEED_LIMIT


def _blank(code: str) -> str:
    """`code` with comments, strings, symbols and characters blanked out.

    Same length, so a position found in it is a position in `code`.
    """
    out = list(code)
    i, n = 0, len(code)

    def clear(start: int, end: int) -> None:
        for k in range(start, min(end, n)):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        c = code[i]
        if code.startswith("//", i):
            end = code.find("\n", i)
            end = n if end < 0 else end
            clear(i, end)
            i = end
        elif code.startswith("/*", i):
            # SuperCollider block comments nest.
            depth, j = 1, i + 2
            while j < n and depth:
                if code.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif code.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            clear(i, j)
            i = j
        elif c in "\"'":
            j = i + 1
            while j < n and code[j] != c:
                j += 2 if code[j] == "\\" else 1
            clear(i, j + 1)
            i = j + 1
        elif c == "$" and i + 1 < n:
            # A character literal: $a, $", $\\ .
            end = i + 3 if code[i + 1] == "\\" else i + 2
            clear(i, end)
            i = end
        else:
            i += 1
    return "".join(out)


_OPEN, _CLOSE = "([{", ")]}"


def _depth_scan(blank: str, start: int, stops: str) -> int:
    """The first position from `start` holding one of `stops` at depth 0, or -1.

    A closing bracket that would go below depth 0 also ends the scan (-1).
    """
    depth = 0
    for k in range(start, len(blank)):
        c = blank[k]
        if depth == 0 and c in stops:
            return k
        if c in _OPEN:
            depth += 1
        elif c in _CLOSE:
            depth -= 1
            if depth < 0:
                return -1
    return -1


def _skip_space(blank: str, i: int) -> int:
    while i < len(blank) and blank[i].isspace():
        i += 1
    return i


def _keyword(blank: str, i: int, word: str) -> bool:
    end = i + len(word)
    return blank.startswith(word, i) and (
        end >= len(blank) or not (blank[end].isalnum() or blank[end] == "_")
    )


def _seed_point(blank: str, brace: int) -> int:
    """Where a statement may go in the function opening at `brace`: after its
    arguments and variable declarations, which sclang wants first."""
    i = _skip_space(blank, brace + 1)
    if i < len(blank) and blank[i] == "|":
        close = blank.find("|", i + 1)
        i = _skip_space(blank, close + 1) if close >= 0 else i
    elif _keyword(blank, i, "arg"):
        end = _depth_scan(blank, i, ";")
        i = _skip_space(blank, end + 1) if end >= 0 else i
    point = i
    while _keyword(blank, i, "var"):
        end = _depth_scan(blank, i, ";")
        if end < 0:
            break
        point = end + 1
        i = _skip_space(blank, point)
    if point == i:
        # No declarations: go straight after the brace (or the arguments).
        while point > brace + 1 and blank[point - 1].isspace():
            point -= 1
    return point


def seed_patch(code: str, seed: int) -> str:
    """`code` with every SynthDef that lacks a seed starting with `seed_line`.

    A SynthDef that already calls RandSeed keeps its own. Positions are found
    with comments and strings blanked, so text inside them is never touched.
    """
    blank = _blank(code)
    points: list[int] = []
    for found in _SYNTHDEF.finditer(blank):
        brace = _depth_scan(blank, found.end(), "{")
        if brace < 0:
            continue
        end = _depth_scan(blank, brace + 1, "}")
        if end < 0 or re.search(r"\bRandSeed\b", blank[brace:end]):
            continue
        points.append(_seed_point(blank, brace))
    for point in reversed(points):
        after = code[point:]
        gap = after[: len(after) - len(after.lstrip())]
        if "\n" not in gap:
            # The function goes on along this line: the seed joins it.
            text = " " + seed_line(seed)
        else:
            # Otherwise the seed is a line of its own, indented like the next.
            indent = gap[gap.rfind("\n") + 1 :]
            text = "\n" + indent + seed_line(seed)
        code = code[:point] + text + code[point:]
    return code


def _sc(text: str) -> str:
    """A SuperCollider expression for `text`, of any length.

    sclang reads a literal's characters as themselves, control characters
    included, and cuts a literal at about 8 KB, so long text is split into
    pieces and joined (see `audio_chain.sc_text`; `sc_unquote` reads it back).
    """
    return sc_text(text)


class SuperCollider(ToolModule):
    id = "supercollider"
    name = "SuperCollider"
    family = "joinery-sound"
    suffix = ".scd"
    script_ext = ".scd"
    authoring = True
    text_source = True
    binary = "sclang"
    desktop = "supercollider"
    ready_summary = "{name} {version} ready (offline renders)"
    broken = "SuperCollider failed its check"
    features = (
        Feature(
            "live-audition",
            "live audition",
            broken="Live audition error",
        ),
        # The same ffmpeg edits Surge XT's renders get, run after the patch.
        SURGE.feature("audio-edit"),
    )
    checks = (
        ImageCheck(CREATIVE_WORKER),
        WorkerExec(
            CREATIVE_WORKER,
            ("sclang", "-v"),
            expect=r"sclang \d",
            version=r"sclang (\d+\.\d+(?:\.\d+)?)",
            on_failure="needs_image",
            failure="SuperCollider is missing from the creative worker; rebuild it",
        ),
        McpProbe(
            "joinery-sound",
            "document_capabilities",
            capability="supercollider",
            image=CREATIVE_WORKER,
        ),
        AUDIO_EDIT_CHECK,
        ImageCheck(MCP_WORKER, feature="live-audition"),
        InstallCheck("supercollider", feature="live-audition"),
        McpProbe(
            "claude-collider",
            "cc_status",
            expect=r"Server: running",
            feature="live-audition",
            image=MCP_WORKER,
        ),
    )

    #: Live audio is real time and cannot be replayed, so it never becomes an
    #: asset (joinery.workers keeps recordings inside the worker).
    mcp_notes = (
        (
            "cc_recording",
            (
                "Joinery: audition only. Recordings stay inside the worker and are "
                "discarded with it. Render a sound you want to keep with "
                "joinery-sound operate_document (an NRT patch), which records a "
                "replayable recipe."
            ),
        ),
    )

    def script(self, source: str, base: str, code: str, exports: list[str]) -> str:
        # The log recreates the exact native patch before running it, so a
        # clean replay never depends on the previous source output remaining.
        # Rendering is NRT with a completion action; no sound device is needed.
        # - OnError catches an error raised later, such as in recordNRT's action;
        # - the SystemClock limit ends a patch that never calls exit;
        # - compile returns nil on a syntax error (interpret would carry on);
        # - try catches an error while the patch runs.
        header = (
            "(\nvar patch;\n"
            + f"OnError.add({{ {_sc(RUN_ERROR)}.postln; 1.exit }});\n"
            + f"SystemClock.sched({RENDER_LIMIT}, {{ {_sc(TIMEOUT)}.postln; 2.exit; nil }});\n"
            + f'File.use({_sc(source)}, "w", {{ |file| file.write({_sc(code)}); }});\n'
        )
        seed = ""
        found = patch_seed(code)
        if found is not None:
            # Language-side randomness (rrand, choose) starts from the seed too.
            seed = f"  thisThread.randSeed = {found};\n"
        if not _EXITS.search(code):
            # Without an exit sclang idles after the render until the limit;
            # say so at once instead of running the patch.
            return header + f"{_sc(NO_EXIT)}.postln;\n1.exit;\n)\n"
        return (
            header
            + f"patch = {_sc(code)}.compile;\n"
            + f"if(patch.isNil) {{ {_sc(PARSE_ERROR)}.postln; 1.exit }} {{\n"
            + seed
            + f"  {{ patch.value }}.try {{ |error| error.reportError; {_sc(RUN_ERROR)}.postln; 1.exit }};\n"
            + "};\n)\n"
        )

    def replay_argv(self, script: str) -> list[str]:
        return ["timeout", str(REPLAY_TIMEOUT), "sclang", script]

    def seed_code(self, code: str, source: str, seed: int | None) -> str:
        # Every SynthDef starts from the seed: the one given, else the one the
        # patch already sets, else one derived from the source path.
        if seed is None:
            seed = patch_seed(code)
        if seed is None:
            seed = derived_seed(source)
        if not 0 <= seed < SEED_LIMIT:
            raise ValueError(f"A seed is a whole number from 0 to {SEED_LIMIT - 1}.")
        return seed_patch(code, seed)

    def recipe_seed(self, script: str) -> int | None:
        return patch_seed(script)

    def renders_vary(self, script: str) -> bool:
        # scsynth seeds WhiteNoise, Dust, Rand and the rest from the clock, as
        # does sclang for rrand or choose, so such a patch renders new grains
        # every run. A patch that seeds them (RandSeed, randSeed) renders the
        # same bytes and keeps the exact check; so does one with no randomness.
        if _SEEDED.search(script):
            return False
        return bool(_RANDOM.search(script))


MODULE = SuperCollider()
