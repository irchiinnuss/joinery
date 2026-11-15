# 0087. Play questions, rules for some files, and the clone check

Status: accepted (2026-11-15)

## Context

Three gaps showed up when comparing Joinery with other game-making setups:

- The new-project wizard asks for a pitch and an audience, and the starting point
  fills in genre, loop and verbs. Nothing asks the few questions that shape a game
  before it exists (what the player does most, what makes it hard, how it should
  feel, how long one sitting lasts), and nothing records which parts of the brief the
  user chose and which were filled in for them.
- `JOURNEYMAN.md` (ADR 0077) is one flat file in every turn's stable prefix. A rule
  that only matters in one corner of the game ("enemies use the behaviour feature")
  costs every turn, and grows the cached prefix for everyone. Agents also mostly
  create files, so a rule shown only when a file is read misses the moment it matters.
- Asked to "make Super Mario Bros" or "copy Stardew Valley's art", an agent tends
  either to rebuild someone else's game or to refuse outright. Neither is what the
  user needs.

## Decision

### Play questions in the wizard

1. **Data per starting point.** `blueprints/<id>/questions.toml` holds four to six
   short questions for that starting point (each of the seven has one): the loop,
   the challenge, the feel, the length of a sitting, and one that fits the genre.
   Every question has two to five choices, one of them the suggested default, and
   names the brief section its answer goes into. `joinery/genre_questions.py` reads
   and checks them; `joinery blueprints --json` carries each starting point's round,
   and `joinery brief --questions --blueprint ID` prints one.
2. **Optional twice over.** The wizard's last step has a **How it plays** section,
   closed by default (patch 0042). Closed, the round is skipped and the brief is
   drafted exactly as before. Open, each question is a picker with the suggestion
   selected and named so, and **Skip** last.
3. **Every answer says where it came from.** The wizard's brief answers file (and
   `joinery new --answers`, `joinery game apply`) carry a `questions` object: a
   choice id or the user's own words (asked), `"skip"` (skipped), `"default"` or
   nothing (the suggestion stands). Each becomes one line in its brief section:
   `- Most of the time: … _(your answer)_`, `… _(suggested default, not confirmed)_`
   or `- What makes it hard: _Not decided yet._ _(skipped)_`. A skipped question
   leaves its section open, so Journeyman asks about it later. A round that doesn't
   fit the starting point is refused before anything is written.
4. **A suggestion is never a rule.** The design check (ADR 0053) reads the brief
   without unconfirmed defaults and skipped questions (`design.brief_section`), and
   drops an agent finding that rests only on such a line. The brief summary tells
   Journeyman which defaults still stand, and the shared instructions say to ask
   before building on one and, when the user agrees, to propose a brief edit that
   marks it as their answer. Only the user turns a suggestion into a choice.

### Rules for some files only

5. **Where they live.** A section headed `## When working on <patterns>` in
   `JOURNEYMAN.md`, or a file `.joinery/rules/<name>.md` whose first line is
   `paths: <patterns>` (kept in git with the project). Patterns are project paths:
   `**` spans folders, `*` stays within one, and a pattern without a slash matches
   that name anywhere (`*.gdshader`). `joinery/scoped_rules.py`.
6. **Out of the stable prefix.** `memory.block` leaves these sections out, and they
   don't count toward its 6,000 characters, so adding or changing a rule changes no
   cached byte (ADR 0056). A test compares the whole stable text with and without
   rules.
7. **Given when the agent works on a matching file.** In the turn's own part, for
   the selection and the open scene; and in the result of any gateway call that
   names a matching file: reads, writes and new files alike, so a rule reaches the
   agent that creates the file. Each rule is given once per turn, at most four at a
   time and 3,000 characters together. Every provider reaches the project through the
   same turn text and gateway, so this holds for all of them.
8. **Edited as a reviewed change.** `/remember for game/ai/**: <rule>` and
   `joinery rules --project P add --path GLOB <rule>` stage the line under its
   section for Review; `joinery rules` lists the rules and what it can't use, and
   `joinery rules match PATH` shows which hold.

### The clone check

9. **Ask, don't copy, don't refuse.** The shared instructions say: when a request
   asks to copy or recreate a specific existing game, or its levels, characters or
   look, ask once whether the user wants their own take or a twist, and offer an
   original game in the same genre; never rebuild another game's levels, characters,
   art, music, names or UI. Genre, mechanics and feel are free to borrow, and never
   refused.
10. **A light reminder in the turn.** `joinery/clone_guard.py` spots such a request
    (a verb such as clone, recreate or remake with a game's name, "an exact copy of",
    "the same characters as", "<Name>'s levels") and adds a short reminder to that
    turn's own part, naming the game. It never blocks or stops a turn, and requests
    that only borrow a genre or a mechanic ("a dash like Celeste's", "inspired by
    Spelunky"), or that edit the project's own things ("copy the Player node"), pass
    without one.

## Consequences

- A brief made with the round tells the user's choices from Joinery's suggestions
  at a glance, and later checks can rely on the difference.
- Project rules can be as specific as the game needs without slowing every turn.
- A request to reproduce a game becomes a short conversation about the user's own
  version rather than a copy or a refusal. The detector is a heuristic: it can miss
  a phrasing, and the instructions still cover that case.
