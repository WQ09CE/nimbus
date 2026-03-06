#!/usr/bin/env python3
"""
ChatGPT Export Converter

Converts ChatGPT export data (conversations JSON files) into:
  1. Individual Markdown files per conversation
  2. JSONL format for programmatic processing
  3. A summary Markdown file with monthly stats and topic listing

Usage:
    python3 chatgpt_export_converter.py --input-dir /path/to/export --output-dir /path/to/output

Pure Python standard library -- no third-party dependencies required.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Tree traversal helpers
# ---------------------------------------------------------------------------

def _find_root(mapping: dict[str, Any]) -> str | None:
    """Find the root node (no parent) in the mapping tree."""
    for node_id, node in mapping.items():
        if node.get("parent") is None:
            return node_id
    return None


def _trace_active_path(mapping: dict[str, Any], current_node: str | None) -> list[str]:
    """Trace from current_node back to root, then reverse to get root-to-leaf order.

    This handles branching trees (edits / regenerations) by following only
    the active branch indicated by ``current_node``.
    """
    if current_node is None or current_node not in mapping:
        # Fallback: walk from root taking the *last* child at each branch
        return _walk_from_root(mapping)

    path: list[str] = []
    node_id: str | None = current_node
    while node_id is not None:
        path.append(node_id)
        node_id = mapping[node_id].get("parent")
    path.reverse()
    return path


def _walk_from_root(mapping: dict[str, Any]) -> list[str]:
    """Fallback DFS from root, always picking the last child (most recent branch)."""
    root = _find_root(mapping)
    if root is None:
        return []
    path: list[str] = []
    node_id: str | None = root
    while node_id is not None:
        path.append(node_id)
        children = mapping[node_id].get("children", [])
        node_id = children[-1] if children else None
    return path


# ---------------------------------------------------------------------------
# Message extraction
# ---------------------------------------------------------------------------

def _extract_text(parts: list[Any]) -> str:
    """Extract text from message parts, skipping non-string (multimodal) entries."""
    texts: list[str] = []
    for part in parts:
        if isinstance(part, str):
            texts.append(part)
        # dict parts (images, assets) are silently skipped
    return "\n".join(texts)


def extract_messages(conversation: dict[str, Any]) -> list[dict[str, str]]:
    """Return an ordered list of {role, content, model} dicts for a conversation.

    Only ``user`` and ``assistant`` messages with non-empty text are included.
    """
    mapping = conversation.get("mapping", {})
    current_node = conversation.get("current_node")
    path = _trace_active_path(mapping, current_node)

    messages: list[dict[str, str]] = []
    model_slug: str = ""

    for node_id in path:
        node = mapping.get(node_id, {})
        msg = node.get("message")
        if msg is None:
            continue

        role = msg.get("author", {}).get("role", "")
        if role not in ("user", "assistant"):
            continue

        content_obj = msg.get("content", {})
        parts = content_obj.get("parts", [])
        text = _extract_text(parts).strip()
        if not text:
            continue

        # Capture model from assistant messages
        if role == "assistant":
            slug = msg.get("metadata", {}).get("model_slug") or ""
            if slug:
                model_slug = slug

        messages.append({"role": role, "content": text, "model": model_slug})

    return messages


def _detect_model(conversation: dict[str, Any], messages: list[dict[str, str]]) -> str:
    """Determine the model used for the conversation.

    Checks ``default_model_slug`` first, then falls back to the model captured
    from individual assistant messages.
    """
    model = conversation.get("default_model_slug") or ""
    if not model:
        # Pick the last non-empty model from messages
        for m in reversed(messages):
            if m.get("model"):
                model = m["model"]
                break
    return model or "unknown"


# ---------------------------------------------------------------------------
# Filename sanitisation
# ---------------------------------------------------------------------------

_UNSAFE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MULTI_UNDERSCORE = re.compile(r"_+")


def sanitize_filename(title: str, max_length: int = 80) -> str:
    """Convert a conversation title into a safe filename component."""
    name = _UNSAFE_RE.sub("_", title)
    name = name.replace(" ", "_")
    name = _MULTI_UNDERSCORE.sub("_", name)
    name = name.strip("_.")
    if len(name) > max_length:
        name = name[:max_length].rstrip("_")
    return name or "untitled"


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------

def _format_ts(ts: float | None) -> str:
    """Format a Unix timestamp to ``YYYY-MM-DD HH:MM`` (UTC)."""
    if ts is None:
        return "unknown"
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError, OverflowError):
        return "unknown"


def _format_date(ts: float | None) -> str:
    """Format a Unix timestamp to ``YYYY-MM-DD`` (UTC)."""
    if ts is None:
        return "unknown"
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d")
    except (OSError, ValueError, OverflowError):
        return "unknown"


def _format_month(ts: float | None) -> str:
    """Format a Unix timestamp to ``YYYY-MM`` (UTC)."""
    if ts is None:
        return "unknown"
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime("%Y-%m")
    except (OSError, ValueError, OverflowError):
        return "unknown"


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def write_markdown(
    conversation: dict[str, Any],
    messages: list[dict[str, str]],
    output_dir: Path,
) -> str | None:
    """Write a single conversation as a Markdown file. Returns the filename."""
    if not messages:
        return None

    title = conversation.get("title") or "Untitled"
    create_ts = conversation.get("create_time")
    date_str = _format_date(create_ts)
    model = _detect_model(conversation, messages)
    msg_count = len(messages)

    safe_title = sanitize_filename(title)
    filename = f"{date_str}_{safe_title}.md"
    filepath = output_dir / filename

    # Handle duplicates
    counter = 1
    while filepath.exists():
        filename = f"{date_str}_{safe_title}_{counter}.md"
        filepath = output_dir / filename
        counter += 1

    lines: list[str] = [
        f"# {title}",
        "",
        f"- Date: {_format_ts(create_ts)}",
        f"- Model: {model}",
        f"- Messages: {msg_count}",
        "",
        "---",
        "",
    ]

    for msg in messages:
        role_label = "User" if msg["role"] == "user" else "Assistant"
        lines.append(f"**{role_label}:**")
        lines.append(msg["content"])
        lines.append("")
        lines.append("---")
        lines.append("")

    filepath.write_text("\n".join(lines), encoding="utf-8")
    return filename


def write_jsonl_line(
    conversation: dict[str, Any],
    messages: list[dict[str, str]],
    fh: Any,
) -> bool:
    """Append one JSONL line for a conversation. Returns True if written."""
    if not messages:
        return False

    title = conversation.get("title") or "Untitled"
    conv_id = conversation.get("id") or conversation.get("conversation_id", "")
    create_ts = conversation.get("create_time")
    date_str = _format_date(create_ts)
    model = _detect_model(conversation, messages)

    record = {
        "id": conv_id,
        "title": title,
        "date": date_str,
        "model": model,
        "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
    }
    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return True


# ---------------------------------------------------------------------------
# Summary / keyword extraction
# ---------------------------------------------------------------------------

# Common stopwords for keyword extraction (English + Chinese function words)
_STOPWORDS = frozenset(
    "a an the is are was were be been being have has had do does did will would "
    "shall should can could may might must need dare to of in for on with at by "
    "from as into about between through during before after above below up down "
    "out off over under again further then once here there when where why how all "
    "each every both few more most other some such no nor not only own same so "
    "than too very it its i me my myself we our ours ourselves you your yours "
    "yourself yourselves he him his himself she her hers herself they them their "
    "theirs themselves what which who whom this that these those am and but if or "
    "because until while that just don t s re ve ll d m the".split()
)


def _extract_keywords(titles: list[str], top_n: int = 30) -> list[tuple[str, int]]:
    """Extract top keywords from conversation titles."""
    counter: Counter[str] = Counter()
    for title in titles:
        words = re.findall(r"[a-zA-Z\u4e00-\u9fff]{2,}", title.lower())
        for w in words:
            if w not in _STOPWORDS:
                counter[w] += 1
    return counter.most_common(top_n)


def write_summary(
    conversations: list[dict[str, Any]],
    output_dir: Path,
) -> None:
    """Write a summary.md with monthly stats and topic keywords."""
    # Group by month
    monthly: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    all_titles: list[str] = []

    for conv in conversations:
        month = _format_month(conv.get("create_time"))
        monthly[month].append(conv)
        title = conv.get("title") or "Untitled"
        all_titles.append(title)

    # Model usage stats
    model_counter: Counter[str] = Counter()
    for conv in conversations:
        mapping = conv.get("mapping", {})
        for node in mapping.values():
            msg = node.get("message")
            if msg and msg.get("author", {}).get("role") == "assistant":
                slug = msg.get("metadata", {}).get("model_slug")
                if slug:
                    model_counter[slug] += 1
                    break  # one per conversation is enough

    keywords = _extract_keywords(all_titles)

    lines: list[str] = [
        "# ChatGPT Conversation Summary",
        "",
        f"Total conversations: {len(conversations)}",
        "",
        "## Model Usage",
        "",
    ]

    for model, count in model_counter.most_common():
        lines.append(f"- **{model}**: {count} conversations")
    lines.append("")

    lines.append("## Top Keywords / Topics")
    lines.append("")
    kw_parts: list[str] = []
    for word, count in keywords:
        kw_parts.append(f"`{word}` ({count})")
    lines.append(", ".join(kw_parts))
    lines.append("")

    lines.append("## Monthly Breakdown")
    lines.append("")

    for month in sorted(monthly.keys()):
        convs = monthly[month]
        lines.append(f"### {month} ({len(convs)} conversations)")
        lines.append("")
        # Sort by create_time within month
        convs.sort(key=lambda c: c.get("create_time") or 0)
        for conv in convs:
            title = conv.get("title") or "Untitled"
            date_str = _format_date(conv.get("create_time"))
            model = conv.get("default_model_slug") or ""
            model_tag = f" [{model}]" if model else ""
            lines.append(f"- {date_str}: {title}{model_tag}")
        lines.append("")

    filepath = output_dir / "summary.md"
    filepath.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def load_conversations(input_dir: Path) -> list[dict[str, Any]]:
    """Load all conversations from conversations-*.json files."""
    pattern = str(input_dir / "conversations-*.json")
    files = sorted(glob.glob(pattern))

    if not files:
        print(f"ERROR: No conversations-*.json files found in {input_dir}")
        sys.exit(1)

    conversations: list[dict[str, Any]] = []
    for filepath in files:
        print(f"  Loading {Path(filepath).name} ...", end=" ")
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"{len(data)} conversations")
        conversations.extend(data)

    return conversations


def convert(input_dir: Path, output_dir: Path) -> None:
    """Run the full conversion pipeline."""
    print(f"Input directory:  {input_dir}")
    print(f"Output directory: {output_dir}")
    print()

    # Load
    print("Loading conversations...")
    conversations = load_conversations(input_dir)
    print(f"Total: {len(conversations)} conversations loaded")
    print()

    # Sort by create_time
    conversations.sort(key=lambda c: c.get("create_time") or 0)

    # Prepare output dirs
    md_dir = output_dir / "conversations"
    jsonl_dir = output_dir / "conversations_jsonl"
    md_dir.mkdir(parents=True, exist_ok=True)
    jsonl_dir.mkdir(parents=True, exist_ok=True)

    # Convert
    md_count = 0
    jsonl_count = 0
    empty_count = 0
    total = len(conversations)

    jsonl_path = jsonl_dir / "conversations.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as jsonl_fh:
        for i, conv in enumerate(conversations, 1):
            title = conv.get("title") or "Untitled"
            messages = extract_messages(conv)

            if not messages:
                empty_count += 1
                continue

            # Markdown
            fname = write_markdown(conv, messages, md_dir)
            if fname:
                md_count += 1

            # JSONL
            if write_jsonl_line(conv, messages, jsonl_fh):
                jsonl_count += 1

            # Progress every 50 conversations
            if i % 50 == 0 or i == total:
                print(f"  Processed {i}/{total} conversations...")

    print()

    # Summary
    print("Generating summary...")
    write_summary(conversations, output_dir)

    # Stats
    print()
    print("=" * 60)
    print("Conversion Complete!")
    print("=" * 60)
    print(f"  Total conversations:  {total}")
    print(f"  Markdown files:       {md_count}")
    print(f"  JSONL entries:        {jsonl_count}")
    print(f"  Empty (skipped):      {empty_count}")
    print()
    print(f"Output files:")
    print(f"  Markdown:  {md_dir}/")
    print(f"  JSONL:     {jsonl_path}")
    print(f"  Summary:   {output_dir / 'summary.md'}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert ChatGPT export data to Markdown, JSONL, and summary formats.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --input-dir ~/Downloads/chatgpt-export\n"
            "  %(prog)s --input-dir ./export --output-dir ./converted\n"
        ),
    )
    parser.add_argument(
        "--input-dir",
        required=True,
        help="Path to the ChatGPT export directory containing conversations-*.json files",
    )
    parser.add_argument(
        "--output-dir",
        default=os.path.expanduser("~/Downloads/chatgpt-export-converted"),
        help="Path to the output directory (default: ~/Downloads/chatgpt-export-converted)",
    )

    args = parser.parse_args()

    input_dir = Path(args.input_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()

    if not input_dir.is_dir():
        print(f"ERROR: Input directory does not exist: {input_dir}")
        sys.exit(1)

    convert(input_dir, output_dir)


if __name__ == "__main__":
    main()
