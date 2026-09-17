#!/usr/bin/env python3
"""Turn the running order into an mpv EDL: the whole cut, playable in about a second.

WHY THIS EXISTS. Rendering to look at a cut is the most expensive habit in video
editing and it buys nothing. An mpv EDL is a text file listing `path,start,length` per
kept segment; mpv seeks the mezzanines and plays the real edit with zero encoding. The
cut stays editable for as long as you are willing to keep previewing this way, and the
render happens once, at the end, when the edit is locked.

It also reports the true duration. Summing speech chunks understates it badly -- the
pauses you keep between phrases add roughly a third.

THE RUNNING ORDER IS DATA. assembly.json lists blocks in the order they play:

  {"fps": 24,
   "blocks": [
     {"kind": "file", "src": "renders/intro.mp4"},
     {"kind": "file", "src": "assets/example.mp4", "in": 2.0, "out": 9.5},
     {"kind": "clip", "src": "cam_main", "mezz": "mezz/cam_main.mp4"},
     {"kind": "clip", "src": "cam_main", "mezz": "mezz/cam_main.mp4", "lo": 0, "hi": 120},
     {"kind": "clip", "src": "cam_b",    "mezz": "mezz/cam_b.mp4", "chunks": [4, 5, 9]}
   ],
   "overlays": [
     {"kind": "gfx", "src": "renders/g_title.mp4", "at_chunk": 78, "dur": 12.0, "lead": 0.3}
   ]}

A `clip` block plays kept chunks of one source. `lo`/`hi` bound it to a chunk range so a
section can be SPLIT around an insert without losing the drop list -- an explicit
`chunks` list would resurrect chunks you dropped. An explicit `chunks` list is for the
opposite case: a hook that reuses material the main body also uses, where the two must
not delete each other.

Overlays are not cut into the timeline. They are scheduled against the chunk they
belong to and written to overlays.json with their real timeline position, so the
compositor can lay them over footage that keeps playing underneath.

  build_edl.py assembly.json chunks.json preview.edl
  build_edl.py assembly.json chunks.json preview.edl --drops manual.json
  mpv preview.edl
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path


def load(p):
    return json.loads(Path(p).read_text())


def resolve_drops(path):
    """Model pass + your overrides, where your overrides win.

    Keeping the two separate is what lets the model's pass be re-run without undoing a
    human decision. Never hand-edit the model's output file: that is the change that
    silently reverts the next time anyone regenerates it.
    """
    if not path:
        return set(), set()
    d = load(path)
    drop = {e["i"] if isinstance(e, dict) else e for e in d.get("drop", [])}
    keep = {e["i"] if isinstance(e, dict) else e for e in d.get("keep", [])}
    return drop - keep, keep


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("assembly")
    ap.add_argument("chunks")
    ap.add_argument("out")
    ap.add_argument("--drops", default=None, help="drop/keep overrides")
    ap.add_argument("--overlays-out", default=None, help="default: <out>.overlays.json")
    ap.add_argument("--root", default=".", help="paths in assembly.json are relative to this")
    a = ap.parse_args()

    root = Path(a.root).resolve()
    asm = load(a.assembly)
    ch = load(a.chunks)
    chunks = ch["chunks"] if isinstance(ch, dict) else ch
    by_src = {}
    for c in chunks:
        by_src.setdefault(c.get("src", ""), []).append(c)
    dropped, _ = resolve_drops(a.drops)

    lines = ["# mpv EDL v0"]
    overlays = []
    t = 0.0                      # position on the finished timeline
    chunk_at = {}                # chunk index -> where it lands, for overlay anchoring
    used = skipped = 0

    for b in asm.get("blocks", []):
        kind = b.get("kind", "clip")

        if kind == "file":
            src = (root / b["src"]).resolve()
            if not src.exists():
                sys.exit(f"missing source: {src}")
            t0 = float(b.get("in", 0.0))
            if "out" in b:
                dur = float(b["out"]) - t0
            else:
                r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                    "-of", "csv=p=0", str(src)], capture_output=True, text=True)
                dur = float(r.stdout.strip()) - t0
            lines.append(f"{src},{t0:.3f},{dur:.3f}")
            t += dur
            continue

        pool = by_src.get(b["src"], [])
        if not pool:
            sys.exit(f"no chunks for source {b['src']!r} -- check the src labels in {a.chunks}")
        mezz = (root / b["mezz"]).resolve() if "mezz" in b else None
        if mezz and not mezz.exists():
            sys.exit(f"missing mezzanine: {mezz}")

        if "chunks" in b:
            # Explicit list: deliberately outranks the drop file.
            wanted = [c for i in b["chunks"] for c in pool if c["i"] == i]
        else:
            lo, hi = b.get("lo", -1), b.get("hi", 10**9)
            wanted = [c for c in pool if lo <= c["i"] <= hi and c["i"] not in dropped]
            skipped += sum(1 for c in pool if lo <= c["i"] <= hi and c["i"] in dropped)

        # Adjacent kept chunks become ONE segment. Fewer, longer segments seek better
        # and, more importantly, a cut between two adjacent chunks is not a cut at all --
        # emitting it separately would put an edit point in the middle of a sentence.
        runs = []
        for c in wanted:
            if runs and abs(c["t0"] - runs[-1][1]) < 1e-3:
                runs[-1] = (runs[-1][0], c["t1"], runs[-1][2] + [c["i"]])
            else:
                runs.append((c["t0"], c["t1"], [c["i"]]))

        for t0, t1, idxs in runs:
            dur = t1 - t0
            if dur <= 0:
                continue
            lines.append(f"{mezz},{t0:.3f},{dur:.3f}")
            for i in idxs:
                chunk_at[i] = t + (next(c["t0"] for c in pool if c["i"] == i) - t0)
            t += dur
            used += len(idxs)

    for o in asm.get("overlays", []):
        anchor = o.get("at_chunk")
        if anchor is None or anchor not in chunk_at:
            print(f"[edl] overlay {o.get('src')} anchored to chunk {anchor}, which is not in "
                  f"the cut -- skipped", file=sys.stderr)
            continue
        overlays.append({**o, "t": round(max(0.0, chunk_at[anchor] - float(o.get("lead", 0.0))), 3)})

    Path(a.out).write_text("\n".join(lines) + "\n")
    ov_path = Path(a.overlays_out or (str(a.out) + ".overlays.json"))
    ov_path.write_text(json.dumps({"total": round(t, 3), "fps": asm.get("fps", 24),
                                   "overlays": overlays}, indent=1))

    mm, ss = divmod(t, 60)
    print(f"[edl] {len(lines) - 1} segments  {used} chunks kept, {skipped} dropped")
    print(f"[edl] runtime {int(mm)}:{ss:04.1f}  ({len(overlays)} overlays scheduled)")
    print(f"[edl] -> {a.out}\n[edl] preview:  mpv {a.out}")


if __name__ == "__main__":
    main()
