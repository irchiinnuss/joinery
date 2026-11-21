# 0093. Remote: follow and direct Journeyman from a phone

Status: accepted (2026-11-21)

## Context

Users want to keep a session going away from the desk: watch the steps, send the next
request, press Go, look at the before and after, then Accept or Discard. Claude Code Remote
Control and Codex's remote do this too. Both keep the session on the user's machine and make
the phone a window onto it. Both pair by QR code, use per-device credentials that can be
revoked, and go through the vendor's relay.

Joinery has no vendor and no service. Collaboration is git only (ADR 0071) and Joinery holds
no accounts (ADR 0006). One chat host owns a project (`ProjectLease`). Where the vendor
remotes apply at all, they show the agent's session rather than Joinery's review, and they
cover two of the four agents.

## Decision

1. **The user's machine serves it.** `joinery remote serve` is a per-user server and the only
   piece facing the network. It binds to 127.0.0.1 only. `tailscale serve --bg --https=443
   http://127.0.0.1:<port>` carries it to the phone with a real certificate, over WireGuard,
   with no internet port opened (`joinery remote tailscale` prints that command, or runs it
   when asked). There is no relay, no Joinery account and no LAN mode.
2. **Attach, never duplicate.** While Remote is on (`devices.json` `config.enabled`, checked
   every 3 s), each chat host opens a local channel (`bridge/remote_channel.py`). The channel
   uses the `gateway_rpc` pattern: a unix socket in a fresh 0700 directory, or on Windows a
   loopback port. Either way a 32-byte token is required. The host advertises the channel in
   `<remote home>/hosts/<project id>.json` (0600). The server only attaches to hosts that run.
3. **One input loop.** A device's action joins the host's own loop (`Inputs`: the editor's
   lines first, then the channel's). The loop is `bridge/stdio.py`'s `while` loop, so the
   dispatch code is the editor's.
   - With an editor attached, the actions the sidebar owns go to the sidebar as
     `remote_action`: send, Stop, Go and Cancel on a to-do, and Accept and Discard on the
     change in its own review. `remote_relay.gd` does each one through the sidebar's own
     handlers and answers `remote_ack`.
   - Everything else is dispatched by the host, as is everything when no editor is attached.
   - The device's answer goes to the device. A refusal is not a warning in the sidebar.
4. **Closed action set** (`joinery/remote/actions.py`), checked by the server and again by
   the host before dispatch. Only the listed fields pass, with their types and sizes checked.

   | Action | Host kind | Scope | Route |
   |---|---|---|---|
   | send in the open chat | `prompt` {text, chat_id?} | chat | relay |
   | send to another chat | `chat_task` {text, chat_id?, ref?} | chat | direct |
   | Stop, Stop all, Stop a task | `cancel` {all?, task_id?} | chat | relay (task: direct) |
   | Stop this chat's tasks | `cancel_tasks` | chat | direct |
   | Go / Cancel on a to-do | `todo_go` / `todo_cancel` {todo_id} | chat | relay |
   | Accept / Discard | `decision` {transaction_id, accept} | decide | relay for the sidebar's review, else direct |
   | continue with another agent after a limit | `provider_switch` {agent?} | chat | direct |
   | read the art direction | `art_review` | watch | reply to the device only |

   Everything else is refused with "Not available remotely. Use the editor." and audited.
   Editor-only kinds say where to do them.
5. **Pairing.** The machine makes a 10-character code (no I, L, O, U, 0 or 1; about 49
   bits). It is single use, lasts 5 minutes, and is shown as a QR code of
   `<origin>/#pair=<code>`. The code sits in the URL fragment, so it never reaches a log.
   - The phone posts the code with its name. The device exists only after the machine
     answers: Settings → Remote asks "Pair “Ana's iPhone” (iOS)? It can …" in place with
     Cancel focused, or `joinery remote pair` asks in the terminal.
   - Five wrong codes lock pairing for 10 minutes. Pairing posts are also limited per
     peer.
   - The device token is 256 random bits, delivered once and set as an `HttpOnly; Secure;
     SameSite=Strict` cookie. Only its SHA-256 is stored, in the per-user directory and never
     in a project.
   - Scopes are watch, chat and decide. A device expires after 30 days unused and 90 in all,
     and revoking takes effect on its next request. An open stream checks every 5 s.
6. **Requests.** The server checks the `Host` header against loopback and the configured
   tailnet origin, which stops DNS rebinding. Changing requests need a matching `Origin` and
   an `X-Joinery-Remote: 1` header (CSRF).
   - Every answer carries a CSP of `default-src 'self'`, `frame-ancestors 'none'`,
     `no-referrer`, `nosniff` and `no-store`.
   - Accept is refused (409) for a change with something to read until the device has opened
     it (`actions.needs_opening`): a reviewer's warn or block, a failed check, a crossed
     Keep/Avoid line, an unmet "done when", an art gate, or a departure from the request.
7. **No paths.** Events reach devices without the editor's bookkeeping (`editor_action`,
   `editor_view`, `remote_action`, `quiet`), and with every absolute path made
   project-relative or dropped.
   - Pictures and sounds are served only as `media/<transaction>/<index>/<before|after>`. The
     path is resolved from the host's own `file_preview` or `review_media` items, inside
     `.joinery/transactions/<transaction>/`, with no symlink anywhere along it, and only for
     images and audio. The host and the server both check it.
   - There is no file browser, no shell and no settings endpoint.
8. **Editor-only.** Art bible and brief approval, auto-accept and every other setting,
   pairing, tool setup, and anything that answers the editor (`editor_result`, `mixer_save`,
   `cut_write`, `tidy_remove`, `shutdown`).
9. **Record.** Every action, refusal, pairing and revoke goes to `<remote home>/audit.jsonl`,
   and project actions also to `.joinery/remote/audit.jsonl`. Message bodies are not logged.
   - A decision made from a device carries the device name. It lands in the transaction's
     history (`from`) and in a `Decided-From:` git trailer on the Timeline entry.
   - The sidebar's transcript says "Sent from …", "Accepted from …" or "Stopped from …".
10. **Vendor remotes stay off** for the sessions Joinery starts. Otherwise a phone on the
    vendor's app could steer the agent around the sidebar's review.
    - Claude Code: `disableRemoteControl: true` and `remoteControlAtStartup: false` in the
      session's `--settings` (Joinery already sets `DISABLE_TELEMETRY`, which also stops
      auto-connect).
    - Codex: `CODEX_INTERNAL_APP_SERVER_REMOTE_CONTROL_DISABLED=1` for the app-server, which
      the Codex daemon sets itself for a disabled one, and `-c features.remote_control=false`
      for older versions that had it as a feature. Codex 0.167 lists that feature as
      removed and ignores the flag.
    - Gemini CLI and opencode: no remote control of their own is known for the ACP sessions
      Joinery runs, so nothing is set.
11. **Policy unchanged.** A device reaches no tool, every agent call still passes `joinery
    hook`, and a Rule Zero stop reaches every device as the sidebar shows it. Remote adds no
    MCP server, tool or provider flag.

## Spike findings

- A second input source can feed the host's loop without touching its dispatch. The editor's
  pipe and the channel are merged per line, so lines cannot interleave inside one another.
  Remote turning on while a read already waits is noticed (`Inputs.channel`); turning it off
  ends every stream.
- Most sidebar cards cope poorly with a turn or decision they did not start (`chat_bridge.gd`,
  `journeyman_dock.gd`, `plugin.gd`):
  - a turn the sidebar didn't send leaves it not busy: no Stop, steps and trail dropped, and a
    review opening on the previous card;
  - a Discard from elsewhere leaves the live scene preview on screen and never sends
    `live_decision`;
  - an Accept from elsewhere skips the unsaved-scene and stale-preview checks.

  A plain `file_decision` alone does settle the card correctly. That is why, with an editor
  attached, the sidebar carries out what it owns, as if clicked. A headless host
  (`joinery run`) has no sidebar state, so it dispatches everything itself.

## API for the phone page

All routes are on the server:

- `POST /api/pair` with `{code, name, platform?}` returns `{request_id, secret}`. Poll
  `POST /api/pair/status` with `{request_id, secret}`, which returns `waiting`, `denied`,
  `expired` or `allowed`. `allowed` sets the cookie and returns `token` once.
- `GET /api/me` returns the device (scopes, expiry, last seen).
- `GET /api/projects` returns `[{id, name, agent, editor, started, working, waiting, todo,
  last}]`.
- `GET /api/projects/{id}/chats` returns the saved chats (id, title, provider, dates).
- `GET /api/projects/{id}/events?since=N&started=T` is a Server-Sent Events stream:
  - `hello` gives `{seq, oldest, gap, editor, started}`;
  - `event` has `id` = seq and data = one host event (`message_delta`, `message`,
    `progress`, `trail`, `todo`, `file_preview`, `review_media`, `file_decision`, `done`,
    `art_gate`, `provider_switch`, …);
  - `notice` carries a notification (below), and `gone` says the stream ended (revoked or
    host closed);
  - send `since` with the host's `started` to resume. A different `started` (a new host)
    replays from its start.
- `POST /api/projects/{id}/actions` takes `{kind, …}` and returns `{ok, text, event?}`.
  Refusals return 403, Accept before opening 409, and a host that can't be reached 502.
- `GET /api/projects/{id}/changes/{tx}` returns the review with `needs_opening` and marks it
  opened.
- `GET /api/projects/{id}/media/{tx}/{index}/{before|after}` returns the picture or sound.

## Seams for what comes next

- `joinery.remote.seams.HostLauncher.ensure_host(project)` is for the always-on helper. It
  starts the headless host (`joinery chat --keep-reviews`, as `joinery run` does) for a
  project with no host advertised, taking the project's lease like any host. The server's
  default `NoLauncher` reaches only projects open somewhere. The helper's server is
  constructed with its launcher (`RemoteServer(launcher=…)`). An editor that later opens the
  project asks the headless host to release it, once quiet.
- `joinery.remote.seams.NotificationSink.notify(Notice)` is for Web Push. The server follows
  every advertised host, whether or not a phone is connected, and turns its events into
  notices with `notice_for`:
  - `finished` from `done` ok;
  - `change_ready` from `file_preview`;
  - `needs_you` from a to-do waiting for Go, a provider switch, an art gate, or a Rule Zero
    stop;
  - `error` from a warning or a lost connection.

  A notice's text carries no content ("A change waits in Harbour."). Sinks are passed to
  `RemoteServer(sinks=…)`. The same notices already reach open pages as `notice` events.
- Keep-awake while plugged in is an option for a later stage, default off.

## Consequences

- It works with Claude Code, Codex, Gemini CLI and opencode alike. The channel sits above
  `Bridge`, and the end-to-end test drives each agent id through it.
- Use away from home needs Tailscale. The machine must be awake. A phone shows the stream
  dropping and reconnects with `since`.
- A stolen device token is as strong as the sidebar within its scopes until it is revoked.
  Scopes, expiry, revocation, the audit log and the Accept-after-opening rule limit it.
  Step-up with Face ID or Touch ID (WebAuthn) for Accept is later work.
- New direct dependencies are starlette, uvicorn and sse-starlette (already resolved through
  mcp) and segno (pure-Python QR), all pinned in `uv.lock` with hashes.

## Not verified

- Whether `tailscale serve` passes the original `Host` header. Both the tailnet name (once
  configured) and loopback are accepted. `Tailscale-User-Login` is only logged as the peer,
  and only when the request came from loopback.
- Claude Code Remote Control against a `-p` stream-json session. It is turned off either way.
