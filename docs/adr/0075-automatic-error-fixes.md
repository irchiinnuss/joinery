# 0075. Console errors, grouped, and fixed automatically when the project asks

Status: accepted (2026-11-12)

## Context

The sidebar already watched Godot's Output panel: each new error line got one
quiet offer ("Godot reported an error: … Debug / Dismiss"), and Debug started
the debugger agent and a worker (ADR 0037), whose fix is a normal change the
user reviews. That covered one source (Output), errors only, one line at a
time: the same error printed every frame was a new line each time it moved,
the game's own log and playtest runs were not read, and the user had to press
Debug for every error even when they would always say yes.

Users asked whether Joinery can resolve console errors on its own. It can,
within what Joinery always keeps: the user directs, every change is reviewed
and never accepted for them, and the enforcement and scheduling live in the
shared, agent-agnostic core (bridge + `joinery/`), not in one provider.

## Decision

1. **Sources.** The sidebar (`sidebar_debug.gd`) reads, every second, what the
   editor's Output panel (scripts that fail to parse on save, editor errors,
   the running game as the editor shows it) and the game's log
   (`user://logs/godot.log`, reread from the start when a new run starts a new
   log) gained, and sends it to the chat host as `errors_seen`. The playtester
   hands over its report's console items (`bridge/playtester.py`'s
   `on_report`). GUT's output is read wherever it is printed (Output or the
   log): `[Failed]:` lines under a test script and test name become `test`
   entries.
2. **Grouping** (`joinery/console_errors.py`). Each source is read into
   entries `{kind: error|warning|test, message, where, source, context}`.
   Entries with the same kind, message and place in the project are one
   group; run-to-run noise (object ids, addresses) is taken out of the message
   first. A group's count is the most any one source saw, so the same error
   read from Output and from the log counts once. Joinery's own lines
   (`addons/joinery`, `[Joinery]`), known engine noise (leaks at exit) and the
   project's `[agents] ignore_errors` (substring, `*` wildcard, case-blind)
   are never listed. Warnings are listed only with `[agents] fix_warnings =
   true`.
3. **The setting.** `[agents] fix_errors = "ask" | "auto" | "off"`, default
   `"ask"`, which is the old behaviour: one quiet offer per new group, one at
   a time, now with a third link, "Always fix errors like this", which saves
   `"auto"` and fixes the offered error. Settings → Project has the switch
   "Fix console errors automatically" (on = `"auto"`, off = `"ask"`; read and
   written by `joinery fix-errors`). `"off"` stops sending console text.
4. **Automatic fixes** (`bridge/error_fixer.py`). In `"auto"` each new group
   starts a debug task (`Crew.debug`, ADR 0036/0037) in the background: the
   debugger finds the cause, a worker stages the fix, the checks and the
   reviewer agent run, and the change waits for the user's Accept or Discard.
   Its card reads "Fixing: <error> (player.gd:42)" while it works and
   "Fixed: <error> (player.gd:42) — 3 times" once it waits for review.
   Limits, all in the shared core:
   - `fix_errors_at_once` fixes at a time (default 1, at most 3), never more
     than the project's free turns (`[agents] parallel`);
   - `fix_errors_cooldown` seconds between two automatic starts (default 30);
     a fix the user asks for (Fix, Fix all) skips the pause;
   - no fix starts for an error whose file is in a change waiting for review;
   - no loops: an error that first shows while a change to its own file waits
     for review came with that change and is never fixed on its own (it
     leaves with the change when discarded, and waits for a fix like any
     other when accepted); more of an error being fixed only counts;
   - a failed fix (the debugger found nothing to change, the fix failed its
     checks, or the error came back after its fix was accepted) counts
     against the group; after two, Joinery stops trying it on its own and
     says so once in the transcript ("Couldn't fix … after 2 tries"). Fix on
     its row tries once more.
   - a fix the user discards or stops is not restarted automatically.
5. **The Errors list** (`errors_card.gd`), with the agents' cards in the
   sidebar: "3 console errors", one row per group with its message, place,
   count and state (fixing, waiting for a free agent, fix ready for review,
   stopped after 2 tries, came with a change in review, ignored), and Fix /
   Review / Try again / Restore and an Ignore button per row. "Fix all" shows
   in `"ask"` when two or more groups wait. The host sends the whole list
   (`{kind: "errors"}`) whenever it changes.
6. **Metrics.** `.joinery/errors.json` keeps the groups between sessions and
   the counts: errors seen, distinct groups, fixes started, fixes that reached
   review, accepted, discarded, failed tries and groups given up on.
   `joinery metrics --errors` prints them; `joinery fix-errors --json`
   includes them.

## Amendment (2026-11-14): triage, one turn for many, a compact list

Engine and editor messages (`scene/main/window.cpp:2138 - Condition
"!is_inside_tree()" is true.`) took a full debugger turn each, only for it to
conclude "not a game error", and the feed showed each error three times.

1. **Triage before any agent** (`console_errors.triage`). An error whose only
   place is the engine's or editor's own source (`core/`, `scene/`,
   `servers/`, `platform/`, `editor/`, `drivers/`, `modules/`, `main/` with
   `.cpp/.h/.mm/…`), seen only in the editor's Output, with no `res://` frame
   in its own trace, is listed at once as "Editor message, not a game error"
   (state `editor`). Anything the running game, a playtest or a test printed
   is the game's. Lines from `addons/joinery` are no longer silently dropped:
   they are listed as a Joinery bug (state `joinery`). Neither is offered or
   sent to the fixer, and groups a past session left open are triaged again
   on load. An engine source is never made into a `res://` place.
2. **One turn for many.** The groups waiting together (one `errors_seen`, or
   all queued) go to one debugger turn, up to six; `fix_errors_at_once` now
   counts turns, not groups.
3. **A faster turn.** The debugger reads the trace first and reproduces only
   for game code the trace doesn't explain; an engine-only message is
   reported with an empty fix at once.
4. **A cause with nothing to change** is `explained`, not a failed try: it is
   not retried, and its cause is kept for the list.
5. **The list is the one place.** A console fix task (`console: true` on its
   task events) has no card of its own and leaves no transcript lines: its
   row shows a short title ("window.cpp:2138 — !is_inside_tree()"), the state,
   its current step and timer while fixing, and the cause behind a
   disclosure. Fixed, explained and triaged rows stay listed for ten minutes.

## Amendment (2026-11-18): the import cache briefly out of date

An accepted change that adds or replaces a model, texture or sound, then loads a
scene that uses it before the editor has re-imported it, prints three lines per
file ("Cannot open file 'res://.godot/imported/x.glb-<md5>.scn'", "Failed loading
resource" for that copy and for `res://…/x.glb`). They meant nothing a few seconds
later, yet each went to a debugger turn. The sidebar already held lines naming a
file still being imported, but Output starts an error line with its icon and a
thin space (U+2009) that `strip_edges()` and the regex's `\s` miss, so no Output
line was read as an error and none was held.

1. **Held while it imports** (`sidebar_debug.gd`, `file_settle.gd`). Output lines
   are read without Output's wide spaces. An error naming a copy under
   `.godot/imported`, a file a change just wrote, or (for a load failure) a file
   that has a `.import` is held while the editor scans or imports and for ten
   seconds after a change's files land.
2. **Checked again, imported again once.** Once imports settle, a held error whose
   file's imported copies are all there now is dropped. A file that still has none
   is imported again (`reimport_files`), once a minute at most, and checked after
   that import. What still fails then goes to the host in one report.
3. **One finding per file** (`console_errors.fold_imports`, in the host). The
   host reads each such line against the disk too (the game's log is never held):
   dropped when the imported copy is there, otherwise the lines about one file are
   one group with the cause in plain words: "leaf_rake.glb didn't import: Godot has
   no imported copy of it after a reimport", "… Godot couldn't read it, the file
   may be damaged" (`valid=false` in its `.import`), or "… is missing" (the file is
   gone). The raw lines are its context for the debugger.
4. **Real errors stay.** Only load failures and lines naming `.godot/imported` are
   folded; any other error naming an imported file, a load failure of a file with
   no `.import` (a genuinely missing file), or a copy no project file was made
   from is listed as it was printed. Output's "(3) " repeat count is no longer part
   of a message.

## Consequences

- With the default nothing changes for a user who never touches the setting,
  apart from the list and the extra link on the offer.
- "auto" spends agent turns without a request from the user; the limits keep
  that bounded, and nothing reaches the project until the user accepts it.
- The game's log path is the project's `user://logs/godot.log`; a project that
  turns file logging off or renames the log is read through Output only.
- GUT failures are found only where GUT prints its results as text; GUT's own
  editor panel is not read.
- The grouping key is the message and the place, so an error whose message
  carries changing values Joinery doesn't recognise (a frame count, a
  position) shows as several groups; the ignore list covers what matters.
