"""Bulk chat export to local files.

This is a CLI, not an MCP tool, and deliberately so: an export of a dozen chats
must land on the operator's disk, not in an MCP client's context window. It
shares this package's Telegram plumbing - client construction, device identity,
proxy handling, voice transcription - and adds only what export needs.

JSONL is the source of truth; HTML, Markdown and plain text are rendered from
it, so re-rendering never touches the Telegram API.
"""

__all__ = ["cli"]
