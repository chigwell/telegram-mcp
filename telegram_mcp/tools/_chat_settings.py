"""Private chats implementations; bindings are supplied by public adapters."""

from __future__ import annotations

from typing import Union


async def subscribe_public_channel(
    channel: Union[int, str],
    account: str = None,
    *,
    functions,
    get_client,
    log_and_format_error,
    resolve_entity,
    sanitize_name,
    telethon,
) -> str:
    try:
        cl = get_client(account)
        entity = await resolve_entity(channel, cl)
        await cl(functions.channels.JoinChannelRequest(channel=entity))
        title = sanitize_name(
            getattr(entity, "title", getattr(entity, "username", "Unknown channel"))
        )
        return f"Subscribed to {title}."
    except telethon.errors.rpcerrorlist.UserAlreadyParticipantError:
        title = sanitize_name(
            getattr(entity, "title", getattr(entity, "username", "this channel"))
        )
        return f"Already subscribed to {title}."
    except telethon.errors.rpcerrorlist.ChannelPrivateError:
        return "Cannot subscribe: this channel is private or requires an invite link."
    except Exception as e:
        return log_and_format_error("subscribe_public_channel", e, channel=channel)


async def mute_chat(
    chat_id: Union[int, str],
    account: str = None,
    *,
    functions,
    get_client,
    log_and_format_error,
    resolve_entity,
    resolve_input_entity,
) -> str:
    try:
        cl = get_client(account)
        from telethon.tl.types import InputPeerNotifySettings

        peer = await resolve_entity(chat_id, cl)
        await cl(
            functions.account.UpdateNotifySettingsRequest(
                peer=peer, settings=InputPeerNotifySettings(mute_until=2**31 - 1)
            )
        )
        return f"Chat {chat_id} muted."
    except (ImportError, AttributeError) as type_err:
        try:
            # Alternative approach directly using raw API
            peer = await resolve_input_entity(chat_id, cl)
            await cl(
                functions.account.UpdateNotifySettingsRequest(
                    peer=peer,
                    settings={
                        "mute_until": 2**31 - 1,  # Far future
                        "show_previews": False,
                        "silent": True,
                    },
                )
            )
            return f"Chat {chat_id} muted (using alternative method)."
        except Exception as alt_e:
            return log_and_format_error("mute_chat", alt_e, chat_id=chat_id)
    except Exception as e:
        return log_and_format_error("mute_chat", e, chat_id=chat_id)


async def unmute_chat(
    chat_id: Union[int, str],
    account: str = None,
    *,
    functions,
    get_client,
    log_and_format_error,
    resolve_entity,
    resolve_input_entity,
) -> str:
    try:
        cl = get_client(account)
        from telethon.tl.types import InputPeerNotifySettings

        peer = await resolve_entity(chat_id, cl)
        await cl(
            functions.account.UpdateNotifySettingsRequest(
                peer=peer, settings=InputPeerNotifySettings(mute_until=0)
            )
        )
        return f"Chat {chat_id} unmuted."
    except (ImportError, AttributeError) as type_err:
        try:
            # Alternative approach directly using raw API
            peer = await resolve_input_entity(chat_id, cl)
            await cl(
                functions.account.UpdateNotifySettingsRequest(
                    peer=peer,
                    settings={
                        "mute_until": 0,  # Unmute (current time)
                        "show_previews": True,
                        "silent": False,
                    },
                )
            )
            return f"Chat {chat_id} unmuted (using alternative method)."
        except Exception as alt_e:
            return log_and_format_error("unmute_chat", alt_e, chat_id=chat_id)
    except Exception as e:
        return log_and_format_error("unmute_chat", e, chat_id=chat_id)


async def archive_chat(
    chat_id: Union[int, str],
    account: str = None,
    *,
    functions,
    get_client,
    log_and_format_error,
    resolve_entity,
    types,
    utils,
) -> str:
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)
        peer = utils.get_input_peer(entity)
        await cl(
            functions.folders.EditPeerFoldersRequest(
                folder_peers=[types.InputFolderPeer(peer=peer, folder_id=1)]
            )
        )
        return f"Chat {chat_id} archived."
    except Exception as e:
        return log_and_format_error("archive_chat", e, chat_id=chat_id)


async def unarchive_chat(
    chat_id: Union[int, str],
    account: str = None,
    *,
    functions,
    get_client,
    log_and_format_error,
    resolve_entity,
    types,
    utils,
) -> str:
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)
        peer = utils.get_input_peer(entity)
        await cl(
            functions.folders.EditPeerFoldersRequest(
                folder_peers=[types.InputFolderPeer(peer=peer, folder_id=0)]
            )
        )
        return f"Chat {chat_id} unarchived."
    except Exception as e:
        return log_and_format_error("unarchive_chat", e, chat_id=chat_id)
