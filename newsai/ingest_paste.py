"""Turn a raw paste into a fixture skeleton — labels left blank for review.

    python -m newsai.ingest_paste raw/2026-08-25.txt > fixtures/candidate.jsonl

Input format (see README):

    === mayadeen ===
    [14:02] post text
            more text on the same post
    [14:05] next post

Rules:
  * A line starting with `===` opens a channel block.
  * A `[HH:MM]` prefix opens a post; following lines belong to it until the
    next `[HH:MM]` or `===`. Blank lines are preserved inside a post.
  * Text is written through byte-for-byte. Footers, hashtags, links, emoji
    and bidi marks are NOT stripped here — Stage 0 is what strips them, and
    stripping by hand hides the bugs that adapters.py exists to catch.
  * `expect` is emitted empty. Labelling is a separate, human step.
  * ids are `<chan>-<NN>` — ascii and typeable, because `of` cross-references
    are written by hand. They get renamed to meaningful slugs during labelling.
"""
import json
import re
import sys
from pathlib import Path

CHANNEL = re.compile(r"^\s*={2,}\s*(.+?)\s*={2,}\s*$")
STAMP = re.compile(r"^\s*\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s?(.*)$")
# Post boundary when no timestamps are available. ASCII only — the posts'
# own dividers are U+2500 box-drawing, so these can never collide.
SPLIT = re.compile(r"^\s*-{3,}\s*$")


def short(channel: str) -> str:
    """Typeable channel tag. ids get referenced by hand in `of`, so keep them ascii."""
    t = re.sub(r"[^a-z0-9]", "", channel.lower())
    return (t[:3] or "ch")


def parse(raw: str):
    channel, stamp, buf = None, None, []
    out = []

    def flush():
        if channel and buf:
            text = "\n".join(buf).strip("\n")
            if text.strip():
                out.append({"channel": channel, "time": stamp, "text": text})
        buf.clear()

    for line in raw.splitlines():
        m = CHANNEL.match(line)
        if m:
            flush()
            channel, stamp = m.group(1), None
            continue
        if SPLIT.match(line):
            flush()
            stamp = None
            continue
        m = STAMP.match(line)
        if m:
            flush()
            stamp = m.group(1)
            buf.append(m.group(2))
            continue
        buf.append(line)
    flush()
    return out


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    posts = parse(Path(sys.argv[1]).read_text(encoding="utf-8"))

    seq = 0
    for p in posts:
        seq += 1
        pid = f"{short(p['channel'])}-{seq:02d}"
        row = {
            "id": pid,
            "channel": p["channel"],
            "seq": seq,
            "text": p["text"],
            "expect": {},          # <- filled during review
            "why": "",
        }
        if p["time"]:
            row["time"] = p["time"]
        print(json.dumps(row, ensure_ascii=False))

    print(f"{seq} posts from {len({p['channel'] for p in posts})} channels",
          file=sys.stderr)


if __name__ == "__main__":
    main()
