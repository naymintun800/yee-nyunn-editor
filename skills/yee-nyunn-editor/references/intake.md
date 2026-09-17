# Intake — getting the footage off the device intact

Read this before the first copy. Every rule here is a shoot that had to be re-edited.

## The corruption that passes every naive check

Copying from a phone or camera over FUSE (ifuse, MTP, gvfs) with concurrent reads can
write replies to the **wrong file offsets**. The result:

- correct byte size
- correct duration
- opens and plays
- **three reads of the same file produce three different checksums**

One measured case: corruption began at exactly 34 MiB — a 1 MiB block boundary — and
spanned 20 whole blocks. Eight successive versions of an edit were rejected for
"glitches" that were blamed on the camera, the cable and the filming before anyone
checked the transfer.

**Mount single-threaded.**

```bash
ifuse /mnt/phone -s          # -s is not optional
```

With `-s`: three reads, three identical checksums, and no speed cost (18.2 s vs 17.5 s
for 565 MB). `-o direct_io` and `-o sync_read` are rejected by fuse3; `-s` is the fix.
The mountpoint comes first: `ifuse MNT -s`.

## Verify every file by decoding it

```bash
ffmpeg -v error -i FILE -f null -
```

Three things about this command:

- **No `-an`.** A video-only scan passed a pull that had 113 AAC decode errors beside its
  174 video ones. The whole edit would have been cut from damaged audio.
- **No `-ss`.** Seeking into a file produces decode errors that are artefacts of the seek,
  not the file. Scan sequentially.
- **Size comparison proves nothing** on its own, because the corruption preserves size.
  Trust the decode.

Write a marker file next to each verified source so a re-run never silently re-copies:

```bash
ffmpeg -v error -i "$f" -f null - 2>"$f.err" && [ ! -s "$f.err" ] && touch "$f.ok"
```

Only delete the device's copies once every file has its marker.

## Intake reports on what you asked for, not on what exists

A run that reports `fail=0` means "every file I was told to copy arrived intact". It says
nothing about the clip you forgot to list. One shoot lost its cold-open demo this way — it
sat on the phone, unpulled, while the intake reported success.

**List the device and reconcile against your shot list before transcoding.**

## Device-specific notes

- **iOS**: Settings → Photos → **Keep Originals**, or the phone transcodes on transfer.
- **Camera apps** often store outside DCIM, in their own container:
  `ifuse MNT -s --documents com.example.CameraApp`, then look in `Media/`. There is
  frequently a lower-resolution `Proxy/` copy alongside the real one — check which you
  are taking.
- **Filenames from iOS can contain U+202F** (narrow no-break space), not a regular space.
  Quote paths and do not assume `\s` matches it.
- A FUSE mount goes stale when the device sleeps or is unplugged (`ls` →
  `Input/output error`). Unmount with `fusermount -u` and re-mount with `-s` rather than
  concluding the device is broken; `idevice_id -l` is the real liveness test.

## Mezzanine

Transcode once to an edit-friendly intermediate: constant frame rate, one resolution,
48 kHz stereo. Phone footage is variable frame rate and often 10-bit HLG, and every
frame-count calculation downstream assumes CFR.

The **look** — tonemapping, grade — is the one irreversible choice here. Ask before
converting, and keep the untouched originals.
