# Telegram Reading Tracker Bot

Async Telegram bot for daily reading reminders in a channel, with owner-only control in private chat.

## Features
- Owner-only private commands.
- `/newbook` wizard (title, author, total pages, optional start date/start page).
- Channel header post per book: `📚 Title — Author (DD.MM.YYYY → …)`.
- Daily reminder posting with inline buttons:
  - `✅ Mark as read`
  - `✍️ I read different pages`
  - `⏸️ Pause / ▶️ Resume`
- On mark-as-read:
  - reminder marked done in DB,
  - reminder message deleted from channel,
  - book progress updated.
- On completion:
  - header is edited with finish date,
  - separate `✅ Finished` message is posted.
- Restart-safe (no duplicate reminders for same book/day).

## Tech stack
- Python 3.11+
- `python-telegram-bot` v21+ (async)
- SQLite (`aiosqlite`)
- APScheduler (`AsyncIOScheduler`)
- Timezone: `Asia/Tashkent`

## Project structure
```text
app/
  main.py
  bot.py
  scheduler.py
  db.py
  models.sql
  settings.py
```

## Setup
1. Create bot via `@BotFather`, get token.
2. Add bot to your channel as admin with:
   - permission to post messages,
   - permission to delete messages,
   - permission to edit messages.
3. Install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
4. Create `.env`:
   ```env
   TELEGRAM_BOT_TOKEN=your_bot_token_here
   OWNER_USER_ID=950697133
   CHANNEL_link=https://t.me/masharipovs_notes
   # Optional:
   # DB_PATH=data/bot.sqlite3
   # TZ=Asia/Tashkent
   ```
5. Run:
   ```bash
   python -m app.main
   ```

## First run
1. Open private chat with the bot.
2. Run:
   ```text
   /setchannel -100XXXXXXXXXX
   ```
   Use numeric channel ID, not link/username.
3. Run `/newbook` and complete wizard.

## Get channel ID quickly
Use this helper script to fetch the numeric channel ID:

```bash
source .venv/bin/activate
python scripts/get_channel_id.py
```

Optional custom chat:

```bash
python scripts/get_channel_id.py --chat @masharipovs_notes
```

On success it prints only the ID (for example `-1001234567890`).
Then use it with:

```text
/setchannel -1001234567890
```

## Commands
- `/start` help
- `/setchannel <channel_id>`
- `/newbook`
- `/list`
- `/progress <book_id>`
- `/pause <book_id>`
- `/resume <book_id>`
- `/settings`
- `/cancel`

## Reminder algorithm
- `daily_pages = start_pages + weekly_increment * week_index`
- `week_index = (date - start_date).days // increment_every_days`
- Defaults:
  - `start_pages=10`
  - `weekly_increment=5`
  - `increment_every_days=7`

Range generation:
- `from_page = last_read_page + 1`
- `to_page = min(from_page + daily_pages - 1, total_pages)`

## Message examples
- Header:
  ```text
  📚 Atomic Habits — James Clear (16.01.2026 → …)
  ```
- Reminder:
  ```text
  📅 17.01.2026 — Read pages 11–20
  ```
- Completion:
  ```text
  ✅ Finished: Atomic Habits — James Clear
  ```
