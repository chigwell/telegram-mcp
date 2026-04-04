import os
import logging
import sys
import contextlib
from dotenv import load_dotenv
from pythonjsonlogger import jsonlogger
from telethon import TelegramClient
from telethon.sessions import StringSession
from mcp.server.fastmcp import FastMCP

load_dotenv()

TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
TELEGRAM_SESSION_NAME = os.getenv("TELEGRAM_SESSION_NAME", "telegram_session")
SESSION_STRING = os.getenv("TELEGRAM_SESSION_STRING")

# Client initialization
if SESSION_STRING:
    client = TelegramClient(StringSession(SESSION_STRING), TELEGRAM_API_ID, TELEGRAM_API_HASH)
else:
    client = TelegramClient(TELEGRAM_SESSION_NAME, TELEGRAM_API_ID, TELEGRAM_API_HASH)

# Logging initialization
logger = logging.getLogger("telegram_mcp")
logger.setLevel(logging.ERROR)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.ERROR)
script_dir = os.getcwd()
log_file_path = os.getenv("MCP_LOG_PATH", os.path.join(script_dir, "mcp_errors.log"))

try:
    file_handler = logging.FileHandler(log_file_path, mode="a")
    file_handler.setLevel(logging.ERROR)
    console_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s - %(message)s")
    console_handler.setFormatter(console_formatter)
    json_formatter = jsonlogger.JsonFormatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    file_handler.setFormatter(json_formatter)
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
except Exception as log_error:
    logger.addHandler(console_handler)
    logger.error(f"Failed to set up log file handler: {log_error}")


@contextlib.asynccontextmanager
async def telegram_client_lifespan():
    print("Starting Telegram client...", file=sys.stderr)
    await client.start()
    print("Warming entity cache...")
    await client.get_dialogs()

    try:
        yield client
    finally:
        print("Disconnecting Telegram client...", file=sys.stderr)
        try:
            await client.disconnect()
        except Exception as e:
            logger.warning("Error during telegram client disconnect: %s", e)
