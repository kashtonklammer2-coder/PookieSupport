# PookieSupport Discord Bot

## Files
- `bot.py` — bot source code
- `requirements.txt` — Python dependencies
- `Procfile` — background worker start command for hosts that support Procfiles

## Deploy
1. Upload these files to a private GitHub repository.
2. In your bot host, create a Python/background worker service connected to that repository.
3. Set the start command to `python bot.py` (or let the host use `Procfile`).
4. Add an environment variable named `DISCORD_TOKEN` and paste your Discord bot token as its value.
5. Deploy and check the logs for `Bot is ready!`.

## Discord Developer Portal
Enable **Message Content Intent** and **Server Members Intent** under Bot > Privileged Gateway Intents if you use the related prefix commands and member features.

## Important
- Never put your bot token in this repository or in `bot.py`.
- A free host may sleep, stop, require periodic renewal, or not support always-on workers. Check the provider's current free-plan rules.
- Some data (such as AFK, XP/levels, welcome-channel settings, and interests) is held in memory and resets when the process restarts. The SQLite kiss counter database persists only if the host provides persistent storage and the database file is retained.
