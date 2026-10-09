"""Durable, coalesced dispatch; review policy remains in DraftReviewer."""
from __future__ import annotations
import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

from draft_review import DraftReviewer, GatewayJudge, ReviewLease
from knowledge_guard import guard
from knowledge_pipeline import atomic_json

RESERVATION_SECONDS = 60

def marker_path(brain):
    return brain.runtime / "draft-review-pending.json"

def read_marker(brain):
    try:
        value = json.loads(marker_path(brain).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}

def launch(brain):
    if os.name != "nt":
        raise OSError("Hidden Windows dispatch requires Windows")
    subprocess.Popen([str(pathlib.Path(os.environ["SystemRoot"]) / "System32" / "wscript.exe"),
                      "//B", "//NoLogo", str(brain.root / "Run-DraftReviewer.hidden.vbs"), "immediate"],
                     cwd=brain.root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)

def signal(brain, launcher=None):
    if not brain.settings.get("features", {}).get("auto_draft_review", False):
        return {"status": "disabled"}
    with guard(brain):
        value = read_marker(brain)
        value.update(version=1, generation=int(value.get("generation", 0)) + 1, pending=True)
        lease = ReviewLease(brain.runtime)
        if not lease.acquire():
            atomic_json(marker_path(brain), value)
            return {"status": "coalesced"}
        lease.release()
        if value.get("launch_reserved_until", 0) > time.time():
            atomic_json(marker_path(brain), value)
            return {"status": "coalesced"}
        value["launch_reserved_until"] = time.time() + RESERVATION_SECONDS
        atomic_json(marker_path(brain), value)
        try:
            (launcher or launch)(brain)
        except Exception as exc:
            value.update(launch_reserved_until=0, error_type=type(exc).__name__)
            atomic_json(marker_path(brain), value)
            return {"status": "pending"}
        return {"status": "dispatched"}

def after_commit(brain, committed, kind):
    if kind not in ("writeback", "synthesis") or not committed.get("committed"):
        return
    # The durable draft survives even if dispatch/marker storage fails.
    try:
        signal(brain)
    except Exception:
        pass

def unfinished(reviewer):
    return [path for path in reviewer.candidates() if reviewer.is_unfinished(path)]

def drain(brain, limit=16, seconds=300, reviewer=None, finalized=None):
    lease = ReviewLease(brain.runtime)
    if not lease.acquire():
        return {"status": "coalesced", "processed": 0, "pending": True}
    deadline = time.monotonic() + seconds
    reviewer = reviewer or DraftReviewer(brain, GatewayJudge(brain, require_ready=True, deadline=deadline), require_committed=True)
    counts = {}; processed = 0; error = None
    try:
        with guard(brain):
            value = read_marker(brain)
            value.update(version=1, pending=True, launch_reserved_until=0)
            atomic_json(marker_path(brain), value)
        stopped = False
        attempted = set()
        while True:
            with guard(brain):
                current = unfinished(reviewer)
                todo = [path for path in current if path not in attempted]
                if stopped or not todo or processed >= limit or time.monotonic() >= deadline:
                    pending = bool(current)
                    value = read_marker(brain)
                    value.update(version=1, pending=pending, launch_reserved_until=0)
                    if error:
                        value["error_type"] = error
                    else:
                        value.pop("error_type", None)
                    atomic_json(marker_path(brain), value)
                    lease.release()
                    if finalized:
                        finalized()
                    break
            for path in todo:
                if processed >= limit or time.monotonic() >= deadline:
                    break
                attempted.add(path)
                try:
                    result = reviewer.review(path)
                    state = result["state"]
                    if state not in ("skipped", "unchanged"):
                        processed += 1
                        counts[state] = counts.get(state, 0) + 1
                    if state == "deferred":
                        stopped = True
                        break
                except Exception as exc:
                    error = type(exc).__name__
                    stopped = True
                    break
        result = {"status": "pending" if pending else "drained", "processed": processed,
                  "states": counts, "pending": pending}
        if error:
            result["error_type"] = error
        return result
    finally:
        lease.release()

def run(root, limit=16, seconds=300):
    from localbrain import Brain
    root = pathlib.Path(root).resolve()
    settings = json.loads((root / "config" / "secondbrain.json").read_text(encoding="utf-8"))
    if not settings.get("features", {}).get("auto_draft_review", False):
        return {"status": "disabled", "processed": 0}
    # Read-only view: no DB/config initialization, model request, or knowledge writes.
    view = object.__new__(Brain)
    view.root = root; view.docs = root / "SecondBrain"; view.runtime = root / "runtime"
    if not unfinished(DraftReviewer(view, require_committed=True)) and not read_marker(view).get("pending"):
        return {"status": "quiet", "processed": 0, "pending": False}
    return drain(Brain(root), limit, seconds)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, default=pathlib.Path(__file__).resolve().parents[1])
    parser.add_argument("--limit", type=int, default=16)
    parser.add_argument("--seconds", type=int, default=300)
    args = parser.parse_args()
    if not 1 <= args.limit <= 64 or not 1 <= args.seconds <= 600:
        parser.error("bounded limit/seconds required")
    try:
        result = run(args.root, args.limit, args.seconds)
    except Exception as exc:
        result = {"status": "pending", "pending": True, "error_type": type(exc).__name__}
    print(json.dumps(result, ensure_ascii=True))
    return 1 if result.get("error_type") else 0

if __name__ == "__main__":
    sys.exit(main())
