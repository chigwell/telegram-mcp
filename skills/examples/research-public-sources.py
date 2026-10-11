"""Read Telegram context and separately approved public sources over MCP HTTP."""

import argparse
import asyncio
from datetime import timedelta
import json
import sys
from urllib.parse import urlsplit
import uuid

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

PARALLEL_URL = "https://search.parallel.ai/mcp"
USER_AGENT = "telegram-mcp-public-sources-example/2.0.1"
TIMEOUT = 60


async def call_tool(url, name, arguments):
    """Discover and call one tool with bounded waits and no automatic retries."""
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT) as client:
        async with streamable_http_client(url, http_client=client) as (read, write, _):
            async with ClientSession(
                read, write, read_timeout_seconds=timedelta(seconds=TIMEOUT)
            ) as session:
                await session.initialize()
                tools = await session.list_tools()
                if name not in {tool.name for tool in tools.tools}:
                    raise RuntimeError(f"MCP server does not expose {name}")
                result = await session.call_tool(name, arguments)
                if result.isError:
                    raise RuntimeError(f"{name} failed: {result.content}")
                return result.model_dump(mode="json", exclude_none=True)


async def research(args):
    output = {}
    if args.chat_id is not None:
        arguments = {
            "chat_id": args.chat_id,
            "message_id": args.message_id,
            "context_size": 3,
        }
        if args.account:
            arguments["account"] = args.account
        output["telegram_context"] = await call_tool(
            args.telegram_url, "get_message_context", arguments
        )

    # Deliberately build public arguments only from explicit CLI inputs. Never
    # derive queries, URLs, objectives or session identifiers from chat content.
    session_id = str(uuid.uuid4())
    if args.public_query:
        name = "web_search"
        public_arguments = {
            "objective": args.public_query,
            "search_queries": [args.public_query],
            "session_id": session_id,
        }
    else:
        name = "web_fetch"
        public_arguments = {"urls": [args.public_url], "session_id": session_id}
    output["public_sources"] = await call_tool(PARALLEL_URL, name, public_arguments)
    return output


def public_url(value):
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
        raise argparse.ArgumentTypeError("Use an HTTP(S) public URL without login credentials")
    return value


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--public-query", help="Approved public search terms, preferably 3-6 words"
    )
    source.add_argument("--public-url", type=public_url, help="Approved public page to fetch")
    parser.add_argument("--telegram-url", default="http://127.0.0.1:8765/mcp")
    parser.add_argument("--chat-id", help="Telegram chat ID, username or confirmed alias")
    parser.add_argument("--message-id", type=int)
    parser.add_argument("--account", help="Optional configured Telegram account label")
    args = parser.parse_args(argv)
    if (args.chat_id is None) != (args.message_id is None):
        parser.error("--chat-id and --message-id must be supplied together")
    if args.account and args.chat_id is None:
        parser.error("--account requires --chat-id and --message-id")
    if args.public_query is not None and not args.public_query.strip():
        parser.error("--public-query must not be blank")
    return args


def main():
    args = parse_args()
    try:
        output = asyncio.run(research(args))
    except Exception as exc:
        print(f"Research failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
