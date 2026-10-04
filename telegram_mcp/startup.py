"""Small, side-effect-free process stream helpers."""


def configure_stderr(stream):
    """Keep the existing best-effort UTF-8 stderr configuration."""
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass
