# Close-on-exec for piped child processes on Unix

Accepted: 2026-11-19. Scope: one upstream candidate patch against the pinned
Godot 4.7.2 tree, `drivers/unix/os_unix.cpp`.

The sidebar's chat host runs as a child of the editor through
`OS.execute_with_pipe`. When Yard Day's editor crashed, the host kept running
with no editor: it never read EOF on its stdin. `execute_with_pipe` creates its
pipes with plain `pipe()`, so the editor's ends stay inheritable, and every
process the editor starts later (the game run from Play) gets a copy of the
write end of the host's stdin. The game outlived the editor and held the pipe
open.

Mark all six pipe ends close-on-exec as they are created. The new process
receives its own ends through `dup2`, which clears the flag, so it still reads
and writes the pipe; no other process inherits them. A headless check that
starts a piped child and then a plain one shows the plain child holding three
of the piped child's pipe ends before the change and none after.

The chat host does not rely on this alone: the addon passes `--editor-pid`, and
the host ends once that process is gone (`joinery.platform_support.ProcessWatch`),
which also covers stock Godot and Windows.

This general POSIX fix belongs in its own upstream candidate patch. The
fork-area guard permits only this patch's exact implementation file with this
ADR; it does not open the drivers directory to other changes.
