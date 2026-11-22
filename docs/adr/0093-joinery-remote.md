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
     Cancel focused, or `joinery remote pair` asks in the terminal. The question also says
     the tailnet login the request came with ("Signed in to Tailscale as …"), or that it
     had none, since anyone can type a phone's name.
   - Settings' QR picture is written in the per-user directory (`qr/`, 0700, the file
     0600, renamed into place) and deleted as soon as it is shown; never a shared temp
     folder.
   - Five wrong codes lock pairing for 10 minutes. Pairing posts are also limited per
     peer.
   - The device token is 256 random bits, delivered once and set as an `HttpOnly; Secure;
     SameSite=Strict` cookie. Only its SHA-256 is stored, in the per-user directory and never
     in a project.
   - Scopes are watch, chat and decide. A device expires after 30 days unused and 90 in all,
     and revoking takes effect on its next request. An open stream checks every 5 s.
6. **Requests.** The server checks the `Host` header against loopback and the tailnet
   origin, which stops DNS rebinding. Changing requests need a matching `Origin` and an
   `X-Joinery-Remote: 1` header (CSRF).
   - The tailnet origin is what `tailscale serve status --json` says serves
     `127.0.0.1:<port>` at `/`: a `Web` key `"<name>:<https port>"` whose `/` handler proxies
     to the port, and not in `AllowFunnel`. It is read by `joinery remote status` (which
     Settings → Remote runs when shown), `pair` and `tailscale`, stored in `devices.json`
     when seen and cleared when no longer served, however serve was set up.
   - Settings offers "Put it on my tailnet" until then, and "Copy address" after. Pairing
     waits for it too: a QR code carries only a served address (`joinery remote pair
     --local` pairs a browser on the machine itself).
   - `tailscale serve` passes the browser's `Host` on unchanged and leaves `Origin` alone
     (see "How `tailscale serve` forwards a request" below), so the server accepts exactly
     `Host: <name>` (or `<name>:<port>` off 443) and `Origin: https://<name>[:<port>]`.
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
  last, open}]`. Projects a host served here before follow with `open: false` and
  `can_open`, which is true only under the always-on helper.
- `POST /api/projects/{id}/open` starts a closed project's headless host (chat scope). It
  returns 409 without the helper.
- `GET /api/projects/{id}/chats` returns the saved chats (id, title, provider, dates), and
  `GET /api/projects/{id}/chats/{chat}` returns a chat's last 100 turns.
- `GET /api/push` returns `{public_key, subscribed, kinds}`. `POST /api/push/subscribe`
  takes `{subscription, kinds?}` and `POST /api/push/unsubscribe` takes `{endpoint}`.
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

## The phone page

`joinery/remote/static` is plain HTML, CSS and ES modules. It loads nothing from elsewhere,
and its CSP has no `unsafe-inline`.
- It installs as a web app: a manifest, icons, and a service worker that keeps only the
  page's own files and never `/api`.
- It uses the sidebar's palette and is laid out for phones (360–430 px), in dark and light.
- Screens: pairing, projects (open, and closed ones the helper can open), the live chat and
  saved chats, and settings (notifications).
- The chat shows:
  - the steps and replies;
  - the change card with facts, the checks line, reviewer notes worst first, a before and
    after compare (dragged, or with a slider), sounds A/B and the changed lines;
  - Accept only once a change with notes has been opened;
  - the to-do with Go and Cancel;
  - task Stops and the provider offer;
  - the Rule Zero banner, read and dismissed with no retry, and the art-gate notice.
- The message box is never redrawn, so an event arriving while the user types costs
  nothing.
- After a drop the page reconnects from its last event and says when the Mac was last seen.
  A message that couldn't go stays in the box.
- Events replayed on connect are shown but never announced.
- The page's state model is tested in node (`tests/remote_page/state.test.mjs`). The page is
  tested in headless Chrome over its DevTools protocol, with no extra package
  (`tests/remote_page/browse.py`, 30 checks in dark and light, with screenshots).

## The always-on helper

`joinery remote serve --helper` is the same server given a `HeadlessLauncher`
(`joinery/remote/helper.py`, implementing the `HostLauncher` seam). It is installed as a
login item by `joinery remote install-helper` and removed by `uninstall-helper`: a
LaunchAgent on macOS, a `systemd --user` unit on Linux, a logon task on Windows. Settings →
Remote offers it as "When Joinery is closed".

- While Remote is off nothing listens: `serve` exits at once (status 0, so the login item
  doesn't start it again), a running server answers 503 and ends itself on its next look.

- When a phone opens a closed project, the helper starts the host `joinery run` uses
  (`joinery chat --keep-reviews`) with the agent of the project's last chat. That host takes
  the project's lease like any other, answers editor-only calls with "No editor is open",
  and keeps changes for review.
- The helper stops that host after 15 minutes with nothing running, nothing waiting, no
  to-do and no phone watching. It holds no lease of itself.
- **Handover** (`joinery/remote/handoff.py`): an editor opening such a project first asks
  that host to let go (channel `op: release`, which only hosts with no editor accept). The
  host lets go at once when quiet, else when its request finishes, never by stopping it.
  The editor says "A request from your phone is still running…" and waits up to 5 minutes.
  The editor's own lease wait covers the close. Changes waiting for review appear in its
  Review tab.

## Web Push

`joinery/remote/push.py` implements the `NotificationSink` seam. It is the only thing Remote
sends off the machine, and only to phones that turned notifications on.

- The browser's subscription is accepted only for HTTPS endpoints on the known push
  services (Apple, Google, Mozilla, Microsoft), with a valid P-256 key and a 16-byte secret.
  A device can't make the machine post anywhere else.
- Each push is encrypted for that phone (RFC 8291 `aes128gcm`; the test checks the RFC's
  worked example byte for byte) and signed with this machine's VAPID key (RFC 8292, ES256,
  kept in the per-user directory at 0600).
- The payload is `{kind, project, project_id, text}` with no content, for example "A change
  waits in Harbour.".
- By default only `needs_you`, `change_ready` and `error` are sent. A push service that says
  a subscription is gone (404/410) drops it.
- Nothing is pushed while the user is at that project's editor: the sidebar reports focus,
  and the host passes it on as `presence`. Nothing is shown while the page itself is in
  front.

## Keep-awake

`joinery remote keep-awake on|off` (Settings → Remote → "Keep this Mac awake") is off by
default. While on, the server holds the machine awake:
- macOS: `caffeinate -s`, which holds only on AC power;
- Linux: `systemd-inhibit`;
- Windows: `SetThreadExecutionState`.

## Consequences

- It works with Claude Code, Codex, Gemini CLI and opencode alike. The channel sits above
  `Bridge`, and the end-to-end test drives each agent id through it.
- Use away from home needs Tailscale. The machine must be awake. A phone shows the stream
  dropping and reconnects with `since`.
- A stolen device token is as strong as the sidebar within its scopes until it is revoked.
  Scopes, expiry, revocation, the audit log and the Accept-after-opening rule limit it.
  Step-up with Face ID or Touch ID (WebAuthn) for Accept is later work.
- New direct dependencies are starlette, uvicorn and sse-starlette (already resolved through
  mcp) and segno (pure-Python QR), all pinned in `uv.lock` with hashes. Web Push uses
  `cryptography`, already resolved through mcp and sigstore.
- On iOS, notifications need the page added to the Home Screen (Safari's rule).

## How `tailscale serve` forwards a request

Checked against Tailscale's source (`ipn/ipnlocal/serve.go`, the `reverseProxy` that
`tailscale serve` uses for a `Proxy` handler, and `ipn/serve.go` for the config) and the
Serve guide (https://tailscale.com/kb/1312/serve, "Identity headers"):

- `Host`: the proxy's `Rewrite` sets `r.Out.Host = r.In.Host` for a TCP backend, so the
  server sees the name the phone opened (`mac.tail1234.ts.net`, with `:<port>` only off
  443). Only a unix-socket backend gets the backend's own host.
- `Origin`: not read or changed; the browser's `https://<name>` arrives as sent.
- Added: `X-Forwarded-Host` (the incoming Host), `X-Forwarded-Proto: https`, and
  `X-Forwarded-For` (the peer's tailnet address). Remote doesn't rely on them; uvicorn runs
  with `proxy_headers=False`.
- Identity: `Tailscale-User-Login`, `-Name`, `-Profile-Pic`, `Tailscale-Headers-Info` and
  `Tailscale-Funnel-Request` are always deleted from the incoming request first, so a peer
  can't spoof them through serve. The identity ones are then set for a signed-in user's
  device, and not for a tagged device or a request from outside the tailnet.
- Funnel: a request that came through Funnel instead gets `Tailscale-Funnel-Request: ?1`
  and no identity headers.
- The connection to the server comes from loopback either way, so a local process can send
  any of these headers itself; they say who a tailnet request is from, never that it is
  allowed.

## Not verified

- A real `tailscale serve` against this server (the forwarding above is from the source and
  docs; the tests use a stand-in `tailscale` program).
- Claude Code Remote Control against a `-p` stream-json session. It is turned off either way.
- A real push through Apple's, Google's or Mozilla's service, a real `tailscale serve`, and
  loading the login item: none was run on the development machine. The encryption, the
  signature, the subscription checks and the login item files are tested.
