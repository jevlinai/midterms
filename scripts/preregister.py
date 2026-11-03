"""Pre-registration: freeze the method before the first polls close (Tue 3 Nov 2026, 6:00pm ET).

  uv run python scripts/preregister.py check     # what would be frozen, and what still blocks the freeze
  uv run python scripts/preregister.py freeze    # write prereg/method-<stamp>.tar + PREREGISTRATION.md

Freeze:
1. pins the TypeSafe model in detector/questions.json to the exact version the API reports now;
2. builds a deterministic tar of the method files (sorted names, fixed mtimes and owners), so anyone
   can rebuild it from the commit and get the same SHA-256;
3. writes PREREGISTRATION.md with the hash, the file list with per-file hashes, and committed_at.
Then commit both and post the hash from @jevlinai (by hand).

Verify later (anyone): `uv run python scripts/preregister.py verify` rebuilds the tar from the working
tree and compares it with PREREGISTRATION.md.
"""
import datetime as dt
import hashlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tarfile
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
ET = dt.timezone(dt.timedelta(hours=-5))
METHOD_FILES = [
    "data/races.json",
    "data/house-competitive.json",
    "poller/config.json",
    "poller/poller.py",
    "detector/questions.json",
    "detector/textmatch.py",
    "detector/jev.py",
    "detector/detect.py",
    "state/build_state.py",
    "docs/SCORING.md",
]


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def build_tar():
    """Deterministic tar: same bytes for the same file contents, on any machine."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name in sorted(METHOD_FILES):
            data = (ROOT / name).read_bytes()
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = len(data), 0, 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def jev_version():
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        sys.exit("TYPESAFE_API_KEY not set (load ~/.config/midterms/env)")
    body = {"model": "jev-latest", "state": {"page": "x"}, "questions": {"q": {"type": "noul", "instructions": "Is this text empty?"}}}
    req = urllib.request.Request("https://api.typesafe.ai/v1/systemone", json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", "authorization": f"Bearer {key}"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())["model"]


def blockers():
    out = []
    races = json.loads((ROOT / "data" / "races.json").read_text())
    if "DRY RUN" in races.get("status", "") or (ROOT / "DRYRUN").exists():
        out.append("this is a dry-run checkout")
    unchecked = [r["id"] for r in races["races"] if r.get("review", {}).get("status") != "checked"]
    if unchecked:
        out.append(f"{len(unchecked)} races not checked by a person yet (data/REVIEW.md), e.g. {unchecked[:3]}")
    # One person per race since 3 Nov (election night is staffed by one person; scripts/review.py need()).
    nobody = [r["id"] for r in races["races"] if r.get("review", {}).get("status") == "checked"
              and not r["review"].get("checked_by")]
    if nobody:
        out.append(f"{len(nobody)} races marked checked with no name")
    dirty = subprocess.run(["git", "status", "--porcelain", "--", *METHOD_FILES], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if dirty:
        out.append("method files have uncommitted changes:\n    " + dirty.replace("\n", "\n    "))
    return out


def write_md(tar_hash, files, model, at, commit):
    rows = "\n".join(f"| `{n}` | `{h}` |" for n, h in files)
    (ROOT / "PREREGISTRATION.md").write_text(f"""# Pre-registration

The method for measuring which search engine finds US election results first was frozen **before the first polls closed** (Tue 3 Nov 2026, 6:00pm ET).

- **SHA-256 of the method archive:** `{tar_hash}`
- committed_at: {at}
- **Parent commit:** `{commit}`
- **Detector model:** TypeSafe `{model}` (pinned in `detector/questions.json`)

The archive is a deterministic tar of the files below (sorted names, zeroed timestamps and owners), built by `scripts/preregister.py`. To check it, run `uv run python scripts/preregister.py verify` on this commit.

| File | SHA-256 |
|---|---|
{rows}

Any change after this point is logged in `CHANGES.md` with a timestamp, and published.
""")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "verify":
        text = (ROOT / "PREREGISTRATION.md").read_text()
        got = sha256(build_tar())
        print("match" if got in text else f"MISMATCH: rebuilt {got}")
        sys.exit(0 if got in text else 1)
    b = blockers()
    print("method files:", *(f"  {f}" for f in METHOD_FILES), sep="\n")
    print(f"current archive SHA-256 (before pinning): {sha256(build_tar())}")
    if cmd == "check":
        print("BLOCKERS:" if b else "no blockers", *(f"  - {x}" for x in b), sep="\n")
        return
    if cmd != "freeze":
        sys.exit("usage: preregister.py check|freeze|verify")
    if b and "--force" not in sys.argv:
        sys.exit("refusing to freeze; fix the blockers above (or --force with Hammad's explicit OK)")
    q = json.loads((ROOT / "detector" / "questions.json").read_text())
    q["model"] = jev_version()
    (ROOT / "detector" / "questions.json").write_text(json.dumps(q, indent=1, ensure_ascii=False) + "\n")
    tar = build_tar()
    out = ROOT / "prereg"
    out.mkdir(exist_ok=True)
    stamp = dt.datetime.now(ET)
    (out / f"method-{stamp:%Y%m%dT%H%M}.tar").write_bytes(tar)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    files = [(n, sha256((ROOT / n).read_bytes())) for n in sorted(METHOD_FILES)]
    write_md(sha256(tar), files, q["model"], stamp.isoformat(timespec="seconds"), commit)
    print(f"\nFROZEN. SHA-256 {sha256(tar)}, Jev {q['model']}. Now: git add detector/questions.json "
          f"PREREGISTRATION.md && git commit && git push; then post the hash from @jevlinai. "
          f"(The tar in prereg/ is rebuildable from the commit; `verify` checks it.)")


if __name__ == "__main__":
    main()
