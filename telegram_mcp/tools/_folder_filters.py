"""Rebuild folder filters while preserving the existing constructor fields."""


def rebuild_filter(
    target_folder, include_peers, pinned_peers, *, DialogFilter, DialogFilterChatlist
):
    if isinstance(target_folder, DialogFilterChatlist):
        return DialogFilterChatlist(
            id=target_folder.id,
            title=target_folder.title,
            emoticon=getattr(target_folder, "emoticon", None),
            pinned_peers=pinned_peers,
            include_peers=include_peers,
            title_noanimate=getattr(target_folder, "title_noanimate", None),
            color=getattr(target_folder, "color", None),
        )
    else:
        return DialogFilter(
            id=target_folder.id,
            title=target_folder.title,
            emoticon=getattr(target_folder, "emoticon", None),
            pinned_peers=pinned_peers,
            include_peers=include_peers,
            exclude_peers=list(getattr(target_folder, "exclude_peers", [])),
            contacts=getattr(target_folder, "contacts", False),
            non_contacts=getattr(target_folder, "non_contacts", False),
            groups=getattr(target_folder, "groups", False),
            broadcasts=getattr(target_folder, "broadcasts", False),
            bots=getattr(target_folder, "bots", False),
            exclude_muted=getattr(target_folder, "exclude_muted", False),
            exclude_read=getattr(target_folder, "exclude_read", False),
            exclude_archived=getattr(target_folder, "exclude_archived", False),
            title_noanimate=getattr(target_folder, "title_noanimate", None),
            color=getattr(target_folder, "color", None),
        )
