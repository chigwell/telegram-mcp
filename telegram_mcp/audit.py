"""Local audit logging for destructive message operations (Closes #321).

Records append-only JSONL entries covering message deletion attempts,
refusals, and outcomes without persisting any message body text.
"""

from __future__ import annotations

import datetime
import json
import os
import threading
from pathlib import Path
from typing import Any, List, Optional, Union

_LOG_LOCK = threading.Lock()
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def get_audit_log_path() -> Path:
    """Return the configured or default path to the deletion audit log."""
    env_path = os.getenv("TELEGRAM_AUDIT_LOG", "").strip()
    if env_path:
        return Path(env_path).resolve()
    return PROJECT_ROOT / "telegram_audit.log"


def record_audit_event(
    tool: str,
    chat_id: Union[int, str],
    outcome: str,
    account: Optional[str] = None,
    message_ids: Optional[List[int]] = None,
    revoke: Optional[bool] = None,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    """Append a metadata-only audit record in JSONL format."""
    try:
        log_path = get_audit_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)

        record: dict[str, Any] = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "account": account,
            "tool": tool,
            "chat_id": chat_id,
            "message_ids": message_ids,
            "revoke": revoke,
            "outcome": outcome,
        }
        if extra:
            record["extra"] = extra

        line = json.dumps(record, ensure_ascii=False) + "\n"
        with _LOG_LOCK:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        # Audit logging failure must never disrupt tool execution
        pass
