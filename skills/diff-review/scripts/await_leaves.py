#!/usr/bin/env python
"""Collect diff-review leaf verdicts from the leaves' own sidecar transcripts.

Polls <home>/projects/*/<session>/subagents/agent-<id>.jsonl (+ .meta.json) until
each leaf's last record shows it finished, prints that leaf's report (its
SubagentHandback message if it made one, else its final text) under a header, then
names whatever is still pending. Read-only; never writes; exit 0 always.
"""
import argparse
import glob
import json
import os
import sys
import time

POLL_SECONDS = 5


def projects_root():
    home = os.environ.get("BALLAST_CLAUDE_HOME") or os.path.expanduser("~/.claude")
    return os.path.join(home, "projects")


def resolve(root, session, leaf_id):
    # Narrow (this session) first; the wide glob is the resumed-session fallback —
    # ids are globally unique, so split sidecars under another uuid still resolve.
    # An empty session (unsubstituted ${CLAUDE_SESSION_ID}) skips straight to wide.
    hits = glob.glob(os.path.join(root, "*", session, "subagents",
                                  "agent-%s.jsonl" % leaf_id)) if session else []
    if not hits:
        hits = glob.glob(os.path.join(root, "*", "*", "subagents", "**",
                                      "agent-%s.jsonl" % leaf_id), recursive=True)
    # Containment: a junction/symlink must not resolve a sidecar outside the tree.
    rroot = os.path.realpath(root)
    return next((h for h in hits if os.path.realpath(h).startswith(rroot)), None)


def last_assistant_record(path):
    # Tail-read only: the last assistant record (blank/undecodable/other lines fall through).
    start = max(0, os.path.getsize(path) - 65536)
    with open(path, "rb") as fh:
        fh.seek(start)
        lines = fh.read().decode("utf-8", "replace").split("\n")
    for line in reversed(lines[1:] if start else lines):  # drop the first partial line
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("type") == "assistant":
            return rec
    return None


def handback_block(block):
    # input.message of a SubagentHandback tool_use block, else None.
    if (isinstance(block, dict) and block.get("type") == "tool_use"
            and block.get("name") == "SubagentHandback"):
        msg = (block.get("input") or {}).get("message")
        return msg if isinstance(msg, str) and msg.strip() else None
    return None


def handback_message(path):
    # A leaf in auto mode reports through a SubagentHandback tool call, and its
    # trailing text is only a recap/LEAF-DONE — so the latest hand-back is the report.
    # Whole-file read, once per finished leaf: a long report can predate the tail
    # window. Fails open (None -> the caller keeps the trailing text).
    try:
        with open(path, "rb") as fh:
            lines = fh.read().decode("utf-8", "replace").split("\n")
    except OSError:
        return None
    for line in reversed(lines):
        if "SubagentHandback" not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict) or rec.get("type") != "assistant":
            continue
        content = (rec.get("message") or {}).get("content") or []
        for b in reversed(content if isinstance(content, list) else []):
            msg = handback_block(b)
            if msg is not None:
                return msg
    return None


def leaf_done(rec):
    # (final_text, is_error) for a finished assistant turn, else None.
    if not isinstance(rec, dict) or rec.get("type") != "assistant":
        return None
    content = (rec.get("message") or {}).get("content") or []
    # An error record is done even with empty/non-text content — check it first.
    if rec.get("isApiErrorMessage"):
        return next((b.get("text") or "" for b in reversed(content)
                     if isinstance(b, dict) and b.get("type") == "text"), ""), True
    last = content[-1] if content else None
    hb = handback_block(last)
    if hb is not None:                  # the hand-back itself delivers the report
        return hb, False
    if not isinstance(last, dict) or last.get("type") != "text":
        return None
    text = last.get("text") or ""
    if (any(ln.strip() == "LEAF-DONE" for ln in text.split("\n"))
            or (rec.get("message") or {}).get("stop_reason") == "end_turn"):
        return text, False
    return None


def header(path, leaf_id, is_error):
    # ## <description | agentType | id> (<id>)[ (error)]; meta may be absent/partial.
    label = leaf_id
    meta = os.path.join(os.path.dirname(path), "agent-%s.meta.json" % leaf_id)
    try:
        with open(meta, encoding="utf-8", errors="replace") as fh:
            d = json.load(fh)
        label = d.get("description") or d.get("agentType") or leaf_id
    except (OSError, ValueError):
        pass
    return "## %s (%s)%s" % (label, leaf_id, " (error)" if is_error else "")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default="", nargs="?", const="",
                    help="narrows the sidecar search to one session; optional "
                         "(a bare --session or an unsubstituted ${CLAUDE_SESSION_ID} arrives empty)")
    ap.add_argument("--ids", required=True)
    ap.add_argument("--timeout", type=float, default=540)
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    root = projects_root()
    ids = [t[len("agent-"):] if t.startswith("agent-") else t
           for t in (x.strip() for x in args.ids.split(",")) if t]
    paths, done = {}, set()
    deadline = time.monotonic() + args.timeout
    while True:
        for leaf_id in ids:
            if leaf_id in done:
                continue
            path = paths.get(leaf_id) or resolve(root, args.session, leaf_id)
            if path is None:
                continue
            paths[leaf_id] = path                        # cache once found
            result = leaf_done(last_assistant_record(path))
            if result is None:
                continue
            text, is_error = result
            text = handback_message(path) or text
            done.add(leaf_id)
            print("%s\n\n%s\n" % (header(path, leaf_id, is_error), text))
            sys.stdout.flush()
        if len(done) == len(ids) or time.monotonic() >= deadline:
            break
        time.sleep(POLL_SECONDS)
    print("PENDING: %s" % (",".join(i for i in ids if i not in done) or "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
