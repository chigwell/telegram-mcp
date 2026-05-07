"""MCPL wire-format types used by this server.

Mirrors the TypeScript definitions in
agent-framework/src/mcpl/types.ts (same monorepo). Only the subset this
server emits or accepts is modeled here. Use plain TypedDicts so the JSON
serialization is trivial and the host's type expectations stay authoritative.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict


# ---------------------------------------------------------------------------
# JSON-RPC envelope
# ---------------------------------------------------------------------------


class JsonRpcRequest(TypedDict, total=False):
    jsonrpc: Literal["2.0"]
    method: str
    id: int | str
    params: dict[str, Any]


class JsonRpcResponse(TypedDict, total=False):
    jsonrpc: Literal["2.0"]
    id: int | str
    result: Any
    error: "JsonRpcError"


class JsonRpcError(TypedDict, total=False):
    code: int
    message: str
    data: Any


# ---------------------------------------------------------------------------
# Content blocks (Section 4)
# ---------------------------------------------------------------------------


class McplTextContent(TypedDict):
    type: Literal["text"]
    text: str


class McplImageContent(TypedDict, total=False):
    type: Literal["image"]
    data: str
    mimeType: str
    uri: str


class McplAudioContent(TypedDict, total=False):
    type: Literal["audio"]
    data: str
    mimeType: str
    uri: str


class McplResourceContent(TypedDict):
    type: Literal["resource"]
    uri: str


McplContentBlock = McplTextContent | McplImageContent | McplAudioContent | McplResourceContent


# ---------------------------------------------------------------------------
# Channels (Section 14)
# ---------------------------------------------------------------------------


class ChannelDescriptor(TypedDict, total=False):
    id: str
    type: str
    label: str
    direction: Literal["outbound", "inbound", "bidirectional"]
    address: dict[str, Any]
    metadata: dict[str, Any]


class ChannelAuthor(TypedDict):
    id: str
    name: str


class ChannelIncomingMessage(TypedDict, total=False):
    channelId: str
    messageId: str
    threadId: str
    author: ChannelAuthor
    timestamp: str
    content: list[McplContentBlock]
    metadata: dict[str, Any]


class ChannelsRegisterParams(TypedDict):
    channels: list[ChannelDescriptor]


class ChannelsChangedParams(TypedDict, total=False):
    added: list[ChannelDescriptor]
    removed: list[str]
    updated: list[ChannelDescriptor]


class ChannelsIncomingParams(TypedDict):
    messages: list[ChannelIncomingMessage]


class ChannelsPublishParams(TypedDict, total=False):
    conversationId: str
    channelId: str
    stream: bool
    content: list[McplContentBlock]


class ChannelsPublishResult(TypedDict, total=False):
    delivered: bool
    messageId: str


# ---------------------------------------------------------------------------
# Push events (Section 9)
# ---------------------------------------------------------------------------


class PushEventPayload(TypedDict):
    content: list[McplContentBlock]


class PushEventParams(TypedDict, total=False):
    featureSet: str
    eventId: str
    timestamp: str
    origin: dict[str, Any]
    payload: PushEventPayload
