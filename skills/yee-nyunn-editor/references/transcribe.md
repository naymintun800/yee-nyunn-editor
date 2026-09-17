# Transcript — backends, contracts, and scripts without word spaces

## The division of labour

> The recogniser tells you WHERE. A reading pass tells you WHAT. You decide the cut.

This is not a style preference; it is what the tools are actually good at. Recognisers
place boundaries well and misread words — especially names, product names, and any
language mixed into another. A model reading the transcript understands meaning and
cannot place a timestamp to save its life (3–6 s of drift, measured). Take timing from
one and meaning from the other, and never let either make the final call.

A concrete case: a speaker slates every take in English — "ok this is the chatterbox
turbo testing clip" — and a Burmese-tuned recogniser rendered it as
`အိုကေ ဒီလောက်ပါဦး။`, unrecognisable as a slate. Every slate survived into the cut,
because nothing downstream could tell it was one.

## Backends

`scripts/transcribe.py --backend {auto,nara,whisper,none}`

**nara** — `nara-sar-1`, Burmese + English with code-switching preserved (Burmese stays
Burmese, English loanwords stay Latin). Word-level timestamps. Needs `NARA_API_KEY`; see
`nara-mcp.md`. Hard limit of **30 s per request** — which is why the script chunks first,
and why chunking is a requirement rather than a nicety.

**whisper** — `faster-whisper`, any model size, runs locally. Fine for timing. Do **not**
enable `word_timestamps=True` on a fine-tuned model: it is unreliable there, and for
languages Whisper treats as space-free the "words" are syllable fragments with fuzzy
edges.

**none** — boundaries only. Useful when you want cut units immediately and will get text
later, and for checking that your silence threshold is sane before spending anything.

## The contract

```json
{"source": "cam_main.mov", "duration": 1398.2, "backend": "nara", "noise_db": -45.0,
 "chunks": [
   {"i": 0, "t0": 12.31, "t1": 14.08, "text": "...", "src": "cam_main",
    "words": [{"text": "...", "start": 12.35, "end": 12.61, "conf": -0.04}]}
 ]}
```

`i` is stable and is what every later stage refers to. When you transcribe several
sources into one cut, give each a distinct `--src-label`, and **renumber so indices
cannot collide** — an index that means two different chunks is a whole afternoon.

`words` are shifted into whole-file time by the script, so nothing downstream needs to
know the audio was cut into pieces. `conf` from nara-sar-1 is a log-probability (0 is
certain, more negative is less), not a 0-1 score -- do not threshold it as a percentage.

Measured on a real 7.53 s silence-bounded chunk: the first word began at 0.241 s and the
last ended at 7.239 s — 241 ms of head margin, 288 ms of tail.

Across the whole 2:42 recording, though, **2 of 46 chunks (4.3%) had an edge more than one
frame inside a word.** Good, not perfect. Run `scripts/verify_edges.py` on every shoot
rather than trusting the method, and note the caveat it documents: one recogniser span in
that file was timed at 4.94 s for a single word, which straddled three chunks and made the
chunker look wrong when the chunker was right.

## Tuning the chunker

The script measures a threshold from the material (`mean_volume − 6 dB`) and prints it,
along with the chunk count and median length. Sanity-check those numbers rather than
trusting the default:

| symptom | cause | fix |
|---|---|---|
| very few, very long chunks | threshold too low, pauses not registering | raise `--noise` toward 0 |
| hundreds of fragments | threshold too high, catching breath as speech | lower `--noise` |
| edges clip word attacks | not enough padding | raise `--pad` |
| retake and keeper share a chunk | pause between them shorter than `--min-sil` | lower `--min-sil` |

A healthy long-form talking-head profile: median 1.8–2.5 s, longest under 12 s, 80–90% of
the runtime classified as speech.

Edges the script had to force (a run longer than `--max-phrase` with no pause inside) are
reported. They are the only edges not guaranteed to sit in silence — check those by ear.

## Languages without word spaces

Burmese, Thai, Lao, Khmer, Japanese and Chinese do not separate words with spaces, which
breaks several common assumptions:

- **Word-level timestamps are really phrase-level.** Do not promise sub-word precision.
- **A grapheme forced aligner blurs Latin runs.** Loanwords ("Google", "sorry") are
  out-of-vocabulary for a tokenizer built on the local script, and their spans compress
  or smear. Never place a cut on an aligner edge inside a Latin-script run — snap it to
  the nearest silence instead.
- **Never sort aligner spans by time** when reconstructing text. Walk TEXT order and
  clamp times forward; sorting by frame scrambles combining marks into unreadable
  output.
- **On-screen text: letter-spacing must be 0.** Tracking splits combining clusters — a
  Burmese syllable renders as two broken pieces. Keep wide tracking for pure-Latin spans
  only. This one is invisible to anyone who does not read the script, so it ships.
- **Never author the text yourself** in a language you do not read. Use the speaker's own
  words from the transcript, and have a native speaker check anything generated. When a
  human corrects that text, see the generator rule in `graphics.md` — their correction is
  the single most destructible artefact in the project.

## Sanity-check the model's INPUT, not only its output

If a model drops an implausible share of the transcript as "filler", print the text it
actually received before blaming its judgment. One garbled-encoding bug made a model drop
235 of 350 phrases; its reasoning was sound and the input was gibberish.
