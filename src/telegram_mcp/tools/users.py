"""User and profile management tools for telegram-mcp."""

import json
from typing import Union, List, Optional

from telethon import functions

from ..app import mcp, client
from ..exceptions import log_and_format_error
from ..validators import validate_id
from ..formatters import format_entity
from ..logging_config import logger
from mcp.types import ToolAnnotations


@mcp.tool(annotations=ToolAnnotations(title="Get Me", openWorldHint=True, readOnlyHint=True))
async def get_me() -> str:
    """
    Get your own user information.
    """
    try:
        me = await client.get_me()
        return json.dumps(format_entity(me), indent=2)
    except Exception as e:
        return log_and_format_error("get_me", e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Update Profile", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
async def update_profile(
    first_name: Optional[str] = None, last_name: Optional[str] = None, about: Optional[str] = None
) -> str:
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


@mcp.tool(
    annotations=ToolAnnotations(
        title="Set Profile Photo", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
async def set_profile_photo(file_path: str) -> str:
    """
    Set a new profile photo.
    """
    try:
        await client(
            functions.photos.UploadProfilePhotoRequest(file=await client.upload_file(file_path))
        )
        return "Profile photo updated."
    except Exception as e:
        return log_and_format_error("set_profile_photo", e, file_path=file_path)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Delete Profile Photo", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
async def delete_profile_photo() -> str:
    """
    Delete your current profile photo.
    """
    try:
        from telethon.tl.types import InputUserSelf, InputPhoto

        photos_result = await client(
            functions.photos.GetUserPhotosRequest(user_id=InputUserSelf(), offset=0, max_id=0, limit=1)
        )
        photo_list = getattr(photos_result, "photos", [])
        if not photo_list:
            return "No profile photo to delete."
        photo = photo_list[0]
        input_photo = InputPhoto(id=photo.id, access_hash=photo.access_hash, file_reference=photo.file_reference)
        await client(functions.photos.DeletePhotosRequest(id=[input_photo]))
        return "Profile photo deleted."
    except Exception as e:
        return log_and_format_error("delete_profile_photo", e)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Privacy Settings", openWorldHint=True, readOnlyHint=True)
)
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


@mcp.tool(
    annotations=ToolAnnotations(
        title="Set Privacy Settings", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@validate_id("allow_users", "disallow_users")
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
                        user = await client.get_entity(user_id)
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
                        user = await client.get_entity(user_id)
                        disallow_entities.append(user)
                    except Exception as user_err:
                        logger.warning(f"Could not get entity for user ID {user_id}: {user_err}")

                if disallow_entities:
                    rules.append(InputPrivacyValueDisallowUsers(users=disallow_entities))
            except Exception as disallow_err:
                logger.error(f"Error processing disallowed users: {disallow_err}")
                return log_and_format_error("set_privacy_settings", disallow_err, key=key)

        try:
            await client(
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


@mcp.tool(
    annotations=ToolAnnotations(title="Get User Photos", openWorldHint=True, readOnlyHint=True)
)
@validate_id("user_id")
async def get_user_photos(user_id: Union[int, str], limit: int = 10) -> str:
    """
    Get profile photos of a user.
    """
    try:
        from telethon.utils import get_input_user

        user = await client.get_entity(user_id)
        input_user = get_input_user(user)
        photos = await client(
            functions.photos.GetUserPhotosRequest(user_id=input_user, offset=0, max_id=0, limit=limit)
        )
        photo_list = getattr(photos, "photos", [])
        return json.dumps([p.id for p in photo_list], indent=2)
    except Exception as e:
        return log_and_format_error("get_user_photos", e, user_id=user_id, limit=limit)


@mcp.tool(
    annotations=ToolAnnotations(title="Get User Status", openWorldHint=True, readOnlyHint=True)
)
@validate_id("user_id")
async def get_user_status(user_id: Union[int, str]) -> str:
    """
    Get the online status of a user.
    """
    try:
        user = await client.get_entity(user_id)
        status = getattr(user, "status", None)
        if status is None:
            return "Status not available for this entity type."
        return str(status)
    except Exception as e:
        return log_and_format_error("get_user_status", e, user_id=user_id)
