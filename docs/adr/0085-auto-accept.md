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
