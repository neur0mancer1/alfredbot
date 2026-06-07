# Alfred

Alfred is a long-running Telegram bot that parses Tesco receipt emails, lets a
household claim items, and calculates settlements. It uses Telegram long
polling, so it does not need a public HTTP endpoint.

Landing page: https://a1fredbot.vercel.app

Each household member should run `/join` once in the group. This securely links
their household name to their Telegram account, allowing only the selected payer
to confirm that money was received. To pin confirmed-payment summaries in a
group, make Alfred an administrator with permission to pin messages.

After a shop is settled, Alfred automatically reminds the household group if
payment is still outstanding after 1, 2, and 4 hours. `/nudge` also shows all
outstanding shops on demand. `/weeklywrap` covers the rolling previous 7 days;
`/monthlywrap` and `/wrap` cover the rolling previous 30 days; `/alltimewrap`
tallies the household's complete expense history.

New groups require a one-use access code. Issue codes locally with:

```bash
PYTHONPATH=src .venv/bin/python scripts/access_codes.py issue --kind paid --count 1
PYTHONPATH=src .venv/bin/python scripts/access_codes.py issue --kind promo --count 5
```

The customer activates one group with `/activate ALFRED-CODE`. Existing
households can be grandfathered with:

```bash
PYTHONPATH=src .venv/bin/python scripts/access_codes.py grandfather
```

## Run locally

Requires Python 3.12+.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# Set TELEGRAM_BOT_TOKEN in .env
PYTHONPATH=src .venv/bin/python -m alfred.bot.telegram_bot
```

Run the tests:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -v
```

## Deploy to Railway

The checked-in `railway.json` configures Railpack, starts the Telegram daemon,
and restarts it if it exits. Keep the service at exactly one replica because
Telegram long polling must not run concurrently with the same bot token.

1. Create a Railway project and an empty service.
2. Add `TELEGRAM_BOT_TOKEN` to the service variables.
3. Add optional variables from `.env.example` if email intake or Mubit is
   required. Do not upload the local `.env` file.
4. Attach a volume to the service and mount it at `/data`. Alfred automatically
   detects Railway's `RAILWAY_VOLUME_MOUNT_PATH` and stores households, receipts,
   and local memory there.
5. Deploy from this directory:

   ```bash
   railway login
   railway link
   railway up
   ```

6. Confirm the deployment logs contain `Alfred is live as @...`.

Without a volume, the bot still runs, but its JSON state is lost on redeploy.
The existing local `data/store` is intentionally excluded from deployments
because it contains household data.

## Variables

`TELEGRAM_BOT_TOKEN` is required. `IMAP_USER` and `IMAP_PASS` enable mailbox
polling; `IMAP_HOST`, `IMAP_TO`, and `IMAP_POLL_SECONDS` tune it.
`MUBIT_API_KEY` enables Mubit-backed memory. `ALFRED_DATA_DIR` explicitly
overrides the storage location when needed.
