# 0086. Seeded sounds and one-session batches

Status: accepted (2026-11-14)

## Context

scsynth seeds WhiteNoise, Dust, Rand and the other random UGens from the
clock, and sclang does the same for `rrand` or `choose`. An unseeded
SuperCollider patch therefore renders new samples every run, and its recipe
could only be checked by sound (`snd:`, `joinery.audio_signature`), never by
bytes. The sound kit already seeded its own patches (ADR 0048); patches an
agent wrote with `operate_document` did not.

`make_sounds` rendered each kit sound in its own worker run, up to four at a
time. Every run starts a container and sclang (with its class library) for a
render that takes well under a second.

## Decision

**Every SuperCollider patch is seeded.** `operate_document` (and every other
path through `edit_document`) inserts `RandID.ir(0); RandSeed.ir(1, <seed>)`
at the top of each SynthDef that has no RandSeed of its own, after its
arguments and `var` declarations (`supercollider.seed_patch`; comments and
strings are skipped). The seed is the one passed (`seed`), else the patch's
own RandSeed, else one derived from the source path (below 2^24, exact as a
UGen's float input), so editing the same sound keeps its randomness. The
recipe records it (`seed = N`), the recipe script also sets
`thisThread.randSeed` for language-side randomness, and `renders_vary` is
false, so rebuild uses the exact `sha256:` check. The sound kit's patches get
the same line. Old recipes without a seed keep the `snd:` comparison.

**Kit sounds in SuperCollider render in one sclang session.**
`make_sounds` stages each sound's own standalone recipe script (exactly what
`make_sound` writes), then runs one batch script
(`supercollider.batch_script`) that, sound after sound, writes the native
source and edit program, runs the patch with its `{ 0.exit }` completion
action replaced by a signal to the next sound, waits for the render, runs
the edit program, and prints `JOINERY_SOUND_OK|FAILED <index>`. Each NRT score
still renders in its own scsynth (recordNRT starts one per score); what the
batch shares is the container, sclang and its start-up. Each sound is then
recorded from its own part of the log against its own recipe, and the lock
file is written once for the batch. A sound that fails is that sound's error
alone; its files are put back.

The batch is never a recipe. Rebuild replays every sound's own script alone.
That is sound only because the patches are seeded: the batch and the solo
replay run the same score and the same edit program, so they write the same
bytes, and the exact check proves it on every rebuild. (Verified in the
creative worker: four kit sounds from one batch, each rebuilt alone, matched
byte for byte.) A patch that does not end with exactly one `{ 0.exit }` (an
agent's own patch) is not batched; it renders alone as before. Surge XT
sounds still render side by side, a few at a time.

Side-by-side renders put their files on disk before their recipes exist, so
a lock written in between found files with no recipe and failed the sound.
Sounds rendered side by side now record with `lock=False` and the lock is
written once when they have all finished.

## Consequences

- Rebuild checks every new SuperCollider render exactly; `snd:` remains only
  for recipes recorded before this.
- A batch of SuperCollider kit sounds costs one container and one sclang
  start instead of one per sound.
- A batch that hangs is stopped by its own limit (one patch's limit plus 30 s
  per sound); the sounds it finished are kept and the rest are reported as
  failed.
