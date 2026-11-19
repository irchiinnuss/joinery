# Revert asks first and can be undone

Accepted: 2026-11-19.

## Context

Revert on an accepted change ran at once on a single click, committed
"Revert: …" to the Timeline (ADR 0034) and dropped the change's restore data
(ADR 0026). A user pressed it by mistake on an accepted model change (the
lockfile, its recipes, the recipe history, a preimage `.blend` and two LFS
models) and had no way back inside Joinery: they had to `git revert` the
revert by hand, which also left the change's journal saying "reverted".

## Decision

- **Revert asks first, in place.** Every Revert (the chat's change cards, on
  the Chats page too; the feature plan card; the Review tab's decided rows)
  opens one question under its line, never a dialog: "Revert “Hedge has small
  leaves…”? Its 7 files go back to how they were before it. You can undo
  this." with Revert and Cancel. Focus starts on Cancel and Escape answers
  Cancel, so Enter right after a stray click does nothing. A change kept
  without asking says Undo in the same words. One widget (`revert_ask.gd`)
  asks everywhere.
- **Undo revert.** `joinery transactions unrevert --id` (and
  `FileTransaction.unrevert`) puts a reverted change back as it was accepted,
  through the transaction path: under the history lock, the files about to be
  replaced are saved as the new restore data, the intent is journalled
  (`committing`, so recovery puts everything back after a crash), the files
  are written, and the journal goes back to `accepted` with its decisions
  listed in `history` (`accepted`, `reverted`, `accepted` by `undo revert`,
  each with its Timeline commit). It is its own Timeline entry, "Restore: …"
  with "Restored in Joinery". The core CLI does it, so every agent and the
  sidebar share it.
- **Where the accepted files come from.** A revert now keeps them (`redo/` in
  the transaction folder) before it restores the earlier files. A revert made
  before that, or whose copies retention dropped, reads them from the
  Timeline: each file's newest commit (or its parent) whose bytes hash to the
  journal's `after`, checked out through Git's filters, so LFS models come
  back as models. Without either, nothing changes and Joinery says so.
- **Never over newer edits.** A file changed since the revert stops the undo
  with the files named and two choices: keep them (nothing changes), or
  `--overwrite` (sidebar: Replace them), which saves the edited copies first,
  so a later Revert brings them back. A file already as accepted (a `git
  revert` of the revert by hand) is left alone; the journal is just put right,
  and its earlier files come from the Timeline so Revert still works.
- **Undo is near.** Right after a revert, a toast above the message box reads
  "Reverted “…”" with Undo for eight seconds (held while pointed at or
  focused, never taking focus). The card, the plan card and the Review row
  keep "Undo revert" for as long as the change stays reverted, and the
  Timeline offers it on the revert entry of a change still reverted
  (`reverted_change` in `joinery timeline list`).
- **Retention.** A reverted change's copies count as restore data and follow
  `[history] keep` like accepted ones; Compact drops them.

## Consequences

- A mistaken Revert costs one more click to make and one click to undo.
- Undo revert is as safe as accept: snapshot first, journalled, restored on
  failure, never over newer edits without saying so.
- Old reverts can be undone while the Timeline has their files; projects
  without git can undo only reverts made from now on.
