"""Renderers. Every format is produced from the JSONL, never from the network.

That split is deliberate: a re-render after a change of mind costs nothing and
touches no Telegram API, and the JSONL stays the one artifact that has to be
right.
"""

import datetime as dt
import html
import json
from pathlib import Path
from string import Template
from typing import Iterator, Optional

from .entities import to_html, to_markdown
from .util import format_duration, human_size, log

PAGE_SIZE = 3000

# Telegram Desktop assigns each participant one of a small palette; the same
# trick keeps long group exports readable.
_PALETTE = ["#c03d33", "#4fad2d", "#d09306", "#168acd", "#8544d6", "#cd4073", "#2996ad", "#ce671b"]


def read_records(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _parse_date(value: Optional[str]) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def _colour(sender_id: Optional[int]) -> str:
    if sender_id is None:
        return "#707579"
    return _PALETTE[abs(int(sender_id)) % len(_PALETTE)]


def _initials(name: Optional[str]) -> str:
    if not name:
        return "?"
    parts = [p for p in name.split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:1].upper()
    return (parts[0][:1] + parts[1][:1]).upper()


def _sender_label(record: dict) -> str:
    if record.get("from_name"):
        return record["from_name"]
    if record.get("outgoing"):
        return "You"
    if record.get("from_id"):
        return f"id{record['from_id']}"
    return "Unknown"


def _service_text(record: dict) -> str:
    service = record.get("service") or {}
    action = service.get("action", "Service message")
    label = action.replace("MessageAction", "")
    title = service.get("title")
    return f"{label}: {title}" if title else label


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------

_HTML_PAGE = Template("""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$title</title>
<style>
  :root { color-scheme: light; }
  body { margin: 0; background: #e6ebee; font: 15px/1.45 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; color: #000; }
  .wrap { max-width: 780px; margin: 0 auto; padding: 16px 12px 64px; }
  .chat-header { background: #fff; border-radius: 10px; padding: 16px 20px; margin-bottom: 16px; box-shadow: 0 1px 2px rgba(16,35,47,.12); }
  .chat-header h1 { font-size: 20px; margin: 0 0 6px; }
  .chat-header .meta { color: #6d7883; font-size: 13px; }
  .divider { text-align: center; margin: 18px 0 12px; }
  .divider span { background: rgba(0,0,0,.16); color: #fff; border-radius: 12px; padding: 3px 12px; font-size: 12px; }
  .service { text-align: center; margin: 10px 0; }
  .service span { background: rgba(0,0,0,.1); color: #43484d; border-radius: 12px; padding: 3px 12px; font-size: 12px; }
  .msg { display: flex; gap: 10px; margin: 2px 0; padding: 4px 0; }
  .msg.joined .avatar { visibility: hidden; }
  .msg.joined .name { display: none; }
  .avatar { flex: 0 0 42px; width: 42px; height: 42px; border-radius: 50%; color: #fff; display: flex; align-items: center; justify-content: center; font-size: 15px; font-weight: 600; }
  .body { background: #fff; border-radius: 10px; padding: 8px 12px; box-shadow: 0 1px 1px rgba(16,35,47,.10); min-width: 0; max-width: 640px; }
  .msg.out .body { background: #eeffde; }
  .name { font-weight: 600; margin-bottom: 2px; }
  .text { white-space: pre-wrap; overflow-wrap: anywhere; }
  .time { color: #a1aab3; font-size: 12px; margin-top: 4px; }
  .fwd, .reply { border-left: 2px solid #3390ec; padding: 2px 0 2px 8px; margin-bottom: 6px; font-size: 13px; color: #4b5a68; }
  .reply a { color: #3390ec; text-decoration: none; }
  .media img, .media video { max-width: 100%; border-radius: 6px; display: block; margin: 6px 0; }
  .filechip { display: inline-block; background: #f1f3f5; border-radius: 6px; padding: 6px 10px; margin: 6px 0; font-size: 13px; color: #43484d; }
  .filechip a { color: #3390ec; text-decoration: none; }
  .transcript { background: #f6f7f8; border-radius: 6px; padding: 6px 8px; margin: 6px 0; font-size: 13px; color: #43484d; }
  .transcript .src { color: #8b96a0; font-size: 11px; display: block; margin-top: 3px; }
  .reactions { margin-top: 5px; font-size: 12px; color: #6d7883; }
  .reactions span { background: #f1f3f5; border-radius: 10px; padding: 2px 7px; margin-right: 4px; display: inline-block; }
  .spoiler { background: #b6bcc2; border-radius: 3px; color: transparent; }
  .spoiler:hover { background: transparent; color: inherit; }
  blockquote { border-left: 3px solid #c4c9ce; margin: 4px 0; padding-left: 8px; color: #43484d; }
  pre { background: #f1f3f5; padding: 8px; border-radius: 6px; overflow-x: auto; }
  code { background: #f1f3f5; border-radius: 3px; padding: 1px 4px; }
  .nav { text-align: center; margin: 22px 0 0; font-size: 14px; }
  .nav a { color: #3390ec; text-decoration: none; margin: 0 8px; }
  .edited { color: #a1aab3; font-size: 12px; }
</style>
</head>
<body>
<div class="wrap">
<div class="chat-header">
  <h1>$title</h1>
  <div class="meta">$meta</div>
</div>
$content
<div class="nav">$nav</div>
</div>
</body>
</html>
""")


def _media_html(record: dict) -> str:
    info = record.get("media")
    if not info:
        return ""
    kind = info.get("kind")
    path = info.get("file")
    label = info.get("name") or kind
    size = human_size(info.get("size"))
    if path:
        src = html.escape(path, quote=True)
        if kind in ("photo", "sticker"):
            return f'<div class="media"><img src="{src}" alt="{html.escape(str(label))}" loading="lazy"></div>'
        if kind in ("video", "animation", "round"):
            return f'<div class="media"><video src="{src}" controls preload="none"></video></div>'
        if kind in ("voice", "audio"):
            duration = format_duration(info.get("duration"))
            return (
                f'<div class="media"><audio src="{src}" controls preload="none"></audio>'
                f'<div class="filechip">{html.escape(str(kind))} {duration}</div></div>'
            )
        return (
            f'<div class="filechip">📎 <a href="{src}">{html.escape(str(label))}</a> '
            f"<span>{size}</span></div>"
        )
    note = info.get("skipped") or "not downloaded"
    duration = f" {format_duration(info['duration'])}" if info.get("duration") else ""
    return (
        f'<div class="filechip">📎 {html.escape(str(kind))}{duration} · {size} '
        f"· <em>{html.escape(str(note))}</em></div>"
    )


def _transcript_html(record: dict) -> str:
    transcript = record.get("transcript")
    if not transcript:
        return ""
    if transcript.get("text"):
        engine = html.escape(str(transcript.get("engine") or "?"))
        return (
            f'<div class="transcript">{html.escape(transcript["text"])}'
            f'<span class="src">machine transcript · engine: {engine} · not a verbatim quote</span></div>'
        )
    error = html.escape(str(transcript.get("error") or "failed"))
    return f'<div class="transcript"><em>transcription failed: {error}</em></div>'


def _message_html(record: dict, joined: bool, name_by_id: dict) -> str:
    if record.get("service"):
        return f'<div class="service"><span>{html.escape(_service_text(record))}</span></div>'

    sender = _sender_label(record)
    colour = _colour(record.get("from_id"))
    stamp = _parse_date(record.get("date"))
    time_text = stamp.strftime("%H:%M") if stamp else ""
    classes = ["msg"]
    if record.get("outgoing"):
        classes.append("out")
    if joined:
        classes.append("joined")

    pieces = [
        f'<div class="{" ".join(classes)}" id="msg{record["id"]}">',
        f'<div class="avatar" style="background:{colour}">{html.escape(_initials(sender))}</div>',
        '<div class="body">',
        f'<div class="name" style="color:{colour}">{html.escape(sender)}</div>',
    ]

    forward = record.get("forward")
    if forward:
        origin = forward.get("from_name") or (
            f"id{forward['from_id']}" if forward.get("from_id") else "unknown"
        )
        pieces.append(f'<div class="fwd">Forwarded from {html.escape(str(origin))}</div>')

    reply = record.get("reply")
    if reply and reply.get("reply_to_msg_id"):
        target = reply["reply_to_msg_id"]
        who = name_by_id.get(target)
        label = f"In reply to {html.escape(who)}" if who else "In reply to a message"
        pieces.append(f'<div class="reply"><a href="#msg{target}">{label}</a></div>')

    pieces.append(_media_html(record))
    pieces.append(_transcript_html(record))

    body = to_html(record.get("text"), record.get("entities"))
    if body:
        pieces.append(f'<div class="text">{body}</div>')

    reactions = record.get("reactions") or []
    if reactions:
        chips = "".join(
            f'<span>{html.escape(str(r.get("reaction")))} {r.get("count")}</span>'
            for r in reactions
        )
        pieces.append(f'<div class="reactions">{chips}</div>')

    edited = ' <span class="edited">edited</span>' if record.get("edit_date") else ""
    pieces.append(f'<div class="time">{time_text}{edited}</div>')
    pieces.append("</div></div>")
    return "".join(pieces)


def render_html(export_dir: Path, meta: dict, page_size: int = PAGE_SIZE) -> list[Path]:
    """Write messages.html (+ messages2.html, …) next to the JSONL."""
    source = export_dir / "messages.jsonl"
    records = list(read_records(source))
    name_by_id = {r["id"]: _sender_label(r) for r in records}

    pages = [records[i : i + page_size] for i in range(0, len(records), page_size)] or [[]]
    written: list[Path] = []

    for index, page in enumerate(pages, start=1):
        chunks: list[str] = []
        previous_day = None
        previous_sender = None
        previous_stamp = None
        for record in page:
            stamp = _parse_date(record.get("date"))
            day = stamp.date() if stamp else None
            if day != previous_day:
                label = day.strftime("%d %B %Y") if day else "Unknown date"
                chunks.append(f'<div class="divider"><span>{label}</span></div>')
                previous_day = day
                previous_sender = None
            sender_id = record.get("from_id")
            gap = None
            if stamp and previous_stamp:
                gap = (stamp - previous_stamp).total_seconds()
            joined = (
                not record.get("service")
                and sender_id is not None
                and sender_id == previous_sender
                and gap is not None
                and gap < 300
            )
            chunks.append(_message_html(record, joined, name_by_id))
            previous_sender = None if record.get("service") else sender_id
            previous_stamp = stamp

        nav_parts = []
        if index > 1:
            previous_name = "messages.html" if index == 2 else f"messages{index - 1}.html"
            nav_parts.append(f'<a href="{previous_name}">← previous</a>')
        nav_parts.append(f"page {index} of {len(pages)}")
        if index < len(pages):
            nav_parts.append(f'<a href="messages{index + 1}.html">next →</a>')

        title = meta.get("chat", {}).get("title") or "Telegram chat"
        window = meta.get("window", {})
        meta_line = " · ".join(
            part
            for part in [
                meta.get("chat", {}).get("type"),
                f"@{meta['chat']['username']}" if meta.get("chat", {}).get("username") else None,
                f"id {meta.get('chat', {}).get('id')}",
                f"{meta.get('message_count', 0)} messages",
                f"{window.get('first') or '?'} … {window.get('last') or '?'}",
            ]
            if part
        )
        page_html = _HTML_PAGE.substitute(
            title=html.escape(title),
            meta=html.escape(meta_line),
            content="\n".join(chunks) or "<p>No messages in this window.</p>",
            nav=" ".join(nav_parts),
        )
        name = "messages.html" if index == 1 else f"messages{index}.html"
        path = export_dir / name
        path.write_text(page_html, encoding="utf-8")
        written.append(path)

    log(f"  html: {len(written)} page(s)")
    return written


# --------------------------------------------------------------------------
# Markdown / plain text
# --------------------------------------------------------------------------


def _media_note(record: dict) -> str:
    info = record.get("media")
    if not info:
        return ""
    bits = [info.get("kind") or "media"]
    if info.get("duration"):
        bits.append(format_duration(info["duration"]))
    if info.get("name"):
        bits.append(info["name"])
    if info.get("size"):
        bits.append(human_size(info["size"]))
    label = " · ".join(str(b) for b in bits)
    if info.get("file"):
        return f"[{label}]({info['file']})"
    return f"[{label} · not downloaded]"


def render_markdown(export_dir: Path, meta: dict) -> Path:
    source = export_dir / "messages.jsonl"
    chat = meta.get("chat", {})
    lines = [f"# {chat.get('title') or 'Telegram chat'}", ""]
    window = meta.get("window", {})
    lines.append(
        f"*{chat.get('type')} · id `{chat.get('id')}` · {meta.get('message_count', 0)} messages · "
        f"{window.get('first') or '?'} … {window.get('last') or '?'}*"
    )
    lines.append("")

    previous_day = None
    for record in read_records(source):
        stamp = _parse_date(record.get("date"))
        day = stamp.date() if stamp else None
        if day != previous_day:
            lines.extend(["", f"## {day.isoformat() if day else 'unknown date'}", ""])
            previous_day = day
        if record.get("service"):
            lines.append(f"*{_service_text(record)}*")
            lines.append("")
            continue
        time_text = stamp.strftime("%H:%M") if stamp else "--:--"
        lines.append(f"**{time_text} {_sender_label(record)}**")
        forward = record.get("forward")
        if forward:
            lines.append(f"> forwarded from {forward.get('from_name') or forward.get('from_id')}")
        reply = record.get("reply")
        if reply and reply.get("reply_to_msg_id"):
            lines.append(f"> in reply to message {reply['reply_to_msg_id']}")
        note = _media_note(record)
        if note:
            lines.append(note)
        transcript = record.get("transcript")
        if transcript and transcript.get("text"):
            lines.append(f"> transcript ({transcript.get('engine')}): {transcript['text']}")
        body = to_markdown(record.get("text"), record.get("entities"))
        if body:
            lines.append(body)
        reactions = record.get("reactions") or []
        if reactions:
            lines.append("· " + "  ".join(f"{r['reaction']} {r['count']}" for r in reactions))
        lines.append("")

    path = export_dir / "messages.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    log("  markdown: messages.md")
    return path


def render_text(export_dir: Path, meta: dict) -> Path:
    source = export_dir / "messages.jsonl"
    lines: list[str] = []
    for record in read_records(source):
        stamp = _parse_date(record.get("date"))
        time_text = stamp.strftime("%d.%m.%Y %H:%M") if stamp else "?"
        if record.get("service"):
            lines.append(f"[{time_text}] -- {_service_text(record)}")
            lines.append("")
            continue
        lines.append(f"{_sender_label(record)}, [{time_text}]")
        note = _media_note(record)
        if note:
            lines.append(note)
        transcript = record.get("transcript")
        if transcript and transcript.get("text"):
            lines.append(f"(transcript, {transcript.get('engine')}): {transcript['text']}")
        if record.get("text"):
            lines.append(record["text"])
        lines.append("")
    path = export_dir / "messages.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    log("  text: messages.txt")
    return path


RENDERERS = {"html": render_html, "md": render_markdown, "txt": render_text}


def render(export_dir: Path, meta: dict, formats: list[str]) -> None:
    for name in formats:
        if name == "jsonl":
            continue
        renderer = RENDERERS.get(name)
        if renderer is None:
            raise SystemExit(f"Unknown format '{name}'. Known: jsonl, html, md, txt.")
        renderer(export_dir, meta)
