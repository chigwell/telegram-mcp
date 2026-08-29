"""Command line: login, chats, export, render."""

import argparse
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Optional

from .fetch import chat_meta, display_name, iter_records, resolve_target
from .render import read_records, render
from .session import build_client, connected, ensure_authorised, session_path
from .util import json_default, log, months_ago, parse_date, safe_name

try:  # the console script is installed from this package
    from importlib.metadata import version as _dist_version

    __version__ = _dist_version("telegram-mcp")
except Exception:  # running from a source checkout without an install
    __version__ = "dev"

DEFAULT_OUT = Path("out")
KNOWN_FORMATS = ("jsonl", "html", "md", "txt")


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telegram-mcp-export",
        description="Export Telegram chats to local files. JSONL is always written; "
        "every other format is rendered from it.",
    )
    parser.add_argument(
        "--version", action="version", version=f"telegram-mcp-export {__version__}"
    )
    parser.add_argument(
        "--session",
        help="Path to the session file (default ~/.config/telegram-mcp/export.session).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="Authorise this tool as a new Telegram device (interactive).")
    sub.add_parser("whoami", help="Show the logged-in account.")

    chats = sub.add_parser("chats", help="List dialogs so you can pick export targets.")
    chats.add_argument("--limit", type=int, default=0, help="Stop after N dialogs (0 = all).")
    chats.add_argument("--out", help="Also write the list as TSV to this file.")

    export = sub.add_parser("export", help="Export one or more chats.")
    export.add_argument("targets", nargs="*", help="@username, t.me link, chat id, or chat title.")
    export.add_argument(
        "--from-file", help="File with one target per line ('#' comments allowed)."
    )
    window = export.add_mutually_exclusive_group(required=True)
    window.add_argument(
        "--all", action="store_true", help="Whole history, from the first message."
    )
    window.add_argument("--months", type=int, help="Only the last N months.")
    window.add_argument("--since", help="Only messages from this date (YYYY-MM-DD).")
    export.add_argument("--until", help="Stop at this date (YYYY-MM-DD).")
    export.add_argument(
        "--format",
        default="jsonl",
        help=f"Comma-separated: {', '.join(KNOWN_FORMATS)} (default jsonl).",
    )
    export.add_argument("--media", action="store_true", help="Download media files.")
    export.add_argument(
        "--media-max-mb", type=float, default=0, help="Skip media larger than this (0 = no limit)."
    )
    export.add_argument(
        "--transcribe",
        nargs="?",
        const="groq",
        help="Transcribe voice and video notes (default engine: groq).",
    )
    export.add_argument(
        "--no-raw",
        action="store_true",
        help="Drop the raw Telegram payload from the JSONL (smaller, lossy).",
    )
    export.add_argument(
        "--out", default=str(DEFAULT_OUT), help="Output directory (default ./out)."
    )
    export.add_argument(
        "--resume",
        action="store_true",
        help="Append only messages newer than the last export in that folder.",
    )

    render_cmd = sub.add_parser("render", help="Re-render an existing export from its JSONL.")
    render_cmd.add_argument("directory", help="An export folder containing messages.jsonl.")
    render_cmd.add_argument(
        "--format", default="html", help=f"Comma-separated: {', '.join(KNOWN_FORMATS)}."
    )
    return parser


def parse_formats(raw: str) -> list[str]:
    names = [item.strip().lower() for item in raw.split(",") if item.strip()]
    unknown = [name for name in names if name not in KNOWN_FORMATS]
    if unknown:
        raise SystemExit(
            f"Unknown format(s): {', '.join(unknown)}. Known: {', '.join(KNOWN_FORMATS)}."
        )
    if "jsonl" not in names:
        names.insert(0, "jsonl")
    return names


def collect_targets(args) -> list[str]:
    targets = list(args.targets)
    if args.from_file:
        path = Path(args.from_file).expanduser()
        if not path.exists():
            raise SystemExit(f"No such file: {path}")
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                targets.append(line)
    if not targets:
        raise SystemExit("No chats given. Pass targets on the command line or use --from-file.")
    seen, ordered = set(), []
    for target in targets:
        if target not in seen:
            seen.add(target)
            ordered.append(target)
    return ordered


def window_bounds(args) -> tuple[Optional[dt.datetime], Optional[dt.datetime]]:
    since = None
    if args.months:
        since = months_ago(args.months)
    elif args.since:
        since = parse_date(args.since)
    until = parse_date(args.until) if args.until else None
    return since, until


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


async def cmd_login(args) -> int:
    client = build_client(args.session)
    async with connected(client):
        await ensure_authorised(client, interactive=True)
        me = await client.get_me()
        log(f"Logged in as {display_name(me)} (id {me.id}).")
        log(f"Session file: {session_path(args.session)}")
    return 0


async def cmd_whoami(args) -> int:
    client = build_client(args.session)
    async with connected(client):
        await ensure_authorised(client, interactive=False)
        me = await client.get_me()
        print(
            json.dumps(
                {
                    "id": me.id,
                    "name": display_name(me),
                    "username": me.username,
                    "premium": bool(getattr(me, "premium", False)),
                    "session": str(session_path(args.session)),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


async def cmd_chats(args) -> int:
    client = build_client(args.session)
    rows = []
    async with connected(client):
        await ensure_authorised(client, interactive=False)
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            rows.append(
                {
                    "id": dialog.id,
                    "type": (
                        "user" if dialog.is_user else ("channel" if dialog.is_channel else "group")
                    ),
                    "title": dialog.name or "",
                    "username": getattr(entity, "username", None) or "",
                    "last": dialog.date.isoformat() if dialog.date else "",
                }
            )
            if args.limit and len(rows) >= args.limit:
                break

    header = f"{'id':>16}  {'type':<8}  {'username':<22}  title"
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{row['id']:>16}  {row['type']:<8}  {('@' + row['username']) if row['username'] else '':<22}  {row['title']}"
        )

    if args.out:
        path = Path(args.out).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            handle.write("id\ttype\tusername\ttitle\tlast_message\n")
            for row in rows:
                handle.write(
                    f"{row['id']}\t{row['type']}\t{row['username']}\t{row['title']}\t{row['last']}\n"
                )
        log(f"Wrote {len(rows)} dialogs to {path}")
    return 0


def _resume_floor(export_dir: Path) -> int:
    source = export_dir / "messages.jsonl"
    if not source.exists():
        return 0
    last = 0
    for record in read_records(source):
        last = max(last, int(record.get("id") or 0))
    return last


async def export_one(
    client, target: str, args, formats: list[str], since, until
) -> Optional[dict]:
    entity = await resolve_target(client, target)
    meta = await chat_meta(client, entity)
    folder = f"{safe_name(meta['title'] or 'chat')}_{meta['id']}"
    export_dir = Path(args.out).expanduser() / folder
    export_dir.mkdir(parents=True, exist_ok=True)

    min_id = _resume_floor(export_dir) if args.resume else 0
    mode = "a" if (args.resume and min_id) else "w"
    if mode == "w" and (export_dir / "messages.jsonl").exists():
        (export_dir / "messages.jsonl").unlink()

    log(
        f"→ {meta['title']} ({meta['type']}, id {meta['id']})"
        + (f" · resuming after message {min_id}" if min_id else "")
    )

    media_root = export_dir / "media" if args.media else None
    if media_root:
        media_root.mkdir(parents=True, exist_ok=True)
    media_max = int(args.media_max_mb * 1024 * 1024) if args.media_max_mb else None

    people: dict[int, str] = {}
    count = 0
    first_date = last_date = None
    jsonl_path = export_dir / "messages.jsonl"

    with jsonl_path.open(mode, encoding="utf-8") as handle:
        async for record in iter_records(
            client,
            entity,
            since=since,
            until=until,
            min_id=min_id,
            media_root=media_root,
            media_max_bytes=media_max,
            transcribe_engine=args.transcribe,
            include_raw=not args.no_raw,
            people=people,
        ):
            handle.write(json.dumps(record, default=json_default, ensure_ascii=False) + "\n")
            count += 1
            stamp = record.get("date")
            if stamp is not None:
                stamp = stamp.isoformat() if hasattr(stamp, "isoformat") else str(stamp)
                first_date = first_date or stamp
                last_date = stamp

    # On a resume the window has to describe the whole file, not just the tail.
    total = count
    if mode == "a":
        total = sum(1 for _ in read_records(jsonl_path))
        rows = list(read_records(jsonl_path))
        first_date = rows[0].get("date") if rows else first_date
        last_date = rows[-1].get("date") if rows else last_date

    meta_payload = {
        "tool": f"telegram-mcp-export {__version__}",
        "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "chat": meta,
        "message_count": total,
        "added_this_run": count,
        "window": {
            "requested_since": since.isoformat() if since else None,
            "requested_until": until.isoformat() if until else None,
            "first": first_date,
            "last": last_date,
        },
        "options": {
            "media": bool(args.media),
            "media_max_mb": args.media_max_mb or None,
            "transcribe": args.transcribe,
            "raw_included": not args.no_raw,
            "formats": formats,
        },
        "people": {str(k): v for k, v in sorted(people.items())},
    }
    (export_dir / "meta.json").write_text(
        json.dumps(meta_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    log(f"  jsonl: {count} new message(s), {total} total → {jsonl_path}")
    render(export_dir, meta_payload, formats)
    return {"dir": export_dir, "meta": meta_payload}


async def cmd_export(args) -> int:
    targets = collect_targets(args)
    if args.transcribe:
        # Validated here rather than as an argparse choice so that --help and
        # the offline commands never import the MCP runtime.
        from telegram_mcp.transcription import ENGINES

        if args.transcribe not in ENGINES:
            raise SystemExit(
                f"Unknown transcription engine '{args.transcribe}'. "
                f"Known: {', '.join(sorted(ENGINES))}."
            )
    formats = parse_formats(args.format)
    since, until = window_bounds(args)
    if args.transcribe == "groq":
        import os

        if not os.getenv("GROQ_API_KEY"):
            log(
                "warning: --transcribe=groq needs GROQ_API_KEY; falling back to no transcription "
                "would be silent, so set the key or pass --transcribe=telegram."
            )

    client = build_client(args.session)
    results, failures = [], []
    async with connected(client):
        await ensure_authorised(client, interactive=False)
        for index, target in enumerate(targets, start=1):
            log(f"[{index}/{len(targets)}] {target}")
            try:
                result = await export_one(client, target, args, formats, since, until)
                if result:
                    results.append(result)
            except SystemExit as exc:
                failures.append((target, str(exc)))
                log(f"  ! skipped: {exc}")
            except Exception as exc:
                failures.append((target, repr(exc)))
                log(f"  ! failed: {exc}")

    log("")
    log(f"Done: {len(results)} chat(s) exported, {len(failures)} failed.")
    for target, reason in failures:
        log(f"  failed: {target} - {reason}")
    return 1 if failures and not results else 0


async def cmd_render(args) -> int:
    export_dir = Path(args.directory).expanduser()
    meta_path = export_dir / "meta.json"
    if not (export_dir / "messages.jsonl").exists():
        raise SystemExit(f"{export_dir} has no messages.jsonl.")
    meta = (
        json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {"chat": {}}
    )
    render(export_dir, meta, parse_formats(args.format))
    return 0


COMMANDS = {
    "login": cmd_login,
    "whoami": cmd_whoami,
    "chats": cmd_chats,
    "export": cmd_export,
    "render": cmd_render,
}


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    handler = COMMANDS[args.command]
    try:
        return asyncio.run(handler(args))
    except KeyboardInterrupt:
        log("Interrupted. Whatever was written stays on disk; re-run with --resume.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
