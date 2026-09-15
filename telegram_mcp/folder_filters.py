"""Rebuild Telegram folder filters while preserving their existing settings."""

from telethon.tl.types import DialogFilter, DialogFilterChatlist


def replace_folder_peers(folder, *, include_peers, pinned_peers):
    common = dict(
        id=folder.id,
        title=folder.title,
        emoticon=getattr(folder, "emoticon", None),
        pinned_peers=pinned_peers,
        include_peers=include_peers,
        title_noanimate=getattr(folder, "title_noanimate", None),
        color=getattr(folder, "color", None),
    )
    if isinstance(folder, DialogFilterChatlist):
        return DialogFilterChatlist(**common)
    return DialogFilter(
        **common,
        exclude_peers=list(getattr(folder, "exclude_peers", [])),
        contacts=getattr(folder, "contacts", False),
        non_contacts=getattr(folder, "non_contacts", False),
        groups=getattr(folder, "groups", False),
        broadcasts=getattr(folder, "broadcasts", False),
        bots=getattr(folder, "bots", False),
        exclude_muted=getattr(folder, "exclude_muted", False),
        exclude_read=getattr(folder, "exclude_read", False),
        exclude_archived=getattr(folder, "exclude_archived", False),
    )
