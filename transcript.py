#!/usr/bin/env python3
"""Search Claude Code harness transcripts.

Temporary shim pending a Nexus. Reads JSONL session files from
~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl.
"""

import argparse
import glob
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude" / "projects"
DEFAULT_CONTEXT = 2
DEFAULT_TEXT_CAP = 200


def find_project_dirs():
    """Return all project directories under ~/.claude/projects/."""
    if not CLAUDE_DIR.exists():
        return []
    return sorted(CLAUDE_DIR.iterdir())


def resolve_transcript(spec):
    """Resolve a transcript spec to (path, session_id).

    Spec can be:
      - An absolute path to a .jsonl file
      - A full UUID
      - An 8-hex short id prefix
    """
    p = Path(spec)
    if p.is_file():
        return p, p.stem

    # Search all project dirs for matching session
    for proj_dir in find_project_dirs():
        if not proj_dir.is_dir():
            continue
        for f in proj_dir.glob("*.jsonl"):
            if f.stem == spec or f.stem.startswith(spec):
                return f, f.stem

    return None, None


def parse_record(line_text):
    """Parse a JSONL line, returning None on failure."""
    try:
        return json.loads(line_text)
    except (json.JSONDecodeError, ValueError):
        return None


@dataclass(frozen=True)
class InputClassification:
    """The provenance decision for one possible input turn.

    ``ambiguous`` is intentional: datom-shaped text does not prove who
    authored it, so callers must not silently turn it into a living quote.
    """

    status: str  # living, machine, ambiguous, or not_input
    text: str
    source: str
    reason: str


MACHINE_MARKERS = (
    "<local-command-caveat>",
    "<local-command-stdout>",
    "<command-name>",
    "<system-reminder>",
    "<task-notification>",
    "<environment_context>",
)
RELAY_MARKER = re.compile(r"(?im)^(?:Soft|MiddleAbrupt|HardAbrupt)\.")
DATOM_HEAD = re.compile(r"^\s*[A-Z][A-Za-z0-9_-]*\.")


def _record_text(record):
    """Return text from Claude or Codex message content."""
    msg = record.get("message", {})
    content = msg.get("content", [])
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text = "\n".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict)
            and item.get("type") in ("text", "input_text", "output_text")
        )
        if text:
            return text
    payload = record.get("payload", {})
    item = payload.get("item", {}) if isinstance(payload, dict) else {}
    content = item.get("content", []) if isinstance(item, dict) else []
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") in ("text", "input_text")
        )
    return ""


def classify_input(record):
    """Classify an authenticated Claude/Codex input without guessing authorship."""
    rtype = record.get("type", "")
    source = ""
    if rtype == "user" and record.get("message", {}).get("role") == "user":
        # Claude records identify the user stream with userType. Meta entries
        # are harness-generated even though they carry the user role.
        if record.get("isMeta") is True:
            return InputClassification("machine", _record_text(record), "claude", "meta")
        user_type = record.get("userType")
        if user_type not in ("external", "human"):
            status = "ambiguous" if user_type is None else "machine"
            reason = "missing_authentication" if user_type is None else "user_type"
            return InputClassification(status, _record_text(record), "claude", reason)
        source = "claude_user"
    elif (
        rtype == "event_msg"
        and record.get("payload", {}).get("type") == "item_completed"
        and record.get("payload", {}).get("item", {}).get("type") == "UserMessage"
    ):
        source = "codex_user_message"
    else:
        return InputClassification("not_input", "", "", "record_shape")

    text = _record_text(record)
    if any(marker in text for marker in MACHINE_MARKERS) or RELAY_MARKER.search(text):
        return InputClassification("machine", text, source, "explicit_machine_marker")
    if DATOM_HEAD.match(text):
        return InputClassification("ambiguous", text, source, "datom_shape_without_authorship")
    return InputClassification("living", text, source, "authenticated_input_stream")


def extract_text(record):
    """Extract text content from a message record.

    Returns (role, text, is_human_origin, origin_kind, timestamp).
    """
    classification = classify_input(record)
    msg = record.get("message", {})
    role = msg.get("role", "")
    content = msg.get("content", [])
    origin = record.get("origin", {})
    origin_kind = origin.get("kind", "")
    timestamp = record.get("timestamp", "")

    texts = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(item.get("text", ""))

    if classification.source == "codex_user_message":
        return "user", classification.text, classification.status == "living", classification.status, timestamp
    return role, "\n".join(texts), classification.status == "living", classification.status, timestamp


def trim_text(text, cap):
    """Trim text to cap characters, adding ellipsis if trimmed."""
    if len(text) <= cap:
        return text
    return text[:cap] + "..."


def list_subagents(session_path):
    """List subagent transcripts for a session."""
    session_dir = session_path.parent / session_path.stem
    subagent_dir = session_dir / "subagents"
    if not subagent_dir.exists():
        return []
    agents = []
    for f in sorted(subagent_dir.glob("*.jsonl")):
        meta_path = f.with_suffix(".meta.json")
        meta = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
            except (json.JSONDecodeError, ValueError):
                pass
        agents.append((f.name, f, meta))
    return agents


def fmt_ts(ts):
    """Format a timestamp for display."""
    if not ts:
        return ""
    # Trim to just time portion for compactness
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, AttributeError):
        return ts[:16]


# --- Command: show ---

def cmd_show(args):
    """Show live-typed user messages with preceding assistant context."""
    path, session_id = resolve_transcript(args.transcript)
    if path is None:
        print(f"error: cannot resolve transcript: {args.transcript}", file=sys.stderr)
        return 1

    context_n = args.context
    text_cap = args.cap

    lines = path.read_text().splitlines()
    # Pre-scan: collect all records with their line numbers
    records = []
    for i, line_text in enumerate(lines, 1):
        rec = parse_record(line_text)
        if rec:
            records.append((i, rec))

    # Find assistant text responses (not tool calls, not tool results)
    # and human-origin user messages
    print(f"session: {session_id}")
    print(f"file:    {path}")
    print(f"lines:   {len(lines)}")
    print()

    # Build list of assistant text entries for context lookback
    assistant_texts = []  # (line_no, timestamp, text)
    human_messages = []   # (line_no, timestamp, text, context_indices)

    for line_no, rec in records:
        rtype = rec.get("type", "")
        if rtype == "assistant":
            role, text, _, _, ts = extract_text(rec)
            if text.strip():
                assistant_texts.append((line_no, ts, text))
        elif rtype in ("user", "event_msg"):
            if classify_input(rec).status == "living":
                role, text, _, _, ts = extract_text(rec)
                # Find the last N assistant text responses before this line
                ctx_start = max(0, len(assistant_texts) - context_n)
                ctx = list(range(ctx_start, len(assistant_texts)))
                human_messages.append((line_no, ts, text, ctx))

    if not human_messages:
        print("(no live-typed user messages found)")
    else:
        for line_no, ts, text, ctx_indices in human_messages:
            # Print context assistant messages
            for ci in ctx_indices:
                a_line, a_ts, a_text = assistant_texts[ci]
                print(f"  L{a_line:<6} [{fmt_ts(a_ts)}] assistant: {trim_text(a_text, text_cap)}")
            # Print the human message
            print(f"* L{line_no:<6} [{fmt_ts(ts)}] human: {text}")
            print()

    # List subagents
    subagents = list_subagents(path)
    if subagents:
        print(f"subagents ({len(subagents)}):")
        for name, spath, meta in subagents:
            desc = meta.get("description", "")
            print(f"  {name}  {desc}")

    return 0


# --- Command: search ---

def cmd_search(args):
    """Search live-typed messages across transcripts."""
    pattern = re.compile(args.pattern, re.IGNORECASE)
    text_cap = args.cap
    also_assistant = args.assistant

    # Determine file set
    files = _resolve_file_set(args)
    if not files:
        print("error: no transcript files found", file=sys.stderr)
        return 1

    hits = 0
    for fpath in files:
        lines = fpath.read_text().splitlines()
        for i, line_text in enumerate(lines, 1):
            rec = parse_record(line_text)
            if rec is None:
                continue
            rtype = rec.get("type", "")

            # Filter to human-origin user messages (and optionally assistant text)
            if rtype in ("user", "event_msg"):
                if classify_input(rec).status != "living":
                    continue
            elif rtype == "assistant" and also_assistant:
                pass
            else:
                continue

            role, text, is_human, origin_kind, ts = extract_text(rec)
            if not text:
                continue

            m = pattern.search(text)
            if m:
                hits += 1
                label = "human" if is_human else "assistant"
                # Show a window around the match
                start = max(0, m.start() - 40)
                end = min(len(text), m.end() + 40)
                snippet = text[start:end].replace("\n", " ")
                if start > 0:
                    snippet = "..." + snippet
                if end < len(text):
                    snippet = snippet + "..."
                print(f"{fpath.stem[:8]}  L{i:<6} [{fmt_ts(ts)}] {label}: {snippet}")

    if hits == 0:
        print("(no matches)")
    else:
        print(f"\n{hits} hit(s)")
    return 0


def _resolve_file_set(args):
    """Resolve the set of files to search based on args."""
    files = []

    if args.project:
        # Encode the project directory path
        encoded = args.project.replace("/", "-")
        if encoded.startswith("-"):
            encoded = encoded  # keep leading dash
        proj_dir = CLAUDE_DIR / encoded
        if proj_dir.is_dir():
            files = sorted(proj_dir.glob("*.jsonl"))
        else:
            # Try all project dirs for partial match
            for d in find_project_dirs():
                if encoded in d.name:
                    files.extend(sorted(d.glob("*.jsonl")))
    elif args.recent:
        # N most recent files by mtime across all projects
        all_files = []
        for d in find_project_dirs():
            if d.is_dir():
                all_files.extend(d.glob("*.jsonl"))
        all_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        files = all_files[:args.recent]
    elif args.date_from or args.date_to:
        # Date range filter by file mtime
        all_files = []
        for d in find_project_dirs():
            if d.is_dir():
                all_files.extend(d.glob("*.jsonl"))
        for f in all_files:
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            if args.date_from:
                dt_from = datetime.strptime(args.date_from, "%Y-%m-%d")
                if mtime < dt_from:
                    continue
            if args.date_to:
                dt_to = datetime.strptime(args.date_to, "%Y-%m-%d").replace(
                    hour=23, minute=59, second=59
                )
                if mtime > dt_to:
                    continue
            files.append(f)
        files.sort(key=lambda f: f.stat().st_mtime)
    else:
        # Default: all files in all projects
        for d in find_project_dirs():
            if d.is_dir():
                files.extend(sorted(d.glob("*.jsonl")))

    return files


# --- Command: raw ---

def cmd_raw(args):
    """Print raw records at specific line numbers."""
    path = Path(args.file)
    if not path.is_file():
        path, _ = resolve_transcript(args.file)
    if path is None or not path.is_file():
        print(f"error: cannot find file: {args.file}", file=sys.stderr)
        return 1

    line_nums = set()
    for spec in args.lines:
        if "-" in spec:
            start, end = spec.split("-", 1)
            for n in range(int(start), int(end) + 1):
                line_nums.add(n)
        else:
            line_nums.add(int(spec))

    lines = path.read_text().splitlines()
    for n in sorted(line_nums):
        if 1 <= n <= len(lines):
            rec = parse_record(lines[n - 1])
            if rec:
                print(f"--- L{n} ---")
                print(json.dumps(rec, indent=2, ensure_ascii=False))
            else:
                print(f"--- L{n} (unparseable) ---")
                print(lines[n - 1][:500])
        else:
            print(f"--- L{n} (out of range, file has {len(lines)} lines) ---")

    return 0


# --- Main ---

def main():
    parser = argparse.ArgumentParser(
        prog="transcript",
        description="Search Claude Code harness transcripts.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # show
    p_show = sub.add_parser(
        "show",
        help="Show live-typed user messages with assistant context.",
    )
    p_show.add_argument("transcript", help="Path, UUID, or 8-hex short id")
    p_show.add_argument(
        "-n", "--context", type=int, default=DEFAULT_CONTEXT,
        help=f"Number of preceding assistant text responses (default {DEFAULT_CONTEXT})",
    )
    p_show.add_argument(
        "--cap", type=int, default=DEFAULT_TEXT_CAP,
        help=f"Max characters per text block (default {DEFAULT_TEXT_CAP})",
    )

    # search
    p_search = sub.add_parser(
        "search",
        help="Search live-typed messages across transcripts.",
    )
    p_search.add_argument("pattern", help="Regex pattern to search for")
    p_search.add_argument(
        "--project", help="Encoded project directory name to scope search",
    )
    p_search.add_argument(
        "--recent", type=int, help="Search only the N most recent transcripts",
    )
    p_search.add_argument("--date-from", help="Start date (YYYY-MM-DD)")
    p_search.add_argument("--date-to", help="End date (YYYY-MM-DD)")
    p_search.add_argument(
        "--assistant", action="store_true",
        help="Also search assistant text responses",
    )
    p_search.add_argument(
        "--cap", type=int, default=80,
        help="Max snippet length (default 80)",
    )

    # raw
    p_raw = sub.add_parser(
        "raw",
        help="Print raw JSONL records at specific line numbers.",
    )
    p_raw.add_argument("file", help="Path, UUID, or 8-hex short id")
    p_raw.add_argument(
        "lines", nargs="+",
        help="Line numbers or ranges (e.g. 42 10-15)",
    )

    args = parser.parse_args()
    if args.command == "show":
        return cmd_show(args)
    elif args.command == "search":
        return cmd_search(args)
    elif args.command == "raw":
        return cmd_raw(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
