# Nara — ASR and generation for this pipeline

[Nara](https://naraaudio.app) is a Burmese-first speech and video service. It matters here
for two separate reasons: **transcription** (the pipeline's step 2) and **generation**
(making example footage, voice-over, or B-roll that the edit then uses).

Everything below is optional. The pipeline works with local Whisper and no Nara account.

## Transcription — `nara-sar-1`

Burmese + English, code-switching preserved: Burmese stays in Burmese script, English
loanwords stay Latin. That property is what makes it usable for editing — a recogniser
that forces English through the local script turns every English slate and product name
into something no downstream reader can match.

Returns `words`: `{text, start, end, conf}` in seconds from the start of the submitted
audio. On Burmese these are phrase-sized, because the script has no spaces between words.

**The 30-second ceiling is the important constraint.** The model is trained on
utterance-length clips and refuses longer requests, so a 25-minute recording has to be
chunked before it is sent. This is not an obstacle to work around — it is the same
silence-first chunking the cut needs anyway, so do it once and both problems are solved:

```bash
export NARA_API_KEY=nara_sk_...            # create one at naraaudio.app
scripts/transcribe.py recording.mov chunks.json --backend nara
```

That script chunks locally and posts each piece, which is what you want when you need the
chunk contract the rest of this pipeline reads. If you only need a transcript and have the
MCP connected, the `transcribe` tool's `file_path` mode is less work — see below.

Cost: **1 credit per 20 s of audio**, minimum 1, charged only on a successful decode. A
25-minute recording is ~75 credits. Quote that before starting a long job.

Direct API, if you are building something else on it:

```
POST https://naraaudio.app/api/v1/speech-to-text
Authorization: Bearer nara_sk_...
Content-Type: audio/wav          (or multipart/form-data with a "file" field)
body: the audio bytes, <= 30 s, <= 15 MB
-> { text, words, seconds, model, credits_spent, balance }
```

## The `transcribe` MCP tool

Three sources, and which one you pick matters more than it looks:

| input | what happens | use it for |
|---|---|---|
| `url` | fetched and decoded server-side | anything already on the web |
| `data_base64` + `content_type` | decoded server-side | one SHORT clip only |
| `file_path` (alone) | returns an upload command you run | anything on local disk |

**`file_path` is the one to use.** It hands back a single `curl` carrying a short-lived
grant: the server chunks and decodes, and replies with the whole transcript. No API key,
no local chunking. Measured on a 2:42 recording: 244 timed words in 50 s for 9 credits.

Two practical limits the note on the tool does not mention:

- **Upload audio, not video.** The command posts the file as-is and the endpoint rejects
  a large one with `413`. Extract first — a 2:42 mezzanine is 252 MB of video and 5 MB of
  16 kHz mono WAV:
  ```bash
  ffmpeg -v error -y -i INPUT -vn -ac 1 -ar 16000 -c:a pcm_s16le audio.wav
  ```
- **`data_base64` does not scale.** It is the only mode needing no grant, but the audio
  travels through the model's context: ~26 k characters for 7 seconds. Fine for a sample,
  hopeless for a recording. Reach for `file_path`.

`seconds` in the reply is the AUDIO duration, which on a video file is not the container
duration — they differed by 64 ms on a real clip. Timestamps are relative to the audio, so
that is the figure to trust.

## Generation — when the edit needs footage that does not exist

The MCP tools: `transcribe`, `get_directing_guide`, `list_voices`, `list_speech_clips`,
`generate_speech`, `generate_video`, `generate_image`, `upscale_video`, `add_reference`,
`update_reference`, `list_references`, `get_job`, `get_balance`.

Order matters, and the guide is not optional:

1. **`get_directing_guide` first, before writing any video prompt.** Video prompts must
   follow an exact section format; free prose renders with dead lips and is rejected.
2. **`list_speech_clips` before generating speech.** If the user refers to a line they
   already made, reuse its id — free. Generating it again bills again.
3. **Show the user the exact text and voice, and get a yes, before `generate_speech`.** It
   bills on success.
4. **References keep faces and places consistent.** Without them every render invents a
   new person and a new room. `add_reference` takes a URL, or a local path (it returns an
   upload command for you to run — never ask the user to upload anything themselves).
5. **`get_job`** polls; clips also land in the account's library and Telegram.

For a multi-scene story, plan before rendering: break it into 4–15 s beats, list the
speech, references and cost per beat, present the total, and only start once approved.
Nara does not stitch beats — you assemble them, which is exactly what the rest of this
skill is for, and it means a bad take can be dropped without re-rendering the others.

## Where generated clips enter the edit

As ordinary blocks in `assembly.json`:

```json
{"kind": "file", "src": "assets/example_shot.mp4", "in": 0.0, "out": 6.5}
```

Two things to check when mixing generated footage with camera footage:

- **Aspect.** Generated clips are often vertical or square. The compositor fits rather
  than stretches, but decide deliberately whether a portrait clip should be pillarboxed or
  cropped.
- **Loudness.** Generated speech and camera speech rarely sit at the same level. Run
  `levels.py` after adding them — this is exactly the spread it exists to fix.
