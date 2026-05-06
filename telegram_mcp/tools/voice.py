"""Voice transcription MCP tools (local Whisper)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from telegram_mcp import voice
from telegram_mcp.runtime import *  # noqa: F401,F403


@mcp.tool(
    annotations=ToolAnnotations(
        title="Transcribe Voice",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
@with_account(readonly=True)
@validate_id("chat_id")
async def transcribe_voice(
    chat_id: Union[int, str],
    message_id: int,
    language: Optional[str] = None,
    account: str = None,
) -> str:
    """
    Download a voice message and transcribe it locally via Whisper.

    The active backend is auto-detected at startup (Apple MLX → NVIDIA CUDA →
    Intel OpenVINO → CPU) and configurable via ``WHISPER_BACKEND`` /
    ``WHISPER_DEVICE`` / ``WHISPER_MODEL`` env vars. No audio is uploaded
    anywhere — transcription runs entirely on the host.

    Args:
        chat_id: The chat ID or username containing the voice message.
        message_id: The message ID with the voice (or audio) attachment.
        language: ISO 639-1 code (e.g. ``"ru"``, ``"en"``). ``None`` =
            auto-detect from audio (slightly slower).

    Returns:
        Transcribed text, or a JSON error blob if transcription is
        unavailable, the message has no voice attachment, or download fails.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        entity = await resolve_entity(chat_id, cl)
        msg = await cl.get_messages(entity, ids=message_id)
        if msg is None:
            return f"Message {message_id} in chat {chat_id} not found."
        if not (msg.voice or msg.audio):
            return (
                f"Message {message_id} in chat {chat_id} has no voice/audio attachment "
                "(only voice notes and audio messages can be transcribed)."
            )

        with tempfile.TemporaryDirectory(prefix="telegram-mcp-voice-") as td:
            target = Path(td) / "voice"  # Telethon will append correct extension.
            downloaded = await cl.download_media(msg, file=str(target))
            if not downloaded:
                return f"Failed to download voice from message {message_id}."
            try:
                text = voice.transcribe(Path(downloaded), language=language)
            except voice.VoiceTranscriptionUnavailable as exc:
                return json.dumps(
                    {
                        "error": "transcription_unavailable",
                        "message": str(exc),
                        "config": voice.get_backend_info(),
                    },
                    ensure_ascii=False,
                )
        return text
    except Exception as e:
        return log_and_format_error(
            "transcribe_voice",
            e,
            chat_id=chat_id,
            message_id=message_id,
            language=language,
        )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Voice Transcription Info",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
async def voice_transcription_info() -> str:
    """
    Report current voice transcription configuration and active backend.

    Useful to verify which Whisper backend/device the server picked up
    (Intel Arc, NVIDIA CUDA, Apple MPS, or CPU fallback).
    """
    return json.dumps(voice.get_backend_info(), ensure_ascii=False, indent=2)
