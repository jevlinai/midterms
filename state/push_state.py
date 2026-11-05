"""Push state/state.json to Workers KV, but only when the results changed (ignoring generated_at),
or every 30 minutes so the page's timestamp stays fresh. Workers KV's free tier allows 1,000
writes a day and the state job runs every minute. Kept out of build_state.py, which is a
pre-registered method file.

  python3 state/push_state.py
"""
import hashlib
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from confirm.confirm import load, save_atomic  # noqa: E402
from detector.races import ROOT  # noqa: E402
from state.build_state import push_kv  # noqa: E402


def main():
    state = json.loads((ROOT / "state" / "state.json").read_text())
    mark = ROOT / "state" / "last-push.json"
    sha = hashlib.sha256(json.dumps({k: v for k, v in state.items() if k != "generated_at"}, sort_keys=True).encode()).hexdigest()
    last = load(mark, {})
    if sha == last.get("sha") and time.time() - last.get("at", 0) < 1800:
        print("state unchanged, not pushed")
        return
    push_kv(state)
    save_atomic(mark, {"sha": sha, "at": time.time()})
    print("pushed to KV")


if __name__ == "__main__":
    main()
