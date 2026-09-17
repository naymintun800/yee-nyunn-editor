# Graphics — HyperFrames comps as overlays

This file covers the **integration** only. For authoring compositions — timing
attributes, animation, motion continuity — use the upstream skills, which stay current:

```bash
npx hyperframes skills update
```

Then `/hyperframes` (entry point and routing), `/hyperframes-core` (the composition
contract), `/motion-doctrine` (how scenes connect), `/hyperframes-cli` (rendering).

## The shape of the integration

HyperFrames renders HTML to mp4. It does not cut footage and it should not try: it takes
one browser screenshot per frame, which is right for a 6-second graphic and absurd for
21,000 frames of a talking head. So:

```
comps (HTML)  --hyperframes render-->  graphic.mp4  --composite.py-->  master
```

The comp draws the graphic full-frame and **leaves a hole for the speaker**. The
compositor scales the camera into that hole (`slot` on the overlay, or `--slot` as the
default), so the audio and the face never stop while a graphic is up.

```json
{"kind": "gfx", "src": "renders/g_title.mp4", "at_chunk": 78,
 "dur": 12.0, "lead": 0.3, "slot": [70, 300, 780, 439]}
```

`at_chunk` anchors the graphic to the sentence that earns it; `lead` starts it slightly
before the words. Anchoring to a chunk rather than a timestamp is what lets the cut keep
moving — re-cut anywhere and the graphic follows its sentence.

Slot coordinates are authored against a 1920-wide frame and scaled by the compositor, so
the same comp works at any output size.

## A generator must never overwrite a hand edit

If a script generates comp HTML and a human edits the same file on the Studio canvas,
re-running the generator destroys their work. This happened twice in one evening on the
project this skill came from, the second time unrecoverably, both times immediately after
someone was told their edits were safe.

Structural fix, not a promise:

```bash
scripts/guard.py check comps/*.html     # before generating: exits 1 if any was edited
<your generator>
scripts/guard.py stamp comps/*.html     # after: record what you wrote
```

A **missing stamp counts as edited**. Unknown provenance is not permission — the cost of
refusing to regenerate an untouched file is one command; the cost of the reverse is
somebody's work, in a language you may not be able to re-author.

When a check fails, ask the human. Do not reach for `--force`.

## Re-render the mp4 after any comp edit

The HTML is not the deliverable. After a human edits a comp, re-render it, or the
composite is built from the pre-edit graphic — silently, since the mp4 still exists and
still plays.

```bash
for h in comps/*.html; do
  m="renders/$(basename "${h%.html}").mp4"
  [ -f "$m" ] && [ "$m" -ot "$h" ] && echo "STALE: $m is older than $h"
done
```

Run that before every master build. On the project this came from it caught a delivered
master that predated **all** of its graphics, not just the one that had been edited.

## Length lives in the comp, not the timeline

A sub-composition's duration is a property of the composition. Dragging its clip longer
in a timeline UI cannot extend it — the extra time renders as a frozen last frame or a
duplicate. When a graphic is too short for the speech it covers, change the comp's
duration and the timing of the rows inside it, then re-render.

Budget the length from the transcript: find the first and last chunk the graphic should
span and use that span, rather than guessing and discovering a 7-second graphic under 46
seconds of speech.

## Studio will refuse a project unless

- every element with `data-start` also has an `id` — without one it is silently **frozen**
  in renders while looking fine in preview
- there is exactly **one** root-level `data-composition-id`; sub-comps live in their own
  directory
- `window.__timelines` exists
- asset paths are relative to the **project root** — a `../` inside a sub-comp directory
  404s in Studio even though the renderer would have rewritten it
- `data-fps` is set, or comps freeze at the wrong rate

Run the linter before opening Studio. It catches all of the above in seconds, which is
considerably better than finding them in a render.

## Two things that look like framework bugs and are not

- **A CSS transition will not animate under a seeking renderer.** Transitions are
  wall-clock; the renderer seeks the timeline. An underline built with
  `transition: width` grew non-monotonically and read as a glitch. Drive it from the
  animation timeline as a real element instead.
- **A styling wrapper must not carry clip/timing attributes around a nested `<video>`** —
  the framework freezes the video.
