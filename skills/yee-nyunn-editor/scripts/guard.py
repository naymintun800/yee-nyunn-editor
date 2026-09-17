#!/usr/bin/env python3
"""Stop a generator from overwriting a file a human edited.

THE FAILURE THIS PREVENTS. A script generates a file (a composition, a config, a
template). A human then edits that same file in an editor or on a canvas. The next
time the generator runs it rewrites the file from scratch and their work is gone --
silently, with no diff, and usually just after someone promised it was safe.

Losing hand-authored content is not like other bugs: you cannot re-derive it. If the
text was in a language you do not write, or it was a judgment call about phrasing, it
is simply gone. So the protection has to be structural rather than a habit.

HOW IT WORKS. `stamp` records a hash of exactly what the generator wrote. `check`
compares the file on disk against that hash and fails if it differs.

THE ASYMMETRY THAT MATTERS: a file with NO stamp also fails. Unknown provenance is
treated exactly like a hand edit, because the cost of refusing to regenerate a file
that nobody touched is one extra command, and the cost of the opposite mistake is
somebody's work. Do not "fix" a failing check by forcing it -- ask the human.

  guard.py check FILE...        exit 0 = safe to overwrite, 1 = do not touch
  guard.py stamp FILE...        record what you just generated
  guard.py backup FILE          snapshot before a deliberate overwrite
  guard.py status [FILE...]     what is stamped, what has diverged

The stamp file lives next to the guarded files as .guard-stamps.json by default;
override with GUARD_STAMPS=/path/to/stamps.json.
"""
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path


def stamps_path(files):
    env = os.environ.get("GUARD_STAMPS")
    if env:
        return Path(env)
    base = Path(files[0]).resolve().parent if files else Path.cwd()
    return base / ".guard-stamps.json"


def load(p):
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def digest(f):
    return hashlib.sha256(Path(f).read_bytes()).hexdigest()


def key(f):
    return str(Path(f).resolve())


def cmd_check(files):
    sp = stamps_path(files)
    st = load(sp)
    blocked = []
    for f in files:
        p = Path(f)
        if not p.exists():
            continue  # nothing to lose; generating a new file is always fine
        known = st.get(key(f))
        cur = digest(f)
        if known is None:
            blocked.append((f, "no stamp -- unknown provenance, treated as hand-edited"))
        elif known != cur:
            blocked.append((f, "changed since it was generated -- hand-edited"))
    for f, why in blocked:
        print(f"REFUSE  {f}\n        {why}", file=sys.stderr)
    if blocked:
        print(
            "\nThese files were not overwritten. To regenerate one deliberately:\n"
            "  guard.py backup FILE && <your generator> && guard.py stamp FILE\n"
            "Ask the human first -- a refusal usually means their edit is in there.",
            file=sys.stderr,
        )
        return 1
    print(f"ok: {len(files)} file(s) safe to overwrite")
    return 0


def cmd_stamp(files):
    sp = stamps_path(files)
    st = load(sp)
    n = 0
    for f in files:
        if not Path(f).exists():
            print(f"skip (missing): {f}", file=sys.stderr)
            continue
        st[key(f)] = digest(f)
        n += 1
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(st, indent=1, sort_keys=True))
    print(f"stamped {n} file(s) -> {sp}")
    return 0


def cmd_backup(files):
    for f in files:
        p = Path(f)
        if not p.exists():
            print(f"skip (missing): {f}", file=sys.stderr)
            continue
        d = p.parent / ".guard-backups"
        d.mkdir(parents=True, exist_ok=True)
        dst = d / f"{p.stem}.{time.strftime('%Y%m%d_%H%M%S')}{p.suffix}"
        shutil.copy2(p, dst)
        print(f"backed up -> {dst}")
    return 0


def cmd_status(files):
    sp = stamps_path(files or ["."])
    st = load(sp)
    targets = files or [k for k in st]
    if not targets:
        print(f"no stamps recorded ({sp})")
        return 0
    print(f"{'state':<12} file")
    for f in targets:
        p = Path(f)
        if not p.exists():
            state = "missing"
        elif key(f) not in st:
            state = "UNSTAMPED"
        elif st[key(f)] == digest(f):
            state = "generated"
        else:
            state = "HAND-EDITED"
        print(f"{state:<12} {f}")
    return 0


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, files = sys.argv[1], sys.argv[2:]
    fns = {"check": cmd_check, "stamp": cmd_stamp, "backup": cmd_backup, "status": cmd_status}
    if cmd not in fns:
        sys.exit(f"unknown command {cmd!r}\n{__doc__}")
    if cmd != "status" and not files:
        sys.exit(f"{cmd} needs at least one file")
    return fns[cmd](files)


if __name__ == "__main__":
    sys.exit(main())
