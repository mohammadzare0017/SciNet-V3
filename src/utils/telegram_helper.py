import logging
from pathlib import Path
from typing import Union
from telegram import InputFile
from telegram.ext import Application

logger = logging.getLogger(__name__)

async def send_file_to_chat(app: Application, chat_id: Union[int,str], file_path: Union[str, Path], caption: str = ""):
    path = str(file_path)
    try:
        async with app.bot:
            await app.bot.send_document(chat_id=chat_id, document=InputFile(path), caption=caption)
    except Exception as e:
        logger.exception("Failed to send file %s to chat %s: %s", path, chat_id, e)
