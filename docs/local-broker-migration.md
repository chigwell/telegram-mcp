# Local shared broker migration

This migration replaces competing Telegram-owning stdio servers with one
loopback streamable HTTP broker. Codex and Claude become HTTP clients; only the
broker owns Telethon and the machine-wide `flock`.

The repository change does **not** edit user config, replace the installed
launcher, or start a daemon. Run these steps only after code review.

## 1. Preflight: identify and stop the current owner normally

The status probe is read-only. It neither creates the lock file nor kills a
process.

```bash
set +e
STATUS_JSON="$(
  cd "$HOME/telegram-mcp" &&
  TELEGRAM_MCP_LOCK="$HOME/.local/state/telegram-mcp.lock" \
    PYTHONDONTWRITEBYTECODE=1 \
    "$HOME/telegram-mcp/.venv/bin/python" \
    -m telegram_mcp.launcher --status --pretty
)"
STATUS_RC=$?
set -e
printf '%s\n' "$STATUS_JSON"
case "$STATUS_RC" in
  0|75) ;;
  *) echo "Status probe failed with exit $STATUS_RC" >&2; exit "$STATUS_RC" ;;
esac
```

- `0` means free; continue.
- `75` means busy; stop here. Use owner metadata in the JSON. A legacy launcher
  may have no metadata, so inspect it read-only with:

  ```bash
  /usr/sbin/lsof "$HOME/.local/state/telegram-mcp.lock"
  ```

Close or disconnect Telegram MCP from the owning Codex task or Claude session,
then wait for that client to finish its child process. Do **not** send `kill`,
delete the lock file, or start the broker beside it. Re-run preflight and
continue only after status returns `free`.

Keep both clients closed until their HTTP entries have been installed below;
their existing configs still point at stdio during the migration.

## 2. Back up both client configs and the launcher

Backups use stable names and are created once, so re-running this block cannot
overwrite the original pre-migration state.

```bash
set -euo pipefail

backup_once() {
  source_path="$1"
  backup_path="$2"
  if [[ ! -f "$source_path" ]]; then
    echo "Required source is missing: $source_path" >&2
    return 1
  fi
  if [[ -e "$backup_path" ]]; then
    echo "Keeping existing backup: $backup_path"
    return 0
  fi
  temporary="${backup_path}.tmp.$$"
  cp -p "$source_path" "$temporary"
  mv "$temporary" "$backup_path"
}

backup_once "$HOME/.codex/config.toml" \
  "$HOME/.codex/config.toml.pre-telegram-broker"
backup_once "$HOME/.claude.json" \
  "$HOME/.claude.json.pre-telegram-broker"
backup_once "$HOME/.local/bin/telegram-mcp" \
  "$HOME/.local/bin/telegram-mcp.pre-telegram-broker"
```

Do not run either `mcp remove` command until all three backups exist.

## 3. Install the guarded launcher atomically

```bash
set -euo pipefail
temporary="$HOME/.local/bin/telegram-mcp.new.$$"
install -m 0755 "$HOME/telegram-mcp/scripts/telegram-mcp" "$temporary"
mv "$temporary" "$HOME/.local/bin/telegram-mcp"

status_tmp_dir="$(mktemp -d)"
cd /tmp
TELEGRAM_MCP_LOCK="$status_tmp_dir/telegram.lock" \
  "$HOME/.local/bin/telegram-mcp" --status --pretty
test ! -e "$status_tmp_dir/telegram.lock"
test ! -e "$status_tmp_dir/telegram.lock.owner.json"
rmdir "$status_tmp_dir"
```

The `/tmp` smoke must return `free` and must not create its reported lock or
owner metadata path.

## 4. Create distinct client tokens in Keychain

```bash
set -euo pipefail

ensure_token() {
  account="$1"
  if security find-generic-password -s telegram-mcp -a "$account" -w \
      >/dev/null 2>&1; then
    echo "Keeping existing Keychain token: $account"
    return 0
  fi
  security add-generic-password -s telegram-mcp -a "$account" \
    -w "$(openssl rand -hex 32)"
}

ensure_token broker_codex
ensure_token broker_claude
```

Tokens stay in Keychain. They are not written to this repository or printed.
Their suffixes become `codex` and `claude` identities in the audit log.

## 5. Start the broker in the foreground

With Codex and Claude still closed:

```bash
TELEGRAM_MCP_CLIENT_APP=acceptance \
  "$HOME/.local/bin/telegram-mcp" --broker
```

Defaults: `127.0.0.1:18765`, MCP `/mcp`, public minimal `/health`, authenticated
`/status`. `--broker` explicitly sets `MCP_BROKER_MODE=1`; ordinary
`MCP_TRANSPORT=http` and Docker keep their legacy behavior.

If this command exits busy or reports missing credentials, fix the cause and
restart it manually. Nothing retries it in a loop.

## 6. Replace both MCP entries, then smoke-test

In another terminal, while the foreground broker is running:

```bash
set -euo pipefail
export TELEGRAM_MCP_BEARER_CODEX="$(security find-generic-password \
  -s telegram-mcp -a broker_codex -w)"
export TELEGRAM_MCP_BEARER_CLAUDE="$(security find-generic-password \
  -s telegram-mcp -a broker_claude -w)"

codex mcp remove telegram-mcp
codex mcp add telegram-mcp \
  --url http://127.0.0.1:18765/mcp \
  --bearer-token-env-var TELEGRAM_MCP_BEARER_CODEX

claude mcp remove telegram-mcp -s user
claude mcp add --transport http --scope user telegram-mcp \
  http://127.0.0.1:18765/mcp \
  --header 'Authorization: Bearer ${TELEGRAM_MCP_BEARER_CLAUDE}'

"$HOME/.local/bin/telegram-mcp" --status --pretty || [[ $? -eq 75 ]]
curl --fail http://127.0.0.1:18765/health
curl --fail \
  -H "Authorization: Bearer $TELEGRAM_MCP_BEARER_CODEX" \
  http://127.0.0.1:18765/status
codex mcp get telegram-mcp
claude mcp get telegram-mcp
```

Expected state: status is busy with owner `local-broker`/`acceptance`, health is
OK, authenticated status identifies `codex`, and both configs show HTTP.

For GUI-launched clients, arrange secure environment injection before opening
them. A shell `export` is not inherited by an already-running desktop process.
Keep the `${TELEGRAM_MCP_BEARER_CLAUDE}` placeholder; do not store a literal
token in config.

Stop the foreground broker with `Ctrl-C`, wait for `--status` to return `free`,
then proceed. Do not kill an unrelated PID or remove the lock file.

## 7. Make the accepted broker persistent without a retry loop

The supplied LaunchAgent has `RunAtLoad=true` and deliberately has no
`KeepAlive`. Busy lock, missing Keychain data, and other misconfiguration cause
one failed attempt; launchd does not restart it forever.

```bash
set -euo pipefail
mkdir -p "$HOME/.local/state/telegram-mcp" "$HOME/Library/LaunchAgents"
install -m 0644 \
  "$HOME/telegram-mcp/contrib/ru.ontico.telegram-mcp-broker.plist" \
  "$HOME/Library/LaunchAgents/ru.ontico.telegram-mcp-broker.plist"
launchctl bootout "gui/$(id -u)/ru.ontico.telegram-mcp-broker" \
  >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/ru.ontico.telegram-mcp-broker.plist"

sleep 2
"$HOME/.local/bin/telegram-mcp" --status --pretty || [[ $? -eq 75 ]]
curl --fail http://127.0.0.1:18765/health
```

If the one-shot launch fails, inspect
`~/.local/state/telegram-mcp/broker.stderr.log`, correct the cause, and retry
explicitly:

```bash
launchctl kickstart "gui/$(id -u)/ru.ontico.telegram-mcp-broker"
```

## Rollback

These commands stop the LaunchAgent and atomically restore all three original
files. The Keychain tokens are harmless and may remain for another attempt.

```bash
set -euo pipefail

launchctl bootout "gui/$(id -u)/ru.ontico.telegram-mcp-broker" \
  >/dev/null 2>&1 || true

restore_file() {
  backup_path="$1"
  destination="$2"
  if [[ ! -f "$backup_path" ]]; then
    echo "Rollback backup is missing: $backup_path" >&2
    return 1
  fi
  temporary="${destination}.rollback.$$"
  cp -p "$backup_path" "$temporary"
  mv "$temporary" "$destination"
}

restore_file "$HOME/.codex/config.toml.pre-telegram-broker" \
  "$HOME/.codex/config.toml"
restore_file "$HOME/.claude.json.pre-telegram-broker" \
  "$HOME/.claude.json"
restore_file "$HOME/.local/bin/telegram-mcp.pre-telegram-broker" \
  "$HOME/.local/bin/telegram-mcp"

echo "Rollback complete. Restart Codex and Claude to reload restored configs."
```

Do not start both the restored stdio owner and the broker. After rollback, the
machine-wide legacy lock still permits only one Telegram-owning process.
