"""Miscellaneous tools for telegram-mcp (polls, drafts, etc.)."""

import json
import random
from datetime import datetime
from typing import Union, Optional

from telethon import functions
from telethon.tl.types import (
    InputMediaPoll,
    Poll,
    PollAnswer,
    TextWithEntities,
    InputReplyToMessage,
)

from ..app import mcp, client
from ..exceptions import log_and_format_error
from ..validators import validate_id
from ..formatters import json_serializer
from ..logging_config import logger
from mcp.types import ToolAnnotations


@mcp.tool(
    annotations=ToolAnnotations(title="Create Poll", openWorldHint=True, destructiveHint=True)
)
async def create_poll(
    chat_id: int,
    question: str,
    options: list,
    multiple_choice: bool = False,
    quiz_mode: bool = False,
    public_votes: bool = True,
    close_date: Optional[str] = None,
) -> str:
    """
    Create a poll in a chat using Telegram's native poll feature.

    Args:
        chat_id: The ID of the chat to send the poll to
        question: The poll question
        options: List of answer options (2-10 options)
        multiple_choice: Whether users can select multiple answers
        quiz_mode: Whether this is a quiz (has correct answer)
        public_votes: Whether votes are public
        close_date: Optional close date in ISO format (YYYY-MM-DD HH:MM:SS)
    """
    try:
        peer = await client.get_input_entity(chat_id)

        if len(options) < 2:
            return "Error: Poll must have at least 2 options."
        if len(options) > 10:
            return "Error: Poll can have at most 10 options."

        close_date_obj = None
        if close_date:
            try:
                close_date_obj = datetime.fromisoformat(close_date.replace("Z", "+00:00"))
            except ValueError:
                return f"Invalid close_date format. Use YYYY-MM-DD HH:MM:SS format."

        poll = Poll(
            id=random.randint(0, 2**63 - 1),
            question=TextWithEntities(text=question, entities=[]),
            answers=[
                PollAnswer(text=TextWithEntities(text=option, entities=[]), option=bytes([i]))
                for i, option in enumerate(options)
            ],
            multiple_choice=multiple_choice,
            quiz=quiz_mode,
            public_voters=public_votes,
            close_date=close_date_obj,
        )

        await client(
            functions.messages.SendMediaRequest(
                peer=peer,
                media=InputMediaPoll(poll=poll),
                message="",
                random_id=random.randint(0, 2**63 - 1),
            )
        )

        return f"Poll created successfully in chat {chat_id}."
    except Exception as e:
        logger.exception(f"create_poll failed (chat_id={chat_id}, question='{question}')")
        return log_and_format_error(
            "create_poll", e, chat_id=chat_id, question=question, options=options
        )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Save Draft", openWorldHint=True, destructiveHint=False, idempotentHint=True
    )
)
@validate_id("chat_id")
async def save_draft(
    chat_id: Union[int, str],
    message: str,
    reply_to_msg_id: Optional[int] = None,
    no_webpage: bool = False,
) -> str:
    """
    Save a draft message to a chat or channel. The draft will appear in the Telegram
    app's input field when you open that chat, allowing you to review and send it manually.

    Args:
        chat_id: The chat ID or username/channel to save the draft to
        message: The draft message text
        reply_to_msg_id: Optional message ID to reply to
        no_webpage: If True, disable link preview in the draft
    """
    try:
        peer = await client.get_input_entity(chat_id)

        reply_to = None
        if reply_to_msg_id:
            reply_to = InputReplyToMessage(reply_to_msg_id=reply_to_msg_id)

        await client(
            functions.messages.SaveDraftRequest(
                peer=peer,
                message=message,
                no_webpage=no_webpage,
                reply_to=reply_to,
            )
        )

        return f"Draft saved to chat {chat_id}. Open the chat in Telegram to see and send it."
    except Exception as e:
        logger.exception(f"save_draft failed (chat_id={chat_id})")
        return log_and_format_error("save_draft", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="Get Drafts", openWorldHint=True, readOnlyHint=True))
async def get_drafts() -> str:
    """
    Get all draft messages across all chats.
    Returns a list of drafts with their chat info and message content.
    """
    try:
        result = await client(functions.messages.GetAllDraftsRequest())

        drafts_info = []

        updates = getattr(result, "updates", None)
        if updates:
            for update in updates:
                if hasattr(update, "draft") and update.draft:
                    draft = update.draft
                    peer_id = None

                    if hasattr(update, "peer"):
                        peer = update.peer
                        if hasattr(peer, "user_id"):
                            peer_id = peer.user_id
                        elif hasattr(peer, "chat_id"):
                            peer_id = -peer.chat_id
                        elif hasattr(peer, "channel_id"):
                            peer_id = -1000000000000 - peer.channel_id

                    draft_data = {
                        "peer_id": peer_id,
                        "message": getattr(draft, "message", ""),
                        "date": (
                            draft.date.isoformat()
                            if hasattr(draft, "date") and draft.date
                            else None
                        ),
                        "no_webpage": getattr(draft, "no_webpage", False),
                        "reply_to_msg_id": (
                            draft.reply_to.reply_to_msg_id
                            if hasattr(draft, "reply_to") and draft.reply_to
                            else None
                        ),
                    }
                    drafts_info.append(draft_data)

        if not drafts_info:
            return "No drafts found."

        return json.dumps(
            {"drafts": drafts_info, "count": len(drafts_info)}, indent=2, default=json_serializer
        )
    except Exception as e:
        logger.exception("get_drafts failed")
        return log_and_format_error("get_drafts", e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Clear Draft", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@validate_id("chat_id")
async def clear_draft(chat_id: Union[int, str]) -> str:
    """
    Clear/delete a draft from a specific chat.

    Args:
        chat_id: The chat ID or username to clear the draft from
    """
    try:
        peer = await client.get_input_entity(chat_id)

        await client(
            functions.messages.SaveDraftRequest(
                peer=peer,
                message="",
            )
        )

        return f"Draft cleared from chat {chat_id}."
    except Exception as e:
        logger.exception(f"clear_draft failed (chat_id={chat_id})")
        return log_and_format_error("clear_draft", e, chat_id=chat_id)
