"""Tests for telegram_mcp/transcription.py: config, cache, engines, budget, render."""

import asyncio
import os
import stat
from types import SimpleNamespace

import pytest

from telegram_mcp import transcription


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner


def _voice_msg(**overrides):
    base = dict(
        id=1,
        message=None,
        voice=SimpleNamespace(),
        video_note=None,
        file=SimpleNamespace(duration=23, ext=".oga", mime_type="audio/ogg"),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# Config toggles
# ---------------------------------------------------------------------------


def test_transcribe_mode_defaults_to_on_demand(monkeypatch):
    monkeypatch.delenv("TELEGRAM_TRANSCRIBE", raising=False)
    assert transcription.transcribe_mode() == "on-demand"


@pytest.mark.parametrize("value", ["off", "on-demand", "auto", "AUTO", " off "])
def test_transcribe_mode_accepts_known_values_case_and_space_insensitive(monkeypatch, value):
    monkeypatch.setenv("TELEGRAM_TRANSCRIBE", value)
    assert transcription.transcribe_mode() == value.strip().lower()


def test_transcribe_mode_rejects_unknown_value(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TRANSCRIBE", "sometimes")
    with pytest.raises(SystemExit):
        transcription.transcribe_mode()


def test_default_engine_defaults_to_groq(monkeypatch):
    monkeypatch.delenv("TELEGRAM_TRANSCRIBE_ENGINE", raising=False)
    assert transcription.default_engine() == "groq"


def test_default_engine_accepts_telegram(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TRANSCRIBE_ENGINE", "telegram")
    assert transcription.default_engine() == "telegram"


def test_default_engine_rejects_unknown_value(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TRANSCRIBE_ENGINE", "whisper-local")
    with pytest.raises(SystemExit):
        transcription.default_engine()


def test_validate_transcription_config_raises_on_bad_mode(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TRANSCRIBE", "always")
    with pytest.raises(SystemExit):
        transcription.validate_transcription_config()


# ---------------------------------------------------------------------------
# SQLite cache
# ---------------------------------------------------------------------------


def test_cache_miss_returns_none(transcript_cache_dir):
    assert transcription.get_cached_transcript(1, 2) is None


def test_cache_round_trip(transcript_cache_dir):
    transcription.save_transcript(1, 2, "groq", "hello world", duration=23, lang="ru")
    cached = transcription.get_cached_transcript(1, 2)
    assert cached["text"] == "hello world"
    assert cached["source"] == "groq"
    assert cached["duration"] == 23
    assert cached["lang"] == "ru"
    assert cached["created_at"]


def test_cache_upsert_overwrites_previous_entry(transcript_cache_dir):
    transcription.save_transcript(1, 2, "telegram", "first pass")
    transcription.save_transcript(1, 2, "groq", "second pass")
    cached = transcription.get_cached_transcript(1, 2)
    assert cached["text"] == "second pass"
    assert cached["source"] == "groq"


def test_cache_keyed_by_chat_and_message_independently(transcript_cache_dir):
    transcription.save_transcript(1, 100, "groq", "chat one")
    transcription.save_transcript(2, 100, "groq", "chat two")
    assert transcription.get_cached_transcript(1, 100)["text"] == "chat one"
    assert transcription.get_cached_transcript(2, 100)["text"] == "chat two"
    assert transcription.get_cached_transcript(1, 999) is None


def test_cache_pinned_to_an_engine_never_serves_the_other_engines_text(transcript_cache_dir):
    """The whole point of choosing groq is that the native engine drops the
    recording's last segment. A telegram transcript answering a groq request
    would hand back that truncated text with no way to tell."""
    transcription.save_transcript(1, 2, "telegram", "truncated tail")
    assert transcription.get_cached_transcript(1, 2, source="groq") is None
    assert transcription.get_cached_transcript(1, 2, source="telegram")["text"] == (
        "truncated tail"
    )


def test_cache_keeps_both_engines_side_by_side(transcript_cache_dir):
    transcription.save_transcript(1, 2, "telegram", "native text")
    transcription.save_transcript(1, 2, "groq", "groq text")
    assert transcription.get_cached_transcript(1, 2, source="telegram")["text"] == "native text"
    assert transcription.get_cached_transcript(1, 2, source="groq")["text"] == "groq text"


def test_legacy_cache_keyed_without_engine_is_migrated_in_place(transcript_cache_dir):
    """Builds before the fix keyed on (chat_id, message_id) alone. Rows must
    survive the rebuild, and the pinned lookup must start working on them."""
    import sqlite3

    path = transcription._cache_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE transcripts (
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            text TEXT NOT NULL,
            duration INTEGER,
            lang TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (chat_id, message_id)
        );
        INSERT INTO transcripts VALUES (1, 2, 'telegram', 'old row', 23, 'ru', '2026-08-20');
        """)
    conn.commit()
    conn.close()

    assert transcription.get_cached_transcript(1, 2, source="telegram")["text"] == "old row"
    assert transcription.get_cached_transcript(1, 2, source="groq") is None
    transcription.save_transcript(1, 2, "groq", "new row")
    assert transcription.get_cached_transcript(1, 2, source="groq")["text"] == "new row"
    assert transcription.get_cached_transcript(1, 2, source="telegram")["text"] == "old row"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions only")
def test_cache_file_and_directory_get_restrictive_permissions(transcript_cache_dir):
    transcription.save_transcript(1, 2, "groq", "hi")
    db_path = transcription.cache_dir() / "transcripts.db"
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(transcript_cache_dir.stat().st_mode) == 0o700


# ---------------------------------------------------------------------------
# Voice/video-note detection and formatting
# ---------------------------------------------------------------------------


def test_is_transcribable_true_for_voice():
    assert transcription.is_transcribable(_voice_msg())


def test_is_transcribable_true_for_video_note():
    assert transcription.is_transcribable(_voice_msg(voice=None, video_note=SimpleNamespace()))


def test_is_transcribable_false_for_plain_message():
    assert not transcription.is_transcribable(_voice_msg(voice=None, file=None))


def test_voice_duration_reads_file_attribute():
    assert transcription.voice_duration(_voice_msg()) == 23


def test_voice_duration_none_without_file():
    assert transcription.voice_duration(_voice_msg(file=None)) is None


@pytest.mark.parametrize(
    "seconds,expected",
    [(0, "0:00"), (23, "0:23"), (83, "1:23"), (3661, "1:01:01"), (None, "?:??")],
)
def test_format_duration(seconds, expected):
    assert transcription.format_duration(seconds) == expected


# ---------------------------------------------------------------------------
# Batch budget
# ---------------------------------------------------------------------------


def test_budget_reads_defaults_from_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TRANSCRIBE_MAX_VOICES", "2")
    monkeypatch.setenv("TELEGRAM_TRANSCRIBE_MAX_SECONDS", "60")
    budget = transcription.TranscribeBudget()
    assert budget.max_voices == 2
    assert budget.max_seconds == 60


def test_budget_voice_count_cap():
    budget = transcription.TranscribeBudget(max_voices=2, max_seconds=10_000)
    assert budget.can_afford(10)
    budget.charge(10)
    assert budget.can_afford(10)
    budget.charge(10)
    assert not budget.can_afford(1)


def test_budget_seconds_cap():
    budget = transcription.TranscribeBudget(max_voices=10, max_seconds=50)
    assert budget.can_afford(40)
    budget.charge(40)
    assert not budget.can_afford(20)  # 40 + 20 > 50
    assert budget.can_afford(10)  # 40 + 10 <= 50


# ---------------------------------------------------------------------------
# voice_attachment_info / render_voice_text
# ---------------------------------------------------------------------------


def test_voice_attachment_info_none_for_non_voice_message():
    msg = SimpleNamespace(id=1, voice=None, video_note=None, file=None)
    assert transcription.voice_attachment_info(msg, chat_id=1) is None


def test_voice_attachment_info_shows_duration_only_without_chat_id(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TRANSCRIBE", "on-demand")
    info = transcription.voice_attachment_info(_voice_msg(), chat_id=None)
    assert info == {
        "duration": 23,
        "transcript": None,
        "transcript_source": None,
        "transcript_status": None,
    }


def test_voice_attachment_info_disabled_when_mode_off(monkeypatch, transcript_cache_dir):
    monkeypatch.setenv("TELEGRAM_TRANSCRIBE", "off")
    transcription.save_transcript(1, 1, "groq", "should not surface")
    info = transcription.voice_attachment_info(_voice_msg(id=1), chat_id=1)
    assert info["transcript_status"] is None
    assert info["transcript"] is None
    assert info["duration"] == 23  # duration is free, shown regardless of the toggle


def test_voice_attachment_info_cache_hit_is_ready(monkeypatch, transcript_cache_dir):
    monkeypatch.setenv("TELEGRAM_TRANSCRIBE", "on-demand")
    transcription.save_transcript(1, 1, "groq", "hello", duration=23)
    info = transcription.voice_attachment_info(_voice_msg(id=1), chat_id=1)
    assert info["transcript_status"] == "ready"
    assert info["transcript"] == "hello"
    assert info["transcript_source"] == "groq"


def test_voice_attachment_info_cache_miss_is_pending(monkeypatch, transcript_cache_dir):
    monkeypatch.setenv("TELEGRAM_TRANSCRIBE", "on-demand")
    info = transcription.voice_attachment_info(_voice_msg(id=99), chat_id=1)
    assert info["transcript_status"] == "pending"
    assert info["transcript"] is None


def test_render_voice_text_ready_marks_source_and_not_verbatim():
    info = {
        "duration": 23,
        "transcript": "hi there",
        "transcript_source": "groq",
        "transcript_status": "ready",
    }
    assert (
        transcription.render_voice_text(info)
        == "voice 0:23 | transcript (groq, not verbatim): hi there"
    )


def test_render_voice_text_pending():
    info = {
        "duration": 23,
        "transcript": None,
        "transcript_source": None,
        "transcript_status": "pending",
    }
    assert transcription.render_voice_text(info) == "voice 0:23 | transcript pending"


def test_render_voice_text_disabled_shows_duration_only():
    info = {
        "duration": 23,
        "transcript": None,
        "transcript_source": None,
        "transcript_status": None,
    }
    assert transcription.render_voice_text(info) == "voice 0:23"


# ---------------------------------------------------------------------------
# Telegram engine: pending must be polled, not silently dropped
# ---------------------------------------------------------------------------


class _FakeTelegramClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def get_input_entity(self, entity):
        return "peer"

    async def __call__(self, request):
        self.calls += 1
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


@pytest.mark.asyncio
async def test_transcribe_via_telegram_polls_until_ready(monkeypatch):
    monkeypatch.setattr(transcription, "account_is_premium", _async_return(True))
    monkeypatch.setattr(transcription.asyncio, "sleep", _async_return(None))
    client = _FakeTelegramClient(
        [
            SimpleNamespace(pending=True, text=None),
            SimpleNamespace(pending=True, text=None),
            SimpleNamespace(pending=False, text="full text, no dropped tail"),
        ]
    )

    result = await transcription._transcribe_via_telegram(client, object(), SimpleNamespace(id=42))

    assert result == {"status": "ok", "text": "full text, no dropped tail"}
    assert client.calls == 3  # 1 initial + 2 polls, matches the live 83s-recording measurement


@pytest.mark.asyncio
async def test_transcribe_via_telegram_first_call_ready_needs_no_poll(monkeypatch):
    monkeypatch.setattr(transcription, "account_is_premium", _async_return(True))
    monkeypatch.setattr(transcription.asyncio, "sleep", _async_return(None))
    client = _FakeTelegramClient([SimpleNamespace(pending=False, text="instant")])

    result = await transcription._transcribe_via_telegram(client, object(), SimpleNamespace(id=1))

    assert result == {"status": "ok", "text": "instant"}
    assert client.calls == 1


@pytest.mark.asyncio
async def test_transcribe_via_telegram_gives_up_after_poll_budget_and_degrades(monkeypatch):
    monkeypatch.setattr(transcription, "account_is_premium", _async_return(True))
    monkeypatch.setattr(transcription.asyncio, "sleep", _async_return(None))
    monkeypatch.setattr(transcription, "_POLL_ATTEMPTS", 2)
    client = _FakeTelegramClient([SimpleNamespace(pending=True, text=None)] * 10)

    result = await transcription._transcribe_via_telegram(client, object(), SimpleNamespace(id=1))

    assert result == {"status": "pending"}  # honest degradation, not a crash


@pytest.mark.asyncio
async def test_transcribe_via_telegram_requires_premium(monkeypatch):
    monkeypatch.setattr(transcription, "account_is_premium", _async_return(False))
    client = _FakeTelegramClient([])

    result = await transcription._transcribe_via_telegram(client, object(), SimpleNamespace(id=1))

    assert result == {"status": "premium_required"}
    assert client.calls == 0  # never even attempted the RPC


@pytest.mark.asyncio
async def test_transcribe_via_telegram_rpc_error_maps_to_premium_required(monkeypatch):
    monkeypatch.setattr(transcription, "account_is_premium", _async_return(True))
    monkeypatch.setattr(transcription, "is_premium_rpc_error", lambda e: True)
    client = _FakeTelegramClient([RuntimeError("PREMIUM_ACCOUNT_REQUIRED")])

    result = await transcription._transcribe_via_telegram(client, object(), SimpleNamespace(id=1))

    assert result == {"status": "premium_required"}


# ---------------------------------------------------------------------------
# Groq engine: in-memory download, never touches disk
# ---------------------------------------------------------------------------


class _FakeGroqResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeHttpxClient:
    def __init__(self, response, capture):
        self._response = response
        self._capture = capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, data=None, files=None):
        self._capture.append({"url": url, "headers": headers, "data": data, "files": files})
        return self._response


@pytest.mark.asyncio
async def test_transcribe_via_groq_success(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    calls = []
    response = _FakeGroqResponse({"text": " hello world ", "language": "ru"})
    monkeypatch.setattr(
        transcription.httpx, "AsyncClient", lambda **kw: _FakeHttpxClient(response, calls)
    )

    msg = _voice_msg()

    async def _download_media(m, file=None):
        assert m is msg
        assert file is bytes  # in-memory download, no disk path
        return b"raw-audio-bytes"

    client = SimpleNamespace(download_media=_download_media)

    result = await transcription._transcribe_via_groq(client, msg)

    assert result == {"status": "ok", "text": "hello world", "lang": "ru"}
    assert calls[0]["headers"]["Authorization"] == "Bearer test-key"
    assert calls[0]["files"]["file"][0] == "voice.oga"
    assert calls[0]["data"]["model"] == transcription.GROQ_MODEL


@pytest.mark.asyncio
async def test_transcribe_via_groq_requires_api_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    result = await transcription._transcribe_via_groq(SimpleNamespace(), _voice_msg())

    assert result["status"] == "error"
    assert "GROQ_API_KEY" in result["error"]


@pytest.mark.asyncio
async def test_transcribe_via_groq_empty_download_is_error(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")

    async def _download_media(m, file=None):
        return b""

    client = SimpleNamespace(download_media=_download_media)
    result = await transcription._transcribe_via_groq(client, _voice_msg())

    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_transcribe_via_groq_empty_transcript_is_error(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    response = _FakeGroqResponse({"text": "", "language": "ru"})
    monkeypatch.setattr(
        transcription.httpx, "AsyncClient", lambda **kw: _FakeHttpxClient(response, [])
    )

    async def _download_media(m, file=None):
        return b"raw-audio-bytes"

    client = SimpleNamespace(download_media=_download_media)
    result = await transcription._transcribe_via_groq(client, _voice_msg())

    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# transcribe() dispatcher
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transcribe_dispatches_to_telegram_engine(monkeypatch):
    monkeypatch.setattr(
        transcription, "_transcribe_via_telegram", _async_return({"status": "ok", "text": "tg"})
    )
    result = await transcription.transcribe(None, None, None, "telegram")
    assert result["text"] == "tg"


@pytest.mark.asyncio
async def test_transcribe_dispatches_to_groq_engine(monkeypatch):
    monkeypatch.setattr(
        transcription, "_transcribe_via_groq", _async_return({"status": "ok", "text": "groq"})
    )
    result = await transcription.transcribe(None, None, None, "groq")
    assert result["text"] == "groq"


@pytest.mark.asyncio
async def test_transcribe_unknown_engine_is_an_error():
    result = await transcription.transcribe(None, None, None, "bogus")
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Batch prefetch (mode="auto")
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prefetch_is_noop_outside_auto_mode(monkeypatch, transcript_cache_dir):
    monkeypatch.setenv("TELEGRAM_TRANSCRIBE", "on-demand")
    monkeypatch.setattr(transcription, "transcribe", _async_return({"status": "ok", "text": "x"}))

    await transcription.prefetch_transcripts(None, None, 1, [_voice_msg(id=1)])

    assert transcription.get_cached_transcript(1, 1) is None


@pytest.mark.asyncio
async def test_prefetch_fills_cache_up_to_voice_budget(monkeypatch, transcript_cache_dir):
    monkeypatch.setenv("TELEGRAM_TRANSCRIBE", "auto")
    monkeypatch.setenv("TELEGRAM_TRANSCRIBE_MAX_VOICES", "1")
    monkeypatch.setenv("TELEGRAM_TRANSCRIBE_MAX_SECONDS", "1000")

    calls = []

    async def _fake_transcribe(cl, entity, msg, engine):
        calls.append(msg.id)
        return {"status": "ok", "text": f"text-{msg.id}", "lang": "ru"}

    monkeypatch.setattr(transcription, "transcribe", _fake_transcribe)

    await transcription.prefetch_transcripts(None, None, 7, [_voice_msg(id=1), _voice_msg(id=2)])

    assert calls == [1]  # budget exhausted after the first voice message
    assert transcription.get_cached_transcript(7, 1)["text"] == "text-1"
    assert transcription.get_cached_transcript(7, 2) is None  # left for next explicit call


@pytest.mark.asyncio
async def test_prefetch_skips_cached_and_already_texted_messages(
    monkeypatch, transcript_cache_dir
):
    monkeypatch.setenv("TELEGRAM_TRANSCRIBE", "auto")
    transcription.save_transcript(7, 1, "telegram", "already have it")
    calls = []

    async def _fake_transcribe(cl, entity, msg, engine):
        calls.append(msg.id)
        return {"status": "ok", "text": "new"}

    monkeypatch.setattr(transcription, "transcribe", _fake_transcribe)

    messages = [_voice_msg(id=1), _voice_msg(id=2, message="already has text")]
    await transcription.prefetch_transcripts(None, None, 7, messages)

    assert calls == []


@pytest.mark.asyncio
async def test_prefetch_swallows_engine_errors_without_raising(monkeypatch, transcript_cache_dir):
    monkeypatch.setenv("TELEGRAM_TRANSCRIBE", "auto")

    async def _boom(cl, entity, msg, engine):
        raise RuntimeError("network down")

    monkeypatch.setattr(transcription, "transcribe", _boom)

    await transcription.prefetch_transcripts(None, None, 7, [_voice_msg(id=5)])  # must not raise

    assert transcription.get_cached_transcript(7, 5) is None


@pytest.mark.asyncio
async def test_prefetch_does_not_cache_pending_results(monkeypatch, transcript_cache_dir):
    monkeypatch.setenv("TELEGRAM_TRANSCRIBE", "auto")
    monkeypatch.setattr(transcription, "transcribe", _async_return({"status": "pending"}))

    await transcription.prefetch_transcripts(None, None, 7, [_voice_msg(id=5)])

    assert transcription.get_cached_transcript(7, 5) is None


# ---------------------------------------------------------------------------
# One paid call per recording
# ---------------------------------------------------------------------------
#
# Reviewed on PR #197: two concurrent callers both missed the cache, both
# downloaded the same audio and both paid Groq for it.


def _counting_engine(counter, text="hello"):
    async def _fake(cl, entity, msg, engine):
        counter["calls"] += 1
        # Long enough for a second caller to reach the same key meanwhile.
        await asyncio.sleep(0.02)
        return {"status": "ok", "text": text, "lang": "en"}

    return _fake


@pytest.mark.asyncio
async def test_concurrent_callers_pay_the_engine_once(transcript_cache_dir, monkeypatch):
    counter = {"calls": 0}
    monkeypatch.setattr(transcription, "transcribe", _counting_engine(counter))
    msg = _voice_msg(id=77)

    results = await asyncio.gather(
        *[
            transcription.transcribe_cached(None, None, msg, "groq", -100, duration=23)
            for _ in range(5)
        ]
    )

    assert counter["calls"] == 1, "each concurrent caller paid the engine separately"
    assert all(r["status"] == "ok" and r["text"] == "hello" for r in results)
    assert sum(1 for r in results if not r["cached"]) == 1
    assert transcription.get_cached_transcript(-100, 77, source="groq")["text"] == "hello"


@pytest.mark.asyncio
async def test_second_caller_after_the_first_finished_is_a_plain_cache_hit(
    transcript_cache_dir, monkeypatch
):
    counter = {"calls": 0}
    monkeypatch.setattr(transcription, "transcribe", _counting_engine(counter))
    msg = _voice_msg(id=78)

    first = await transcription.transcribe_cached(None, None, msg, "groq", -100)
    second = await transcription.transcribe_cached(None, None, msg, "groq", -100)

    assert counter["calls"] == 1
    assert first["cached"] is False and second["cached"] is True


@pytest.mark.asyncio
async def test_dedup_is_keyed_by_engine(transcript_cache_dir, monkeypatch):
    counter = {"calls": 0}
    monkeypatch.setattr(transcription, "transcribe", _counting_engine(counter))
    msg = _voice_msg(id=79)

    await transcription.transcribe_cached(None, None, msg, "groq", -100)
    await transcription.transcribe_cached(None, None, msg, "telegram", -100)

    # A groq request must never be answered with a telegram transcript, so the
    # two engines are two separate paid calls, not one shared one.
    assert counter["calls"] == 2


@pytest.mark.asyncio
async def test_failed_transcription_is_not_cached_and_leaks_no_lock(
    transcript_cache_dir, monkeypatch
):
    async def _failing(cl, entity, msg, engine):
        return {"status": "error", "error": "groq request failed: 503"}

    monkeypatch.setattr(transcription, "transcribe", _failing)
    msg = _voice_msg(id=80)

    result = await transcription.transcribe_cached(None, None, msg, "groq", -100)

    assert result["status"] == "error"
    assert transcription.get_cached_transcript(-100, 80, source="groq") is None
    # The registry must not grow with every message ever attempted.
    assert transcription._INFLIGHT_LOCKS == {}


# ---------------------------------------------------------------------------
# Groq upload ceiling
# ---------------------------------------------------------------------------


def test_groq_limit_defaults_to_the_free_tier(monkeypatch):
    monkeypatch.delenv("TELEGRAM_TRANSCRIBE_GROQ_MAX_MB", raising=False)
    assert transcription.groq_max_upload_bytes() == 25 * 1024 * 1024


@pytest.mark.parametrize("raw", ["", "nonsense", "0", "-4"])
def test_groq_limit_falls_back_on_unusable_values(monkeypatch, raw):
    monkeypatch.setenv("TELEGRAM_TRANSCRIBE_GROQ_MAX_MB", raw)
    assert transcription.groq_max_upload_bytes() == 25 * 1024 * 1024


def test_groq_limit_is_raisable_for_paid_tiers(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TRANSCRIBE_GROQ_MAX_MB", "100")
    assert transcription.groq_max_upload_bytes() == 100 * 1024 * 1024


@pytest.mark.asyncio
async def test_oversized_recording_is_refused_without_downloading(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    downloads = {"calls": 0}

    async def _download(*args, **kwargs):
        downloads["calls"] += 1
        return b"x"

    cl = SimpleNamespace(download_media=_download)
    msg = _voice_msg(
        file=SimpleNamespace(
            duration=9000, ext=".oga", mime_type="audio/ogg", size=40 * 1024 * 1024
        )
    )

    result = await transcription._transcribe_via_groq(cl, msg)

    assert result["status"] == "error"
    assert result["reason"] == "too_large"
    assert "40.0 MB" in result["error"] and "25 MB" in result["error"]
    assert downloads["calls"] == 0, "paid for a download of a file that cannot be uploaded"


@pytest.mark.asyncio
async def test_oversized_download_is_refused_when_the_size_was_not_declared(monkeypatch):
    """Telegram does not always state a size, and the stated one can be stale."""
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("TELEGRAM_TRANSCRIBE_GROQ_MAX_MB", "1")
    posted = {"calls": 0}

    async def _download(*args, **kwargs):
        return b"0" * (2 * 1024 * 1024)

    def _fail_post(*args, **kwargs):
        posted["calls"] += 1
        raise AssertionError("uploaded a file above the limit")

    monkeypatch.setattr(transcription.httpx, "AsyncClient", _fail_post)
    cl = SimpleNamespace(download_media=_download)
    msg = _voice_msg(file=SimpleNamespace(duration=600, ext=".oga", mime_type="audio/ogg"))

    result = await transcription._transcribe_via_groq(cl, msg)

    assert result["reason"] == "too_large"
    assert posted["calls"] == 0


@pytest.mark.asyncio
async def test_recording_within_the_limit_still_uploads(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("TELEGRAM_TRANSCRIBE_GROQ_MAX_MB", "25")

    async def _download(*args, **kwargs):
        return b"0" * 1024

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"text": " transcribed ", "language": "ru"}

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return _Resp()

    monkeypatch.setattr(transcription.httpx, "AsyncClient", _FakeClient)
    cl = SimpleNamespace(download_media=_download)
    msg = _voice_msg(
        file=SimpleNamespace(duration=30, ext=".oga", mime_type="audio/ogg", size=1024)
    )

    result = await transcription._transcribe_via_groq(cl, msg)

    assert result == {"status": "ok", "text": "transcribed", "lang": "ru"}
