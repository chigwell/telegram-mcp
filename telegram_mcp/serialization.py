"""JSON conversion shared by the legacy and structured tool formatters."""

from datetime import datetime


def json_serializer(obj):
    """Convert supported non-JSON values without changing the output encoding."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
