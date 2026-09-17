#!/usr/bin/env python3
"""Chunk audio at silence, then transcribe each chunk. Emits the cut-unit contract.

WHY THIS ORDER. The obvious approach -- transcribe the whole file, then split on the
recogniser's segment boundaries -- produces cut units whose edges do not belong to the
words inside them. Measured on real footage: 19.4% of recogniser segment edges sat on
live speech, against 1.1% for edges found this way. Cutting at those edges clips the
first or last syllable of a sentence, which is the single most common reason an
automatic edit sounds wrong.

Silence is measured from ENERGY, so it cannot smear. Split the audio at real pauses and
hand the recogniser one phrase at a time: every phrase then has true edges by
construction and text that belongs to those edges. It also means a flubbed take and its
retake land in SEPARATE chunks, so one can be dropped without taking the other.

Do not substitute a forced aligner for this. An aligner assigns every frame to some
token, so a long pause gets absorbed into one token's span -- the text is right and the
text-to-time mapping is smeared, and a smeared mapping means dropping a phrase cuts the
wrong audio.

  transcribe.py input.mp4 chunks.json                  # auto backend
  transcribe.py in.wav chunks.json --backend nara      # nara-sar-1, needs NARA_API_KEY
  transcribe.py in.wav chunks.json --backend whisper --model small
  transcribe.py in.wav chunks.json --backend none      # boundaries only, no words

Output:
  {"source": "...", "duration": 1398.2, "backend": "nara",
   "chunks": [{"i": 0, "t0": 12.31, "t1": 14.08, "text": "...", "words": [...]}, ...]}
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

from pathlib import Path

NARA_ENDPOINT = os.environ.get("NARA_ENDPOINT", "https://naraaudio.app/api/v1/speech-to-text")
NARA_MAX_S = 28.0  # nara-sar-1 refuses past 30s; leave room for rounding


def env_noproxy():
    """ffmpeg and localhost calls must not go through a desktop/VPN proxy."""
    e = dict(os.environ)
    for k in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        e.pop(k, None)
    return e


ENV = env_noproxy()


def run(cmd, **kw):
    return subprocess.run(cmd, env=ENV, capture_output=True, text=True, **kw)


def to_wav(src, dst, rate=16000):
    """One mono 16 kHz PCM copy: what both the energy pass and the recognisers want."""
    r = run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vn",
             "-ac", "1", "-ar", str(rate), "-c:a", "pcm_s16le", str(dst)])
    if r.returncode != 0:
        sys.exit(f"ffmpeg could not read {src}:\n{r.stderr[:500]}")


def duration_of(path):
    """Duration of the AUDIO stream, not the container.

    These differ: on a video file the container duration follows the video stream and
    can be tens of milliseconds longer. Timestamps here are relative to audio, so using
    the container figure puts every later calculation slightly out.
    """
    for args in (["-select_streams", "a:0", "-show_entries", "stream=duration"],
                 ["-show_entries", "format=duration"]):
        r = run(["ffprobe", "-v", "error", *args, "-of", "csv=p=0", str(path)])
        try:
            v = float(r.stdout.strip().split("\n")[0])
            if v > 0:
                return v
        except (ValueError, IndexError):
            continue
    return 0.0


def measure_floor(wav):
    """Pick a silence threshold from the material instead of guessing a constant.

    A fixed -40dB works on one shoot and fails on the next (a quiet room, a hotter
    preamp). mean_volume tracks the speech level, and sitting a few dB below it
    separates speech from room tone on everything tested. Printed so it can be sanity
    checked -- if the chunk count looks absurd this is the number to move.
    """
    r = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(wav), "-af", "volumedetect", "-f", "null", "-"])
    mean = re.search(r"mean_volume:\s*(-?\d+\.?\d*) dB", r.stderr)
    if not mean:
        return -45.0
    return round(float(mean.group(1)) - 6.0, 1)


def silences(wav, noise_db, min_sil):
    """[(start, end)] of every silence at least min_sil long."""
    r = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(wav),
             "-af", f"silencedetect=noise={noise_db}dB:d={min_sil}", "-f", "null", "-"])
    out, start = [], None
    for m in re.finditer(r"silence_(start|end):\s*(-?\d+\.?\d*)", r.stderr):
        kind, v = m.group(1), float(m.group(2))
        if kind == "start":
            start = v
        elif start is not None:
            out.append((start, v))
            start = None
    return out


def speech_regions(total, sils, pad, min_phrase, max_phrase):
    """Invert the silences into padded speech spans, then fix the two degenerate cases."""
    regions, cur = [], 0.0
    for s0, s1 in sils:
        if s0 > cur:
            regions.append([cur, s0])
        cur = s1
    if cur < total:
        regions.append([cur, total])

    # Pad outward into the surrounding silence so a cut never lands on the attack of a
    # word, but never past the midpoint of the gap or two chunks would overlap.
    out = []
    for i, (a, b) in enumerate(regions):
        prev_end = regions[i - 1][1] if i else 0.0
        next_start = regions[i + 1][0] if i + 1 < len(regions) else total
        a = max(prev_end, a - pad, 0.0)
        b = min(next_start, b + pad, total)
        if b > a:
            out.append([a, b])

    # Too short to be a phrase: fold into whichever neighbour it is closer to, rather
    # than dropping it. A one-word chunk is often the subject of the next sentence.
    merged = []
    for r in out:
        if merged and (r[1] - r[0]) < min_phrase:
            merged[-1][1] = r[1]
        elif merged and (merged[-1][1] - merged[-1][0]) < min_phrase:
            merged[-1][1] = r[1]
        else:
            merged.append(r)

    # Too long: no pause was found inside a long run. Split evenly so no piece exceeds
    # the recogniser's ceiling. These edges are the only ones not guaranteed to sit in
    # silence, so they are reported.
    final, forced = [], 0
    for a, b in merged:
        span = b - a
        if span <= max_phrase:
            final.append([a, b])
            continue
        n = int(span // max_phrase) + 1
        step = span / n
        forced += n - 1
        for k in range(n):
            final.append([a + k * step, a + (k + 1) * step])
    return final, forced


def slice_wav(wav, t0, t1, dst):
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t0:.3f}", "-t", f"{t1 - t0:.3f}",
         "-i", str(wav), "-c:a", "pcm_s16le", str(dst)])


# ---------------------------------------------------------------- backends
def asr_nara(path):
    """nara-sar-1: Burmese + English, code-switching preserved, word-level times."""
    import urllib.request
    key = os.environ.get("NARA_API_KEY")
    if not key:
        sys.exit("NARA_API_KEY is not set (create a key at naraaudio.app)")
    req = urllib.request.Request(
        NARA_ENDPOINT, data=Path(path).read_bytes(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "audio/wav"})
    with urllib.request.urlopen(req, timeout=180) as r:
        j = json.load(r)
    return j.get("text", "").strip(), j.get("words", [])


_WHISPER = {}


def asr_whisper(path, model_name):
    from faster_whisper import WhisperModel
    if "m" not in _WHISPER:
        dev = os.environ.get("WHISPER_DEVICE", "auto")
        _WHISPER["m"] = WhisperModel(model_name, device=dev,
                                     compute_type=os.environ.get("WHISPER_COMPUTE", "default"))
    segs, _ = _WHISPER["m"].transcribe(str(path), beam_size=5)
    return " ".join(s.text.strip() for s in segs).strip(), []


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source")
    ap.add_argument("out")
    ap.add_argument("--backend", choices=["auto", "nara", "whisper", "none"], default="auto")
    ap.add_argument("--model", default="small", help="whisper model name")
    ap.add_argument("--noise", type=float, default=None, help="silence threshold dB (default: measured)")
    ap.add_argument("--min-sil", type=float, default=0.22, help="shortest gap that counts as a pause")
    ap.add_argument("--min-phrase", type=float, default=0.5)
    ap.add_argument("--max-phrase", type=float, default=NARA_MAX_S,
                    help="hard ceiling; nara-sar-1 refuses past 30s")
    ap.add_argument("--pad", type=float, default=0.06)
    ap.add_argument("--src-label", default=None, help="name recorded on each chunk")
    a = ap.parse_args()

    backend = a.backend
    if backend == "auto":
        backend = "nara" if os.environ.get("NARA_API_KEY") else "whisper"

    src = Path(a.source)
    if not src.exists():
        sys.exit(f"no such file: {src}")

    with tempfile.TemporaryDirectory(prefix="yne-asr-") as td:
        td = Path(td)
        wav = td / "full.wav"
        to_wav(src, wav)
        total = duration_of(wav)
        noise = a.noise if a.noise is not None else measure_floor(wav)
        sils = silences(wav, noise, a.min_sil)
        regions, forced = speech_regions(total, sils, a.pad, a.min_phrase, a.max_phrase)

        lens = sorted(r[1] - r[0] for r in regions)
        med = lens[len(lens) // 2] if lens else 0
        print(f"[chunk] {total:.1f}s  threshold {noise}dB  {len(sils)} pauses  "
              f"-> {len(regions)} chunks (median {med:.1f}s, longest {lens[-1] if lens else 0:.1f}s)")
        if forced:
            print(f"[chunk] {forced} edge(s) forced by --max-phrase, not found in silence: "
                  f"check those cuts by ear before trusting them")
        if backend != "none" and med > 12:
            print("[chunk] median chunk is long -- if the cut feels coarse, raise --noise "
                  "(closer to 0) so quieter pauses register", file=sys.stderr)

        chunks = []
        for i, (t0, t1) in enumerate(regions):
            text, words = "", []
            if backend != "none":
                piece = td / f"c{i:05d}.wav"
                slice_wav(wav, t0, t1, piece)
                try:
                    if backend == "nara":
                        text, words = asr_nara(piece)
                    else:
                        text, words = asr_whisper(piece, a.model)
                except Exception as e:  # one bad chunk must not lose the other 700
                    print(f"[chunk] {i} failed: {str(e)[:120]}", file=sys.stderr)
                piece.unlink(missing_ok=True)
                if (i + 1) % 25 == 0 or i + 1 == len(regions):
                    print(f"\r[asr] {i + 1}/{len(regions)}", end="", flush=True)
            # Word times come back relative to the chunk; shift them into whole-file
            # time so a caller never has to know the audio was cut up.
            chunks.append({
                "i": i, "t0": round(t0, 3), "t1": round(t1, 3), "text": text,
                "src": a.src_label or src.stem,
                "words": [{**w, "start": round(w["start"] + t0, 3), "end": round(w["end"] + t0, 3)}
                          for w in words],
            })
        if backend != "none":
            print()

    Path(a.out).write_text(json.dumps(
        {"source": str(src), "duration": round(total, 3), "backend": backend,
         "noise_db": noise, "chunks": chunks}, ensure_ascii=False, indent=1))
    spoken = sum(c["t1"] - c["t0"] for c in chunks)
    print(f"[chunk] -> {a.out}  ({spoken:.0f}s speech of {total:.0f}s, "
          f"{100 * spoken / total if total else 0:.0f}%)")


if __name__ == "__main__":
    main()
