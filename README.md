# Conten-Savod-TG

Telegram bot for Chinese-language school content management: generating posts, images, templates, scheduling, autoposting, and analytics.

## Features

- AI post generation with provider fallback (Gemini, Groq, Qwen, Pollinations)
- Chinese-learning focused writing (hanzi, pinyin, examples, grammar nuances)
- Image generation in unified Chinese minimal chibi style
- Post templates: Fact of the day, Chinese wisdom, Weekly news, Hieroglyph breakdown
- Post preview/editing, hashtags, polls, reactions
- Scheduling and autopost queue
- Post history, stats, and calendar view
- Channel connection and bot settings

## Tech Stack

- Python 3.11
- `python-telegram-bot` 20.7
- `aiohttp`, `aiosqlite`, `Pillow`, `feedparser`, `beautifulsoup4`

## Project Structure

- `bot.py` - bot entrypoint and routing
- `handlers/` - Telegram command/callback handlers
- `services/` - AI text/image generation, HTTP session, logging
- `database.py` - SQLite storage layer
- `config.py` - runtime config and style prompts
- `utils.py` - helper utilities

## Quick Start

1. Create and activate virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Create `.env` and configure:

- `BOT_TOKEN`
- `ADMIN_ID` (optional; auto-set on first run if empty)
- `CHANNEL_ID` (optional; can be connected in bot)
- AI keys (optional but recommended): `GEMINI_API_KEY`, `GROQ_API_KEY`, `QWEN_API_KEY`, `HF_API_KEY`

4. Run bot:

```bash
python bot.py
```

Or on Windows:

```bat
run_bot.bat restart
```

## Notes

- `.env`, database, logs, and generated images are excluded from git.
- If Gemini quota is exhausted, the bot automatically falls back to other providers.

## License

Private/internal project unless you add a license explicitly.
