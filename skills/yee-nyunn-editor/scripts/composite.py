#!/usr/bin/env python3
"""Render the locked cut: piecewise, frame-exact, cached by content.

Run this ONCE, when the edit is locked. While the cut is still moving, preview the EDL
in mpv instead -- see build_edl.py.

THE TWO THINGS THAT MAKE A PIECEWISE RENDER CORRECT
---------------------------------------------------
1. FRAME-EXACT, NOT TIME-EXACT. `-t <dur>` hands back ceil(dur*fps) frames, so pieces
   gain a frame here and there: 80 pieces drifted a 14:53 timeline by +2.747s, enough to
   put a graphic a second and a half late. Rounding each piece's own duration still
   accumulates (-0.402s measured). The fix is the difference of ROUNDED TIMELINE
   POSITIONS, so the error cancels at every boundary rather than summing:

       nframes = round(t1*fps) - round(t0*fps)        # not round((t1-t0)*fps)

   and then ask the decoder for slightly MORE input than that (READ_SLACK), because
   otherwise it supplies N-1 frames and -frames:v cannot invent the missing one.

2. NAMED BY CONTENT, NOT POSITION. With the index in the filename, cutting 13 seconds
   anywhere renames every later piece, so it misses the cache and re-encodes bytes that
   are byte-identical. Content-addressing took a full rebuild from ~15 minutes to ~3.
   The signature has to include the source MTIMES and the OUTPUT GEOMETRY as well as the
   obvious inputs -- a re-rendered graphic keeps its filename, and a resolution change
   would otherwise serve half the timeline from a differently-sized cache.

  composite.py preview.edl --out master.mp4
  composite.py preview.edl --out master.mp4 --levels levels.json --size 1920x1080 --cq 18
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

READ_SLACK = 0.5     # extra input seconds so -frames:v can always be satisfied


def env_noproxy():
    e = dict(os.environ)
    for k in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        e.pop(k, None)
    return e


ENV = env_noproxy()


def sh(cmd):
    return subprocess.run(cmd, env=ENV, capture_output=True, text=True)


def have_nvenc():
    r = sh(["ffmpeg", "-hide_banner", "-encoders"])
    return "h264_nvenc" in r.stdout


_HAS_AUDIO = {}


def has_audio(path):
    """Does this source carry an audio stream?

    Graphics renders and silent b-roll often do not. Every piece must still end up with
    BOTH streams, because the final concat is a stream copy: one piece missing audio
    desynchronises everything after it, or the concat refuses outright. So a silent base
    gets real silence rather than no track.
    """
    if path not in _HAS_AUDIO:
        r = sh(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                "stream=index", "-of", "csv=p=0", str(path)])
        _HAS_AUDIO[path] = bool(r.stdout.strip())
    return _HAS_AUDIO[path]


def mtime(p):
    try:
        return int(Path(p).stat().st_mtime)
    except OSError:
        return 0


def read_edl(path):
    """[(src, start, length)] in running order."""
    segs = []
    for line in Path(path).read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        src, ss, dur = line.rsplit(",", 2)
        segs.append((src, float(ss), float(dur)))
    return segs


def timeline(segs):
    """Each segment with the [t0,t1) it occupies on the finished timeline."""
    out, t = [], 0.0
    for src, ss, dur in segs:
        out.append({"src": src, "ss": ss, "t0": t, "t1": t + dur})
        t += dur
    return out, t


def cut_points(tl, overlays, total):
    """Every boundary a piece may not straddle: segment edges and overlay edges."""
    pts = {0.0, total}
    for s in tl:
        pts.add(s["t0"]); pts.add(s["t1"])
    for o in overlays:
        pts.add(o["t"]); pts.add(min(total, o["t"] + float(o["dur"])))
    return sorted(p for p in pts if 0.0 <= p <= total)


def base_at(tl, t):
    """Which source is playing at timeline position t, and where in it."""
    for s in tl:
        if s["t0"] <= t < s["t1"] - 1e-6:
            return s["src"], s["ss"] + (t - s["t0"])
    last = tl[-1]
    return last["src"], last["ss"] + (t - last["t0"])


def overlay_at(overlays, t):
    """The overlay covering t.

    GRAPHICS WIN over picture-in-picture inserts. Returning the first match instead cost
    two graphics that silently never appeared in an export, because a long screen-insert
    window swallowed them. Sorting makes the precedence explicit rather than
    order-of-definition.
    """
    hit = [o for o in overlays if o["t"] <= t < o["t"] + float(o["dur"]) - 1e-6]
    if not hit:
        return None
    hit.sort(key=lambda o: (o.get("kind") != "gfx", o["t"]))
    return hit[0]


def fit_filter(label, w, h, out):
    """Letterbox a non-matching aspect over a blurred, cover-scaled copy of itself.

    A naked scale=W:H stretches: a portrait clip came out +211% wide and the user
    noticed immediately. Fitting keeps the geometry honest, and the blurred backdrop
    keeps the frame from reading as an accident.
    """
    return (f"[{label}]split=2[bgsrc][fgsrc];"
            f"[bgsrc]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
            f"boxblur=luma_radius=min(h\\,w)/20:luma_power=1[bg];"
            f"[fgsrc]scale={w}:{h}:force_original_aspect_ratio=decrease,setsar=1[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[{out}]")


def source_gain(levels, src):
    return float(levels.get(src, 0.0))


def render_piece(t0, t1, tl, overlays, levels, cfg, outdir):
    fps = cfg["fps"]
    w, h = cfg["w"], cfg["h"]
    k = w / 1920.0                       # coordinates below were authored on a 1920 frame

    nframes = max(1, int(round(t1 * fps)) - int(round(t0 * fps)))
    dur = nframes / fps
    bsrc, bss = base_at(tl, t0)
    ov = overlay_at(overlays, t0 + dur / 2)
    g = source_gain(levels, bsrc)

    sig = hashlib.sha256(json.dumps(
        [bsrc, round(bss, 3), nframes, ov, g, cfg["cq"], cfg["enc"], w, h, fps,
         has_audio(bsrc), mtime(bsrc), mtime(ov["src"]) if ov else 0],
        sort_keys=True).encode()).hexdigest()[:16]
    out = outdir / f"p_{sig}.mp4"
    if out.exists() and out.stat().st_size > 0:
        return out, True

    readdur = dur + READ_SLACK
    # Gain first, then a limiter IN THE PIECE. If a per-source gain pushes peaks to
    # 0 dBFS the clipping is baked into the encoded audio, and the final loudnorm then
    # just turns down something already broken -- it measured its own input as fine.
    aflt = ["-af", f"volume={g:+.2f}dB,alimiter=limit=0.891:attack=5:release=50:level=disabled"]
    enc = ["-c:v", cfg["enc"], *cfg["encopts"], "-pix_fmt", "yuv420p",
           "-r", str(fps), "-fps_mode", "cfr", "-frames:v", str(nframes),
           "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-ac", "2"]

    ins = ["-ss", f"{bss:.3f}", "-t", f"{readdur:.3f}", "-i", bsrc]
    if ov is None:
        fc = fit_filter("0:v", w, h, "v") + f";[v]fps={fps},setsar=1[vo]"
    else:
        oss = float(ov.get("in", 0.0)) + (t0 - ov["t"])
        if ov.get("kind") == "gfx":
            # Full-frame graphic; the speaker is inset into the slot the comp left for
            # them, so his voice and face never stop.
            sx, sy, sw = (int(v * k) for v in (ov.get("slot") or cfg["slot"])[:3])
            fc = (f"[1:v]scale={w}:{h},setsar=1[g];"
                  f"[0:v]scale={sw}:-2,setsar=1[pip];"
                  f"[g][pip]overlay={sx}:{sy},fps={fps},setsar=1[vo]")
        else:
            # Screen insert or b-roll: it fills the frame, the speaker shrinks to a corner.
            px, py, pw = (int(v * k) for v in cfg["pip"])
            fc = (fit_filter("1:v", w, h, "scr") + ";"
                  f"[0:v]scale={pw}:-2,setsar=1[pip];"
                  f"[scr][pip]overlay={px}:{py},fps={fps},setsar=1[vo]")
        ins += ["-ss", f"{oss:.3f}", "-t", f"{readdur:.3f}", "-i", ov["src"]]

    if has_audio(bsrc):
        amap = "0:a:0"
    else:
        amap = f'{ins.count("-i")}:a:0'   # the silence is the next input
        ins += ["-f", "lavfi", "-t", f"{readdur:.3f}",
                "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    cmd = ["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", fc,
           "-map", "[vo]", "-map", amap, *aflt, *enc, str(out)]

    r = sh(cmd)
    if r.returncode != 0 or not out.exists():
        out.unlink(missing_ok=True)
        sys.exit(f"piece {t0:.2f}-{t1:.2f} failed:\n{' '.join(cmd)}\n{r.stderr[-1500:]}")
    return out, False


def frames_of(p):
    r = sh(["ffprobe", "-v", "error", "-select_streams", "v:0",
            "-count_frames", "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(p)])
    try:
        return int(r.stdout.strip().split("\n")[0])
    except (ValueError, IndexError):
        return -1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("edl")
    ap.add_argument("--overlays", default=None, help="default: <edl>.overlays.json")
    ap.add_argument("--out", default="master.mp4")
    ap.add_argument("--levels", default=None, help="per-source gains from levels.py")
    ap.add_argument("--work", default="pieces", help="piece cache directory")
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--fps", type=int, default=None, help="default: from overlays.json, else 24")
    ap.add_argument("--cq", type=int, default=20, help="lower is better quality/bigger")
    ap.add_argument("--target-lufs", type=float, default=-14.0)
    ap.add_argument("--no-loudnorm", action="store_true")
    ap.add_argument("--slot", default="80,380,560", help="x,y,width of the face slot in gfx overlays")
    ap.add_argument("--pip", default="1516,20,384", help="x,y,width of the corner PiP")
    ap.add_argument("--probe", type=float, default=None,
                    help="render only the first N seconds -- do this before paying for a full build")
    a = ap.parse_args()

    ovp = Path(a.overlays or (a.edl + ".overlays.json"))
    meta = json.loads(ovp.read_text()) if ovp.exists() else {"overlays": [], "fps": 24}
    overlays = meta.get("overlays", [])
    levels = json.loads(Path(a.levels).read_text()) if a.levels else {}
    w, h = (int(v) for v in a.size.lower().split("x"))
    fps = a.fps or meta.get("fps", 24)

    nv = have_nvenc()
    cfg = {
        "w": w, "h": h, "fps": fps, "cq": a.cq,
        "enc": "h264_nvenc" if nv else "libx264",
        "encopts": (["-preset", "p5", "-rc", "vbr", "-cq", str(a.cq), "-b:v", "0"] if nv
                    else ["-preset", "medium", "-crf", str(a.cq)]),
        "slot": [float(v) for v in a.slot.split(",")],
        "pip": [float(v) for v in a.pip.split(",")],
    }

    segs = read_edl(a.edl)
    tl, total = timeline(segs)
    if a.probe:
        total = min(total, a.probe)
    pts = [p for p in cut_points(tl, overlays, total) if p <= total]
    pieces = [(pts[i], pts[i + 1]) for i in range(len(pts) - 1) if pts[i + 1] - pts[i] > 1e-6]

    outdir = Path(a.work)
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"[master] {total:.2f}s  {len(pieces)} pieces  {w}x{h}@{fps}  "
          f"{cfg['enc']} cq{a.cq}  {len(overlays)} overlays")

    built = cached = 0
    files, expect = [], 0
    for i, (t0, t1) in enumerate(pieces):
        p, hit = render_piece(t0, t1, tl, overlays, levels, cfg, outdir)
        files.append(p)
        expect += int(round(t1 * fps)) - int(round(t0 * fps))
        cached += hit
        built += not hit
        print(f"\r[master] {i + 1}/{len(pieces)}  built {built} cached {cached}", end="", flush=True)
    print()

    lst = outdir / "concat.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in files))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    r = sh(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
            "-c", "copy", "-movflags", "+faststart", str(out)])
    if r.returncode != 0:
        sys.exit(f"concat failed:\n{r.stderr[:800]}")

    got = frames_of(out)
    print(f"[master] frames {got} / {expect}  {'EXACT' if got == expect else 'MISMATCH'}")
    if got != expect:
        print("[master] frame count is off -- do not ship this; re-check READ_SLACK and the "
              "piece boundaries before rendering again", file=sys.stderr)

    if not a.no_loudnorm:
        # Two-pass: a single pass guesses from a running estimate and pumps over a
        # 15-minute programme.
        print("[master] loudness 1/2 (measuring)...", flush=True)
        m = sh(["ffmpeg", "-hide_banner", "-nostats", "-i", str(out),
                "-af", f"loudnorm=I={a.target_lufs}:TP=-1.5:LRA=11:print_format=json",
                "-f", "null", "-"])
        meas = None
        try:
            blob = m.stderr[m.stderr.rindex("{"):]
            meas = json.loads(blob[:blob.index("}") + 1])
        except (ValueError, json.JSONDecodeError):
            pass
        af = f"loudnorm=I={a.target_lufs}:TP=-1.5:LRA=11"
        if meas:
            af += (f":measured_I={meas['input_i']}:measured_TP={meas['input_tp']}"
                   f":measured_LRA={meas['input_lra']}:measured_thresh={meas['input_thresh']}"
                   f":offset={meas['target_offset']}:linear=true")
            print(f"[master]   measured I={meas['input_i']} LUFS  TP={meas['input_tp']} dB")
        af += ",alimiter=limit=0.94"
        tmp = out.with_suffix(".ln.mp4")
        print("[master] loudness 2/2 (applying)...", flush=True)
        r2 = sh(["ffmpeg", "-v", "error", "-y", "-i", str(out), "-c:v", "copy", "-af", af,
                 "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-ac", "2",
                 "-movflags", "+faststart", str(tmp)])
        if r2.returncode == 0 and tmp.exists():
            tmp.replace(out)
        else:
            print(f"[master] loudnorm failed, keeping the raw mix: {r2.stderr[:300]}", file=sys.stderr)

    # Report the number actually achieved. loudnorm with linear=true will not exceed its
    # true-peak ceiling, so a mix that already peaks near 0 comes out UNDER target rather
    # than at it -- silently. Hitting the target from there needs real limiting, which
    # changes the sound, so it is a decision for a human and not a thing to force here.
    fin = sh(["ffmpeg", "-hide_banner", "-nostats", "-i", str(out),
              "-af", "ebur128=framelog=quiet", "-f", "null", "-"])
    import re as _re
    got_l = _re.findall(r"I:\s*(-?\d+\.?\d*)\s*LUFS", fin.stderr)
    if got_l:
        val = float(got_l[-1])
        off = val - a.target_lufs
        note = ("on target" if abs(off) < 0.6 else f"{off:+.1f} dB off target") \
            if not a.no_loudnorm else "raw mix, loudnorm skipped"
        print(f"[master] final loudness {val:.1f} LUFS ({note})")
        if off < -0.6 and not a.no_loudnorm:
            print("[master] under target: loudnorm hit its true-peak ceiling before it "
                  "reached the goal. Raising it means limiting transients, which changes "
                  "the sound -- ask before doing it.")
    sz = out.stat().st_size
    print(f"[master] -> {out}  {sz / 1048576:.0f} MB")


if __name__ == "__main__":
    main()
