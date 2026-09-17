#!/usr/bin/env python3
"""Measure every audio source in the cut and compute a per-source gain.

WHY A PER-SOURCE PASS, GIVEN THERE IS ALREADY A FINAL LOUDNORM.

The master gets a two-pass loudnorm at the end, which sets the level of the programme as
a whole. It cannot fix BALANCE inside the programme. If a voice sits at -25 LUFS and a
demo clip sits at -18, normalising the average just makes everything louder with the demo
still 7 dB hotter, and the viewer reaches for the volume knob every time it plays. A real
cut measured 10.2 dB of spread across its sources.

So each SOURCE is measured once and given one static gain toward a common target.

Static gain, NOT a per-clip loudnorm: loudnorm on a short piece re-normalises that
piece's own dynamics, so a deliberately quiet beat gets pushed up to match a loud one and
the performance flattens. A fixed dB offset moves the clip without touching what happens
inside it.

Order of operations:
    per-source static gain   -> the mix is balanced
    final two-pass loudnorm  -> the programme hits the platform target

  levels.py preview.edl levels.json
  levels.py preview.edl levels.json --target -16
"""
import argparse
import json
import os
import re
import subprocess
from pathlib import Path


def env_noproxy():
    e = dict(os.environ)
    for k in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        e.pop(k, None)
    return e


def integrated_lufs(path, env):
    """EBU R128 integrated loudness, or None when the file has no audible audio."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
                        "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
                       env=env, capture_output=True, text=True)
    m = re.findall(r"I:\s*(-?\d+\.?\d*)\s*LUFS", r.stderr)
    if not m:
        return None
    v = float(m[-1])
    return None if v < -70 else v


def sources_of(edl):
    """Distinct media paths in an mpv EDL, in first-appearance order."""
    out = []
    for line in Path(edl).read_text().splitlines():
        if line and not line.startswith("#"):
            s = line.rsplit(",", 2)[0]
            if s not in out:
                out.append(s)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("edl")
    ap.add_argument("out")
    ap.add_argument("--target", type=float, default=-16.0,
                    help="common per-source target in LUFS; the final loudnorm then lifts "
                         "the programme to the platform number (-14 for YouTube)")
    ap.add_argument("--max-gain", type=float, default=12.0,
                    help="clamp -- a source needing more than this is usually broken, not quiet")
    a = ap.parse_args()

    env = env_noproxy()
    out = {}
    print(f"target {a.target} LUFS\n")
    print(f"{'source':<46} {'measured':>10}  {'gain':>8}")
    print("-" * 68)
    for s in sources_of(a.edl):
        lufs = integrated_lufs(s, env)
        name = Path(s).name
        if lufs is None:
            print(f"{name:<46} {'silent':>10}  {'-':>8}")
            continue
        gain = max(-a.max_gain, min(a.max_gain, a.target - lufs))
        out[s] = round(gain, 2)
        flag = "  <- clamped" if abs(a.target - lufs) > a.max_gain else ""
        print(f"{name:<46} {lufs:>9.1f}L  {gain:>+7.2f}dB{flag}")

    if out:
        spread = max(out.values()) - min(out.values())
        print(f"\nspread across sources: {spread:.1f} dB "
              f"({'worth fixing' if spread > 1.5 else 'already even'})")
    Path(a.out).write_text(json.dumps(out, indent=1))
    print(f"-> {a.out}")


if __name__ == "__main__":
    main()
