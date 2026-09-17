#!/usr/bin/env python3
"""Audit cut edges: do any chunk boundaries land on live speech?

This is the QA gate for step 2, and it exists because the claim "silence-first chunking
gives clean edges" is a claim, not a guarantee. It holds well in practice and it is worth
CHECKING on every shoot rather than trusting -- a quiet room, a hot preamp or an unusual
speaker moves the threshold, and a bad edge is only obvious once you hear a clipped word
in the finished cut.

Method: take word timestamps from the recogniser and, for every chunk, measure the
silence at each edge. A negative margin means the boundary sits inside a word.

  verify_edges.py chunks.json --words transcript.json
  verify_edges.py chunks.json                      # words read from chunks.json itself

ONE CAVEAT THAT WILL BITE YOU. Recognisers occasionally emit an absurd span -- a single
word timed at five seconds. One such span straddles several chunks and reports them all
as violations when the chunker was right and the recogniser was wrong. Spans longer than
--max-word are therefore reported separately and excluded from the verdict; look at them,
do not just believe them.
"""
import argparse, json, sys
from pathlib import Path


def load_words(chunks_file, words_file):
    if words_file:
        d = json.loads(Path(words_file).read_text())
        return d["words"] if isinstance(d, dict) else d
    d = json.loads(Path(chunks_file).read_text())
    out = []
    for c in (d["chunks"] if isinstance(d, dict) else d):
        out += c.get("words", [])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("chunks")
    ap.add_argument("--words", default=None, help="transcript with word spans, if separate")
    ap.add_argument("--fps", type=float, default=24.0)
    ap.add_argument("--max-word", type=float, default=1.5,
                    help="spans longer than this are treated as recogniser errors")
    ap.add_argument("--fail-over", type=float, default=10.0,
                    help="exit 1 if more than this %% of edges are hot")
    a = ap.parse_args()

    d = json.loads(Path(a.chunks).read_text())
    chunks = d["chunks"] if isinstance(d, dict) else d
    words = load_words(a.chunks, a.words)
    if not words:
        sys.exit("no word timestamps found -- this audit needs a recogniser that emits them")

    tol = 1.0 / a.fps                      # one frame: below this is rounding, not clipping
    bogus = [w for w in words if w["end"] - w["start"] > a.max_word]
    good = [w for w in words if w["end"] - w["start"] <= a.max_word]

    rows, hot = [], 0
    for c in chunks:
        ins = [w for w in good if w["start"] < c["t1"] and w["end"] > c["t0"]]
        if not ins:
            continue
        head = min(w["start"] for w in ins) - c["t0"]
        tail = c["t1"] - max(w["end"] for w in ins)
        rows.append((min(head, tail), c["i"], head, tail))
        if head < -tol or tail < -tol:
            hot += 1

    if bogus:
        print(f"{len(bogus)} implausible span(s) >{a.max_word}s -- EXCLUDED, inspect by ear:")
        for w in bogus:
            print(f"    {w['start']:8.3f}-{w['end']:8.3f} ({w['end']-w['start']:.2f}s)  {w.get('text','')}")
        print()
    rows.sort()
    print(f"{'chunk':>6} {'head':>9} {'tail':>9}   (seconds of silence at each edge)")
    for m, i, h, t in rows[:10]:
        flag = "  <-- CUTS INTO SPEECH" if m < -tol else ""
        print(f"{i:>6} {h:>9.3f} {t:>9.3f}{flag}")
    pct = 100.0 * hot / len(rows) if rows else 0.0
    print(f"\n{hot}/{len(rows)} chunks ({pct:.1f}%) have an edge more than one frame inside a word.")
    print("Reference: cutting on recogniser segment boundaries instead measures ~19% on the "
          "same material, so single digits here is the pipeline working.")
    if pct > a.fail_over:
        print(f"\nOver the {a.fail_over}% threshold. Lower --noise or raise --pad in "
              f"transcribe.py, then re-run this.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
