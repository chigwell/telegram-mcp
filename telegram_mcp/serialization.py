"""JSON fallback shared by the legacy result formatters."""


def json_default(obj, *, datetime_type):
    """Preserve datetime, bytes, and unsupported-object serialization."""
    if isinstance(obj, datetime_type):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
