# 0090. Fresh sessions from a digest, plan then build, and a model per step

Status: accepted (2026-11-16)

## Context

Every model call of a turn re-sends the whole session. In Ironworks run 3 (48 M
tokens, 95% cache reads) a fresh request started at about 24 k tokens, but a request
sent into an earlier chat started where that chat ended, at 110-350 k, and paid it on
every call: Music cost 3.5 M before it did anything big. The blockout cost 8.6 M over
a plan, a fix after failed tests and a follow-up, each re-sending the reads of the one
before. A turn that stopped (a stalled Docker worker, a usage limit, an idle stop) was
carried on by re-sending the stopped session at its full size. And every step ran on
the chat's model, including fixes that only read a test report and change a line.

ADR 0061 routes the reviewer, the session summary and the check wording to a light
model. ADR 0080 continues a chat on another provider from Joinery's own record. Both
pieces exist; this decision applies them to the request itself.

## Decision

1. **A digest instead of the old conversation** (`joinery.request_digest`). At most
   3000 characters, built from Joinery's own records: the game in one line (the
   brief), the chat's last three turns (request, reply, what became of each change),
   what a stopped turn left, the changes waiting for review, what changed since the
   chat's last turn (`chats.since_digest`), and the latest Timeline entries. Context,
   not instructions; the agent reads anything else from the project.
2. **When a request starts fresh** (`request_digest.starts_fresh`, used by
   `bridge.chat_record.ChatRecorder`, the same for every agent):
   - a new chat's first prompt carries the digest;
   - a session holding at least `[agents] fresh_at` tokens (default 60 000; 0 keeps
     every session, as before) is not continued for a new request: a fresh provider
     session starts from the digest. A small session is continued; replaying it
     costs less than the digest saves;
   - a follow-up keeps its session: `joinery run --chat` marks one (the prompt's
     `follow_up`). Without `--chat` a run starts fresh from the digest;
   - a turn that stopped is never replayed at size: the next request in that chat,
     follow-up or not, starts from the digest, which says what the stopped turn left
     in review;
   - Go on a to-do always starts fresh: the to-do is the plan.
   The saved chat records its session's size (`context`) so a reopened chat is judged
   the same way. The sidebar sees a chat event with mode `fresh` and no notice.
3. **A fix after failed checks in a big session starts fresh.** When the turn's
   session holds `fresh_at` tokens or more, the one fix Joinery asks for starts a
   fresh session from the digest, the request, the files staged so far (each new,
   changed or removed, as on a provider switch, ADR 0080) and the problems. The
   staged change belongs to the gateway, so nothing is lost.
4. **Plan, then build.** A big request (a blockout, or a to-do of three steps or more,
   `done_when.splits`) that doesn't wait for the user's Go is planned in its session:
   `propose_todo` answers `build_next` and the agent ends its prompt. Joinery then
   starts a fresh session from the digest and the plan (`done_when.build_prompt`),
   keeping the request's attachments (the sketch). `[agents] split_requests =
   "never"` builds where it was planned. The blockout request now asks for the plan
   first. Spans carry `joinery.request.split`.
5. **A model per step** (`joinery.routing.step_tier`, `step_model`). The main model
   plans and makes: the request and the build of a plan. With `[agents]
   light_model` set, two kinds of step run on it:
   - the fix after Joinery's checks when every failure is fixed in code or settings
     (`ROUTINE_CHECKS`: tests, Godot import, references, dependencies, saves,
     replays); any other check (the look, provenance, models, sound) means making an
     asset again and stays on the main model;
   - a to-do step run as a parallel task that reads as a small edit (set, rename,
     wire, tune …) and names nothing to make (model, texture, sound, level …).
   A light fix never decides alone: when it fails the checks again, the main model
   gets one more fix. The model is the prompt's `selection.model`, which every
   provider already takes per prompt, so routing is the same for Claude Code, Codex,
   Gemini CLI and opencode. Spans carry `joinery.model.tier = "light"`.
6. **Shorter tool results** (`joinery.short_results`, applied by `compact.compact`
   before the even cut). By shape, not by tool: a check report counts its staged
   paths by kind and folds alike problems (same check and rule) into the first, each
   cut to its first line; an operation drops the records of steps nested in it and
   keeps their files; models read one line per mesh; a blockout lists only the goals
   not reached; the setup counts each unit's files. The full result is kept for
   `details` even when it was under the budget.

## Measurement

`evals/request_size.py` replays every model call of a recorded run's transcripts
under each cut (offline; `evals/results/request-size.md`). Ironworks run 3: 47.99 M
before; fresh −15.8 M, resume −5.2 M, split −17.6 M, trim −0.5 M; all together
24.93 M (−48%), largest turn 2.98 M, the blockout's agent turns 8.57 → 3.18 M. The
light model takes 1.06 M of fix work off the main model.

## Consequences

- A request no longer pays for the chat before it. What it loses is the earlier
  conversation's detail; the digest keeps the outcomes, what waits for review and
  what changed, and the agent reads the rest from the project.
- A follow-up still has its session when the user says so, and when the session is
  small.
- A fresh fix or build may re-read a file the earlier session had read. The
  measurement keeps the recorded calls, so it does not count that; it is a small
  share of what each fresh start saves.
- The light model changes cost, not tokens, and only when the project names one.
- Enforcement is unchanged: every fresh session is bound to the same gateway, stage
  and `joinery hook` gate (ADR 0006).
