# Yee Nyunn's Editor

A Claude Code plugin that teaches an agent to edit long-form video end to end — raw
camera and screen recordings into an upload-ready master.

It is not a wrapper around ffmpeg. It is the set of **contracts between editing stages**,
and the traps that break each one, learned from shipping real videos: transfers that copy
the right number of bytes but the wrong ones, cuts made on timestamps that don't belong to
the words, renders that drift 2.7 seconds by the end, caches that serve stale graphics,
and generators that quietly delete a human's work.

## Install

### Claude Code

```
/plugin marketplace add naymintun800/yee-nyunn-editor
/plugin install yee-nyunn-editor
```

### Codex, or any agent following the Agent Skills standard

```bash
curl -fsSL https://raw.githubusercontent.com/naymintun800/yee-nyunn-editor/main/install.sh | bash
```

That copies one directory and writes nothing else. It picks the skills directory your
client actually uses — `~/.agents/skills` or `~/.codex/skills`, whichever exists —
and re-running it updates in place. `--dir <path>` to choose, `--uninstall` to remove.

If you would rather read a script before running it (fair — it is 60 lines):

```bash
curl -fsSL https://raw.githubusercontent.com/naymintun800/yee-nyunn-editor/main/install.sh -o install.sh
less install.sh && bash install.sh
```

Or do it by hand:

```bash
git clone https://github.com/naymintun800/yee-nyunn-editor
mkdir -p ~/.agents/skills && cp -r yee-nyunn-editor/skills/yee-nyunn-editor ~/.agents/skills/
```

The `.claude-plugin/` manifests are Claude Code specific and are simply ignored elsewhere.
Two things to know when running outside Claude Code:

- **`references/nara-mcp.md` names MCP tools the way Claude Code exposes them**
  (`mcp__nara__transcribe`). Your client will name them differently. The same file
  documents the plain HTTP API, which is what the bundled scripts actually use, so
  nothing breaks — only the tool names in that one document are client-specific.
- **The graphics step defers to the upstream HyperFrames skills.** If your agent cannot
  load those, `references/graphics.md` still covers the integration, and everything from
  the transcript through to delivery is independent of them.

### Just the scripts

They have no agent dependency at all — Python 3 standard library plus ffmpeg:

```bash
skills/yee-nyunn-editor/scripts/transcribe.py recording.mov chunks.json
skills/yee-nyunn-editor/scripts/verify_edges.py chunks.json
```

## What it does

```
0 intake      verify the bytes            decode-scan BOTH streams
1 mezzanine   normalise once              CFR, one size, one rate
2 transcript  words WITH times            silence-first chunking
3 the cut     decide from text            your overrides outrank the model
4 assembly    running order as data       preview as an EDL, DO NOT RENDER
5 graphics    HyperFrames comps           generated files never overwrite hand edits
6 composite   frame-exact piecewise       content-addressed cache
7 delivery    loudness, then encode       measure the output, not the input
```

Steps 2–4 iterate in seconds. Step 6 is the only expensive one. The whole design keeps the
edit in steps 2–4 for as long as possible, because rendering to look at a cut is the
biggest waste in the process.

## Scripts

Each runs standalone; `--help` on any of them.

| script | what it does |
|---|---|
| `transcribe.py` | Chunk at silence, then transcribe each chunk. Nara Sar, Whisper, or boundaries-only. |
| `build_edl.py` | Running order → mpv EDL. The whole cut, playable in about a second, zero encoding. |
| `levels.py` | Per-source loudness measurement → static gains that balance the mix. |
| `composite.py` | The final render: frame-exact, piecewise, cached by content. |
| `guard.py` | Stops a generator from overwriting a file a human edited. |

## Requirements

- `ffmpeg` / `ffprobe` (any recent build; `h264_nvenc` is used when present, `libx264`
  otherwise)
- `mpv` for previewing
- Python 3.9+, standard library only
- Optional: `faster-whisper` for local ASR, or a `NARA_API_KEY` for Burmese + English
- Optional: [HyperFrames](https://www.npmjs.com/package/hyperframes) for motion graphics —
  the skill routes to the upstream HyperFrames skills rather than duplicating them

## Credit

Built from the production pipeline behind [Yee Nyunn](https://www.youtube.com/@yeenyunn)
(ရည်ညွှန်း), a Burmese AI and technology channel. Every rule in it is a measured failure,
not a preference.

MIT.
