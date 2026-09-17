---
name: yee-nyunn-editor
description: >
  Edit long-form video end to end — raw camera and screen recordings into an upload-ready
  master. Covers verified intake, a word-timed transcript, a cut decided from TEXT rather
  than waveform, instant EDL previews, HyperFrames motion graphics, frame-exact ffmpeg
  compositing, and loudness-correct delivery. Use this whenever someone wants to edit, cut,
  assemble, tighten or finish a talking-head, interview, podcast, tutorial, vlog or YouTube
  video — including "cut the filler and bad takes", "remove the ums", "show my screen when
  I'm demoing", "add motion graphics", "layer graphics over my footage", "render it for
  upload", "normalize the audio for YouTube", "my render drifted out of sync", or "transcribe
  this with timestamps". Also use it when a cut already exists and needs re-rendering,
  re-timing, or a loudness pass. Prefer this over ad-hoc ffmpeg for any edit longer than a
  couple of minutes, because the drift, cache and sync traps below are what actually make
  long renders fail.
---

# Yee Nyunn's Editor

A long-form edit is not a sequence of ffmpeg commands. It is a series of **contracts between
stages**, and almost every expensive failure comes from a stage quietly violating one: a
transfer that copies the right number of bytes but the wrong ones, a cut made on timestamps
that don't belong to the words, a render that is 2.7 seconds long by the end.

This skill is that pipeline with the traps already paid for. Each one below is a measured
failure, not a preference.

## The spine

```
0 intake      verify the bytes            → decode-scan BOTH streams
1 mezzanine   normalise once              → CFR, one resolution, one frame rate
2 transcript  words WITH times            → silence-first chunking
3 the cut     decide from text            → your overrides outrank the model
4 assembly    running order as data       → preview as an EDL, DO NOT RENDER
5 graphics    HyperFrames comps           → generated files never overwrite hand edits
6 composite   frame-exact piecewise       → content-addressed cache
7 delivery    loudness, then encode       → measure the output, not the input
```

Steps 2–4 are cheap and iterate in seconds. Step 6 is the only expensive one. The entire
design goal is to keep the edit in steps 2–4 for as long as possible, because **rendering to
look at the cut is the single biggest waste in this whole process.**

---

## 0. Intake — verify, don't assume

Copy, then prove the copy. Size matching proves nothing: the classic transfer corruption
preserves file size exactly.

```bash
ffmpeg -v error -i FILE -f null -     # sequential decode; never -ss (it fakes errors)
```

Note there is **no `-an`**. A video-only scan is how damaged audio reaches the edit — one
real pull had 113 AAC errors alongside 174 video ones, and the video-only scan called it
clean. Scan both streams or you are not scanning.

If a phone or camera mounts over FUSE, mount it **single-threaded**. Concurrent reads over
one channel land at the wrong file offsets: correct size, garbage content, three different
checksums from three reads of one file. `references/intake.md` has the device-specific
details and the reconciliation step (intake reports on the files you *listed*, and says
nothing about the ones you forgot).

## 1. Mezzanine — normalise once

Edit a clean copy, never the camera original: one frame rate, one resolution, constant frame
rate, 48 kHz stereo. Every downstream assumption about frames depends on it.

Decide the LOOK here (tonemapping, grade) and **ask before converting** — it is the one
irreversible aesthetic choice in the pipeline.

## 2. Transcript — the ASR gives WHERE, a reading pass gives WHAT

This division is the heart of the workflow. ASR timestamps are trustworthy; ASR *words* are
not trustworthy enough to cut on, especially for code-switched or non-Latin speech.

**Chunk at silence FIRST, then transcribe each chunk.** Silence is measured from energy, so
it cannot smear. Hand the recogniser one phrase at a time and the edges come from real pauses
rather than from wherever a model decided a segment ended. Measured against transcribing whole
and splitting after: **~19% of recogniser segment edges sit on live speech, versus 1-5% this
way** depending on the material and the recogniser. That range is the point — it is good, not
perfect, so **verify it on every shoot instead of assuming it**:

```bash
scripts/verify_edges.py chunks.json          # % of edges that land inside a word
```

The reason it matters beyond tidiness: a retake and its good take land in *separate* chunks,
so a flub can be dropped without taking the keeper with it.

```bash
scripts/transcribe.py AUDIO chunks.json          # silence-chunk + ASR, either backend
```

The output contract every later stage reads:

```json
{"chunks": [{"i": 0, "t0": 12.31, "t1": 14.08, "text": "...", "src": "cam_main"}]}
```

**Never cut on a forced aligner's edges inside a Latin-script run** in non-Latin speech —
loanwords are out-of-vocabulary for a grapheme tokenizer and their spans blur. Snap those
cuts to the nearest silence instead. See `references/transcribe.md` for backends (Nara Sar
via MCP or API key, local Whisper) and for languages without word spaces.

## 3. The cut — you decide, the model assists

Have a model mark filler and bad takes over the chunk list **by index**, never by its own
timestamps. Then **read the whole transcript yourself.** This is the editor's job and it is
not optional; a model judging each phrase against its neighbours structurally cannot catch:

- **A spoken verdict that condemns an earlier take.** "that one didn't feel natural, let me
  do it again" sits a minute *after* the take it kills. Models drop the verdict as an editor
  cue and keep the take it condemned. When you see a rejection phrase, scan *backwards* for
  the span it refers to.
- **A short chunk carrying meaning.** A lone "AI" dropped as filler turned "AI girlfriend"
  into "girlfriend". Check every dropped chunk under ~3 words that heads the next sentence.

Record your decisions in a plain override file with one reason per entry:

```json
{"drop": [{"i": 214, "why": "second attempt at the same line, first is cleaner"}],
 "keep":  [{"i": 88,  "why": "model called it filler; it is the subject of the next clause"}]}
```

The override is applied **on top of** the model's pass, so re-running the model can never
undo your judgment. Never edit the model's output file directly — that is the one change
that silently reverts.

Scattered single-phrase holes read as skipping. Prefer whole-topic contiguous cuts, and
audit any kept island under ~8 seconds.

## 4. Assembly — the edit is a data file, and the preview is free

The running order lives in `assembly.json`: blocks of source + chunk range, inserts, and
graphics. One clip does not own one section — the same footage can appear in a hook, again
in full, and again as a callback, so blocks carry explicit chunk lists that outrank the drop
files.

```bash
scripts/build_edl.py assembly.json chunks.json preview.edl
mpv preview.edl                                  # the whole cut, instantly, zero encoding
```

An mpv EDL (`file,start,length` per kept segment) plays the real cut straight off the
mezzanines by seeking. **Iterate here.** Every render fired while the cut is still moving is
wall-clock the user spends waiting on nothing, and it buys no information an EDL doesn't
already give. `mpv` also reports the true duration — use that number, not the sum of speech
chunks, because kept pauses add roughly a third.

Screen-recording inserts belong at the sentence that mentions them. If the screen capture and
the camera ran simultaneously, they share a clock: measure the offset once against a visible
sync event and reuse it everywhere.

## 5. Graphics — HyperFrames, and one rule that outranks the rest

Motion graphics are authored as HyperFrames HTML compositions and rendered to mp4, then
composited as overlays. **This skill does not duplicate HyperFrames knowledge** — load the
upstream skills, which stay current:

```bash
npx hyperframes skills update      # then use /hyperframes, /hyperframes-core, /motion-doctrine
```

`references/graphics.md` covers only the integration: comp-to-overlay handoff, face PiP slots,
the Studio constraints that silently freeze elements, and why a sub-composition's length is a
property of the comp (dragging a clip in a timeline UI can never lengthen it).

### A generator must never overwrite a file a human edits

If a script generates the comp HTML and the human edits the same file on a canvas, re-running
the generator destroys their work — silently, and usually right after you've assured them it
is safe. Losing hand-authored text is unrecoverable in a way no other bug here is.

Make the refusal structural, not a promise:

```bash
scripts/guard.py stamp FILE       # after generating: record a hash of what you wrote
scripts/guard.py check FILE       # before generating: refuse if it no longer matches
```

A **missing** stamp counts as edited. Unknown provenance is not permission — that asymmetry is
the whole point, because the failure mode is losing work, not regenerating an extra time. Back
up before any forced overwrite, and when a refusal fires, ask the human rather than passing
`--force`.

**The rendered mp4 is the deliverable, not the HTML.** After a human edits a comp, re-render
it, or the composite is built from the pre-edit graphic. Check every comp's mp4 against its
HTML mtime before a master build — this exact check caught a master that predated *all* of its
graphics.

## 6. Composite — frame-exact or not at all

Render the timeline in pieces so a cut only invalidates its neighbours. Two things make this
work, and both are counter-intuitive.

**A piecewise render must be FRAME-exact, not time-exact.** `-t <duration>` returns
`ceil(dur*fps)` frames, which drifted an 80-piece timeline by **+2.747 s** — enough to put a
graphic a second and a half late. Rounding each piece independently still accumulates
(−0.402 s). The fix is the difference of *rounded timeline positions*, so error cancels at
every boundary:

```python
nframes = round(t1 * FPS) - round(t0 * FPS)      # not round((t1-t0) * FPS)
```

…**plus** reading slightly more input than you need (`dur + 0.5s`), because otherwise the
decoder hands over N−1 frames and `-frames:v` cannot invent the missing one. Probe 120 s and
expect exactly `120*fps` frames before paying for a full build.

**Name each piece by its CONTENT, not its position.** With an index in the filename, cutting
13 seconds anywhere renames every later piece, missing the cache and re-encoding bytes that
are identical. Content-addressing took a rebuild from ~15 minutes to ~3. The signature must
include the source paths, in-point, duration, overlay, gain, encoder settings, **output
geometry**, and the **mtimes of every source** — a re-rendered graphic with the same filename
is otherwise served stale from cache, and that survives `--force` on the other pieces.

```bash
scripts/composite.py assembly.json preview.edl --out master.mp4
```

Other measured traps: graphics must win over screen inserts when their windows overlap (first
match wins is how two graphics silently vanish), and a non-16:9 source must be **fitted over a
blurred cover-scaled copy**, never stretched to fill — a naked `scale=W:H` distorted a portrait
clip by +211%.

## 7. Delivery — measure the output

Two-stage audio, in this order:

1. **Per-source static gain** to balance the mix. Measure each source once and apply a fixed
   dB offset. Not a per-clip loudnorm — that re-normalises each clip's own dynamics and
   flattens the performance.
2. **Two-pass loudnorm** on the finished programme for the platform target (−14 LUFS for
   YouTube), then a limiter.

```bash
scripts/levels.py preview.edl levels.json        # per-source measurement first
```

Clipping check: if per-source gains push peaks to 0 dBFS the distortion is baked into the
piece, and the final loudnorm then just turns down audio that is already broken. Put the
limiter *in the piece*, not only at the end.

**Loudnorm can silently miss its target.** With `linear=true` it will not exceed its true-peak
ceiling, so a mix already peaking near 0 gets turned *down* instead of up — a programme at
−15.8 LUFS with TP −0.9 came out at −16.7 instead of −14. That is not a bug to work around
blindly: reaching the target means real limiting, which changes the sound. Measure the final
file, report the number, and let the human decide.

Finally, QA the **output**: decode-scan it, confirm the frame count matches the timeline, and
confirm the loudness. `references/delivery.md` has the full chain.

---

## Working with Nara (optional)

If the Nara MCP is connected it provides Burmese-first generation and ASR in the same account:
`transcribe` (nara-sar-1, word-level timestamps, Burmese + English code-switching preserved),
`generate_speech`, `generate_video`, `generate_image`, `upscale_video`.

For a long recording, do not call `transcribe` once per chunk through the model — pass
`file_path` and run the command it returns, which chunks locally and posts the pieces. Details
and the cost model are in `references/nara-mcp.md`.

## The short list

If you remember nothing else:

1. Verify transfers by decoding both streams. Size proves nothing.
2. Cut units come from silence, never from recogniser segment edges.
3. Read the whole transcript yourself; keep your decisions in an override file that outranks
   the model's.
4. Preview is an EDL. Rendering is the last step, not a way of looking at the cut.
5. A piecewise render is frame-exact, and its cache is keyed by content including source
   mtimes and output geometry.
6. A generator never overwrites a file a human edits — and no stamp means edited.
7. QA the output, never the input. Report the measured number, including when it misses.
