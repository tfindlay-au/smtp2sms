# smtp2sms

LAN SMTP receiver that routes alerting-device email to SMS (Teltonika RUT956
JSON API) or relays it via an authenticated SMTP account (Gmail/Exchange),
based on the envelope recipient. See `smtp2sms-spdd.md` for the full design.

- `RCPT TO: +61412345678@sms.local` → SMS via RUT956 at `RUT_HOST`
- `RCPT TO: person@example.com` → relayed via `RELAY_HOST` (From: rewritten to
  `RELAY_USER`, original kept in `X-Original-From`)
- Fire-and-forget: always answers `250 OK`; failures are logged JSON on stdout.
- Source-IP whitelist enforced at HELO/EHLO; fails closed if unset.
- Email path is disabled (logged as `outcome=skipped`) until `RELAY_USER` and
  `RELAY_PASSWORD` are set.

## Deploy

```bash
cp .env.example .env   # fill in secrets
chmod 600 .env
docker compose up -d --build
docker logs -f smtp2sms
curl http://localhost:8080/health
```

## Tests

```bash
docker compose run --rm --no-deps --entrypoint python smtp2sms -m unittest discover -s tests -v
```

(or locally: `pip install -r requirements.txt && python -m unittest discover -s tests`)

## Useful log queries

```bash
docker logs smtp2sms | jq 'select(.route=="sms")'
docker logs smtp2sms | jq 'select(.outcome=="failure")'
docker logs smtp2sms | jq 'select(.event=="connection_rejected")'
```

## Notes

- Deployed host: currently **chip (10.1.10.98)**. Do not run on both hosts at
  once — alerts would double-deliver.
- RUT956 SMS uses the modern RutOS JSON API (`/api/login` +
  `/api/messages/actions/send`, modem `1-1.4`) with a dedicated `smsapi` user
  (admin group — the `user` group cannot send SMS).
