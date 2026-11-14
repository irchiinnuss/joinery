# 0085. Accepting changes automatically

Status: accepted (2026-11-13)

## Context

Every change an agent submits waits in the Review tab and on the request's
card until the user accepts or discards it. For many changes that is the
point. For others (a script fix that passed every check, a parallel task's
small edit) the click is a formality, and a user directing several agents
spends their time pressing Accept. Users asked for a way to skip it.

Two things must not move. The art direction is approved by the user alone
(the art gate on asset work depends on it, and agents never approve it), and
the transaction stays the unit of work: snapshot, apply, validate, decide,
commit or restore.

## Decision

A per-project opt-in, off by default: `[agents] auto_accept = true` in
`joinery.toml`, set from Settings → Replies → **Accept changes
automatically** or `joinery auto-accept --project P --set on|off [--json]`.

When it is on, a submitted change that reaches review is kept without asking
only when nothing about it needs the user (`joinery.auto_accept.held`, applied
by `TurnController` after the review opens):

- it has file changes, and none of them is art direction or approval state:
  `art/bible.md`, `design/brief.md`, `art/reference/`, `.joinery/`;
- the submit has no notes for the user (placement and the like), and no art
  gate rides on it;
- the reviewer agent, when on, finished and raised no `warn` or `block` note
  (a review the router skipped counts as finished);
- every done-when item of the request's to-do passed (inconclusive waits);
- no scene it changes, through its files or live, had unsaved edits in the
  editor when the request was sent (the user's own work is never written
  over or saved for them), and no live edit is in a scene never saved;
- a parallel task's change has no live scene edits;
- it is not Joinery's own submit of a change the agent left unsubmitted;
- it is not one of several ways (Try other ways): the user picks among them.

Failed, final and needs-user submits never reach review, so they are not
candidates at all.

Live scene edits are kept by the editor alone: only it holds them. When the
chat's own change would be kept but has live edits, the core accepts nothing
itself. It sends the review with an `auto_accept: true` hint (and an empty
`auto_accept_held` list) on the `file_preview` and the `in_review` turn state,
and the editor's review flow runs its own Accept, the code the Accept button
runs, without opening the Review panel: the files first (a `decision` marked
`auto`, which the host records as `auto_accepted`), then the live edits into
the scene's undo history. A preview that went stale is refused as on a click:
the files are rolled back and the review opens. Undo takes the live edits back
through that undo step (while nothing was done after it), then reverts the
files.

The accept itself is the host's own Accept path (`auto_decide`): the chat's
change goes through `TurnController.decide` and the stdio host's follow-ups
(after-decision playtests and checks, error settling, variant bookkeeping);
a task's through `Crew.decide`. So the commit, the Timeline entry, history,
lock entries and metrics are the same as for a click. The transaction journal
records `auto_accepted`, the decision span carries `joinery.decision.auto`,
and the `file_decision` event says `auto_accepted` with the change's summary.
If the accept fails (the project moved under it), the change opens for review
as usual.

The agent's `submit_for_review` returns `status: "accepted"` with
`auto_accepted: true`; it says what it made and that it was kept, and never
asks the user to accept it.

In the editor no review opens. The request's card shows a quiet outcome line,
**Accepted automatically**, in the muted tint with one action, **Undo**,
which is the same revert any accepted change has (`joinery transactions
revert`). Saved chats remember it.

The rule lives in the shared core (`joinery/auto_accept.py`, `joinery/turn.py`),
not in the editor, so Claude Code, Codex, Gemini CLI, opencode and every
parallel task are judged the same way.

## Why the art direction is excluded

Approving the art bible unlocks asset work for the project; that approval is
the user's own act, recorded by content hash (`joinery.art_approval`). A
change that touches the bible is a draft the user reads before approving, and
accepting it is already a separate click from approving it. Keeping such a
change automatically would put an agent's art direction into the project
without the user having looked at it, which is the opposite of the rule that
agents never approve. The game brief is treated the same way because teammates
re-approve it like the bible (`team.SHARED_DOCUMENTS`), and the art references
and Joinery's own state sit beside them.

## Consequences

- Off by default; nothing changes for a project that doesn't opt in.
- Undo is one click on the card and Revert stays in the Review tab's Decided
  list, so a change kept by mistake is as easy to put back as one accepted by
  hand.
- A change with anything to say still waits, so turning this on never hides a
  warning, a failed done-when item or a reviewer's note.
- The before/after renders of a kept change are not made: nobody reviews them.

## Amendment (2026-11-14): what holds a change

In a real session auto-accept fired once in about fourteen changes. Four
things held nearly every change, none of which needed the user: a reviewer
`warn` on almost every change, done-when `test` items that were always
inconclusive, any edit of `design/brief.md`, and a to-do waiting for a Go
nobody could press. The rule now reads as follows.

**Reviewer levels.** The reviewer reports three levels:

- `block`: the change is wrong and its author must fix it (it doesn't do what
  was asked, breaks the art bible, the brief or the game, or loses work). It
  goes back to the author as before; one still left when the review opens
  always holds the change.
- `warn`: a real risk the user should decide on before keeping it. It holds
  the change when the project sets `[agents] auto_accept_hold = "warn"`, and
  always when its rule is `broken` or `work_lost`. With the default,
  `auto_accept_hold = "block"`, any other warning is shown with the kept change.
- `info` (a note): style, polish, suggestions and things not made yet ("no
  Krita texture yet", "the HUD sits near the screen edge"). It never holds a
  change and is shown with it.

The reviewer's prompt and the `report_review` tool describe these levels and
ask for a rule on every `block` and `warn`. The hold level is set with
`joinery auto-accept --hold-on block|warn` or the **Hold reviewer warnings**
switch under **Accept changes automatically** in Settings, which shows only
while auto-accept is on. A kept change's `file_decision` carries the notes
that didn't hold it (`notes`), and its card lists them under **Accepted
automatically**, so turning auto-accept on still hides nothing.

**Done-when tests.** The project's tests are GUT tests when the project has
GUT, and SceneTree tests either way: a script under `tests/` (or `test/`)
that `extends SceneTree`, checks in `_initialize()`, and calls `quit(0)` on a
pass or prints `FAIL: <why>` and calls `quit(1)`. Joinery's tests check runs
each one headless after the import (`godot -s`, up to 20, two minutes each)
and fails on a non-zero exit, a `FAIL` line, a script or parse error, or a
script that never quits. Engine lines that aren't the test's own (`ERROR:` from
a missing XR runtime) don't fail it. A done-when `test` item whose target is
such a test, or a GUT test, passes when the tests check ran and passed.
Journeyman's instructions and `propose_todo` describe this one convention. A
`look` item still holds the change (only the user can judge it), and
Journeyman is told so, so it prefers a checkable item.

**The game brief.** The art bible, its references and `.joinery/` still
always hold a change. The brief holds it only when its direction changes:
the title, Pitch, Genre and references, Audience and content, Player fantasy,
Core loop, Monetisation, or Scope and milestones sections, or the facts
`genre`, `dimension`, `online`, `max_players`, `monetisation` and
`rating_target` (`brief.direction_changes`); adding or removing the brief
counts too. Routine edits (controls, verbs, camera, saving, open questions,
a renamed action) are kept, with a note naming the sections edited. The brief
was excluded with the bible because teammates re-approve it; that approval
covers what the game is, which is exactly the direction checked here.

**The to-do's Go.** `[agents] todo_go` gains `"auto"`, now the default: it
waits for Go on two steps or more only while someone can press it. With
auto-accept on, or in `joinery run` (the host's `headless`), the agent carries
on with its to-do and the done-when items are checked as usual. An explicit
`"multi"`, `"always"` or `"never"` is the project's own choice and holds either
way.

Everything else in the rule is unchanged: failed or inconclusive done-when
items, notes on the submit, art gates, unsaved scenes, live edits a task
can't keep, Joinery's own last-chance submit and variants all still hold.
