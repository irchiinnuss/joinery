# 0088. Keep and Avoid lines, feature marks, and /cut

Status: accepted (2026-11-15)

## Context

The brief says what the game is, but nothing in it was tied to what the game
does. A user who loved how the jump felt had no way to say "leave that alone"
that a later change would respect, and nothing told them which promised
features were really built. Near the end of a project the findings that say
what is left (design check, playtest, accessibility, performance, console
errors, tidy) sat on separate cards, and nothing turned them into one list or
helped decide what to drop.

## Decision

**Keep and Avoid.** The brief template gains `## Keep` (what must stay true)
and `## Avoid` (what the game must not have), parsed by `joinery.brief`. A
number at the end of a Keep line pins a value ("Jump height 1.2 m"); brackets
can name where it lives (`res://path:property` or a project setting). Words in
brackets on an Avoid line are what to look for; otherwise every content word of
the line must appear.

At submit, `joinery.keep_avoid.check` compares the staged change with the
project as it was. A pinned value is found where the line names it, else by
name (an exported or plain `var`/`const`, a scene or resource property, a
project setting), else by simple matching (a name holding every word, or the
last words in a file whose path has the first). A change that moves it away
from the pin is flagged. An Avoid line is looked for in what the change adds
(added lines, new file names) and in the agent's own summary. A flag never
fails or sends back the change: it is a `warn` review note naming the line, a
`keep_avoid` list on the preview, a note to the agent to ask the user, and a
held reason for automatic accept (ADR 0085). It lives in the shared turn, so
every agent is judged the same.

Keep, Avoid and Features join the brief's direction sections: editing them is
a brief change that always waits for the user. Journeyman's instructions say to
respect them and ask before crossing one.

**Feature marks.** Each line of `## Features`, and each milestone whose "Done
when" is written, may end with what checks it: `(reach: X)`, `(no errors: N s)`,
`(test: res://…)` or `(replay: name)`. `joinery.brief_status` marks each one
built, broken, missing or not checked. Reach and no-errors are the done-when
checker's own (ADR 0060): the same seeded run and the same evidence, pass is
built, fail is broken, a place nothing is named like is missing. Tests run
headless; replays play back (ADR 0059). A line with nothing to check, or whose
check was inconclusive, is not checked, with why. Marks are kept in
`.joinery/brief-status.json` by line; a newer playtest run that covers a line
replaces its mark. `joinery brief --status [--check]` and the `/brief` page
show them with when and what evidence; **Check now** gathers fresh evidence.

**/cut.** `joinery cut` and the `/cut` card gather every source above into one
polish list, ordered by priority, each row naming its source. They propose
cuts with a reason (broken, scope: promised but missing, risk: not built and
named by a high-priority finding) and never one a Keep line covers (they share
a content word). Only for `goal = "sell"` in the brief's facts is there a ship
estimate: a freeze at 70% of the days to `target_date`. Writing makes one
reviewed change: `design/polish.md` and the chosen cuts moved to the brief's
"Out of scope" with the day and the reason. It deletes nothing.

## Consequences

- Name matching can miss a value (a pin under an unrelated name) or catch the
  wrong one; the bracket target is the exact form, and a flag only asks.
- Avoid matching is words, not meaning, so it can flag a harmless use; it holds
  an automatic accept, which costs one click.
- Marks are only as fresh as their evidence; the time is always shown.
- Removing built content after a cut stays a separate request and accept.
