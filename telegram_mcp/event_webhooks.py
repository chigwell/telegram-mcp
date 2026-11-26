"""Opt-in, non-replayable MCP message subscriptions backed by the existing event log."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import anyio

logger = logging.getLogger(__name__)
EVENT_NAME = "telegram.message.created"
DEFAULT_TTL_MS = 3_600_000
MAX_TTL_MS = 86_400_000
MAX_SUBSCRIPTIONS = 100


class EventError(Exception):
    def __init__(self, message, code=-32602, reason=None):
        super().__init__(message)
        self.code = code
        self.reason = reason


def utc_timestamp(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")


def secret_key(secret):
    try:
        if not isinstance(secret, str) or not secret.startswith("whsec_"):
            raise ValueError
        key = base64.b64decode(secret[6:], validate=True)
        if not 24 <= len(key) <= 64:
            raise ValueError
        return key
    except (ValueError, TypeError):
        raise EventError("Expected a whsec_ secret encoding 24–64 bytes.") from None


def callback_url(url):
    try:
        if not isinstance(url, str) or len(url) > 4096:
            raise ValueError
        target = urlsplit(url)
        if (
            target.scheme != "https"
            or not target.hostname
            or target.username is not None
            or target.password is not None
            or target.fragment
            or target.port not in (None, 443)
            or any(ord(c) <= 32 or ord(c) == 127 for c in url)
        ):
            raise ValueError
        return target
    except ValueError:
        raise EventError("Expected an HTTPS callback on port 443 without credentials or fragment.")


async def post_webhook(url, subscription_id, event_id, body, signing_secrets):
    """Resolve once, connect to a public IP, and verify TLS against the original host."""
    with anyio.fail_after(15):
        target = callback_url(url)
        host = target.hostname.encode("idna").decode("ascii")
        addresses = await asyncio.get_running_loop().getaddrinfo(
            host, 443, type=socket.SOCK_STREAM
        )
        if not addresses:
            raise OSError("Callback has no addresses")
        for _, _, _, _, address in addresses:
            ip = ipaddress.ip_address(address[0])
            if not ip.is_global or ip.is_multicast or getattr(ip, "ipv4_mapped", None):
                raise EventError("Callback address is not public.", -32015, "invalid_url")
        ip = addresses[0][4][0]
        authority = "[" + ip + "]" if ":" in ip else ip
        pinned_url = "https://" + authority + (target.path or "/")
        if target.query:
            pinned_url += "?" + target.query
        stamp = str(int(time.time()))
        signed = f"{event_id}.{stamp}.".encode() + body
        signatures = [
            "v1," + base64.b64encode(hmac.digest(secret_key(s), signed, "sha256")).decode()
            for s in signing_secrets
        ]
        # Use the transport directly: AsyncClient logs callback capability URLs at INFO.
        async with httpx.AsyncHTTPTransport(trust_env=False) as transport:
            request = httpx.Request(
                "POST",
                pinned_url,
                content=body,
                headers={
                    "Host": host,
                    "Content-Type": "application/json",
                    "Accept-Encoding": "identity",
                    "webhook-id": event_id,
                    "webhook-timestamp": stamp,
                    "webhook-signature": " ".join(signatures),
                    "X-MCP-Subscription-Id": subscription_id,
                },
                extensions={
                    "sni_hostname": host,
                    "timeout": {"connect": 10, "read": 10, "write": 10, "pool": 10},
                },
            )
            response = await transport.handle_async_request(request)
            try:
                data = bytearray()
                async for chunk in response.aiter_raw():
                    data.extend(chunk)
                    if len(data) > 65536:
                        raise EventError(
                            "Callback response is too large.", -32015, "invalid_response"
                        )
                return response.status_code, bytes(data)
            finally:
                await response.aclose()


def event_definition():
    return {
        "name": EVENT_NAME,
        "description": (
            "New message metadata in a selected Telegram chat, "
            "or explicitly all chats of one account."
        ),
        "delivery": ["webhook"],
        "inputSchema": {
            "type": "object",
            "properties": {
                "account": {
                    "type": "string",
                    "description": "Account label; required with multiple accounts.",
                },
                "chat_id": {
                    "type": ["integer", "string"],
                    "description": "Chat ID, username or saved alias.",
                },
                "all_chats": {
                    "type": "boolean",
                    "default": False,
                    "description": "Explicitly monitor all allowed chats of the selected account.",
                },
                "include_outgoing": {"type": "boolean", "default": False},
            },
            "oneOf": [
                {"required": ["chat_id"], "properties": {"all_chats": {"const": False}}},
                {
                    "required": ["all_chats"],
                    "properties": {"all_chats": {"const": True}},
                    "not": {"required": ["chat_id"]},
                },
            ],
            "additionalProperties": False,
        },
        "payloadSchema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "chat_id": {"type": "integer"},
                "message_id": {"type": "integer"},
                "timestamp": {"type": "string", "format": "date-time"},
            },
            "required": ["account", "chat_id", "message_id", "timestamp"],
            "additionalProperties": False,
        },
    }


class SubscriptionStore:
    """Small, private, atomically replaced subscription file; no message archive."""

    def __init__(self, path):
        self.path = Path(path)
        self.rows = {}
        if self.path.exists():
            fd = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, encoding="utf-8") as handle:
                if os.name != "nt":
                    os.fchmod(handle.fileno(), 0o600)
                self.rows = json.load(handle)
            if not isinstance(self.rows, dict):
                raise ValueError("Invalid MCP Events subscription store")

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_name(self.path.name + "." + secrets.token_hex(8))
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.rows, handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


class MessageSubscriptions:
    def __init__(self, path):
        self.store = SubscriptionStore(path)
        self.store_lock = None
        self.tasks = {}
        self.verified = {}
        self.lock = asyncio.Lock()
        self.running = False

    async def start(self):
        from telegram_mcp.singleton import SessionLock

        self.store_lock = SessionLock(
            "MCP Events", str(self.store.path.resolve()), lock_dir=self.store.path.parent
        )
        self.store_lock.acquire(grace_seconds=0)
        # Reload only after taking the lock; another process may have saved since construction.
        self.store = SubscriptionStore(self.store.path)
        self.running = True
        for sub_id, row in list(self.store.rows.items()):
            if row["expires"] > time.time():
                self._start_worker(sub_id)
            else:
                del self.store.rows[sub_id]
        self.store.save()

    async def close(self):
        self.running = False
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
        if self.store_lock:
            self.store_lock.release()
            self.store_lock = None

    def _start_worker(self, sub_id):
        from telegram_mcp.tools import events

        old = self.tasks.pop(sub_id, None)
        if old:
            old.cancel()
        self.tasks[sub_id] = asyncio.create_task(self._worker(sub_id, events._event_seq))

    async def arguments(self, arguments, *, resolve=True):
        from telegram_mcp import runtime as rt
        from telegram_mcp.account_labels import canonical_account_label

        if not isinstance(arguments, dict) or set(arguments) - {
            "account",
            "chat_id",
            "all_chats",
            "include_outgoing",
        }:
            raise EventError("Invalid event arguments.")
        args = dict(arguments)
        if any(
            type(args.get(key, False)) is not bool for key in ("all_chats", "include_outgoing")
        ):
            raise EventError("all_chats and include_outgoing must be booleans.")
        account = args.get("account")
        if resolve and account is None and len(rt.clients) == 1:
            account = next(iter(rt.clients))
        if account is not None and not isinstance(account, str):
            raise EventError("account must be a label.")
        args["account"] = canonical_account_label(account) if account is not None else None
        if resolve and args["account"] not in rt.clients:
            raise EventError("Select a configured account label.")
        args.setdefault("all_chats", False)
        args.setdefault("include_outgoing", False)
        if args["all_chats"]:
            if "chat_id" in args:
                raise EventError("Choose chat_id or all_chats, not both.")
        else:
            chat_id = args.get("chat_id")
            if (
                isinstance(chat_id, bool)
                or not isinstance(chat_id, (int, str))
                or not str(chat_id).strip()
            ):
                raise EventError("Select chat_id, or explicitly set all_chats=true.")
            if not resolve:
                return args
            args["chat_id"] = await self._resolve_chat(chat_id, args["account"])
        return args

    async def _resolve_chat(self, chat_id, account):
        from telegram_mcp import runtime as rt
        from telegram_mcp.tools import events

        entity = await rt.resolve_entity(rt.apply_alias(chat_id), rt.get_client(account))
        if not rt.is_chat_allowed(chat_id, entity):
            raise EventError("Chat access denied.", -32001)
        if events._chat_kind(entity) is None:
            raise EventError("Only private chats, groups and supergroups are supported.")
        return rt.get_marked_id(entity)

    async def identity(self, params, subscribe):
        if not isinstance(params, dict) or set(params) - {
            "name",
            "arguments",
            "delivery",
            "cursor",
            "ttlMs",
        }:
            raise EventError("Invalid subscription parameters.")
        if params.get("name") != EVENT_NAME:
            raise EventError("Unknown event name.")
        args = await self.arguments(params.get("arguments", {}), resolve=False)
        delivery = params.get("delivery")
        allowed = {"mode", "url", "secret"} if subscribe else {"mode", "url"}
        if (
            not isinstance(delivery, dict)
            or set(delivery) - allowed
            or delivery.get("mode") != "webhook"
        ):
            raise EventError("Expected webhook delivery.")
        callback_url(delivery.get("url"))
        identity = json.dumps(
            [EVENT_NAME, args, delivery["url"]],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return "sub_" + hashlib.sha256(identity.encode()).hexdigest(), args, delivery

    def _prune_expired(self):
        self.verified = {k: v for k, v in self.verified.items() if v > time.time()}
        for expired_id, expired in list(self.store.rows.items()):
            if expired["expires"] <= time.time():
                del self.store.rows[expired_id]
                task = self.tasks.pop(expired_id, None)
                if task:
                    task.cancel()

    async def subscribe(self, params):
        sub_id, args, delivery = await self.identity(params, True)
        secret_key(delivery.get("secret"))
        if params.get("cursor") is not None:
            raise EventError("This event does not support replay; cursor must be null.")
        ttl = params.get("ttlMs", DEFAULT_TTL_MS)
        if ttl is None:
            ttl = MAX_TTL_MS  # Grant a finite lifetime; the client can refresh it.
        if type(ttl) is not int or ttl <= 0:
            raise EventError("ttlMs must be a positive integer or null.")
        # Serialize subscription updates, not normal tools or event delivery.
        async with self.lock:
            self._prune_expired()
            old = self.store.rows.get(sub_id)
            bound_args = await self.arguments(old["arguments"] if old else args)
            if sub_id not in self.store.rows and len(self.store.rows) >= MAX_SUBSCRIPTIONS:
                raise EventError("Subscription limit reached.", -32000)
            cache_key = (delivery["url"], delivery["secret"])
            if cache_key not in self.verified:
                await self._verify(sub_id, delivery)
                self.verified[cache_key] = time.time() + 300
            expires = time.time() + min(ttl, MAX_TTL_MS) / 1000
            row = {
                "arguments": bound_args,
                "url": delivery["url"],
                "secret": delivery["secret"],
                "expires": expires,
            }
            if old and old["expires"] > time.time() and old["secret"] != row["secret"]:
                row.update(previous_secret=old["secret"], rotate_until=time.time() + 300)
            elif old and old.get("rotate_until", 0) > time.time():
                row.update(
                    previous_secret=old["previous_secret"], rotate_until=old["rotate_until"]
                )
            self._save_subscription(sub_id, row, old)
            if self.running and (
                old is None
                or old["expires"] <= time.time()
                or sub_id not in self.tasks
                or self.tasks[sub_id].done()
            ):
                self._start_worker(sub_id)
        return {
            "id": sub_id,
            "refreshBefore": utc_timestamp(expires),
            "cursor": None,
            "truncated": False,
        }

    def _save_subscription(self, sub_id, row, old):
        self.store.rows[sub_id] = row
        try:
            self.store.save()
        except Exception:
            if old is None:
                self.store.rows.pop(sub_id, None)
            else:
                self.store.rows[sub_id] = old
            raise

    async def _verify(self, sub_id, delivery):
        challenge = secrets.token_urlsafe(32)
        body = json.dumps(
            {"type": "verification", "challenge": challenge}, ensure_ascii=False
        ).encode()
        try:
            status, data = await post_webhook(
                delivery["url"],
                sub_id,
                "verify_" + secrets.token_hex(16),
                body,
                [delivery["secret"]],
            )
            answer = json.loads(data).get("challenge")
            if (
                not 200 <= status < 300
                or not isinstance(answer, str)
                or not hmac.compare_digest(answer, challenge)
            ):
                raise ValueError
        except EventError:
            raise
        except (TimeoutError, socket.timeout):
            raise EventError("Callback verification timed out.", -32015, "timeout") from None
        except Exception:
            raise EventError("Callback verification failed.", -32015, "challenge_failed") from None

    async def unsubscribe(self, params):
        sub_id, _, _ = await self.identity(params, False)
        async with self.lock:
            old = self.store.rows.pop(sub_id, None)
            try:
                self.store.save()
            except Exception:
                if old:
                    self.store.rows[sub_id] = old
                raise
            task = self.tasks.pop(sub_id, None)
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        return {}

    def allowed(self, row, record):
        from telegram_mcp import runtime as rt

        args = row["arguments"]
        if row["expires"] <= time.time() or args["account"] not in rt.clients:
            return False
        if record.get("account") != args["account"]:
            return False
        if not args["all_chats"] and record["chat_id"] != args["chat_id"]:
            return False
        if record.get("out") and not args["include_outgoing"]:
            return False
        chat = record.get("_chat")
        if chat is None and any(isinstance(x, str) for x in rt.get_blocked_chat_ids()):
            return False
        if not rt.is_chat_allowed(record["chat_id"], chat):
            return False
        topics = rt.get_allowed_topics(record["chat_id"], chat)
        return topics is None or record.get("topic") in topics

    async def _worker(self, sub_id, after_seq):
        try:
            await self._run_worker(sub_id, after_seq)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("MCP Events delivery worker failed")

    async def _run_worker(self, sub_id, after_seq):
        from telegram_mcp.tools import events

        while self.running and sub_id in self.store.rows:
            row = self.store.rows[sub_id]
            if row["expires"] <= time.time():
                return
            signal = events._current_event_signal()
            records, gap = events._select_events(
                after_seq, None, row["arguments"]["account"], 100, public=False
            )
            if gap:
                logger.warning("MCP Events log overrun; subscriptions do not support replay")
            scanned_through = events._event_seq
            for record in records:
                if self.allowed(self.store.rows[sub_id], record):
                    await self._deliver(sub_id, record)
                after_seq = record["seq"]
            if len(records) < 100:
                after_seq = scanned_through
                try:
                    await asyncio.wait_for(
                        signal.wait(),
                        timeout=min(30, max(0.001, row["expires"] - time.time())),
                    )
                except asyncio.TimeoutError:
                    pass

    @staticmethod
    def _signing_secrets(row):
        signing_secrets = [row["secret"]]
        if row.get("rotate_until", 0) > time.time():
            signing_secrets.append(row["previous_secret"])
        return signing_secrets

    async def _deliver(self, sub_id, record):
        stamp = record.get("date") or utc_timestamp(time.time())
        event_id = (
            "evt_"
            + hashlib.sha256(
                f"{sub_id}:{record['account']}:{record['chat_id']}:{record['message_id']}".encode()
            ).hexdigest()
        )
        data = {
            "account": record["account"],
            "chat_id": record["chat_id"],
            "message_id": record["message_id"],
            "timestamp": stamp,
        }
        body = json.dumps(
            {
                "eventId": event_id,
                "name": EVENT_NAME,
                "timestamp": stamp,
                "data": data,
                "cursor": None,
            },
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        for attempt in range(4):
            row = self.store.rows.get(sub_id)
            if row is None or not self.allowed(row, record):
                return
            signing_secrets = self._signing_secrets(row)
            try:
                status, _ = await post_webhook(row["url"], sub_id, event_id, body, signing_secrets)
                if 200 <= status < 300:
                    return
                if status not in (408, 429) and status < 500:
                    if status == 410:
                        row["expires"] = time.time()
                        self.store.save()
                    return
            except (OSError, TimeoutError, httpx.HTTPError):
                pass
            except EventError:
                logger.warning("MCP Events callback destination rejected")
                return
            if attempt < 3:
                await asyncio.sleep(2**attempt)
        logger.warning("MCP Events delivery exhausted retries")
