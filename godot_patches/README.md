# Godot fork

Joinery's fork is a patch series, not a long-lived repo: `UPSTREAM` pins a Godot
tag and `patches/` holds each change as a small, upstreamable patch. `godot-src/`
is a disposable, gitignored shallow checkout on branch `joinery-4.7`.

```bash
godot_patches/fork.sh sync            # checkout pinned tag + apply patches
godot_patches/fork.sh export          # write joinery-4.7 commits to patches/
godot_patches/fork.sh rebase 4.7.3-stable   # move series to a new tag, update UPSTREAM
godot_patches/fork.sh build [args]    # scons target=editor, cached (see below)
godot_patches/fork.sh cachestats      # ccache hit rates
```

To change a patch: commit (or amend) on `joinery-4.7` in `godot-src/`, run
`export`, then commit `patches/` here. `sync --force` discards unexported commits.

## Builds are cached, because patching means rebuilding often

`fork.sh build` turns on two caches. Measured on an M-series Mac, editor target,
once they had been populated:

- first build, both caches cold: **7m 16s**
- clean rebuild with `bin/obj` deleted: **14.8s**
- one file changed: a compile error surfaces in **~8s**

They do different jobs and both are worth having:

- **SCons cache** (`cache_path`, default `~/.cache/joinery/scons`, 8 GiB) stores
  finished objects by build signature, so a wiped `bin/obj` or a fresh
  `sync --force` restores them instead of recompiling. This is what turns a
  clean rebuild into seconds.
- **ccache** skips recompiling a translation unit whose contents are unchanged,
  which covers editing a file and reverting it, or two branches sharing most of
  their code.

Turn either off with `JOINERY_NO_SCONS_CACHE=1` or `JOINERY_NO_CCACHE=1`: a
cache is the first thing to suspect when a build behaves strangely. Point the
SCons cache elsewhere with `JOINERY_SCONS_CACHE`, size it with
`JOINERY_SCONS_CACHE_GIB`, and see hit rates with `fork.sh cachestats`.

On macOS, `joinery launch` starts a bare build such as
`bin/godot.macos.editor.arm64` from a small wrapper,
`~/Library/Caches/Joinery/dev-app/Joinery.app` (identifier
`org.joinery.editor.dev`), so the Dock and app switcher show Joinery and its
icon instead of the file name. The wrapper holds a hard link to the build (a
copy across volumes) and follows each rebuild; delete it to start fresh.

Intended series: 3D editor context-menu slot and viewport pick hook, meant for
upstream, plus Joinery-only patches that restructure the editor (ADR 0005) and
route project creation through Joinery's questionnaire (ADR 0004): starting
point, features and apps, brief and art direction, all read from the CLI
([ADR 0029](../docs/adr/0029-new-project-wizard.md)). Projects must stay
openable in stock Godot; the addon degrades without these.

The Windows native OpenGL initialization fix is a separate upstream candidate
with an exact-file scope exception ([ADR 0018](../docs/adr/0018-windows-software-opengl.md)).
Normal Windows process-pipe EOF handling is another independent upstream
candidate ([ADR 0019](../docs/adr/0019-windows-process-pipe-eof.md)), and so is
close-on-exec for piped processes on Unix
([ADR 0092](../docs/adr/0092-unix-pipe-close-on-exec.md)).
