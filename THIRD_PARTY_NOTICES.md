# Third-party notices

Joinery's own licences are described in [LICENSING.md](LICENSING.md). This file
lists the third-party material that is part of this repository or of a Joinery
release, and where each licence text lives.

| Component | Where | Licence | Licence text and attribution |
|---|---|---|---|
| Godot Engine 4.7 (the engine the patch series applies to, shipped in releases) | `godot_patches/`, release builds | MIT, © 2014-present Godot Engine contributors, © 2007-2014 Juan Linietsky, Ariel Manzur | [licenses/GODOT-LICENSE.txt](licenses/GODOT-LICENSE.txt) |
| Godot's bundled third-party libraries (shipped inside the engine binary) | release builds | Various (see file) | [licenses/GODOT-COPYRIGHT.txt](licenses/GODOT-COPYRIGHT.txt) |
| ANGLE (macOS release builds, Compatibility renderer) | release builds | BSD-3-Clause, © The ANGLE Project Authors | [licenses/ANGLE-LICENSE.txt](licenses/ANGLE-LICENSE.txt) |
| Windows runtime SDK files (Windows release builds) | release builds | Their own licences | Copied into the release's `licenses/` folder by the build |
| Inter typeface (Regular, Bold) | `godot_addon/joinery/fonts/` | SIL Open Font License 1.1, © 2016 The Inter Project Authors | [LICENSE.Inter.txt](godot_addon/joinery/fonts/LICENSE.Inter.txt) |
| Application logos: Godot, Krita, GIMP, Inkscape, Material Maker, Surge XT, SuperCollider | `godot_addon/joinery/icons/apps/` | CC BY 4.0, CC BY-SA 3.0 US / 4.0, MIT or GPL-3.0, per logo; names and logos are trademarks of their owners | [licenses/tool-logos.md](licenses/tool-logos.md), [NOTICE.txt](godot_addon/joinery/icons/apps/NOTICE.txt), `sources.json` |
| Claude mark (from Material Icon Theme 5.40.0) | `godot_addon/joinery/icons/providers/claude.svg` | MIT, © 2025 Material Extensions; the mark is a trademark of Anthropic | [material-icon-theme-LICENSE.txt](godot_addon/joinery/icons/providers/material-icon-theme-LICENSE.txt) |
| Codex and Gemini marks | `godot_addon/joinery/icons/providers/` | Trademarks of OpenAI and Google, used only to name the agent | [NOTICE.txt](godot_addon/joinery/icons/providers/NOTICE.txt), `sources.json` |

Everything else in `godot_addon/joinery/icons/` (the `ui/` and `tools/` glyphs,
the opencode monogram and the Blender rail glyph) was drawn for Joinery and is
covered by the add-on's MIT licence.

Python dependencies are installed by uv from `uv.lock` and are not part of this
repository; each keeps its own licence. The creative tools (Blender, Krita,
GIMP, Inkscape, Material Maker, Surge XT, SuperCollider, Audacity, FluidSynth)
are separate programs under their own licences. Joinery runs them; it does not
include them.
