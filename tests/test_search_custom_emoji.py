"""Unit tests for the search_custom_emoji MCP tool."""

import asyncio
import inspect
import json
import re
import time
from unittest.mock import AsyncMock

import pytest
from telethon import functions, types
from telethon.errors import EmoticonEmptyError

from telegram_mcp.runtime import mcp
from telegram_mcp.tools import custom_emoji, messages, stickers
from telegram_mcp.tools.custom_emoji import search_custom_emoji

_search_custom_emoji = inspect.unwrap(search_custom_emoji)

SNOWFLAKE = "❄️"
SNOWMAN = "☃️"
FIRE = "🔥"
BIG_ID = 5368324170671202286  # beyond what a JSON number keeps exactly in JavaScript

KEYWORDS = {
    "en": [
        types.EmojiKeyword(keyword="snow", emoticons=[SNOWFLAKE, SNOWMAN]),
        types.EmojiKeyword(keyword="Fire", emoticons=[FIRE]),
        types.EmojiKeywordDeleted(keyword="old", emoticons=[FIRE]),
    ],
    "ru": [types.EmojiKeyword(keyword="снег", emoticons=[SNOWFLAKE])],
}


def _difference(lang_code, keywords):
    return types.EmojiKeywordsDifference(
        lang_code=lang_code, from_version=0, version=1, keywords=keywords
    )


class FakeClient:
    """Answers the two requests the tool makes and records every request.

    found maps an emoticon to the document IDs Telegram has for it, or to the reply
    object itself. error, when set, is raised for every request instead.
    """

    def __init__(self, found=None, keywords=None, error=None):
        self.found = found or {}
        self.keywords = KEYWORDS if keywords is None else keywords
        self.error = error
        self.requests = []

    def sent(self, request_type):
        return [r for r in self.requests if isinstance(r, request_type)]

    async def __call__(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        if isinstance(request, functions.messages.GetEmojiKeywordsRequest):
            await asyncio.sleep(0)  # a real request yields, which lets a second caller arrive
            return _difference(request.lang_code, self.keywords.get(request.lang_code, []))
        if isinstance(request, functions.messages.SearchCustomEmojiRequest):
            reply = self.found.get(request.emoticon, [])
            return types.EmojiList(hash=0, document_id=reply) if isinstance(reply, list) else reply
        raise AssertionError(f"unexpected request {request!r}")


@pytest.fixture(autouse=True)
def _empty_keyword_cache(monkeypatch):
    """The dictionary cache is per process: keep one test's cache away from the next."""
    monkeypatch.delenv("TELEGRAM_EMOJI_SEARCH_LANGUAGE", raising=False)
    custom_emoji._emoji_keywords.clear()
    custom_emoji._emoji_keyword_locks.clear()
    yield
    custom_emoji._emoji_keywords.clear()
    custom_emoji._emoji_keyword_locks.clear()


@pytest.fixture
def connect(monkeypatch):
    """Make the tool talk to the given fake client."""

    def connect(fake):
        monkeypatch.setattr(custom_emoji, "get_client", lambda account=None: fake)
        monkeypatch.setattr(custom_emoji, "ensure_connected", AsyncMock())
        return fake

    return connect


@pytest.fixture
def client(connect):
    return connect(
        FakeClient(
            found={
                SNOWFLAKE: [1, 2],
                SNOWMAN: [2, BIG_ID],
                FIRE: [10, 11, 12],
            }
        )
    )


# ---------------------------------------------------------
# Test: FastMCP Registration & Annotations
# ---------------------------------------------------------
def test_tool_is_registered_and_read_only():
    registered = {t.name: t for t in mcp._tool_manager.list_tools()}

    assert "search_custom_emoji" in registered
    assert registered["search_custom_emoji"].annotations.readOnlyHint is True
    assert stickers.search_custom_emoji is search_custom_emoji


# ---------------------------------------------------------
# Test: a word is turned into emoji, and each emoji is searched
# ---------------------------------------------------------
@pytest.mark.asyncio
async def test_word_is_translated_to_emoji_and_each_is_searched(client):
    data = json.loads(await _search_custom_emoji("snow"))

    assert [r.lang_code for r in client.sent(functions.messages.GetEmojiKeywordsRequest)] == ["en"]
    searches = client.sent(functions.messages.SearchCustomEmojiRequest)
    assert [(r.emoticon, r.hash) for r in searches] == [(SNOWFLAKE, 0), (SNOWMAN, 0)]

    assert data["query"] == "snow"
    assert data["lang_code"] == "en"
    assert data["matched_emoji"] == [SNOWFLAKE, SNOWMAN]
    # 2 is returned for both emoji and listed once, under the first emoji that found it.
    assert data["count"] == 3
    assert data["custom_emojis"] == [
        {"emoji": SNOWFLAKE, "id": "1"},
        {"emoji": SNOWFLAKE, "id": "2"},
        {"emoji": SNOWMAN, "id": str(BIG_ID)},
    ]


@pytest.mark.asyncio
async def test_results_are_accepted_by_send_message_as_custom_emojis(client):
    data = json.loads(await _search_custom_emoji("snow"))

    for entry in data["custom_emojis"]:
        fallback, document_id, error = messages._custom_emoji_item(entry)
        assert error is None
        assert fallback == entry["emoji"]
        assert str(document_id) == entry["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["FIRE", "  fire ", "Fire"])
async def test_word_lookup_ignores_case_and_surrounding_space(client, query):
    data = json.loads(await _search_custom_emoji(query))

    assert data["matched_emoji"] == [FIRE]
    assert [e["id"] for e in data["custom_emojis"]] == ["10", "11", "12"]


@pytest.mark.asyncio
async def test_word_matching_no_emoji_searches_nothing(client):
    data = json.loads(await _search_custom_emoji("nonsenseword"))

    assert data["matched_emoji"] == []
    assert data["count"] == 0
    assert data["custom_emojis"] == []
    assert client.sent(functions.messages.SearchCustomEmojiRequest) == []


@pytest.mark.asyncio
async def test_deleted_keywords_are_not_matches(client):
    data = json.loads(await _search_custom_emoji("old"))

    assert data["matched_emoji"] == []


@pytest.mark.asyncio
async def test_limit_stops_searching_further_emoji(client):
    data = json.loads(await _search_custom_emoji("snow", limit=2))

    assert [e["id"] for e in data["custom_emojis"]] == ["1", "2"]
    # The first emoji already filled the limit: the second is never asked for.
    assert [r.emoticon for r in client.sent(functions.messages.SearchCustomEmojiRequest)] == [
        SNOWFLAKE
    ]


@pytest.mark.asyncio
async def test_limit_cuts_the_last_emoji_s_results(client):
    data = json.loads(await _search_custom_emoji("fire", limit=2))

    assert [e["id"] for e in data["custom_emojis"]] == ["10", "11"]
    assert data["count"] == 2


# ---------------------------------------------------------
# Test: an emoji is searched as it is
# ---------------------------------------------------------
@pytest.mark.asyncio
async def test_emoji_is_searched_directly_without_the_keyword_dictionary(client):
    data = json.loads(await _search_custom_emoji(FIRE))

    assert client.sent(functions.messages.GetEmojiKeywordsRequest) == []
    searches = client.sent(functions.messages.SearchCustomEmojiRequest)
    assert [r.emoticon for r in searches] == [FIRE]
    assert data["matched_emoji"] == [FIRE]
    assert "lang_code" not in data
    assert [e["id"] for e in data["custom_emojis"]] == ["10", "11", "12"]
    assert {e["emoji"] for e in data["custom_emojis"]} == {FIRE}


@pytest.mark.asyncio
@pytest.mark.parametrize("emoji", ["👍🏽", "🇺🇦", "👨‍💻"])
async def test_emoji_sequences_reach_telegram_unchanged(client, emoji):
    client.found[emoji] = [7]

    data = json.loads(await _search_custom_emoji(emoji))

    searches = client.sent(functions.messages.SearchCustomEmojiRequest)
    assert [r.emoticon for r in searches] == [emoji]
    assert data["custom_emojis"] == [{"emoji": emoji, "id": "7"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [[], types.EmojiListNotModified()])
async def test_emoji_without_custom_emoji_gives_an_empty_result(connect, reply):
    connect(FakeClient(found={SNOWFLAKE: reply}))

    data = json.loads(await _search_custom_emoji(SNOWFLAKE))

    assert data["matched_emoji"] == [SNOWFLAKE]
    assert data["count"] == 0
    assert data["custom_emojis"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "   ", "\n"])
async def test_blank_query_is_refused_without_a_request(client, query):
    result = await _search_custom_emoji(query)

    assert result.startswith("Invalid query")
    assert client.requests == []


# ---------------------------------------------------------
# Test: language of the words
# ---------------------------------------------------------
@pytest.mark.asyncio
async def test_lang_code_selects_the_dictionary(client):
    data = json.loads(await _search_custom_emoji("Снег", lang_code="RU"))

    assert [r.lang_code for r in client.sent(functions.messages.GetEmojiKeywordsRequest)] == ["ru"]
    assert data["lang_code"] == "ru"
    assert data["matched_emoji"] == [SNOWFLAKE]


@pytest.mark.asyncio
async def test_language_comes_from_the_environment_unless_given(client, monkeypatch):
    monkeypatch.setenv("TELEGRAM_EMOJI_SEARCH_LANGUAGE", " RU ")

    assert (json.loads(await _search_custom_emoji("снег")))["matched_emoji"] == [SNOWFLAKE]
    assert (json.loads(await _search_custom_emoji("snow", lang_code="en")))["matched_emoji"] == [
        SNOWFLAKE,
        SNOWMAN,
    ]


def test_language_defaults_to_english(monkeypatch):
    monkeypatch.delenv("TELEGRAM_EMOJI_SEARCH_LANGUAGE", raising=False)
    assert custom_emoji.emoji_search_language() == "en"

    monkeypatch.setenv("TELEGRAM_EMOJI_SEARCH_LANGUAGE", "  ")
    assert custom_emoji.emoji_search_language() == "en"


# ---------------------------------------------------------
# Test: the keyword dictionary is fetched once
# ---------------------------------------------------------
@pytest.mark.asyncio
async def test_second_call_does_not_fetch_the_dictionary_again(client):
    await _search_custom_emoji("snow")
    await _search_custom_emoji("fire")
    await _search_custom_emoji("snow")

    assert len(client.sent(functions.messages.GetEmojiKeywordsRequest)) == 1


@pytest.mark.asyncio
async def test_each_language_has_its_own_cached_dictionary(client):
    await _search_custom_emoji("snow")
    await _search_custom_emoji("снег", lang_code="ru")
    await _search_custom_emoji("snow")
    await _search_custom_emoji("снег", lang_code="ru")

    assert [r.lang_code for r in client.sent(functions.messages.GetEmojiKeywordsRequest)] == [
        "en",
        "ru",
    ]


@pytest.mark.asyncio
async def test_concurrent_first_calls_share_one_request(client):
    results = await asyncio.gather(*(_search_custom_emoji("snow") for _ in range(3)))

    assert len(client.sent(functions.messages.GetEmojiKeywordsRequest)) == 1
    assert {json.loads(r)["count"] for r in results} == {3}


@pytest.mark.asyncio
async def test_dictionary_is_fetched_again_after_it_expires(client):
    await _search_custom_emoji("snow")
    _, keywords = custom_emoji._emoji_keywords["en"]
    custom_emoji._emoji_keywords["en"] = (
        time.monotonic() - custom_emoji.EMOJI_KEYWORDS_TTL - 1,
        keywords,
    )

    await _search_custom_emoji("snow")

    assert len(client.sent(functions.messages.GetEmojiKeywordsRequest)) == 2


@pytest.mark.asyncio
async def test_empty_dictionary_is_not_cached(client):
    first = json.loads(await _search_custom_emoji("snow", lang_code="xx"))
    second = json.loads(await _search_custom_emoji("snow", lang_code="xx"))

    assert first["matched_emoji"] == second["matched_emoji"] == []
    assert len(client.sent(functions.messages.GetEmojiKeywordsRequest)) == 2


@pytest.mark.asyncio
async def test_repeated_keywords_merge_their_emoji(connect):
    connect(
        FakeClient(
            found={SNOWFLAKE: [1], SNOWMAN: [2], FIRE: [3]},
            keywords={
                "en": [
                    types.EmojiKeyword(keyword="cold", emoticons=[SNOWFLAKE, SNOWMAN]),
                    types.EmojiKeyword(keyword="COLD", emoticons=[SNOWMAN, FIRE]),
                ]
            },
        )
    )

    data = json.loads(await _search_custom_emoji("cold"))

    assert data["matched_emoji"] == [SNOWFLAKE, SNOWMAN, FIRE]


# ---------------------------------------------------------
# Test: errors
# ---------------------------------------------------------
@pytest.mark.asyncio
async def test_rpc_error_names_telegram_error_without_its_text(connect):
    error = EmoticonEmptyError(request=functions.messages.SearchCustomEmojiRequest(FIRE, 0))
    assert "cannot be empty" in str(error)  # the text the tool must not pass on
    connect(FakeClient(error=error))

    result = await _search_custom_emoji(FIRE)

    assert re.fullmatch(
        r"An error occurred \(code: GEN-ERR-\d{3}\): Telegram returned "
        r"EMOTICON_EMPTY \(EmoticonEmptyError\)\.",
        result,
    )


@pytest.mark.asyncio
async def test_unexpected_error_does_not_leak_its_text(connect):
    connect(FakeClient(error=RuntimeError("exception-payload-secret")))

    result = await _search_custom_emoji("snow")

    assert result.startswith("An error occurred (code:")
    assert "exception-payload-secret" not in result
