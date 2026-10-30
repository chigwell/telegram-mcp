# Audit log storage

`telegram_mcp.audit_log.append_audit_record(record, session_path=...)` appends a
metadata dictionary as one UTF-8 JSON object per line. The server automatically
records completed attempts inside `delete_message`, `delete_messages_bulk`, and
`delete_chat_history`, including cross-chat refusals and Telegram failures.

Records contain a UTC timestamp, account label, tool name, chat identifier,
message IDs (or history `max_id`), requested revoke flag, outcome (`success`,
`refused`, or `failure`), and a structured reason. Cross-chat refusals include
`foreign_message_ids`; errors include only the exception class name. Message
text, session credentials, and raw exception text are excluded. A successful
record means the API call completed; `telegram_pts_count`, when available, is
Telegram's update counter and is not a verified count of deleted messages.
Single-message deletion uses Telethon's default `revoke=True`; channels ignore
revoke flags. History results also include Telegram's continuation `offset`.

Failures to write the audit record are reported through the existing error logger
and a warning appended to the tool response. The deletion result is preserved,
and deletion is never retried for an audit failure. Validation and account-routing
rejections that occur before entering the tool body are outside this log's scope.

## Location and permissions

`TELEGRAM_AUDIT_LOG` overrides the output path (relative paths use the process's
working directory). Otherwise, pass the resolved file-based session path to put
`audit_log.jsonl` beside the session. For string sessions or in-memory sessions, omit
that argument: the default is
`${XDG_STATE_HOME:-~/.local/state}/telegram-mcp/audit_log.jsonl`. Never pass a session
string as a filename. Multiple accounts may share a file; callers must identify
the account in each record.

On POSIX systems, the writer creates new parent directories with mode `0700`
and enforces mode `0600` on the log file. Existing directory permissions are
preserved. Windows users must restrict access using filesystem ACLs; POSIX modes
do not establish equivalent access restrictions there.

Each append uses an operating-system file lock to serialize cooperating writers,
including separate server processes. Use a local filesystem with working file
locks. The helper closes the file after every append and propagates serialization
and I/O errors to its caller. It does not print errors or retry Telegram actions.
Callers must report audit failures while preserving the actual operation result.
This is a local operational log, not tamper-proof storage or a guarantee against
partial records after a crash or disk failure.

## Retention and external rotation

There is no automatic rotation, compression, or expiration. Records accumulate
until the operator archives or removes them. Treat account and chat metadata as
private data when storing backups.

To rotate manually, pause writers, rename `audit_log.jsonl` to an archive such as
`audit_log.jsonl.1`, then resume. The next append creates a new private file. An
external utility such as `logrotate` can perform the same rename-and-create
operation; any replacement file should have the same owner and mode `0600`.
Avoid `copytruncate`, which can lose concurrent writes. If rotation happens while
writers are active, an in-flight append may land in the archive; retain that file
as part of the audit history. Pausing writers gives an exact rotation boundary.
