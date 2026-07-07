# SPDD: `smtp2sms` — SMTP-to-SMS/Email Smart Router

**Version:** 1.1 (routing + email relay added)
**Author:** Design owner (via Claude chat)
**Executor:** Claude Code
**Date:** July 2026
**Status:** Approved for build

---

## 1. TL;DR

Build a small containerised Python service that:

1. **Listens for SMTP** on the LAN — no auth, no TLS — from a whitelisted set of source IPs.
2. **Receives emails** from Vertiv, APC, and Avocent alerting devices.
3. **Routes each message** based on the envelope recipient (`RCPT TO`):
   - If the local-part is a phone number → **SMS** via the Teltonika RUT956's HTTP API.
   - Otherwise → **email** relayed via Exchange 365 (`smtp.office365.com:587` with STARTTLS + SMTP AUTH).
4. **Fire-and-forget**: always ACKs the SMTP transaction with `250 OK`, logs the outcome, does not retry.

Deploy as a Docker container on the existing Portainer host on the LAN.

---

## 2. Background & Why This Exists

The home rack has multiple alerting devices — UPSes, PDUs, environmental monitors, KVM — that only speak SMTP. Historically alerts were relayed by SMTP2GO (~$180/yr, with paid SMS). The replacement architecture is:

- **Teltonika RUT956** industrial cellular router provides an on-device SMS API and cellular WAN backup.
- **Exchange 365** already exists in-domain and can act as an outbound SMTP relay for the email side.
- **This service** is the LAN-local piece that receives SMTP from the alerting devices and decides which output to use.

**Design goal that matters most:** *"House is on fire, no question I get the message."* No external services in the SMS critical path, no cloud dependencies for the fire-alert case, everything on-premise and UPS-backed. Email side can rely on Exchange 365 since email is a nice-to-have, not the last-mile alert path.

Per-alarm routing matters because different events need different recipients. Fire alarm → wife's phone. Battery self-test → owner's phone. Info-level events → owner's mailbox. The alerting device chooses by writing the destination into the SMTP recipient field, which is a native concept in every device we're using.

---

## 3. Environment & Network Context

### 3.1 Network topology

```
   ┌─────────────────────────────────────────┐
   │  Alerting devices (default VLAN)        │
   │  All on 10.1.10.0/24                    │
   │                                         │
   │  ├── Vertiv GXT3 UPS w/ Unity   10.1.10.10│
   │  ├── APC 4423 NMC               10.1.10.30│
   │  ├── APC 7921 Rack PDU          10.1.10.35│
   │  ├── Avocent AV2216 KVM         10.1.10.40│
   │  └── Vertiv Geist Watchdog 100P 10.1.10.50│
   └────────────────┬────────────────────────┘
                    │ SMTP :25
                    ▼
   ┌─────────────────────────────────────────┐
   │  Docker/Portainer host(s)               │
   │  ├── Primary:   10.1.10.97              │
   │  └── Secondary: 10.1.10.98 (manual DR)  │
   │                                         │
   │      smtp2sms  ◀── this service         │
   └────────┬────────────────────┬───────────┘
            │ HTTP               │ SMTPS :587
            ▼                    ▼
   ┌─────────────────┐   ┌──────────────────┐
   │ Unifi USG-PRO-4 │   │ smtp.office365   │
   │ (routes WAN2)   │   │ .com             │
   └────────┬────────┘   └──────────────────┘
            ▼                    ▼
   ┌─────────────────┐   📧 email delivered
   │ RUT956          │
   │ 10.2.10.1/24    │
   │ RutOS 7.23.7    │
   └────────┬────────┘
            │ cellular
            ▼
       📱 SMS delivered
```

### 3.2 Key IPs & config

| Component | Address | Notes |
|---|---|---|
| Docker primary | `10.1.10.97` | Primary deployment target |
| Docker secondary | `10.1.10.98` | Manual DR — keep `.env` in sync, don't run simultaneously (would double-deliver) |
| RUT956 (LAN side) | `10.2.10.1` | Reachable from LAN via existing USG firewall rule |
| RUT956 (WAN side) | Cellular, dynamic | 1NCE SIM in slot 1, roaming Optus AU, RSRP ~-108 (marginal but functional) |
| Exchange 365 relay | `smtp.office365.com:587` | STARTTLS + SMTP AUTH; user to confirm mailbox setup |
| Alerting devices | `10.1.10.10`, `.30`, `.35`, `.40`, `.50` | See §3.1. Bridge should reject SMTP from any other IP. |

### 3.3 VLAN / firewall

All source devices and Docker hosts are on the **default VLAN** (`10.1.10.0/24`), so no inter-VLAN routing config is required — direct L2 reachability.

USG already has `LAN → 10.2.10.0/24 allow`, so the Docker host can reach the RUT956 API.

Outbound to `smtp.office365.com:587` needs to be permitted from the Docker host — likely already the case, but Claude Code should verify with a quick `openssl s_client -starttls smtp -connect smtp.office365.com:587` before writing the mail client.

---

## 4. Functional Requirements

### 4.1 SMTP receiver

| # | Requirement |
|---|---|
| F1 | Listen on TCP port 25 (configurable) on all interfaces inside the container. |
| F2 | Accept unauthenticated SMTP connections (LAN-only, no TLS required). |
| F3 | **IP whitelist**: reject SMTP connections at the `HELO`/`EHLO` stage from any source IP not in the configured allow-list. Log the rejection. |
| F4 | Support standard SMTP verbs: `HELO`, `EHLO`, `MAIL FROM`, `RCPT TO`, `DATA`, `QUIT`, `RSET`. |
| F5 | Accept messages up to 1 MB. |
| F6 | Always respond `250 Message accepted for delivery` after `DATA` completes, regardless of downstream success. Routing failures are logged, not surfaced to the sender. |

### 4.2 Routing decision

The service determines the output path from the **envelope recipient** (`RCPT TO`), which each alerting device sets per-recipient in its native config UI.

| # | Requirement |
|---|---|
| F7 | Extract the local-part (before `@`) from the first `RCPT TO` address. |
| F8 | If the local-part matches an E.164 phone number pattern (`^\+?[1-9]\d{7,14}$`), route as **SMS**. The extracted number (with `+` prefix normalised on) is the destination. |
| F9 | Otherwise, route as **email relay**. The full RCPT TO address is the destination. |
| F10 | If neither branch applies (e.g. local-part empty), log a warning and drop. Still return `250 OK`. |
| F11 | If multiple `RCPT TO` addresses were provided, process each independently — some may SMS, some may email. |

### 4.3 SMS path (RUT956)

| # | Requirement |
|---|---|
| F12 | Construct the SMS text as: `"<subject>: <body>"` — subject then colon-space then plain-text body. |
| F13 | Body extraction: prefer `text/plain` part; fall back to HTML → text via `html2text`; fall back to empty string. |
| F14 | Collapse whitespace runs, trim, truncate to **160 characters** (single-segment SMS). |
| F15 | If the resulting text is empty or only whitespace, substitute `"[empty alert body]"`. |
| F16 | POST to the RUT956 SMS API (see §7). Total time budget: 10s. |
| F17 | **No retries.** Log outcome and move on. |

### 4.4 Email path (Exchange 365 relay)

| # | Requirement |
|---|---|
| F18 | Connect to `smtp.office365.com:587` with STARTTLS. |
| F19 | Authenticate using SMTP AUTH with the credentials in the environment config. |
| F20 | Rewrite the `From:` header to the authenticated mailbox address (Exchange 365 rejects unauthenticated senders). Preserve the original `From:` as `X-Original-From:` for traceability. |
| F21 | Preserve `Subject`, message body (both parts if multipart), and any device-relevant headers. |
| F22 | Add a header `X-smtp2sms-source: <device-ip>` for debugging. |
| F23 | Total time budget: 15s. **No retries** (fire-and-forget consistent with SMS path). |

### 4.5 Observability

| # | Requirement |
|---|---|
| F24 | All logs go to stdout as single-line structured JSON (fields: `ts`, `level`, `event`, `source_ip`, `sender`, `rcpt_to`, `route`, `subject`, `body_len`, `outcome`, `error`). |
| F25 | Log every: connection accepted/rejected, mail received, routing decision, output attempt, output result. |
| F26 | Never log passwords, auth tokens, or the full body (log length + first 40 chars only). |
| F27 | Health endpoint on `:8080/health` returning `200 OK` if the SMTP listener is running. |

---

## 5. Non-Functional Requirements

- **Stateless.** No database or persistent volumes.
- **Container size.** Base on `python:3.12-slim`. Target final image < 100 MB.
- **Startup.** Ready to receive SMTP within 2s of container start.
- **Restart.** `restart: unless-stopped` in compose.
- **Secrets.** All credentials via env vars; never in the image, never in logs.
- **Single-writer.** Deploy on one host at a time. Simultaneous deployment on `.97` and `.98` would double-deliver each alert.
- **Time.** Log in ISO8601 UTC.

---

## 6. Technical Design

### 6.1 Language & libraries

- **Python 3.12**
- [`aiosmtpd`](https://aiosmtpd.readthedocs.io/) — async SMTP server
- [`httpx`](https://www.python-httpx.org/) — async HTTP client (RUT API)
- [`aiosmtplib`](https://aiosmtplib.readthedocs.io/) — async SMTP client (Exchange 365 relay)
- [`html2text`](https://pypi.org/project/html2text/) — HTML → plain text
- [`aiohttp`](https://docs.aiohttp.org/) — for the tiny health endpoint
- Standard library: `email`, `asyncio`, `logging`, `os`, `re`, `json`, `ipaddress`

### 6.2 Configuration

All via environment variables. **No credentials in this document — Claude Code will prompt the user at build time.**

| Env var | Default | Required | Purpose |
|---|---|---|---|
| `ALLOWED_SOURCE_IPS` | — | yes | Comma-separated allow-list, e.g. `10.1.10.10,10.1.10.30,10.1.10.35,10.1.10.40,10.1.10.50` |
| `RUT_HOST` | `10.2.10.1` | yes | RUT956 LAN IP |
| `RUT_SMS_USERNAME` | — | yes | RUT SMS API user (created in RutOS, see §7) — **prompt at build** |
| `RUT_SMS_PASSWORD` | — | yes | RUT SMS API password — **prompt at build** |
| `EXCHANGE_RELAY_HOST` | `smtp.office365.com` | no | Exchange 365 SMTP endpoint |
| `EXCHANGE_RELAY_PORT` | `587` | no | STARTTLS submission port |
| `EXCHANGE_RELAY_USER` | — | yes | Mailbox address used for auth + `From:` rewrite — **prompt at build** |
| `EXCHANGE_RELAY_PASSWORD` | — | yes | Mailbox / app password — **prompt at build** |
| `SMTP_LISTEN_HOST` | `0.0.0.0` | no | Bind address inside container |
| `SMTP_LISTEN_PORT` | `25` | no | SMTP port inside container |
| `HEALTH_LISTEN_PORT` | `8080` | no | Health endpoint port |
| `LOG_LEVEL` | `INFO` | no | `DEBUG`, `INFO`, `WARN`, `ERROR` |

### 6.3 Suggested project layout

```
smtp2sms/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── README.md
└── smtp2sms/
    ├── __init__.py
    ├── main.py            # entrypoint: wires up SMTP + health endpoints
    ├── config.py          # env var loading + validation
    ├── handler.py         # aiosmtpd handler with IP whitelist + routing
    ├── router.py          # RCPT TO → phone-number-or-email decision
    ├── extractor.py       # email → plain text body extraction & truncation
    ├── sms_client.py      # RUT956 API client
    ├── mail_client.py     # Exchange 365 relay client
    └── logging_setup.py   # JSON stdout logger
```

### 6.4 Reference snippets

Illustrative, not final — Claude Code should write idiomatic code from these:

```python
# router.py — the routing decision
import re

E164_RE = re.compile(r"^\+?[1-9]\d{7,14}$")

def route(rcpt_to: str) -> tuple[str, str]:
    """
    Returns ("sms", "+61412345678") or ("email", "person@example.com")
    Raises ValueError if unroutable.
    """
    if "@" not in rcpt_to:
        raise ValueError("no @ in rcpt")
    local, _ = rcpt_to.rsplit("@", 1)
    if E164_RE.match(local):
        return ("sms", local if local.startswith("+") else f"+{local}")
    if "@" in rcpt_to and "." in rcpt_to.split("@", 1)[1]:
        return ("email", rcpt_to)
    raise ValueError(f"unroutable rcpt: {rcpt_to}")
```

```python
# handler.py — IP whitelist + per-recipient routing
class Smtp2SmsHandler:
    def __init__(self, allowed_ips, router, extractor,
                 sms_client, mail_client, log):
        self.allowed_ips = set(allowed_ips)
        self.router = router
        ...

    async def handle_EHLO(self, server, session, envelope, hostname):
        peer_ip = session.peer[0]
        if peer_ip not in self.allowed_ips:
            self.log.warning({"event": "connection_rejected",
                              "source_ip": peer_ip})
            return "550 sender not permitted"
        session.host_name = hostname
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):
        for rcpt in envelope.rcpt_tos:
            try:
                kind, dest = self.router.route(rcpt)
            except ValueError as e:
                self.log.warning({"event": "rcpt_dropped",
                                  "rcpt_to": rcpt, "error": str(e)})
                continue
            if kind == "sms":
                await self._send_sms(envelope, dest)
            else:
                await self._send_email(envelope, dest, session.peer[0])
        return "250 Message accepted for delivery"
```

### 6.5 Dockerfile

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY smtp2sms/ ./smtp2sms/
EXPOSE 25 8080
CMD ["python", "-m", "smtp2sms.main"]
```

Binding to port 25 inside the container as root is fine; container isolation is the boundary.

### 6.6 docker-compose.yml

```yaml
services:
  smtp2sms:
    build: .
    container_name: smtp2sms
    restart: unless-stopped
    ports:
      - "25:25"
      - "8080:8080"
    environment:
      ALLOWED_SOURCE_IPS: "10.1.10.10,10.1.10.30,10.1.10.35,10.1.10.40,10.1.10.50"
      RUT_HOST: "10.2.10.1"
      RUT_SMS_USERNAME: "${RUT_SMS_USERNAME}"
      RUT_SMS_PASSWORD: "${RUT_SMS_PASSWORD}"
      EXCHANGE_RELAY_HOST: "smtp.office365.com"
      EXCHANGE_RELAY_PORT: "587"
      EXCHANGE_RELAY_USER: "${EXCHANGE_RELAY_USER}"
      EXCHANGE_RELAY_PASSWORD: "${EXCHANGE_RELAY_PASSWORD}"
      LOG_LEVEL: "INFO"
    healthcheck:
      test: ["CMD", "wget", "-q", "-O-", "http://localhost:8080/health"]
      interval: 30s
      timeout: 5s
      retries: 3
```

Values are loaded from a `.env` file alongside compose. Claude Code will prompt the user for values during setup and write them into `.env` with `chmod 600`.

---

## 7. RUT956 SMS API — **critical unknown**

**Claude Code: this section requires actual verification, not just trust of the doc.**

The RUT956 running RutOS 7.23.7 exposes SMS sending via HTTP. Two candidate APIs, unclear which is available in this firmware version:

### Option A — legacy POST/GET SMS API

Historically enabled under **Services → Mobile Utilities → Messages → HTTP API** (older UIs called this "Post/Get"). Endpoint format:

```
GET  http://10.2.10.1/cgi-bin/sms_send?username=USER&password=PASS&number=+61412345678&text=Hello
POST http://10.2.10.1/cgi-bin/sms_send   (same params as body)
```

Simple, no session token. Use if present.

### Option B — modern JSON-RPC / REST API

RutOS 7.x exposes `/api/` endpoints requiring a session token:

```
POST /api/login  { "username": "...", "password": "..." }
    → returns { "data": { "token": "..." } }

POST /api/messages/actions/send
Headers: Authorization: Bearer <token>
Body: { "data": { "number": "+61412345678", "message": "Hello" } }
```

More complex (token refresh, error handling) but always available in modern RutOS.

### What Claude Code should do

1. **Log in to the RUT WebUI** at `http://10.2.10.1` with the admin credentials the user provides.
2. **Check Package Manager** for a "POST/GET SMS" or "HTTP SMS API" package. Install if available.
3. **Under Services → Mobile Utilities**, look for the SMS API config page. Create a dedicated API user (do NOT reuse admin creds). Note the credentials to place in `.env`.
4. **Test with curl from the Docker host** before writing any Python:
   ```bash
   # Option A test
   curl "http://10.2.10.1/cgi-bin/sms_send?username=USER&password=PASS&number=+61XXXXXXXXX&text=curl+test"

   # Option B test
   TOKEN=$(curl -sX POST http://10.2.10.1/api/login \
     -H 'Content-Type: application/json' \
     -d '{"username":"USER","password":"PASS"}' | jq -r '.data.token')
   curl -X POST http://10.2.10.1/api/messages/actions/send \
     -H "Authorization: Bearer $TOKEN" \
     -H 'Content-Type: application/json' \
     -d '{"data":{"number":"+61XXXXXXXXX","message":"curl test"}}'
   ```
5. **Wrap whichever works in `sms_client.py`** so if the API surface changes later, only that file changes.

If Option B is required, cache the token in memory and refresh on 401.

---

## 8. Exchange 365 Relay Setup

Before the email path will work, the user needs to prepare an Exchange 365 mailbox for SMTP AUTH submission. Claude Code should walk the user through this if not already done:

1. A **licensed Exchange Online mailbox** must exist (Business Basic or above). A dedicated service mailbox is recommended, e.g. `alerts@<domain>`.
2. **Authenticated SMTP** must be enabled on that mailbox:
   - Microsoft 365 admin center → Users → Active users → select mailbox → Mail tab → Manage email apps → tick **Authenticated SMTP**.
3. If the mailbox has MFA enabled, either:
   - Create an **app password** for the mailbox (recommended), or
   - Use a mailbox without MFA (acceptable for a low-privilege service account with no admin roles).
4. Note the mailbox address and password/app password for the `.env` values.

The `From:` address on relayed mail **must equal** the authenticated mailbox address, or Exchange will reject with `550 5.7.60`. The service rewrites the `From:` header to comply and preserves the original as `X-Original-From:`.

**Tenant policy caveat:** some Microsoft 365 tenants disable SMTP AUTH tenant-wide. If Claude Code's initial test returns `535 5.7.139`, the fix is:
```
Set-TransportConfig -SmtpClientAuthenticationDisabled $false
```
(run in Exchange Online PowerShell by an admin), or the per-mailbox override:
```
Set-CASMailbox -Identity alerts@domain.com -SmtpClientAuthenticationDisabled $false
```

---

## 9. Deployment

1. On `10.1.10.97`, create the project directory and files.
2. Claude Code prompts the user for:
   - RUT SMS API username/password
   - Exchange 365 mailbox address + password/app password
3. Values are written to `.env` alongside `docker-compose.yml`. `chmod 600 .env`.
4. `docker compose up -d --build`.
5. Verify: `docker logs -f smtp2sms` should show `SMTP listener ready on 0.0.0.0:25` within 2s.
6. Health check: `curl http://10.1.10.97:8080/health` → `200 OK`.

**Secondary host (`10.1.10.98`):** copy the project directory + `.env` across but **do not `up` it**. It's manual DR — if the primary fails, `docker compose up -d` on `.98` and update the SMTP server IP in each alerting device.

---

## 10. Testing Plan

### 10.1 Unit tests

Cover:
- `router.route()`: valid E.164 with `+`, without `+`, invalid phone (too short, letters), valid email, malformed input, empty local-part.
- `extractor.extract_and_truncate()`: plain-text body, multipart with plain + HTML, HTML-only, empty body, > 160 chars truncation, non-UTF-8 charset.

### 10.2 Local integration — SMS path

From the Docker host, using `swaks`:

```bash
swaks --to "+61412345678@sms.local" \
      --from "ups@rack.local" \
      --server localhost:25 \
      --header "Subject: TEST ALERT" \
      --body "Battery low on GXT UPS. Runtime 3 minutes."
```

Expected: SMS arrives on the destination phone within 30s. Container log shows `route=sms`, `outcome=success`.

### 10.3 Local integration — email path

```bash
swaks --to "admin@yourdomain.com" \
      --from "ups@rack.local" \
      --server localhost:25 \
      --header "Subject: Info: nightly self-test passed" \
      --body "GXT3 UPS self-test completed successfully."
```

Expected: email arrives in the admin mailbox with `From: alerts@<domain>`, original sender preserved in `X-Original-From:`, correct subject and body.

### 10.4 IP whitelist test

Attempt SMTP from a host **not** in `ALLOWED_SOURCE_IPS`. Expected: rejected at EHLO with `550 sender not permitted`. Log entry `event=connection_rejected`.

### 10.5 Failure-mode tests

- **RUT unreachable**: block outbound to `10.2.10.1`, send SMS-routed email, verify SMTP still returns `250 OK` and log shows `outcome=failure` with the timeout error.
- **Exchange auth fails**: use a deliberately wrong password, verify email-routed message is dropped cleanly with `outcome=failure`.
- **Malformed RCPT TO**: e.g. `garbage@nothing`, verify `event=rcpt_dropped` in the log.
- **Multi-recipient**: one phone number RCPT TO + one email RCPT TO in the same envelope, verify both fire.

### 10.6 End-to-end validation

Trigger a real test alarm from **each** of the five devices, using both a phone-number and an email recipient. Verify delivery on both paths for at least one device (typically the Geist Watchdog has the easiest test-alarm button).

---

## 11. Device Configuration Notes

Post-deployment, each device is configured with its notification recipients. The bridge's IP is `10.1.10.97` (primary) or `.98` (during DR), port 25, no auth, no TLS.

**Recipient format the devices will use:**
- SMS: `+61412345678@sms.local` (bridge parses local-part as phone number; domain is ignored)
- Email: real address, e.g. `admin@yourdomain.com`

**Common settings for all five devices:**
- SMTP Server: `10.1.10.97`
- SMTP Port: `25`
- Authentication: `None`
- TLS: `Off`
- From address: whatever the device defaults to (e.g. `geist@rack.local`) — logged but not routed

**Device-specific notes:**

| Device | Notes |
|---|---|
| Vertiv GXT3 UPS w/ Unity (`10.1.10.10`) | IntelliSlot / Unity card. Notifications → Email. Multiple recipients supported. If Unity firmware demands SMTP AUTH, this is a v1.1 issue. |
| APC 4423 NMC (`10.1.10.30`) | Email server config under `Configuration → Notification → Email → Server`. Recipients under `... → Recipients`. |
| APC 7921 Rack PDU (`10.1.10.35`) | Same NMC family as 4423. Identical config path. |
| Avocent AV2216 KVM (`10.1.10.40`) | Under `Settings → Event Destinations → SMTP`. Multiple event categories can map to different recipients. |
| Vertiv Geist Watchdog 100P (`10.1.10.50`) | `Setup → Alarms → Recipients`. Best device to test with — has a clean "test alarm" button. |

**Test each device individually** before considering deployment complete.

---

## 12. Known Unknowns / Open Questions

| # | Question | Owner | Blocking? |
|---|---|---|---|
| U1 | Which SMS API version (§7 A vs B) is available on RutOS 7.23.7? | Claude Code to verify | Yes |
| U2 | Is Authenticated SMTP enabled on the Exchange 365 service mailbox? Is tenant-wide SMTP AUTH allowed? | User + Claude Code (§8) | Yes for email path |
| U3 | Do any of the five devices require SMTP AUTH from the bridge? | Verify during §10.6 | v1.1 fix if so |
| U4 | Do the APC and Avocent cards support arbitrary email addresses as recipients, including the `+` character in local-parts? | Test during §11 | Workaround: strip `+` and add it back in the router |

---

## 13. Explicitly Out of Scope (v1.0)

- No SMTP AUTH on the receiving side.
- No TLS on the receiving side.
- No retry logic (fire-and-forget on both paths, per user requirement).
- No rate limiting (volume is 5–10 alerts/month max).
- No persistent queue.
- No web UI.
- No multi-tenant / multi-Exchange-tenant support.
- No OAuth2 for Exchange (basic SMTP AUTH is sufficient for this volume).
- No automatic failover between Docker hosts (`.97` ↔ `.98` is manual).

---

## 14. Future Enhancements (post-v1.0)

- **v1.1** — SMTP AUTH on the receiving side (if any device demands it).
- **v1.2** — Keepalived/VIP-based automatic failover between `10.1.10.97` and `.98`.
- **v1.3** — Wire the Geist smoke detector's dry contact directly into the RUT956's digital INPUT pin (pin 3 on the 4-pin power connector). RutOS Event Juggler can then fire an SMS *without going through this bridge at all* — belt-and-suspenders redundancy for the fire alert specifically.
- **v1.4** — OAuth2 for Exchange 365 (once/if basic auth is deprecated for the tenant).
- **v1.5** — Prometheus metrics endpoint alongside the health check.
- **v1.6** — Optional message deduplication window (some devices spam identical alarms during a storm).

---

## 15. Success Criteria

Considered done when all of the following are true:

1. Container builds and runs on `10.1.10.97`.
2. `swaks` SMS test (§10.2) results in an SMS delivered to a phone.
3. `swaks` email test (§10.3) results in an email delivered via Exchange 365.
4. IP whitelist test (§10.4) rejects an unauthorised source.
5. All five devices (§11) successfully route both an SMS-destined and email-destined test alarm.
6. Container has run continuously for 7 days without unexplained restarts.
7. SMTP2GO subscription is cancelled.

---

## 16. Handover Notes to Claude Code

- **Read §7 and §8 first.** Both the RUT SMS API and Exchange 365 SMTP AUTH have real setup steps that need verification before writing Python. Validate both with `curl` / `openssl s_client` before touching the codebase.
- **Prompt the user for all credentials** during setup — do not read them from anywhere else, do not assume defaults, do not log them. Write to `.env` with `chmod 600`.
- **The routing logic is the heart of this service.** `router.py` should be thoroughly unit-tested — bad routing decisions are the most likely source of silent alert loss.
- **Keep sms_client.py and mail_client.py isolated.** Each is one external API; swapping either later should not require changes elsewhere.
- **Log everything as JSON** from day one. The user should be able to `docker logs smtp2sms | jq 'select(.route=="sms")'` and get useful output.
- **The IP whitelist matters.** Even on a LAN, a compromised IoT device could turn this into a spam relay if the whitelist is missing or misconfigured. Fail closed — if the env var is missing, refuse to start.
- **Fire-and-forget means fire-and-forget.** Do not add retry logic without explicit user approval — they've considered and rejected it.
- **Do not deploy on both `.97` and `.98` simultaneously.** Devices sending to the shared `.97` IP would work fine, but if devices are configured with hostnames or a VIP that resolves to both, alerts would double-deliver.
- **Ship v1.0 minimal.** Anything not in §4 or §5 is post-v1.0.

---

*End of document.*
