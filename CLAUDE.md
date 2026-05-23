# telegram-mcp — project notes

A fork of [chigwell/telegram-mcp](https://github.com/chigwell/telegram-mcp)
that adds a **streamable-HTTP transport with OAuth 2.1 + PKCE** so the
server can run on a VM and be attached to Claude Desktop as a *custom
connector* — usable both from a local Mac and from Anthropic's
remote-routine workers.

The upstream stdio mode still works exactly as documented in `README.md`.
This file describes the additions and the end-to-end remote deployment.

---

## What the fork adds

| File | What it does |
|---|---|
| `telegram_mcp/auth/single_user_provider.py` | Implements MCP SDK's `OAuthAuthorizationServerProvider` + `TokenVerifier` for one user. Renders a small `/login` HTML form; password compared with `hmac.compare_digest` against `TELEGRAM_MCP_AUTH_PASSWORD`. |
| `telegram_mcp/auth/storage.py` | SQLite-backed store (clients, auth codes, access/refresh tokens). Each row holds the Pydantic model's JSON; schema is trivial, no migrations required. All access wrapped in `asyncio.to_thread`. |
| `telegram_mcp/runner_http.py` | HTTP entrypoint. Connects Telegram clients + warms entity caches (same as stdio), then serves the FastMCP streamable-HTTP ASGI app via uvicorn with `proxy_headers=True` so `X-Forwarded-*` from the tunnel is trusted. |
| `telegram_mcp/runtime.py` | `mcp = _build_mcp()` branches on `TELEGRAM_MCP_TRANSPORT`. In HTTP mode it wires the provider + `AuthSettings` into `FastMCP(...)` — the SDK auto-mounts `/authorize`, `/token`, `/register`, `/revoke`, `/.well-known/oauth-authorization-server`, `/.well-known/oauth-protected-resource/mcp`. |
| `telegram_mcp/runner.py` | `main()` branches: `TELEGRAM_MCP_TRANSPORT=http` → `runner_http.main()`, else existing stdio path. |
| `Dockerfile` | `EXPOSE 8000`, pre-creates `/data` chowned to `appuser` so the OAuth-DB named volume inherits the right perms. |
| `docker-compose.yml` | Two profiles: `stdio` (unchanged) and `http` (the new one with port binding + `oauth-data` named volume + `TELEGRAM_MCP_OAUTH_DB=/data/oauth.db`). |

---

## Environment variables added by the fork

All HTTP-only. Stdio mode ignores them.

| Var | Required | Default | Purpose |
|---|---|---|---|
| `TELEGRAM_MCP_TRANSPORT` | — | `stdio` | Set to `http` to enable the new transport. |
| `TELEGRAM_MCP_PUBLIC_URL` | yes (HTTP) | — | Externally reachable HTTPS base URL (no trailing `/`). Published as OAuth `issuer` and resource identifier and used to build the redirect from `/login` back to the client. **Must exactly match what Claude Desktop connects to.** |
| `TELEGRAM_MCP_AUTH_PASSWORD` | yes (HTTP) | — | The single user's login-form password. |
| `TELEGRAM_MCP_AUTH_USERNAME` | — | `admin` | Username for the login form. |
| `TELEGRAM_MCP_HOST` | — | `0.0.0.0` | uvicorn bind address. |
| `TELEGRAM_MCP_PORT` | — | `8000` | uvicorn bind port. |
| `TELEGRAM_MCP_OAUTH_DB` | — | `:memory:` | SQLite path. Set to a file (`/data/oauth.db` in Docker) for persistence across restarts. |

---

## End-to-end deployment — clone to working connector

The reference deployment runs on the `exit` VM (Ubuntu 24.04 ARM64,
SSH alias `exit`, all commands as root). It uses **Tailscale Funnel**
for HTTPS ingress so no domain or DNS plumbing is needed.

### 0. Prerequisites

- A Tailscale account (free) signed in via Google / GitHub / Microsoft.
- Pre-generated Telegram session strings (one per account you want to
  expose). Generate locally on your workstation with:
  ```bash
  uv run session_string_generator.py
  ```
  The HTTP runner cannot do interactive phone-code login, just like
  the stdio runner — sessions must already be valid.
- The VM has free **port 443 on the tailscale interface** (`100.x`).
  Tailscale Funnel always forwards external traffic to the node on
  port 443 regardless of the public port you choose, so anything
  bound to `0.0.0.0:443` on the host conflicts. If a Docker container
  holds 443, either rebind it to a specific public IP only, or stop
  it.

### 1. Clone on the VM

```bash
ssh exit
cd /root
git clone https://github.com/almax07082005/telegram-mcp.git
cd telegram-mcp
```

### 2. Create `.env`

In `/root/telegram-mcp/.env`:

```bash
TELEGRAM_API_ID=<from my.telegram.org/apps>
TELEGRAM_API_HASH=<from my.telegram.org/apps>

# One block per account; labels are lowercased and become the `account`
# parameter value in MCP tool calls.
TELEGRAM_SESSION_STRING_WORK=<session string>
TELEGRAM_SESSION_STRING_PERSONAL=<session string>

# HTTP transport / OAuth
TELEGRAM_MCP_TRANSPORT=http
TELEGRAM_MCP_PUBLIC_URL=https://<hostname>.<tailnet-id>.ts.net
TELEGRAM_MCP_AUTH_USERNAME=admin
TELEGRAM_MCP_AUTH_PASSWORD=<paste output of `python -c 'import secrets;print(secrets.token_urlsafe(32))'`>
```

`TELEGRAM_MCP_PUBLIC_URL` is set with a placeholder; we fill in the
real hostname after Tailscale assigns one (step 3).

### 3. Install Tailscale and create the funnel hostname

```bash
# Install (Ubuntu / Debian)
curl -fsSL https://tailscale.com/install.sh | sh

# Sign in. Pick a stable machine name -- this is the hostname Claude
# Desktop will use forever.
systemd-run --unit=tg-mcp-tsup --collect --remain-after-exit bash -c \
  '/usr/bin/tailscale up --hostname=tg-mcp >/tmp/tsup.log 2>&1'
sleep 3 && cat /tmp/tsup.log
# Open the printed login URL, authorize with your Tailscale account.
# Poll `tailscale status` until it shows the node.
```

Find your tailnet's DNS suffix and the final hostname:

```bash
tailscale status --json | python3 -c '
import json,sys
s=json.load(sys.stdin)
print("URL:", "https://"+s["Self"]["DNSName"].rstrip("."))'
```

Paste that URL into `TELEGRAM_MCP_PUBLIC_URL` in `.env`.

### 4. Enable HTTPS certs + Funnel in the Tailscale dashboard

Two one-time clicks per Tailscale account:

1. **Enable HTTPS Certificates** — https://login.tailscale.com/admin/dns
   → "HTTPS Certificates" card → **Enable**.
2. **Enable Funnel for this node** — `tailscale funnel --bg --yes 8000`
   will print a `https://login.tailscale.com/f/funnel?node=<id>` link the
   first time. Open it and authorize. Once authorized, re-run the
   `funnel` command.

```bash
tailscale funnel --bg --yes 8000
tailscale funnel status
# Expected:
#   # Funnel on:
#   #     - https://tg-mcp.<tailnet>.ts.net
#   https://tg-mcp.<tailnet>.ts.net (Funnel on)
#   |-- / proxy http://127.0.0.1:8000
```

### 5. Start the container

```bash
cd /root/telegram-mcp
docker compose --profile http up -d --build
docker logs telegram-mcp-http -f
# Wait for "Serving MCP over HTTP on 0.0.0.0:8000  (public URL: ...)"
```

### 6. Verify the public path

Use an external probe (so you exercise the real Funnel route, not a
loopback shortcut):

```bash
curl -fsS https://<hostname>/.well-known/oauth-authorization-server | python3 -m json.tool
# Should return the OAuth AS metadata with issuer matching your public URL.

curl -i https://<hostname>/mcp
# Should return 401 with a WWW-Authenticate header.
```

If TLS handshake aborts mid-handshake (`SSL_ERROR_SYSCALL` /
`UNEXPECTED_EOF_WHILE_READING`) the most common cause is the client's
egress IP being dropped by Tailscale's edge — see **Mac caveat** below.

### 7. Add the connector in Claude Desktop

**Settings → Connectors → Add custom connector**:

- **Name**: any
- **Remote MCP server URL**: `https://<hostname>/mcp`
- Leave OAuth Client ID / Secret empty (the server runs RFC 7591
  Dynamic Client Registration; Claude Desktop self-registers).

Click **Add**. A browser tab opens, you sign in with
`TELEGRAM_MCP_AUTH_USERNAME` / `TELEGRAM_MCP_AUTH_PASSWORD`, the tab
closes, the connector goes green.

---

## Mac caveat — local VPNs break Tailscale Funnel

A common failure: the connector says "can't open page" only on the user's
Mac, even though external probes (`check-host.net`, the VM itself) reach
the same URL fine.

**Cause**: Tailscale Funnel's ingress IPs (`185.40.234.0/24`) are often
rate-limited or blocked when the source IP is a known VPN exit. If the
Mac is routing through a personal VPN (Streisand, WireGuard,
OpenConnect, Mullvad, etc. — visible as default route via
`utun*` in `route -n get default`), the Tailscale edge drops the TLS
handshake.

**Fix**: install Tailscale on the Mac too. Once it joins the tailnet,
MagicDNS resolves the funnel hostname directly to the `100.x` IP and the
connection routes peer-to-peer through Tailscale's mesh, completely
bypassing the local VPN tunnel.

```bash
# On the Mac
brew install --cask tailscale-app
open -a Tailscale  # menu-bar login, same Tailscale account as the VM
```

**Do not turn Funnel off** even after the Mac switches to tailnet
routing — remote routines run on Anthropic infra, which can't be added
to your tailnet, so they need the public Funnel path. Both work
simultaneously: Funnel for outside callers, tailnet for your devices.

---

## What survives a VM reboot

| Component | Survives | Why |
|---|---|---|
| Tailscale daemon + Funnel state | yes | `tailscaled` is a systemd service, enabled by the installer. Funnel config lives in tailscaled state. |
| Container | yes | `restart: unless-stopped` in the http compose profile. |
| OAuth tokens + DCR clients | yes | SQLite at `/data/oauth.db` in the `telegram-mcp_oauth-data` named volume. |
| Login session cookie | no, by design | 10-min in-memory; only matters while the user is mid-flow on the login form. |
| Claude Desktop connector entry | yes (client-side) | Refresh tokens last 30 days; Claude Desktop renews silently. |

`docker compose --profile http down` keeps the volume; only
`docker compose --profile http down -v` wipes the OAuth DB.

---

## Common operational tasks

### Rotate the login password

```bash
ssh exit
sed -i 's|^TELEGRAM_MCP_AUTH_PASSWORD=.*|TELEGRAM_MCP_AUTH_PASSWORD=<new>|' /root/telegram-mcp/.env
cd /root/telegram-mcp && docker compose --profile http restart
# Existing access tokens remain valid (the password only guards new logins).
# To invalidate everything immediately: also `docker compose --profile http down -v && up -d`.
```

### Add another Telegram account

```bash
# Generate the session on your workstation:
uv run session_string_generator.py
# Append to /root/telegram-mcp/.env:
#   TELEGRAM_SESSION_STRING_<LABEL>=<session>
# Restart:
ssh exit "cd /root/telegram-mcp && docker compose --profile http restart"
```

The `account` parameter in MCP tool calls is the lowercased `<LABEL>`.

### Change the public hostname

If you rename the Tailscale machine (`tailscale set --hostname=<new>`)
or migrate off Tailscale Funnel:

1. Update `TELEGRAM_MCP_PUBLIC_URL` in `.env`.
2. `docker compose --profile http restart`.
3. Remove + re-add the connector in Claude Desktop (the OAuth `issuer`
   changed, so existing tokens are no longer valid for the new URL).

### Tail logs

```bash
ssh exit
docker logs -f telegram-mcp-http       # MCP server + Telethon
journalctl -u tailscaled -f             # funnel ingress / cert errors
```

### Inspect the OAuth DB

```bash
ssh exit "docker exec telegram-mcp-http python -c '
from telegram_mcp.auth.storage import OAuthStore
s = OAuthStore(\"/data/oauth.db\")
with s._cursor() as c:
    for tbl in (\"clients\",\"auth_codes\",\"access_tokens\",\"refresh_tokens\"):
        n = c.execute(f\"SELECT COUNT(*) FROM {tbl}\").fetchone()[0]
        print(tbl, n)
'"
```

---

## Architecture quick reference

```
                                  Anthropic remote-routine worker
                                                │
                                                │ https (public)
                                                ▼
External clients ──https──▶  Tailscale Funnel edge (185.40.234.x)
                                                │ tailnet, TLS passthrough
                                                ▼
Mac with Tailscale ─tailnet─▶  100.112.244.125:443 (tailscaled on VM)
                                                │ http://127.0.0.1:8000
                                                ▼
                                       telegram-mcp-http container
                                                ├── streamable-http /mcp
                                                ├── /login (form)
                                                └── /authorize, /token,
                                                    /register, /revoke,
                                                    /.well-known/*
                                                          │
                                                ┌─────────┴───────┐
                                                ▼                 ▼
                                          SingleUserOAuth   Telethon clients
                                          Provider + Store  (work, personal)
                                                │
                                                ▼
                                          /data/oauth.db
                                          (named volume)
```

Persistent state is *only* the SQLite DB in the named volume and the
`.env` file. Everything else is reproducible from the repo.
