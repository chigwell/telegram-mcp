"""Public contracts captured before the refactor; changes require API review."""

import json
import importlib
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon import types

import main
from telegram_mcp import runtime, tools
from telegram_mcp.tools import messages

FIXTURE = Path(__file__).parent / "fixtures" / "refactor_contracts.json"


@pytest.mark.parametrize(
    "module_name",
    [
        "accounts",
        "chats",
        "contacts",
        "events",
        "folders",
        "groups",
        "media",
        "messages",
        "profile",
    ],
)
def test_historical_runtime_attributes_remain_importable(module_name):
    module = importlib.import_module(f"telegram_mcp.tools.{module_name}")
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for name in expected["runtime_exports"]:
        assert hasattr(module, name), f"Missing compatibility attribute: {module_name}.{name}"
    with pytest.raises(AttributeError):
        getattr(module, "nonexistent_refactor_attribute")


def test_runner_star_import_preserves_historical_exports():
    namespace = {}
    exec("from telegram_mcp.runner import *", namespace)
    expected = json.loads(FIXTURE.with_name("runner_exports.json").read_text(encoding="utf-8"))
    assert [name for name in namespace if not name.startswith("_")] == expected


def message_samples():
    """Synthetic Telegram data covers omissions, formatting, and attribution."""
    date = datetime(2026, 8, 1, 12, 30, tzinfo=timezone.utc)
    base = dict(id=7, sender=None, date=date, message="", from_id=None, media=None)
    yield SimpleNamespace(**base)
    rich = dict(base)
    rich.update(
        message="Hello 👋\nworld",
        sender=SimpleNamespace(first_name="Ada", last_name="Lovelace", username="ada"),
        sender_id=42,
        out=True,
        photo=object(),
        grouped_id=99,
        reply_to=SimpleNamespace(reply_to_msg_id=3, quote_text="Quoted\ntext", quote_offset=0),
        edit_date=date,
        pinned=True,
        views=0,
        forwards=2,
        reactions=SimpleNamespace(results=[SimpleNamespace(count=3)]),
        replies=SimpleNamespace(replies=0),
        buttons=[[SimpleNamespace(text="Open", url="https://example.com")]],
        entities=[types.MessageEntityTextUrl(0, 5, "https://example.com")],
        fwd_from=SimpleNamespace(date=date, channel_post=12, post_author="Author"),
        forward=SimpleNamespace(
            chat=SimpleNamespace(title="Channel", username="channel"),
            chat_id=-1000000000042,
        ),
        via_bot_id=9,
        ttl_period=60,
    )
    yield SimpleNamespace(**rich)
    block = dict(base)
    block["rich_message"] = SimpleNamespace(
        blocks=[types.PageBlockParagraph(types.TextPlain("Rich paragraph"))]
    )
    yield SimpleNamespace(**block)


async def collect_contracts():
    registered = await runtime.mcp.list_tools()
    return {
        "tools": [tool.model_dump(mode="json", exclude_none=True) for tool in registered],
        "runtime_exports": runtime.__all__,
        "tool_exports": tools.__all__,
        "main_exports": sorted(name for name in vars(main) if not name.startswith("_")),
        "messages": [
            {"json": messages.message_to_dict(msg), "text": messages.format_message_line(msg)}
            for msg in message_samples()
        ],
    }


@pytest.mark.asyncio
async def test_public_contracts_match_pre_refactor_snapshot(monkeypatch):
    monkeypatch.setattr(messages, "LINK_DOMAIN", "t.me")
    # Use the same wire conversions as the existing tools for datetime values.
    actual = json.loads(json.dumps(await collect_contracts(), default=runtime.json_serializer))
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for section in expected:
        assert actual[section] == expected[section], f"Contract changed: {section}"
