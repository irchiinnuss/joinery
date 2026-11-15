# Contributing to Joinery

Thanks for helping. Bug reports, fixes, tests and docs are all welcome. For a
larger change, open an issue first so we can agree on the approach.

## The one rule: creation, not generation

Joinery never uses generative AI for assets. Contributions must not add,
import, call or suggest image, 3D, audio, music, voice or texture generators,
AI upscalers, or generative APIs, not even behind a flag. Assets are made by
operating real tools (Blender, Krita, Material Maker, Surge XT, SuperCollider
and the others), and every asset needs a `joinery.lock` entry with a replayable
recipe. Do not add assets fetched from the internet. Code is written normally.
The full rule is in [CLAUDE.md](CLAUDE.md) under "Rule Zero"; CI and the policy
hook enforce it, and pull requests that weaken it will not be merged.

## Setup

You need Python 3.12, [uv](https://docs.astral.sh/uv/), Git with
[Git LFS](https://git-lfs.com/), and Docker for the offline tool workers.

```sh
git clone https://github.com/irchiinnuss/joinery.git
cd joinery
git lfs pull
uv sync --frozen
uv run joinery doctor
```

## Checks

Run these before opening a pull request:

```sh
uv run pytest -q
uv run ruff check . && uv run mypy joinery servers
uv run joinery check all
```

The default test suite needs no Docker, accounts or API keys. GDScript is
statically typed and tested with the harnesses in `tests/godot/`; see the
[testing guide](docs/testing.md) for those and for the native tool checks.

## The Godot patch series

Engine changes live in `godot_patches/patches/` as a patch series on a pinned
Godot 4.7 tag, not as a fork repository. `godot_patches/godot-src/` is a
disposable checkout and is never committed.

```sh
godot_patches/fork.sh sync     # check out the pinned tag and apply the patches
# commit or amend on the joinery-4.7 branch inside godot_patches/godot-src/
godot_patches/fork.sh export   # write those commits back to godot_patches/patches/
godot_patches/fork.sh build    # build the editor
```

Then commit the changed files in `godot_patches/patches/`. Keep patches small;
general fixes should be upstreamable to Godot. Prefer the editor add-on
(`godot_addon/joinery`) over an engine patch, and keep the add-on working on
stock Godot. Details: [godot_patches/README.md](godot_patches/README.md).

## Pull requests

- One change per pull request, in small commits with plain messages.
- Add or update tests for behaviour you change.
- Record a design decision as an ADR in `docs/adr/NNNN-title.md`.
- Enforcement changes go in the shared core so every supported agent (Claude
  Code, Codex, Gemini CLI, opencode) is covered, never one agent only.

## Licence

Joinery is AGPL-3.0-or-later; the parts copied into games and the engine patches
are MIT ([LICENSING.md](LICENSING.md)). By contributing you agree your work is
released under the licence of the part you change.

Please follow the [code of conduct](CODE_OF_CONDUCT.md).
