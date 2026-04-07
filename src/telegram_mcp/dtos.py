from typing import List, Optional, Union
from pydantic import BaseModel, Field

class ImportContactsPayload(BaseModel):
    contact_list: list = Field(
        description="A list of contacts. Each contact should be a dict with phone, first_name, last_name."
    )

class CreateFolderPayload(BaseModel):
    title: str = Field(description="Folder name (required)")
    emoticon: Optional[str] = Field(default=None, description="Folder emoji (optional, e.g., '📁', '🏠', '💼')")
    chat_ids: Optional[List[Union[int, str]]] = Field(
        default=None, description="List of chat IDs or usernames to include (optional)"
    )
    include_contacts: bool = Field(default=False, description="Include all contacts")
    include_non_contacts: bool = Field(default=False, description="Include all non-contacts")
    include_groups: bool = Field(default=False, description="Include all groups")
    include_broadcasts: bool = Field(default=False, description="Include all channels")
    include_bots: bool = Field(default=False, description="Include all bots")
    exclude_muted: bool = Field(default=False, description="Exclude muted chats")
    exclude_read: bool = Field(default=False, description="Exclude read chats")
    exclude_archived: bool = Field(default=True, description="Exclude archived chats (default True)")
