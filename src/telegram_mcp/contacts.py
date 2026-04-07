import os
import time
import asyncio
from typing import List, Dict, Optional, Union, Any
from pathlib import Path
from telethon.tl.types import *
from telethon.tl.functions.messages import GetForumTopicsRequest, ReadDiscussionRequest
from telethon import functions, types, utils
from mcp.server.fastmcp import Context

# Use absolute imports for our own module as it's the standard for PyPI packages
from telegram_mcp.client import client, logger
from telegram_mcp.utils import *
from telegram_mcp.security import *


async def get_me() -> str:
    """
    Get your own user information.
    """
    try:
        me = await client.get_me()
        return json.dumps(format_entity(me), indent=2)
    except Exception as e:
        return log_and_format_error("get_me", e)


async def list_contacts() -> str:
    """
    List all contacts in your Telegram account.
    """
    try:
        result = await client(functions.contacts.GetContactsRequest(hash=0))
        users = result.users
        if not users:
            return "No contacts found."
        lines = []
        for user in users:
            name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
            username = getattr(user, "username", "")
            phone = getattr(user, "phone", "")
            contact_info = f"ID: {user.id}, Name: {name}"
            if username:
                contact_info += f", Username: @{username}"
            if phone:
                contact_info += f", Phone: {phone}"
            lines.append(contact_info)
        return "\n".join(lines)
    except Exception as e:
        return log_and_format_error("list_contacts", e)


async def search_contacts(query: str) -> str:
    """
    Search for contacts by name, username, or phone number using Telethon's SearchRequest.
    Args:
        query: The search term to look for in contact names, usernames, or phone numbers.
    """
    try:
        result = await client(functions.contacts.SearchRequest(q=query, limit=50))
        users = result.users
        if not users:
            return f"No contacts found matching '{query}'."
        lines = []
        for user in users:
            name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
            username = getattr(user, "username", "")
            phone = getattr(user, "phone", "")
            contact_info = f"ID: {user.id}, Name: {name}"
            if username:
                contact_info += f", Username: @{username}"
            if phone:
                contact_info += f", Phone: {phone}"
            lines.append(contact_info)
        return "\n".join(lines)
    except Exception as e:
        return log_and_format_error("search_contacts", e, query=query)


async def get_contact_ids() -> str:
    """
    Get all contact IDs in your Telegram account.
    """
    try:
        result = await client(functions.contacts.GetContactIDsRequest(hash=0))
        if not result:
            return "No contact IDs found."
        return "Contact IDs: " + ", ".join((str(cid) for cid in result))
    except Exception as e:
        return log_and_format_error("get_contact_ids", e)


async def add_contact(
    phone: Optional[str] = None,
    first_name: str = "",
    last_name: str = "",
    username: Optional[str] = None,
) -> str:
    """
    Add a new contact to your Telegram account.
    Args:
        phone: The phone number of the contact (with country code). Required if username is not provided.
        first_name: The contact's first name.
        last_name: The contact's last name (optional).
        username: The Telegram username (without @). Use this for adding contacts without phone numbers.

    Note: Either phone or username must be provided. If username is provided, the function will resolve it
    and add the contact using contacts.addContact API (which supports adding contacts without phone numbers).
    """
    try:
        phone = phone or ""
        username = username or ""
        if not phone and (not username):
            return "Error: Either phone or username must be provided."
        if username:
            username_clean = username.lstrip("@")
            if not username_clean:
                return "Error: Username cannot be empty."
            try:
                resolve_result = await client(
                    functions.contacts.ResolveUsernameRequest(username=username_clean)
                )
                if not resolve_result.users:
                    return f"Error: User with username @{username_clean} not found."
                user = resolve_result.users[0]
                if not isinstance(user, User):
                    return f"Error: Resolved entity is not a user."
                user_id = user.id
                access_hash = user.access_hash
                from telethon.tl.types import InputUser

                result = await client(
                    functions.contacts.AddContactRequest(
                        id=InputUser(user_id=user_id, access_hash=access_hash),
                        first_name=first_name,
                        last_name=last_name,
                        phone="",
                    )
                )
                if hasattr(result, "updates") and result.updates:
                    return (
                        f"Contact {first_name} {last_name} (@{username_clean}) added successfully."
                    )
                else:
                    return f"Contact {first_name} {last_name} (@{username_clean}) added successfully (no updates returned)."
            except Exception as resolve_e:
                logger.exception(
                    f"add_contact (username resolve) failed (username={username_clean})"
                )
                return log_and_format_error("add_contact", resolve_e, username=username_clean)
        elif phone:
            from telethon.tl.types import InputPhoneContact

            result = await client(
                functions.contacts.ImportContactsRequest(
                    contacts=[
                        InputPhoneContact(
                            client_id=0, phone=phone, first_name=first_name, last_name=last_name
                        )
                    ]
                )
            )
            if result.imported:
                return f"Contact {first_name} {last_name} added successfully."
            else:
                return f"Contact not added. Response: {str(result)}"
        else:
            return "Error: Phone number is required when username is not provided."
    except (ImportError, AttributeError) as type_err:
        if phone and (not username):
            try:
                result = await client(
                    functions.contacts.ImportContactsRequest(
                        contacts=[
                            {
                                "client_id": 0,
                                "phone": phone,
                                "first_name": first_name,
                                "last_name": last_name,
                            }
                        ]
                    )
                )
                if hasattr(result, "imported") and result.imported:
                    return f"Contact {first_name} {last_name} added successfully (alt method)."
                else:
                    return f"Contact not added. Alternative method response: {str(result)}"
            except Exception as alt_e:
                logger.exception(f"add_contact (alt method) failed (phone={phone})")
                return log_and_format_error("add_contact", alt_e, phone=phone)
        else:
            logger.exception(f"add_contact (type error) failed")
            return log_and_format_error("add_contact", type_err)
    except Exception as e:
        logger.exception(f"add_contact failed (phone={phone}, username={username})")
        return log_and_format_error("add_contact", e, phone=phone, username=username)


async def delete_contact(user_id: Union[int, str]) -> str:
    """
    Delete a contact by user ID.
    Args:
        user_id: The Telegram user ID or username of the contact to delete.
    """
    try:
        user = await resolve_entity(user_id)
        await client(functions.contacts.DeleteContactsRequest(id=[user]))
        return f"Contact with user ID {user_id} deleted."
    except Exception as e:
        return log_and_format_error("delete_contact", e, user_id=user_id)


async def block_user(user_id: Union[int, str]) -> str:
    """
    Block a user by user ID.
    Args:
        user_id: The Telegram user ID or username to block.
    """
    try:
        user = await resolve_entity(user_id)
        await client(functions.contacts.BlockRequest(id=user))
        return f"User {user_id} blocked."
    except Exception as e:
        return log_and_format_error("block_user", e, user_id=user_id)


async def unblock_user(user_id: Union[int, str]) -> str:
    """
    Unblock a user by user ID.
    Args:
        user_id: The Telegram user ID or username to unblock.
    """
    try:
        user = await resolve_entity(user_id)
        await client(functions.contacts.UnblockRequest(id=user))
        return f"User {user_id} unblocked."
    except Exception as e:
        return log_and_format_error("unblock_user", e, user_id=user_id)


async def update_profile(first_name: str = None, last_name: str = None, about: str = None) -> str:
    """
    Update your profile information (name, bio).
    """
    try:
        await client(
            functions.account.UpdateProfileRequest(
                first_name=first_name, last_name=last_name, about=about
            )
        )
        return "Profile updated."
    except Exception as e:
        return log_and_format_error(
            "update_profile", e, first_name=first_name, last_name=last_name, about=about
        )


async def delete_profile_photo() -> str:
    """
    Delete your current profile photo.
    """
    try:
        photos = await client(
            functions.photos.GetUserPhotosRequest(user_id="me", offset=0, max_id=0, limit=1)
        )
        if not photos.photos:
            return "No profile photo to delete."
        await client(functions.photos.DeletePhotosRequest(id=[photos.photos[0]]))
        return "Profile photo deleted."
    except Exception as e:
        return log_and_format_error("delete_profile_photo", e)


async def get_privacy_settings() -> str:
    """
    Get your privacy settings for last seen status.
    """
    try:
        from telethon.tl.types import InputPrivacyKeyStatusTimestamp

        try:
            settings = await client(
                functions.account.GetPrivacyRequest(key=InputPrivacyKeyStatusTimestamp())
            )
            return str(settings)
        except TypeError as e:
            if "TLObject was expected" in str(e):
                return "Error: Privacy settings API call failed due to type mismatch. This is likely a version compatibility issue with Telethon."
            else:
                raise
    except Exception as e:
        logger.exception("get_privacy_settings failed")
        return log_and_format_error("get_privacy_settings", e)


async def set_privacy_settings(
    key: str,
    allow_users: Optional[List[Union[int, str]]] = None,
    disallow_users: Optional[List[Union[int, str]]] = None,
) -> str:
    """
    Set privacy settings (e.g., last seen, phone, etc.).

    Args:
        key: The privacy setting to modify ('status' for last seen, 'phone', 'profile_photo', etc.)
        allow_users: List of user IDs or usernames to allow
        disallow_users: List of user IDs or usernames to disallow
    """
    try:
        from telethon.tl.types import (
            InputPrivacyKeyStatusTimestamp,
            InputPrivacyKeyPhoneNumber,
            InputPrivacyKeyProfilePhoto,
            InputPrivacyValueAllowUsers,
            InputPrivacyValueDisallowUsers,
            InputPrivacyValueAllowAll,
            InputPrivacyValueDisallowAll,
        )

        key_mapping = {
            "status": InputPrivacyKeyStatusTimestamp,
            "phone": InputPrivacyKeyPhoneNumber,
            "profile_photo": InputPrivacyKeyProfilePhoto,
        }
        if key not in key_mapping:
            return f"Error: Unsupported privacy key '{key}'. Supported keys: {', '.join(key_mapping.keys())}"
        privacy_key = key_mapping[key]()
        rules = []
        if allow_users is None or len(allow_users) == 0:
            rules.append(InputPrivacyValueAllowAll())
        else:
            try:
                allow_entities = []
                for user_id in allow_users:
                    try:
                        user = await resolve_entity(user_id)
                        allow_entities.append(user)
                    except Exception as user_err:
                        logger.warning(f"Could not get entity for user ID {user_id}: {user_err}")
                if allow_entities:
                    rules.append(InputPrivacyValueAllowUsers(users=allow_entities))
            except Exception as allow_err:
                logger.error(f"Error processing allowed users: {allow_err}")
                return log_and_format_error("set_privacy_settings", allow_err, key=key)
        if disallow_users and len(disallow_users) > 0:
            try:
                disallow_entities = []
                for user_id in disallow_users:
                    try:
                        user = await resolve_entity(user_id)
                        disallow_entities.append(user)
                    except Exception as user_err:
                        logger.warning(f"Could not get entity for user ID {user_id}: {user_err}")
                if disallow_entities:
                    rules.append(InputPrivacyValueDisallowUsers(users=disallow_entities))
            except Exception as disallow_err:
                logger.error(f"Error processing disallowed users: {disallow_err}")
                return log_and_format_error("set_privacy_settings", disallow_err, key=key)
        try:
            result = await client(
                functions.account.SetPrivacyRequest(key=privacy_key, rules=rules)
            )
            return f"Privacy settings for {key} updated successfully."
        except TypeError as type_err:
            if "TLObject was expected" in str(type_err):
                return "Error: Privacy settings API call failed due to type mismatch. This is likely a version compatibility issue with Telethon."
            else:
                raise
    except Exception as e:
        logger.exception(f"set_privacy_settings failed (key={key})")
        return log_and_format_error("set_privacy_settings", e, key=key)


async def import_contacts(contacts: list) -> str:
    """
    Import a list of contacts. Each contact should be a dict with phone, first_name, last_name.
    """
    try:
        input_contacts = [
            functions.contacts.InputPhoneContact(
                client_id=i,
                phone=c["phone"],
                first_name=c["first_name"],
                last_name=c.get("last_name", ""),
            )
            for i, c in enumerate(contacts)
        ]
        result = await client(functions.contacts.ImportContactsRequest(contacts=input_contacts))
        return f"Imported {len(result.imported)} contacts."
    except Exception as e:
        return log_and_format_error("import_contacts", e, contacts=contacts)


async def export_contacts() -> str:
    """
    Export all contacts as a JSON string.
    """
    try:
        result = await client(functions.contacts.GetContactsRequest(hash=0))
        users = result.users
        return json.dumps([format_entity(u) for u in users], indent=2)
    except Exception as e:
        return log_and_format_error("export_contacts", e)


async def get_blocked_users() -> str:
    """
    Get a list of blocked users.
    """
    try:
        result = await client(functions.contacts.GetBlockedRequest(offset=0, limit=100))
        return json.dumps([format_entity(u) for u in result.users], indent=2)
    except Exception as e:
        return log_and_format_error("get_blocked_users", e)
