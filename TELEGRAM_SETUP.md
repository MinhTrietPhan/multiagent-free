# Agent Hub Telegram interface

Telegram is an additional interface to the existing local Agent Hub. It uses long polling; no public IP, webhook, VPS, or inbound port is required. The Windows PC must remain on, connected to the Internet, and running `hub-telegram`.

## 1. Create the bot

1. Open Telegram and chat with `@BotFather`.
2. Run `/newbot`, follow its prompts, and copy the bot token.
3. Get your numeric Telegram user ID using a trusted user-ID bot such as `@userinfobot`.
4. Do not share the bot token. If it is exposed, revoke it through BotFather and generate a new one.

## 2. Configure the project

Merge the two Telegram variables from `.env.example` into the existing project `.env` (do not overwrite your existing API keys or research settings):

```dotenv
HUB_TELEGRAM_BOT_TOKEN=your_bot_token
HUB_TELEGRAM_ALLOWED_USER_ID=123456789
HUB_TELEGRAM_LOG_LEVEL=INFO
```

`HUB_TELEGRAM_ALLOWED_USER_ID` is mandatory. The bot refuses to start without it and rejects messages from every other Telegram account.

## 3. Install and run (PowerShell)

Activate the same Conda environment used by Agent Hub, then run these commands from the repository root:

```powershell
pip install -e ".[research,telegram]"
hub-telegram
```

If the project is already installed with its other extras, `pip install -e ".[telegram]"` is enough. Keep the terminal open while using the bot. Stop it with `Ctrl+C`.

## 4. Telegram commands

- Send a normal message: Supervisor chooses an agent.
- `/qa <question>`
- `/research <topic>`
- `/search <query>`
- `/context <instruction>`
- `/agents`
- `/reset` creates a new Telegram conversation thread; previous history remains in SQLite.
- `/help`

Telegram history uses separate thread IDs and is stored in the existing `HUB_DATA_DIR` database. It does not mix with the CLI thread. The LangGraph in-memory checkpoint is rebuilt from saved SQLite conversation history after a bot restart.

## Notes

- This bot runs locally on the computer where Agent Hub is installed. Closing the terminal or shutting down the computer stops it.
- Do not commit `.env` or send the bot token in chat.
- The current adapter serializes graph invocations to avoid overlapping writes to the in-memory checkpointer.
