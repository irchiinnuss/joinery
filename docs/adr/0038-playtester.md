# 0038. Playtester

Status: accepted (2026-11-11)

## Context

Every blueprint has scripted play tests (`tests/game/play_test.gd` on the PlayHarness), and
they prove what their author thought to check. Nobody plays the game the way a curious first
player does: walking into walls, jumping off edges, pausing at odd moments, saving mid-fall.
Users asked Journeyman to "try it and tell me what's broken", and the agent could only read
code. Custom games made from the blank blueprint have no play tests at all.

## Decision

1. **A playtest engine that needs no model.** `joinery playtest --project P [--minutes N]
   [--seed S] [--scene res://…] [--json]` (`joinery/playtest.py`) runs the game in stock Godot
   4.7, headless and offline, with a bot (`joinery/playtest_bot/bot.gd`, loaded by `--script`
   from Joinery's own folder, never copied into the game):
   - **It plays through the game's input map** (`bot_drive.gd`): camera-relative
     `move_forward/back/left/right` for 3D, `move_up/down/left/right` for top-down,
     `move_left/right` and `jump` for platformers, clicking visible buttons for a game
     without a player, and random presses of every action and clicks for anything else.
     It heads for the nodes a blueprint puts in goal groups (`coin`, `pickup`,
     `checkpoint`, `goal`, `interactable`, `pressure_plate`, …), taps interact when it gets
     there, turns and jumps when stuck, and never clicks buttons that leave the game.
   - **It uses the game core where there is one**: it boots like the PlayHarness
     (`Game.autostart`), listens to `GameEvents` for levels, scores, checkpoints and world
     changes, carries on after a level ends, and checks pause and resume. With the
     save-load feature it saves, plays on, loads and compares every `saveable` node, and
     a second after a level ends it saves on the result screen: the game must refuse, or
     the save must load into the result again or a game the player can go on with. With
     translations it checks the pause menu's text in every language and in the
     pseudo-locale.
   - **It watches** (`bot_watch.gd`, `bot_logger.gd`): every error and warning through a
     Godot `Logger`, with its script line and the frame; frame times; memory, node and
     orphan counts; a player whose position becomes NaN, who is flung, who falls out of
     the world and is not brought back, or who respawns; and softlocks: the game paused with
     nothing that unpauses it, the player unable to move while the bot holds movement keys,
     the player trapped in one spot, or nothing responding to input. After a softlock it
     restarts the level so the run goes on. A game with nothing to play yet (blank) is not
     stuck.
   - **Deterministic**: the bot's choices come from one seeded generator and the game runs at
     a fixed 60 physics frames a second, so `--seed` replays a run frame for frame. Headless
     runs much faster than real time (two minutes of a blueprint take about a second).
   - **Never writes in the game**: the report, console log and screenshots go to
     `.joinery/playtests/<run>/` (excluded from the Timeline and from Godot's import, last
     ten kept), and the run gets a HOME of its own, so the game's `user://` saves and
     settings never touch the player's. A project without an import cache is imported first.
2. **One report, judged in Python** (`joinery/playtest_report.py`). The bot reports facts;
   Python merges them with Godot's console (how the process ended, errors before the bot
   listened) and gives each item a severity: `critical` (crash, freeze, never started),
   `high` (script error, softlock, fell out of the world, physics, broken save/load),
   `medium` (engine error, frame hitches, growing memory, pause doing nothing, the game
   quitting itself), `low` (warnings, text that doesn't fit) and `note` (balance). Each item
   has the script line where there is one and repro steps: the exact command with the seed
   and when to watch. Balance notes come from timings and deaths: a level finished in under
   20 s by a random bot may be too easy; dying three or more times a minute without
   finishing may be too hard; a minute with no score, checkpoint or change suggests goals
   that are hard to find. They are hints, and say so.
3. **Screenshots from a replay.** Headless Godot draws nothing, so pictures come from a
   second run of the same seed in a window, which saves the frames of the notable moments
   the headless run found (findings, level ends, start and end) at 480 px wide. It runs in
   the Joinery editor binary with `JOINERY_BACKGROUND=1` (ADR 0032), so the window never
   takes focus, and is skipped where nothing can draw. Godot's movie writer would write
   every frame; the bot saves only the frames it needs.
4. **The `playtest` tool.** `playtest(minutes?, seed?, focus?)` is one read-only tool on the
   `joinery-registry` proxy, backed by the gateway RPC method `playtest`, the same for
   Codex, Claude Code, Gemini CLI and opencode, on the lead's, every task's and the
   playtester's endpoint, and on the lead's and tasks' only when the user asked (section 9).
   It returns the report trimmed for an agent and up to three
   screenshots as images (context, never assets). One playtest runs at a time.
5. **The playtester role** (`bridge/playtester.py`, `playtester` in `joinery/roles.py`,
   ADR 0037). `/playtest [focus]` in the sidebar sends `playtest_start`; the host starts the
   chat's provider in the read-only `playtester` role, judged by the same role tables in the
   policy hook (`JOINERY_AGENT_ROLE`) and at its gateway endpoint: it may read, look at the
   editor and call `playtest`, and nothing that changes the project, starts tasks or reports
   as another role. Its prompt is `PLAYTEST_PROMPT`: call
   `playtest` once, read the report and screenshots, write a short report of bugs, softlocks
   and balance notes with severities and repro steps, and end with up to three `Fix:` lines,
   each a request Journeyman can act on alone. The host plays anyway if the agent never
   called the tool, so the card always has facts. `[agents] playtest_after_change = true`
   starts the playtester after an accepted change to scripts, scenes, resources, shaders or
   `project.godot` (off by default); `playtest_minutes` sets the run length (default 2).
   Follow-ups are normal requests; an error the run finds can also go to the debugger
   with `/debug`. `playtest` is a shared read the role tables allow the lead and workers,
   but the gateway lets them play only when the user asked (section 9); the reviewer (timed
   to 90 s) may not.
6. **The card.** Events `{kind: "playtest", playtest_id, state: starting|playing|writing|
   done|failed|stopped, elapsed, seconds, report, summary, followups}` drive one card above
   the footer with the task cards (`playtest_card.gd`, through `sidebar_tasks.gd`): "Playing…
   0:42 / 2:00" in game time with its own Stop, then one count per severity and each finding
   as a row that opens to its detail, script line, seed and screenshot, with **Fix this**.
   The list scrolls past 30 % of the window so the message box stays put. The playtester's
   words go to the transcript and each `Fix:` line becomes an "Ask Journeyman to fix" offer;
   both only fill the message box. The header counts the playtester among agents working.

7. **Coverage, still no model** (2026-11-12). The bot maps the level as it plays
   (`bot_cover.gd`): a grid on the ground plane (2 m cells in 3D, 64 px in 2D, at most 4096
   cells, a bigger level gets bigger cells) whose cells are navigable when the player could
   stand there and get there from where it started. In 3D a downward ray finds a floor no
   more than 6 m above the player with room above it, joined to its neighbours by a step of
   up to 1.6 m or a jump over one missing cell; top-down a cell is empty of what the player
   collides with and joined to the start; side-on it is empty with ground within three cells
   below, joined in any direction. Every cell the player stands in counts too, and a level
   mapped again (a restart) keeps what was found and visited. With no goal to head for (half
   the time when it re-picks), the bot walks to the nearest cell it hasn't stood in by a
   breadth-first search over the grid, steering cell by cell, and gives a cell up for 30 s
   when 6 s don't get it there. The report's `coverage` has the share of the navigable cells
   reached, the grid, and the five biggest unreached areas with their centres in world units;
   a run of a minute or more that reached under 60 % gets a `note`. The bot draws the map as
   `coverage.png` in the run's folder (what `playtest` returns to agents, beside the
   screenshots), and the playtest card draws the same grid in the theme's colours under the
   counts, "Explored 34% of the level", the unreached cells in the danger colour and the start
   ringed. `--reach "key room"` (and `reach` on the tool) names places to head for first, matched
   against node names without articles or spaces; the report's `reach` says for each whether
   a node by that name exists, whether the player got inside its meshes or box shapes or near
   its origin, and when. The done-when checks use it (ADR 0060). All of it is seeded: the
   same seed gives the same grid and the same route.

8. **Fighting through** (2026-11-15). A bot that never shoots back can't show a combat game
   is finishable: it dies before the exit. `bot_fight.gd` is on by default once the game
   shows enemies (group `enemies` or `enemy`, something with `take_damage` or `health` that
   isn't `dead`) and has an attack action (`--fight` forces it, `--no-fight` turns it off;
   3D only). It turns the view with the look actions toward the nearest enemy with a clear
   line from the eyes, holds attack when on target, keeps a fighting distance, switches guns
   with `weapon_next` when the one in hand is empty, backs off below 35 % health, and picks
   up health and ammo pickups (by name) when short. Otherwise it follows the blockout's
   markers: a key the grid joins first, then its door, then the exit (or the `--reach`
   places), pressing interact at an exit that needs using. The coverage grid now knows walls:
   a horizontal ray between neighbouring cells (bodies that move about don't count), a route
   climbs at most 1.1 m a cell, and a door that opens (or an enemy that falls in a doorway)
   has its cells probed again and the grid joined on beyond it. Moves that would walk off a
   ledge are dropped unless the way goes down, and a player fallen below the exit with no way
   on heads for a hazard to be sent back to the checkpoint. Everything is pressed through the
   input map and drawn from its own generator seeded from the run's seed, so the same seed
   plays the same fight. The report's `fight` has keys, doors, kills of enemies seen,
   deaths and where, the goals in order, a trail every 5 s, whether it reached the exit and
   finished, and else the obstacle: which door needs which unreachable key, or the goal it
   was after, how close it got, and whether the map had no way there or it was stuck.

9. **Only when asked** (2026-11-18). Agents ran the bot on their own: a lead called
   `playtest` nine times in a few hours to check its own work and to chase a warning, each
   run minutes long, and the "Fix this" text it was handed asked for another run. A
   playtest is now the user's choice, enforced in the shared core so every agent is held
   to it (`joinery/playtest_gate.py`, `bridge/playtester.py`):
   - It runs from `/playtest`, the card's own buttons, `[agents] playtest_after_change`
     (off by default) or a user request that asks in so many words: "playtest", "play
     test", "test-play", or the bot asked to play ("run the bot", "let the bot play"),
     unless the request says not to ("don't playtest it"). A word list is simple and
     predictable; it errs towards not playing.
   - The chat host checks each user request as it arrives. The lead's `playtest` is
     `Playtester.gated`: it refuses unless that request asked, telling the agent to suggest
     /playtest to the user instead. A task may play only when the user started it with
     such a request, or the lead started it during one. The playtester role's own endpoint
     is not gated: only the user's action starts it.
   - `joinery playtest` in an agent's shell is refused by the policy hook's user-only
     guards (`joinery.policy`), for every agent (`--heatmap`, which plays nothing, stays
     open). The tool description and the agents' guide say the same.
   - `[agents] playtester = false` (Settings > Playtester, `joinery playtester --set
     off`) turns it off for the project: the tool refuses every caller, `/playtest` and the
     card are hidden, the CLI refuses, and done-when `reach`/`no_errors` items are
     inconclusive.
   - Godot's own teardown report as the headless run quits ("17 ObjectDB instances were
     leaked at exit", RIDs, resources still in use) is engine noise, never a finding
     (`console_errors.NOISE`). Repro steps and "Fix this" are plain words with the seed,
     never a command line.
   - The card says what ran ("A bot played Yard Day for 3:00, looking for crashes, errors
     and places it can't reach. It changed nothing."), what it found in plain words, and
     why each kind of finding matters. Its rows are never in an inner scroll.
   - In 3D, a cell is floor only where the player's own collision shape fits standing (at
     its centre or a quarter of the way to a corner; on the navigation mesh when the player
     starts on one), so walls, hedges, props and ground beyond a fence are never "not
     reached". The windowed replay renders the level from straight above with an
     orthographic camera in a SubViewport sharing the game's world (`bot_map.gd`,
     `level.png`), cut 2.4 m above the player's floor like a floor plan, and the card draws
     the walked ground, the bot's path and the unreached places over it. The bot turns
     the view to a goal before pressing interact (first-person games interact with what
     the view points at) and jumps onto a next cell a step up.

## Consequences

- Every blueprint gets a real playtest in CI: a 20 s seeded run each in stock Godot must find
  nothing serious, and a fixture game with a planted script error, hitch and softlock must
  have all three found at the same moments on every run. Two-minute runs with every feature
  are marked slow.
- The bot is a bot. It finds crashes, errors, softlocks and broken saves well; it rarely
  finishes a level, so most balance notes are about deaths and goals it couldn't find, and
  they are worded as hints.
- A custom game gets error capture, crash and freeze detection, and random input; it needs
  `player` or `move_*` actions in its input map for the bot to explore with aim.
- Softlock detection is by time (15 s by default, `--softlock`): a slow puzzle that takes
  longer without any visible change reads as stuck.
- A playtest costs one provider session for the write-up; the run itself costs nothing.
- An agent can no longer check its own change by playing; it says the user can run
  /playtest. Done-when `reach` and `no_errors` items, which the user sees in the to-do,
  still play the staged change.
- The 3D map's picture exists only where something can draw (the screenshot replay); a
  headless-only run shows the grid alone.
- Coverage is an estimate: the grid comes from physics probes, so a floor behind a locked
  door counts as unreached (true, for a bot that cannot open it), and a ledge reached only
  by a long jump may never join the map until the player stands on it.
