# Delivery — compositing details, loudness, and the upload

## Screen recordings shot alongside the camera

If the screen capture and the camera ran at the same time, they share a clock but not a
zero. Measure the offset **once**, against an event visible in both (a click that makes a
sound, a clap, the moment an app opens), then reuse that single number everywhere.

Put the insert at the sentence that mentions it, not wherever the demo happens to start.
Viewers read a screen appearing as "here is the thing I just named"; a screen that
appears thirty seconds early reads as a mistake even when it is technically in sync.

Crop the region that matters. A full 2560-wide desktop scaled into a 1920 frame makes
every UI label unreadable. Measure the real content column — one project cropped from
x=1000 when the content ended at x=1420, and shipped a graphic that was mostly empty
page.

## Aspect ratios

Never `scale=W:H` a source that is not already the output aspect. A 768×1344 portrait clip
stretched into a 1920×1080 frame is **+211% too wide** and the distortion is instantly
obvious on a face. Fit it and fill the sides with a blurred, cover-scaled copy of itself —
`composite.py` does this automatically through `fit_filter`.

## Frame-exactness, restated

The one equation that matters:

```python
nframes = round(t1 * fps) - round(t0 * fps)
```

Piece durations computed independently accumulate error; differences of rounded absolute
positions cancel at every boundary. Read `dur + 0.5s` of input so the decoder can always
satisfy `-frames:v`.

**Probe before paying.** `composite.py --probe 120` should report exactly `120 * fps`
frames. If it does not, nothing about a full build will be better.

## Audio, in order

**1. Per-source static gain.** `levels.py` measures each source and computes one fixed
offset toward a common target (default −16 LUFS). Static, because a per-clip loudnorm
re-normalises each clip's own dynamics and flattens the performance. A real cut had
10.2 dB of spread between its sources — the viewer reaches for the volume knob at every
transition until this is fixed.

**2. A limiter inside each piece.** If a gain pushes peaks to 0 dBFS, the clipping is
encoded into the piece. The final loudnorm then measures its own input as fine and simply
turns down audio that is already broken. `alimiter=limit=0.891` (−1 dBFS) in the piece
prevents it.

**3. Two-pass loudnorm on the finished programme.** One pass guesses from a running
estimate and pumps audibly over a 15-minute programme. Two-pass measures first, then
applies with `linear=true`.

### When loudnorm misses its target — and it will

With `linear=true`, loudnorm will not exceed its true-peak ceiling. If the programme
already peaks near 0, it cannot apply the gain the target needs, so it applies less — or
turns the mix *down*. Measured case: input −15.78 LUFS with TP −0.86 dB, target −14,
result **−16.7 LUFS**. Nothing errors.

This matters because platforms normalise *downward only*: a −16.7 LUFS upload plays
quieter than everyone else's and never gets lifted.

Reaching the target from there means real limiting — applying the gain first and shaving
2–3 dB off transients. That is audible and changes the sound, so **measure, report the
number, and let the human decide.** `composite.py` prints the achieved loudness and says
plainly when it is under.

## Encoding for upload

- **Constant frame rate, always**: `-r <fps> -fps_mode cfr`, and `setsar=1` in the chain.
- **Check the frame count of the finished file** against the timeline. Equal or the render
  is wrong.
- **Decode-scan the output** the same way you scanned the input — QA the output, never the
  input.

### Resolution, and the upscaling question

Platforms pick a better codec above a resolution threshold (YouTube uses VP9 at 1440p and
above), so a 1440p or 2160p upload can look better than 1080p even when the extra pixels
are interpolated. Upscaling a finished 1080p master with `scale=...:flags=lanczos` is a
legitimate way to reach that tier and takes minutes rather than a full re-render.

But do it honestly: check whether your sources can actually carry the higher resolution.
Raising the output size while the sources stay 1080p just upscales earlier in the chain,
costing a full re-encode to produce a *softer* picture than the 1080p you already had. If
you want genuine 1440p, rebuild the mezzanines at that size first.

One trap when upscaling: if the master's video stream starts at a non-zero timestamp
(concat often leaves a few ms), a CFR pass fills the gap by **duplicating the first
frame**, so the output has one frame more than the input. Harmless at ~20 ms, but check
the frame counts and know why they differ rather than discovering it later.

## A last word on rendering

Rendering is the last step, not a way of looking at the cut. Every render fired while the
edit is still moving is wall-clock someone spends waiting for information an EDL preview
would have given instantly. When the cut is locked, render once — and if the cache is
keyed properly, the second render costs only what actually changed.
