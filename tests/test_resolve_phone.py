"""resolve_phone must send contacts.resolvePhone once, and only within the local limits."""

import asyncio
import json
import os
import weakref

import pytest
from telethon import TelegramClient, functions, types
from telethon.errors import FloodWaitError, PhoneNotOccupiedError, PhoneNumberInvalidError
from telethon.sessions import StringSession

from telegram_mcp import resolve_phone_limit, runtime
from telegram_mcp.resolve_phone_limit import MAX_PER_DAY_CEILING, ResolvePhoneLimiter
from telegram_mcp.tools import contacts

DAY = 24 * 60 * 60


class PhoneServer(TelegramClient):
    def __init__(self):
        super().__init__(StringSession(), 12345, "dummy_hash")
        self.requests = []
        self.thresholds_during_call = []
        self.error = None
        self.release = None

    async def __call__(self, request, ordered=False, flood_sleep_threshold=None):
        assert isinstance(request, functions.contacts.ResolvePhoneRequest)
        self.requests.append(request)
        self.thresholds_during_call.append(self.flood_sleep_threshold)
        await asyncio.sleep(0)
        if self.release is not None:
            await self.release.wait()
        if self.error is not None:
            raise self.error
        return types.contacts.ResolvedPeer(
            peer=types.PeerUser(user_id=42),
            chats=[],
            users=[types.User(id=42, first_name="Lead", phone=request.phone)],
        )


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def server(monkeypatch, clock):
    client = PhoneServer()

    async def connected(cl=None):
        return None

    for name in (
        resolve_phone_limit.MAX_PER_DAY_ENV,
        resolve_phone_limit.MIN_INTERVAL_ENV,
        resolve_phone_limit.STATE_FILE_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(contacts, "get_client", lambda account=None: client)
    monkeypatch.setattr(contacts, "ensure_connected", connected)
    monkeypatch.setattr(contacts, "_resolve_phone_limiter", ResolvePhoneLimiter(clock=clock))
    monkeypatch.setattr(contacts, "_resolve_phone_call_locks", weakref.WeakKeyDictionary())
    return client


def _limit(monkeypatch, per_day=None, interval=None, state_file=None):
    if per_day is not None:
        monkeypatch.setenv(resolve_phone_limit.MAX_PER_DAY_ENV, str(per_day))
    if interval is not None:
        monkeypatch.setenv(resolve_phone_limit.MIN_INTERVAL_ENV, str(interval))
    if state_file is not None:
        monkeypatch.setenv(resolve_phone_limit.STATE_FILE_ENV, str(state_file))


def test_resolve_phone_is_read_only():
    registered = {tool.name: tool for tool in runtime.mcp._tool_manager.list_tools()}
    assert registered["resolve_phone"].annotations.readOnlyHint is True
    assert registered["resolve_phone"].annotations.openWorldHint is True


@pytest.mark.asyncio
async def test_resolve_phone_sends_bare_digits_with_auto_sleep_off(server):
    server.flood_sleep_threshold = 60

    result = await contacts.resolve_phone("+7 (999) 000-11.22")

    (request,) = server.requests
    assert request.phone == "79990001122"
    assert server.thresholds_during_call == [0]
    assert server.flood_sleep_threshold == 60
    assert "user_id=42" in result
    assert "first_name='Lead'" in result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        "",
        "+",
        "+123456",
        "+1234567890123456",
        "++79990001122",
        "+7999abc1122",
        "+٧٩٩٩٠٠٠١١٢٢",
        None,
        "79990001122",
        "8 999 000-11-22",
        "0044 20 7946 0958",
        "+044 20 7946 0958",
    ],
    ids=[
        "empty",
        "plus",
        "short",
        "long",
        "double-plus",
        "letters",
        "non-ascii-digits",
        "none",
        "no-plus",
        "local-trunk-8",
        "international-prefix-00",
        "country-code-0",
    ],
)
async def test_resolve_phone_rejects_a_malformed_number_before_any_request(server, bad):
    result = await contacts.resolve_phone(bad)

    assert result.startswith("Error: phone must be an international number")
    assert server.requests == []


@pytest.mark.asyncio
async def test_resolve_phone_explains_an_unused_number(server):
    server.error = PhoneNotOccupiedError(request=None)

    result = await contacts.resolve_phone("+79990001122")

    assert result.startswith("No Telegram user found for this number")


@pytest.mark.asyncio
async def test_resolve_phone_explains_an_invalid_number(server):
    server.error = PhoneNumberInvalidError(request=None)

    result = await contacts.resolve_phone("+79990001122")

    assert result.startswith("Error: Telegram rejected this phone number as invalid")


@pytest.mark.asyncio
async def test_resolve_phone_passes_only_a_masked_number_to_the_error_log(server, monkeypatch):
    server.error = RuntimeError("boom")
    seen = {}

    def capture(function_name, error, **kwargs):
        seen.update(kwargs)
        return "logged"

    monkeypatch.setattr(contacts, "log_and_format_error", capture)

    assert await contacts.resolve_phone("+79990001122") == "logged"
    assert seen["phone"] == "...22"


@pytest.mark.asyncio
async def test_unconfigured_limits_never_refuse_or_write_state(server, tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    _limit(monkeypatch, state_file=state)

    for _ in range(3):
        await contacts.resolve_phone("+79990001122")

    assert len(server.requests) == 3
    assert not state.exists()


@pytest.mark.asyncio
async def test_min_interval_refuses_without_sending_then_allows(server, clock, monkeypatch):
    _limit(monkeypatch, interval=5)

    await contacts.resolve_phone("+79990001122")
    clock.now += 2
    refused = await contacts.resolve_phone("+79990003344")
    clock.now += 3
    await contacts.resolve_phone("+79990003344")

    assert "Retry in 3 seconds" in refused
    assert "no request was sent to Telegram" in refused
    assert [r.phone for r in server.requests] == ["79990001122", "79990003344"]


@pytest.mark.asyncio
async def test_daily_limit_is_a_rolling_window(server, clock, monkeypatch):
    _limit(monkeypatch, per_day=2)
    start = clock.now

    await contacts.resolve_phone("+79990000001")
    clock.now = start + 100
    await contacts.resolve_phone("+79990000002")
    clock.now = start + 200
    refused = await contacts.resolve_phone("+79990000003")
    clock.now = start + DAY
    await contacts.resolve_phone("+79990000004")
    clock.now = start + DAY + 1
    still_refused = await contacts.resolve_phone("+79990000005")

    assert f"Retry in {DAY - 200} seconds" in refused
    assert "Retry in 99 seconds" in still_refused
    assert [r.phone[-1] for r in server.requests] == ["1", "2", "4"]


@pytest.mark.asyncio
async def test_errors_from_telegram_count_and_rejected_input_does_not(server, monkeypatch):
    _limit(monkeypatch, per_day=1)
    server.error = PhoneNotOccupiedError(request=None)

    await contacts.resolve_phone("8 999 000-11-22")
    await contacts.resolve_phone("+79990001122")
    refused = await contacts.resolve_phone("+79990003344")

    assert len(server.requests) == 1
    assert "limit of 1 lookups per 24 h is used up" in refused


@pytest.mark.asyncio
async def test_state_file_survives_a_restart_and_stays_bounded(
    server, clock, tmp_path, monkeypatch
):
    state = tmp_path / "nested" / "state.json"
    _limit(monkeypatch, per_day=2, state_file=state)

    for _ in range(3):
        clock.now += DAY  # each lookup starts a fresh window
        await contacts.resolve_phone("+79990001122")
    monkeypatch.setattr(contacts, "_resolve_phone_limiter", ResolvePhoneLimiter(clock=clock))
    clock.now += 1
    await contacts.resolve_phone("+79990001122")
    clock.now += 1
    refused = await contacts.resolve_phone("+79990001122")

    assert len(server.requests) == 4
    assert "per 24 h is used up" in refused
    saved = json.loads(state.read_text(encoding="utf-8"))["attempts"]
    assert saved == [clock.now - 2, clock.now - 1]
    if os.name != "nt":
        assert state.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[1, 2]",
        '{"attempts": "1"}',
        '{"attempts": [true]}',
        '{"attempts": [NaN]}',
        json.dumps({"attempts": [1.0] * (MAX_PER_DAY_CEILING + 1)}),
    ],
    ids=["not-json", "not-object", "not-list", "bool", "nan", "over-ceiling"],
)
async def test_unreadable_state_file_fails_closed(server, tmp_path, monkeypatch, content):
    state = tmp_path / "state.json"
    state.write_text(content, encoding="utf-8")
    _limit(monkeypatch, per_day=12, state_file=state)

    result = await contacts.resolve_phone("+79990001122")

    assert "remaining budget is unknown" in result
    assert server.requests == []
    assert state.read_text(encoding="utf-8") == content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "env, value",
    [
        (resolve_phone_limit.MAX_PER_DAY_ENV, "twelve"),
        (resolve_phone_limit.MAX_PER_DAY_ENV, "-1"),
        (resolve_phone_limit.MAX_PER_DAY_ENV, str(MAX_PER_DAY_CEILING + 1)),
        (resolve_phone_limit.MIN_INTERVAL_ENV, "soon"),
        (resolve_phone_limit.MIN_INTERVAL_ENV, str(DAY + 1)),
    ],
)
async def test_malformed_limit_settings_refuse_lookups(server, monkeypatch, env, value):
    monkeypatch.setenv(env, value)

    result = await contacts.resolve_phone("+79990001122")

    assert result.startswith("Error: resolve_phone is disabled until the configuration is fixed")
    assert env in result
    assert server.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("use_state_file", [False, True], ids=["memory", "state-file"])
async def test_parallel_tool_calls_spend_one_slot(server, tmp_path, monkeypatch, use_state_file):
    _limit(monkeypatch, interval=5, state_file=tmp_path / "state.json" if use_state_file else None)

    results = await asyncio.gather(
        contacts.resolve_phone("+79990001122"), contacts.resolve_phone("+79990003344")
    )

    assert len(server.requests) == 1
    assert sum("refused locally" in r for r in results) == 1


def test_a_second_process_sees_the_slot_taken_under_the_file_lock(clock, tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    _limit(monkeypatch, per_day=1, state_file=state)

    first, second = ResolvePhoneLimiter(clock=clock), ResolvePhoneLimiter(clock=clock)

    assert first.reserve() is None
    assert "per 24 h is used up" in second.reserve()


@pytest.mark.asyncio
async def test_overlapping_lookups_restore_the_configured_flood_threshold(server):
    server.flood_sleep_threshold = 60

    await asyncio.gather(
        contacts.resolve_phone("+79990001122"), contacts.resolve_phone("+79990003344")
    )

    assert server.thresholds_during_call == [0, 0]
    assert server.flood_sleep_threshold == 60


@pytest.mark.asyncio
async def test_a_clock_moved_back_never_blocks_lookups_for_more_than_a_day(
    server, clock, tmp_path, monkeypatch
):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"attempts": [clock.now + 10 * DAY]}), encoding="utf-8")
    _limit(monkeypatch, per_day=1, state_file=state)

    refused = await contacts.resolve_phone("+79990001122")
    clock.now += DAY
    await contacts.resolve_phone("+79990001122")

    assert f"Retry in {int(DAY)} seconds" in refused
    assert len(server.requests) == 1


@pytest.mark.asyncio
async def test_the_longer_wait_wins_when_both_limits_refuse(server, clock, monkeypatch):
    _limit(monkeypatch, per_day=1, interval=5)

    await contacts.resolve_phone("+79990001122")
    clock.now += 1
    refused = await contacts.resolve_phone("+79990001122")

    assert "per 24 h is used up" in refused
    assert f"Retry in {DAY - 1} seconds" in refused


@pytest.mark.asyncio
async def test_several_accounts_require_an_explicit_account(server, monkeypatch):
    monkeypatch.setattr(runtime, "clients", {"work": server, "home": server})

    result = await contacts.resolve_phone("+79990001122")

    assert result.startswith("Error: 'account' is required")
    assert server.requests == []


class FloodOnceSender:
    """Stands in for MTProtoSender: the first send hits a short FloodWait, a retry would pass."""

    def __init__(self):
        self.sent = []

    def send(self, request, ordered=False):
        self.sent.append(request)
        future = asyncio.get_running_loop().create_future()
        if len(self.sent) == 1:
            future.set_exception(FloodWaitError(request=request, capture=1))
        else:
            future.set_result(
                types.contacts.ResolvedPeer(peer=types.PeerUser(user_id=42), chats=[], users=[])
            )
        return future


@pytest.mark.asyncio
async def test_real_client_returns_a_short_flood_wait_instead_of_sleeping(server, monkeypatch):
    client = TelegramClient(StringSession(), 12345, "dummy_hash", flood_sleep_threshold=60)
    sender = FloodOnceSender()
    client._sender = sender
    monkeypatch.setattr(contacts, "get_client", lambda account=None: client)

    result = await contacts.resolve_phone("+79990001122")

    assert len(sender.sent) == 1
    assert "Rate limit exceeded (FloodWait)" in result
    assert client.flood_sleep_threshold == 60


@pytest.mark.asyncio
async def test_a_cancelled_lookup_restores_the_threshold_and_frees_the_lock(server):
    server.flood_sleep_threshold = 60
    server.release = asyncio.Event()

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(contacts.resolve_phone("+79990001122"), 0.01)

    assert server.flood_sleep_threshold == 60
    assert not contacts._resolve_phone_call_locks[server].locked()


@pytest.mark.asyncio
async def test_a_slow_lookup_on_one_account_does_not_block_another(server, monkeypatch):
    other = PhoneServer()
    accounts = {"work": server, "home": other}
    monkeypatch.setattr(runtime, "clients", accounts)
    monkeypatch.setattr(contacts, "get_client", lambda account=None: accounts[account])
    server.release = asyncio.Event()

    slow = asyncio.create_task(contacts.resolve_phone("+79990001122", account="work"))
    await asyncio.sleep(0)
    assert len(server.requests) == 1
    fast = await asyncio.wait_for(contacts.resolve_phone("+79990003344", account="home"), 1)
    server.release.set()
    await slow

    assert "user_id=42" in fast
    assert len(other.requests) == 1
