# 0091. A native Godot check worker, and checks that always answer

Status: accepted (2026-11-16)

## Context

Every offline worker image was linux/amd64 (ADR 0008): Blender, Material Maker
and Surge XT publish no Linux arm64 build, and the images were one chain built
from a runtime that carries them all. On Apple Silicon every check therefore ran
emulated, including the ones that need nothing but Godot: Godot's import of a
change and the project's GUT and SceneTree tests.

On a real project (14 SceneTree tests) one request spent 20+ minutes in the
checks. Thirteen tests finished in under a minute; one crashed Godot, and the
emulated crash handler hung instead of exiting, so the test waited out its
240 s limit twice. The agent's tool waits 900 s for an answer; the precheck
ran past it, and the time-out reached the agent as an empty error
(`TimeoutError` is an `OSError` with no text), so it ran the same precheck
again.

## Decision

- **A Godot-only check worker** (`joinery-godot-worker`, `sandbox/godot.Dockerfile`)
  is built for the host's own architecture: linux/arm64 on Apple Silicon,
  linux/amd64 elsewhere (and in CI). It carries Godot and the system fonts the
  tool workers have, nothing else. The checks (import, GUT and SceneTree tests)
  run in it when it is present and the project's import reads no `.blend`
  outside a `.gdignore` folder; otherwise they run in the creative worker as
  before, so nothing depends on it existing.
- **The other images stay amd64.** Checked on 2026-11-16: Blender 5.2.1,
  Material Maker 1.8 and Surge XT 1.3.4 have no Linux arm64 download. Krita,
  SuperCollider, Inkscape, GIMP and Audacity are Debian packages with arm64
  builds, but they share the runtime with the three that have none, so the
  chain stays one platform until those publish arm64 builds.
- **Runs follow the image present** (`joinery.worker_platform`): every
  `docker run` asks for the platform of the local image, and only a build picks
  one from the host. An image built before this, or elsewhere, runs as what it
  is; an image of the wrong platform for its build target counts as outdated.
  When setup rebuilds the Godot worker for the host's platform, it removes the
  image it replaced (an amd64 one on Apple Silicon), only after the new one
  starts offline and its Godot answers, and never by force. That is the only
  image setup removes: the amd64 tool images are still the only ones with
  Blender, Material Maker and Surge XT, so they stay.
- **Pins stay pinned.** The arm64 Godot download is hash-checked like every
  other; its hash lives in `checksums-native.txt` (written only by
  `fetch-checksums.sh`, cross-checked against Godot's own SHA512-SUMS) so
  pinning it does not change `checksums.txt`, which every tool image copies and
  would otherwise all rebuild. `--network none`, the unprivileged user and the
  dependency denylist are unchanged.
- **A check always answers.** One check (precheck or submit) has a budget,
  720 s by default (`[checks] check_seconds`, never past 840 s), below the
  tool's 900 s wait. Tests still running at the deadline are stopped and tests
  not started are skipped; both are named in one problem that says they are not
  the change's failure, and neither is cached, so the next check carries on
  with only those. A crash is stopped once nothing new is printed and never run
  twice. No tool error is ever blank, and a check the tool gave up on says what
  was running and not to repeat it. A precheck of an unchanged stage returns
  the last answer instead of running again.

## Consequences

On an arm64 host the checks run natively once `joinery setup` has built the
Godot worker (`docker build --platform linux/arm64 -f sandbox/godot.Dockerfile`
with the Godot pins as build arguments; about a minute, about 110 MB).
Projects that import `.blend` files directly keep the emulated path. Scene-test
results are cached by content, not by platform: a test whose result differs
between amd64 and arm64 is a bug in the test, and it is said as soon as its
inputs change.
