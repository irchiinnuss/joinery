# 0089. Guesses, departures and "not yet played by you"

Status: accepted (2026-11-15)

## Context

A submitted change told the user what changed (`facts`) and what was done for
each part of the request (`done`), but never what the agent decided on its own
or where it did something other than asked. Those are exactly the choices a
person wants to check before keeping a change, and auto-accept (ADR 0085) could
keep a change that quietly did something else.

Checks, tests, replays (ADR 0059), the playtest bot (ADR 0038) and done-when
(ADR 0060) prove the game works. None says whether a person has played it since
a change, so nothing stopped a run of accepted changes from piling up untried.

## Decision

**Every submit can say what was guessed and what isn't as asked.**
`submit_for_review` takes two optional lists, `guessed` (what the agent assumed
that the request didn't say) and `not_as_asked` (where it departed from the
request, and why), at most four short lines each. The shared instructions tell
every agent to fill them honestly and briefly and to leave them empty when there
is nothing to say. The turn (`joinery.turn`) keeps them with the transaction
journal, puts them on the `file_preview` and `in_review` events (so the chat
record keeps them too), and `joinery transactions show`, `joinery run --json`
and the change card show them: one compact line ("Differs from your request · 1
guess") that opens to each line. A `not_as_asked` line holds auto-accept with
the reason "differs from your request"; a guess alone doesn't, and is shown with
the kept change as "Guessed: …".

**Accepted changes to the game are stamped until a person plays.**
`joinery.played` stamps each accepted change whose files are the game (scenes,
scripts, assets, project settings; not docs, design notes, tests, Joinery's own
state) in `.joinery/played.json`. The file is per person and per machine and
stays out of git with the rest of `.joinery/`. A revert drops the stamp.

What counts as a person playing is decided in the game, where the run is: the
core's Replays autoload creates a play report (`core/replays/play_report.gd`)
only for a run started from the editor (debugger attached, `editor_runtime`),
with a window, on a game scene, and with none of the arguments a replay, the
playtest bot, a test or Joinery passes ("Record this play" counts). Once that
run has lasted ten seconds with at least one press, it sends one
`joinery:played` debugger message with when it started. The add-on's debugger
listener (`played_watch.gd`) calls `joinery played record`, which clears every
change accepted before the run started.

The state reaches everything the same way: the gateway puts it on each accept's
`file_decision`, `joinery transactions list`, `joinery timeline list`, `joinery
status` and `joinery run` carry it, and the editor shows it as a quiet line under
the change card's decision ("Not played since this change"), a mark on Timeline
entries, and a count in the sidebar header ("3 changes not played yet") that runs
the game.

## Consequences

- Agent-agnostic: both live in the shared core and the registry tool, so Claude
  Code, Codex, Gemini CLI and opencode get the same fields, rule and state.
- A departure always reaches the user, even with auto-accept on.
- On stock Godot without the game core nothing reports a play; the stamps stay
  and the count stays honest rather than guessing.
- Teammates' pulled changes are not stamped: "played by you" is about the person
  at this editor.
