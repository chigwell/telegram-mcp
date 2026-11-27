"""Authenticated HTTP webhook registrations backed by the shared bounded event source."""

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
import ssl
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import anyio

from telegram_mcp.event_source import (
    EVENT_NAME,
    EventError,
    event_allowed,
    event_definition,
    message_event,
    normalize_arguments,
    utc_timestamp,
    cursor,
    position,
    read_page,
    replay_options,
    replay_start,
    require_scope,
)

logger = logging.getLogger(__name__)
DEFAULT_TTL_MS = 3_600_000
MAX_TTL_MS = 86_400_000
MAX_SUBSCRIPTIONS = 100


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
                raise EventError("Callback address is not public.", -32027, "connection_refused")
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
                if not event_id.startswith("msg_verification_"):
                    return response.status_code, b""
                data = bytearray()
                async for chunk in response.aiter_raw():
                    data.extend(chunk)
                    if len(data) > 65536:
                        raise EventError(
                            "Callback response is too large.", -32027, "challenge_failed"
                        )
                return response.status_code, bytes(data)
            finally:
                await response.aclose()


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
    def __init__(self, path, principals=None):
        self.store = SubscriptionStore(path)
        self.principals = principals
        self.store_lock = None
        self.tasks = {}
        self.readers = {}
        self.verified = {}
        self.verification_hosts = {}
        self.lock = asyncio.Lock()
        self.running = False

    async def start(self):
        from telegram_mcp.singleton import SessionLock

        try:
            self.store_lock = SessionLock(
                "MCP Events", str(self.store.path.resolve()), lock_dir=self.store.path.parent
            )
            self.store_lock.acquire(grace_seconds=0)
            # Reload only after taking the lock; another process may have saved since construction.
            self.store = SubscriptionStore(self.store.path)
            self.running = True
            for sub_id, row in list(self.store.rows.items()):
                if (
                    row["expires"] <= time.time()
                    or not row.get("principal")
                    or self.principals is not None
                    and row["principal"] not in self.principals
                ):
                    del self.store.rows[sub_id]
                    continue
                from telegram_mcp.tools import events

                oldest = (
                    events._event_log[0]["seq"] - 1 if events._event_log else events._event_seq
                )
                self._start_worker(sub_id, oldest, gap=True)
            self.store.save()
        except Exception:
            await self.close()
            raise

    async def close(self):
        self.running = False
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
        self.readers.clear()
        if self.store_lock:
            self.store_lock.release()
            self.store_lock = None

    def _start_worker(
        self, sub_id, after, *, gap=False, max_age=None, replay_head=None, replay_time=None
    ):
        from telegram_mcp.tools import events

        self.readers[sub_id] = {
            "after": after,
            "pending": {},
            "gap": gap,
            "max_age": max_age,
            "replay_head": events._event_seq if replay_head is None else replay_head,
            "replay_time": time.time() if replay_time is None else replay_time,
        }
        self.tasks[sub_id] = asyncio.create_task(self._worker(sub_id))

    async def identity(self, params, subscribe, principal):
        if not principal:
            raise EventError("Webhook subscriptions require authenticated HTTP.", -32024)
        allowed_params = {"name", "arguments", "delivery", "cursor", "ttlMs", "maxAgeMs"}
        if not isinstance(params, dict) or set(params) - allowed_params:
            raise EventError("Invalid subscription parameters.")
        if not isinstance(params.get("name"), str):
            raise EventError("Select an event name.")
        if params["name"] != EVENT_NAME:
            raise EventError("Unknown event name.", -32023, data={"kind": "event"})
        arguments = params.get("arguments", {})
        await normalize_arguments(arguments, resolve=False)
        delivery = params.get("delivery")
        allowed = {"mode", "url", "secret"} if subscribe else {"mode", "url"}
        if not isinstance(delivery, dict) or set(delivery) - allowed:
            raise EventError("Expected webhook delivery.")
        mode = delivery.get("mode", "webhook" if not subscribe else None)
        if mode != "webhook":
            raise EventError(
                "Unsupported delivery mode.",
                -32026,
                data={"feature": "deliveryMode", "value": mode},
            )
        callback_url(delivery.get("url"))
        identity = json.dumps(
            [principal, delivery["url"], EVENT_NAME, arguments],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return "sub_" + hashlib.sha256(identity.encode()).hexdigest(), arguments, delivery

    def safe_cursor(self, sub_id):
        reader = self.readers.get(sub_id)
        if reader is None:
            from telegram_mcp.tools import events

            return cursor(events._event_seq)
        return cursor(min(reader["pending"]) - 1 if reader["pending"] else reader["after"])

    async def subscribe(self, params, principal):
        sub_id, arguments, delivery = await self.identity(params, True, principal)
        secret_key(delivery.get("secret"))
        after, gap = position(params.get("cursor"))
        max_age = replay_options(params)
        if params.get("cursor") is None:
            max_age = None
        from telegram_mcp.tools import events

        replay_head, replay_time = events._event_seq, time.time()
        ttl = params.get("ttlMs", DEFAULT_TTL_MS)
        if ttl is None:
            ttl = MAX_TTL_MS  # Grant a finite lifetime; the client can refresh it.
        if type(ttl) is not int:
            raise EventError("ttlMs must be an integer or null.")
        async with self.lock:
            self._prune_expired(sub_id)
            old = self.store.rows.get(sub_id)
            expired = old is not None and old["expires"] <= time.time()
            active = old if not expired else None
            bound = dict(active["arguments"]) if active else await normalize_arguments(arguments)
            require_scope(bound)
            after, clipped = replay_start(
                bound, after, max_age=max_age, replay_head=replay_head, replay_time=replay_time
            )
            gap = gap or clipped
            if (
                active is None
                and sum(r["expires"] > time.time() for r in self.store.rows.values())
                >= MAX_SUBSCRIPTIONS
            ):
                raise EventError(
                    "Subscription limit reached.",
                    -32025,
                    data={"limit": "subscriptions", "max": MAX_SUBSCRIPTIONS},
                )
            await self._verify_once(sub_id, principal, delivery)
            expired = old is not None and old["expires"] <= time.time()
            expires = time.time() + min(MAX_TTL_MS, max(1000, ttl)) / 1000
            row = {
                "principal": principal,
                "arguments": bound,
                "url": delivery["url"],
                "secret": delivery["secret"],
                "expires": expires,
            }
            if old and not expired and old["secret"] != row["secret"]:
                row.update(previous_secret=old["secret"], rotate_until=time.time() + 300)
            elif active and active.get("rotate_until", 0) > time.time():
                row.update(
                    previous_secret=old["previous_secret"], rotate_until=old["rotate_until"]
                )
            self._save_subscription(sub_id, row, old)
            task = self.tasks.get(sub_id)
            if self.running and (task is None or task.done()):
                self._start_worker(
                    sub_id,
                    after,
                    gap=gap,
                    max_age=max_age,
                    replay_head=replay_head,
                    replay_time=replay_time,
                )
            elif self.running and expired:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                self._start_worker(
                    sub_id,
                    after,
                    gap=gap,
                    max_age=max_age,
                    replay_head=replay_head,
                    replay_time=replay_time,
                )
            elif self.running:
                # A live refresh does not rewind or skip its pending deliveries.
                gap = False
        return {
            "id": sub_id,
            "refreshBefore": utc_timestamp(expires),
            "cursor": self.safe_cursor(sub_id) if self.running else cursor(after),
            "truncated": gap,
        }

    def _prune_expired(self, exclude):
        for key, row in list(self.store.rows.items()):
            if key != exclude and row["expires"] <= time.time() and key not in self.tasks:
                self.store.rows.pop(key)

    async def _verify_once(self, sub_id, principal, delivery):
        key = (principal, delivery["url"])
        now = time.time()
        if self.verified.get(key, 0) <= now:
            host = callback_url(delivery["url"]).hostname
            self.verification_hosts = {
                h: [t for t in stamps if t > now - 1]
                for h, stamps in self.verification_hosts.items()
                if stamps[-1] > now - 1
            }
            stamps = self.verification_hosts.setdefault(host, [])
            if len(stamps) >= 10:
                raise EventError(
                    "Callback verification rate limit reached.",
                    -32025,
                    data={"limit": "callbackVerifications", "max": 10},
                )
            stamps.append(now)
            await self._verify(sub_id, delivery)
            self.verified = {k: t for k, t in self.verified.items() if t > now}
            self.verified[key] = time.time() + 300

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
                "msg_verification_" + secrets.token_hex(16),
                body,
                [delivery["secret"]],
            )
            if not 200 <= status < 300:
                reason = (
                    "http_5xx"
                    if status >= 500
                    else "http_4xx" if status >= 400 else "challenge_failed"
                )
                raise EventError("Callback verification failed.", -32027, reason)
            answer = json.loads(data).get("challenge")
            if not isinstance(answer, str) or not hmac.compare_digest(answer, challenge):
                raise ValueError
        except EventError:
            raise
        except (TimeoutError, socket.timeout, httpx.TimeoutException):
            raise EventError("Callback verification timed out.", -32027, "timeout") from None
        except (ssl.SSLError, httpx.ConnectError) as exc:
            cause = exc
            while cause.__cause__ is not None:
                cause = cause.__cause__
            reason = "tls_error" if isinstance(cause, ssl.SSLError) else "connection_refused"
            raise EventError("Callback verification connection failed.", -32027, reason) from None
        except OSError:
            raise EventError(
                "Callback verification connection failed.", -32027, "connection_refused"
            ) from None
        except (ValueError, AttributeError, TypeError, httpx.HTTPError):
            raise EventError("Callback verification failed.", -32027, "challenge_failed") from None

    async def unsubscribe(self, params, principal):
        sub_id, _, _ = await self.identity(params, False, principal)
        async with self.lock:
            old = self.store.rows.pop(sub_id, None)
            if old is None:
                raise EventError("Subscription not found.", -32023, data={"kind": "subscription"})
            try:
                self.store.save()
            except Exception:
                self.store.rows[sub_id] = old
                raise
            task = self.tasks.pop(sub_id, None)
            self.readers.pop(sub_id, None)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return {}

    @staticmethod
    def allowed(row, record):
        return row["expires"] > time.time() and event_allowed(row["arguments"], record)

    async def _worker(self, sub_id):
        reader = self.readers[sub_id]
        try:
            await self._run_worker(sub_id, reader)
        except asyncio.CancelledError:
            raise
        except EventError as exc:
            row = self.store.rows.get(sub_id)
            if row:
                terminal = dict(row)
                row["expires"] = 0
                self.store.save()
                await self._control(
                    sub_id,
                    {"type": "terminated", "error": {"code": exc.code, "message": str(exc)}},
                    row=terminal,
                )
        except Exception:
            logger.exception("MCP Events delivery worker failed")
        finally:
            pending = list(reader["pending"].values())
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if self.readers.get(sub_id) is reader:
                row = self.store.rows.get(sub_id)
                if row and row["expires"] <= time.time():
                    self.store.rows.pop(sub_id, None)
                    self.store.save()
                self.readers.pop(sub_id, None)
                self.tasks.pop(sub_id, None)

    async def _run_worker(self, sub_id, reader):
        from telegram_mcp.tools import events

        while self.running and sub_id in self.store.rows:
            row = self.store.rows[sub_id]
            remaining = row["expires"] - time.time()
            if remaining <= 0:
                return
            require_scope(row["arguments"])
            signal = events._current_event_signal()
            records, after, gap, more = read_page(
                row["arguments"],
                reader["after"],
                4,
                max_age=reader["max_age"],
                replay_head=reader["replay_head"],
                replay_time=reader["replay_time"],
            )
            if gap or reader["gap"]:
                reader["gap"] = False
                # No earlier delivery is pending between batches.
                reader["after"] = records[0]["seq"] - 1 if records else after
                await self._control(sub_id, {"type": "gap", "cursor": self.safe_cursor(sub_id)})
            reader["after"] = after
            if records:
                if not await self._batch(sub_id, reader, records):
                    return
            elif not more:
                try:
                    await asyncio.wait_for(signal.wait(), min(30, remaining))
                except asyncio.TimeoutError:
                    pass

    async def _batch(self, sub_id, reader, records):
        # Register the whole batch before any delivery can advertise a watermark.
        for record in records:
            reader["pending"][record["seq"]] = None
        for record in records:
            reader["pending"][record["seq"]] = asyncio.create_task(self._deliver(sub_id, record))
        batch = asyncio.gather(*reader["pending"].values())
        try:
            while not batch.done():
                row = self.store.rows.get(sub_id)
                if row is None or row["expires"] <= time.time():
                    return False
                require_scope(row["arguments"])
                # A refresh may extend the grant while deliveries are pending.
                await asyncio.wait({batch}, timeout=min(1, row["expires"] - time.time()))
            await batch
            return True
        finally:
            if not batch.done():
                batch.cancel()
                await asyncio.gather(batch, return_exceptions=True)

    @staticmethod
    def _signing_secrets(row):
        signing_secrets = [row["secret"]]
        if row.get("rotate_until", 0) > time.time():
            signing_secrets.append(row["previous_secret"])
        return signing_secrets

    async def _control(self, sub_id, envelope, *, row=None):
        delivery_id = "msg_" + envelope["type"] + "_" + secrets.token_hex(16)
        body = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode()
        await self._send_delivery(sub_id, delivery_id, body, control_row=row)

    async def _deliver(self, sub_id, record):
        reader = self.readers.get(sub_id)
        event = message_event(record, self.safe_cursor(sub_id))
        delivery_id = "msg_" + secrets.token_hex(16)
        body = json.dumps(event, separators=(",", ":"), ensure_ascii=False).encode()
        try:
            await self._send_delivery(sub_id, delivery_id, body, record)
        finally:
            if reader is not None:
                reader["pending"].pop(record["seq"], None)

    async def _send_delivery(self, sub_id, delivery_id, body, record=None, *, control_row=None):
        for attempt in range(4):
            row = control_row if control_row is not None else self.store.rows.get(sub_id)
            if row is None or row["expires"] <= time.time():
                return
            if record is not None:
                require_scope(row["arguments"])
                if not self.allowed(row, record):
                    return
            try:
                status, _ = await post_webhook(
                    row["url"], sub_id, delivery_id, body, self._signing_secrets(row)
                )
                if 200 <= status < 300 or status < 500 and status not in (408, 425, 429):
                    return
            except (OSError, TimeoutError, httpx.HTTPError):
                pass
            except EventError:
                logger.warning("MCP Events callback destination rejected")
                return
            if attempt < 3:
                await asyncio.sleep(2**attempt)
        logger.warning("MCP Events delivery exhausted retries")
