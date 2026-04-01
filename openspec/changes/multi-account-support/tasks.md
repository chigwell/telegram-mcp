## 1. Configuration & Client Registry

- [x] 1.1 Replace global client initialization (lines 90-106) with config parsing logic: scan env vars for `TELEGRAM_SESSION_STRING_<LABEL>` / `TELEGRAM_SESSION_NAME_<LABEL>` suffixes, detect single vs multi mode
- [x] 1.2 Create `clients: dict[str, TelegramClient]` registry and `get_client(account)` lookup function with error listing available labels
- [x] 1.3 Implement `is_multi_mode()` helper that returns True when more than one account is configured
- [x] 1.4 Update `_main()` startup (lines 4752-4783) to initialize and cache-warm all clients concurrently via `asyncio.gather`

## 2. Decorator & Account Resolution

- [x] 2.1 Implement `@with_account(readonly=True|False)` decorator: adds `account` param, resolves client, injects as `client` kwarg
- [x] 2.2 Implement read-only fan-out logic in decorator: call tool for all accounts concurrently, prefix results with `[label]`, concatenate
- [x] 2.3 Implement write-tool guard in decorator: error with available accounts list when `account` is omitted in multi-mode
- [x] 2.4 Implement single-mode passthrough: ignore `account` param, always use sole client, no output tagging

## 3. Parameterize Shared Helpers

- [x] 3.1 Update `resolve_entity()` and `resolve_input_entity()` (lines 344-365) to accept a `client` parameter
- [x] 3.2 Audit and update any other helper functions that reference the global `client` directly

## 4. Migrate Tools — Read-only

- [x] 4.1 Add `@with_account(readonly=True)` and replace `client` with param in read-only chat tools: `get_chats`, `list_chats`, `get_chat`, `get_participants`, `get_admins`, `get_banned_users`, `get_invite_link`
- [x] 4.2 Add decorator to read-only message tools: `get_messages`, `list_messages`, `get_message_context`, `get_history`, `get_pinned_messages`, `search_messages`, `search_global`, `list_inline_buttons`, `get_message_reactions`, `get_last_interaction`, `list_topics`
- [x] 4.3 Add decorator to read-only contact tools: `list_contacts`, `search_contacts`, `get_contact_ids`, `get_blocked_users`, `get_direct_chat_by_contact`, `get_contact_chats`
- [x] 4.4 Add decorator to read-only profile/user tools: `get_me`, `get_user_photos`, `get_user_status`
- [x] 4.5 Add decorator to read-only media tools: `get_media_info`
- [x] 4.6 Add decorator to read-only discovery tools: `search_public_chats`, `resolve_username`
- [x] 4.7 Add decorator to read-only drafts/folders/stickers/bot tools: `get_drafts`, `list_folders`, `get_folder`, `get_sticker_sets`, `get_bot_info`
- [x] 4.8 Add decorator to any remaining read-only tools (`readOnlyHint=True` or equivalent)

## 5. Migrate Tools — Write / Destructive

- [x] 5.1 Add `@with_account(readonly=False)` to messaging write tools: `send_message`, `reply_to_message`, `edit_message`, `delete_message`, `forward_message`, `pin_message`, `unpin_message`, `mark_as_read`, `press_inline_button`, `create_poll`, `send_reaction`, `remove_reaction`
- [x] 5.2 Add decorator to chat management write tools: `create_group`, `create_channel`, `invite_to_group`, `leave_chat`, `edit_chat_title`, `delete_chat_photo`, `edit_chat_photo`, `mute_chat`, `unmute_chat`, `archive_chat`, `unarchive_chat`, `export_chat_invite`
- [x] 5.3 Add decorator to contact write tools: `add_contact`, `delete_contact`, `block_user`, `unblock_user`, `import_contacts`, `export_contacts`
- [x] 5.4 Add decorator to profile write tools: `update_profile`, `set_profile_photo`, `delete_profile_photo`
- [x] 5.5 Add decorator to admin/permission write tools: `promote_admin`, `demote_admin`, `ban_user`, `unban_user`, `set_privacy_settings`, `get_privacy_settings`
- [x] 5.6 Add decorator to media write tools: `send_file`, `download_media`, `upload_file`, `send_voice`, `send_sticker`, `send_gif`
- [x] 5.7 Add decorator to discovery write tools: `subscribe_public_channel`, `import_chat_invite`, `join_chat_by_link`
- [x] 5.8 Add decorator to drafts/folders write tools: `save_draft`, `clear_draft`, `create_folder`, `edit_folder`, `delete_folder`, `reorder_folders`
- [x] 5.9 Add decorator to bot write tools: `set_bot_commands`
- [x] 5.10 Add decorator to any remaining write tools

## 6. New Tool & Config Files

- [x] 6.1 Implement `list_accounts` tool returning label, display name, phone, and online status for each account
- [x] 6.2 Update `.env.example` with multi-account configuration examples
- [x] 6.3 Update `session_string_generator.py` to prompt for an optional label and output the labeled env var name

## 7. Validation & Cleanup

- [x] 7.1 Grep for any remaining bare `client.` references (outside the registry/decorator code) and fix them
- [x] 7.2 Test single-mode: verify existing `.env` with one account works identically to before
- [ ] 7.3 Test multi-mode: verify two accounts, read-only fan-out, write-tool account requirement, `list_accounts`
