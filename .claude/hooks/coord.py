#!/usr/bin/env python3
"""Enforces the agent coordination protocol (GitHub Issue #1) via Claude Code hooks.

Usage: coord.py <session-start|prompt|pre-edit|post-edit|post-bash|stop>
Hook input arrives as JSON on stdin. Wired up in .claude/settings.json.

Claims are derived from the issue itself, keyed by GitHub login:
  "... CLAIMING: <task>, touching files: a, b/"  -> author holds a, b/
  "... DONE: <summary>"                          -> author's claims are released
If GitHub is unreachable the hooks fail open (edits are allowed, with a warning).
"""
import fnmatch
import json
import os
import re
import subprocess
import sys
import time

ISSUE = "1"
CACHE_TTL = 45  # seconds
ROOT = os.path.realpath(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
STATE_DIR = os.path.join(ROOT, ".claude", ".coord")

CLAIM_RE = re.compile(r"\bCLAIMING\s*:", re.I)
DONE_RE = re.compile(r"\b(DONE|FINISHED)\s*:", re.I)
FILES_RE = re.compile(r"touching files\s*:\s*(.*)", re.I | re.S)
INTERFACE_RE = re.compile(
    r"(^|/)(types?|schemas?|models?|routes?|api|migrations?|proto|contracts?|interfaces?)(/|\.|$)"
    r"|\.(proto|sql|prisma|graphql)$|openapi|\.d\.ts$",
    re.I,
)


def gh(*args):
    try:
        r = subprocess.run(["gh", *args], cwd=ROOT, capture_output=True, text=True, timeout=15)
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


def _read(name, default=None):
    try:
        with open(os.path.join(STATE_DIR, name)) as f:
            return json.load(f)
    except Exception:
        return default


def _write(name, data):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, name), "w") as f:
        json.dump(data, f)


def me():
    login = _read("me.json")
    if not login:
        out = gh("api", "user", "-q", ".login")
        login = out.strip() if out else None
        if login:
            _write("me.json", login)
    return login


def comments(force=False):
    """All issue comments as [{author, body, at}], or None if GitHub is unreachable."""
    cache = _read("comments.json")
    if cache and not force and time.time() - cache["t"] < CACHE_TTL:
        return cache["c"]
    out = gh("issue", "view", ISSUE, "--json", "comments")
    if out is None:
        return None
    try:
        c = [
            {"author": x["author"]["login"], "body": x["body"], "at": x["createdAt"]}
            for x in json.loads(out)["comments"]
        ]
    except Exception:
        return None
    _write("comments.json", {"t": time.time(), "c": c})
    return c


def invalidate():
    try:
        os.remove(os.path.join(STATE_DIR, "comments.json"))
    except OSError:
        pass


def active_claims(cs):
    """{login: [file patterns]} for claims not yet released by a DONE comment."""
    claims = {}
    for c in cs:
        body = c["body"]
        if CLAIM_RE.search(body):
            m = FILES_RE.search(body)
            pats = []
            if m:
                for tok in re.split(r"[,\s]+", m.group(1)):
                    tok = tok.strip("`'\"()[]").rstrip(".;")
                    if tok.startswith("./"):
                        tok = tok[2:]
                    if tok:
                        pats.append(tok)
            claims.setdefault(c["author"], []).extend(pats)
        elif DONE_RE.search(body):
            claims.pop(c["author"], None)
    return claims


def matches(rel, pats):
    for p in pats:
        d = p.rstrip("/")
        if rel == d or rel.startswith(d + "/") or fnmatch.fnmatch(rel, p):
            return True
    return False


def repo_rel(path, cwd):
    """Repo-relative path of a file in this repo or one of its git worktrees, else None."""
    if not path:
        return None
    full = os.path.realpath(path if os.path.isabs(path) else os.path.join(cwd or ROOT, path))
    if full.startswith(ROOT + os.sep):
        rel = os.path.relpath(full, ROOT)
        m = re.match(r"\.claude/worktrees/[^/]+/(.+)", rel)
        return m.group(1) if m else rel
    d = os.path.dirname(full)
    while d and not os.path.isdir(d):
        d = os.path.dirname(d)
    try:
        r = subprocess.run(["git", "-C", d, "rev-parse", "--show-toplevel", "--git-common-dir"],
                           capture_output=True, text=True, timeout=5)
        top, common = r.stdout.strip().split("\n")[:2]
        if not os.path.isabs(common):
            common = os.path.join(top, common)
        if os.path.realpath(common) == os.path.join(ROOT, ".git"):
            return os.path.relpath(full, os.path.realpath(top))
    except Exception:
        pass
    return None


def fmt(cs):
    return "\n".join("- %s (%s): %s" % (c["author"], c["at"], c["body"].strip()) for c in cs)


def emit(obj):
    print(json.dumps(obj))
    sys.exit(0)


def deny(reason):
    emit({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                 "permissionDecision": "deny",
                                 "permissionDecisionReason": reason}})


def context(event, text):
    emit({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}})


def unseen(state, cs, login):
    """Comments from other people posted since this session last looked."""
    new = [c for c in cs[state.get("seen", 0):] if c["author"] != login]
    state["seen"] = len(cs)
    return new


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    sid = re.sub(r"[^A-Za-z0-9_-]", "", str(data.get("session_id", "default"))) or "default"
    sfile = "session-%s.json" % sid
    state = _read(sfile, {})
    login = me()
    tool_input = data.get("tool_input") or {}

    if event == "session-start":
        cs = comments(force=True)
        if cs is None:
            print("[coordination] WARNING: could not read GitHub Issue #%s (is `gh auth login` done?). "
                  "The coordination protocol in CLAUDE.md still applies." % ISSUE)
            return
        state["seen"] = len(cs)
        _write(sfile, state)
        claims = active_claims(cs)
        print("[coordination] Channel: GitHub Issue #%s. You post as GitHub user `%s`." % (ISSUE, login))
        print("Edits are blocked until you post a CLAIMING comment listing the files you will touch, "
              "and blocked on files claimed by anyone else. See CLAUDE.md.")
        print("\nActive claims:")
        if claims:
            for who, pats in claims.items():
                print("- %s%s: %s" % (who, " (you)" if who == login else "", ", ".join(pats) or "(no files listed)"))
        else:
            print("- none")
        print("\nLatest messages:")
        print(fmt(cs[-10:]) or "- none yet")

    elif event == "prompt":
        cs = comments()
        if cs is None:
            return
        new = unseen(state, cs, login)
        _write(sfile, state)
        if new:
            print("[coordination] New messages on Issue #%s since you last checked:\n%s" % (ISSUE, fmt(new)))

    elif event == "pre-edit":
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        rel = repo_rel(path, data.get("cwd"))
        if rel is None:
            return  # outside the repo: not coordinated
        if rel.startswith(os.path.join(".claude", ".coord")):
            return
        cs = comments()
        if cs is None or not login:
            emit({"systemMessage": "[coordination] GitHub unreachable - claim check skipped for %s" % rel})
        claims = active_claims(cs)
        for who, pats in claims.items():
            if who != login and matches(rel, pats):
                deny("BLOCKED by coordination protocol: `%s` is claimed by %s. Do not edit it. "
                     "Post a request instead: gh issue comment %s --body \"[<owner>-agent] REQUEST for %s: <change needed in %s>\""
                     % (rel, who, ISSUE, who, rel))
        mine = claims.get(login)
        how = ("gh issue comment %s --body \"[<owner>-agent] CLAIMING: <task>, touching files: <comma-separated files or dirs/>\""
               % ISSUE)
        if mine is None:
            deny("BLOCKED by coordination protocol: you have no active claim. First run `gh issue view %s --comments`, "
                 "then claim your task before editing: %s" % (ISSUE, how))
        if not matches(rel, mine):
            deny("BLOCKED by coordination protocol: `%s` is not in your claimed files (%s). "
                 "Post an updated claim that includes it: %s" % (rel, ", ".join(mine) or "none listed", how))

    elif event == "post-edit":
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        rel = repo_rel(path, data.get("cwd"))
        if rel is None:
            return
        state["dirty"] = True
        notes = []
        if INTERFACE_RE.search(rel):
            notes.append("`%s` looks like a shared interface (types / API routes / DB schema). If you changed its "
                         "public shape, post it NOW with the exact new signature: "
                         "gh issue comment %s --body \"[<owner>-agent] INTERFACE: <exact new signature>\"" % (rel, ISSUE))
        cs = comments()
        if cs is not None:
            new = unseen(state, cs, login)
            if new:
                notes.append("New messages on Issue #%s:\n%s" % (ISSUE, fmt(new)))
        _write(sfile, state)
        if notes:
            context("PostToolUse", "[coordination] " + "\n".join(notes))

    elif event == "post-bash":
        if re.search(r"\bgh\s+issue\s+(comment|edit|close|reopen)\b", tool_input.get("command") or ""):
            invalidate()
            state["dirty"] = False
            _write(sfile, state)

    elif event == "stop":
        if data.get("stop_hook_active") or not state.get("dirty"):
            return
        state["dirty"] = False
        _write(sfile, state)
        cs = comments()
        if cs is None or login not in active_claims(cs):
            return
        emit({"decision": "block",
              "reason": "[coordination] You edited files under an active claim and have not posted since. "
                        "If the task is FINISHED, post now: gh issue comment %s --body \"[<owner>-agent] DONE: <what you built>, "
                        "branch/PR: <ref>, notes: <anything others need>\" (this releases your claim). "
                        "If you changed a shared interface, post the exact new signature. "
                        "If the task is still in progress, no post is needed - just end your turn." % ISSUE})


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # never break a session because of the coordination hook
        print("[coordination] hook error: %s" % e, file=sys.stderr)
        sys.exit(0)
