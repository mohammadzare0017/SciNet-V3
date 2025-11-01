import asyncio
import logging
import os
from typing import Dict, Any
from pathlib import Path
#سیکذیپ
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
# در src/worker.py
from .utils.state import State
from src.utils.state import State


from downloader.iranpaper import IranPaperClient
from src.utils.telegram_helper import send_file_to_chat

logger = logging.getLogger(__name__)

# مقدار پیش‌فرض؛ از env هم خوانده میشه
MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "2"))
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "./data"))

class DownloadJob(Dict):
    # expected keys: doi (str), requester_chat_id (int), requester_user (dict/optional), job_id (str)
    pass

class WorkerPool:
    def __init__(self, tg_app, state: State):
        self.queue: "asyncio.Queue[DownloadJob]" = asyncio.Queue()
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
        self.tg_app = tg_app  # telegram application / bot object needed to send files
        self.state = state
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    async def enqueue(self, job: dict):
        await self.queue.put(job)
        logger.info("Job enqueued: %s", job.get("job_id"))

    async def start_workers(self, n: int = 1):
        # launch n concurrent worker tasks that run forever
        for i in range(n):
            asyncio.create_task(self._worker_loop(i+1))

    async def _worker_loop(self, worker_index: int):
        client = IranPaperClient()  # uses env for credentials
        logger.info("Worker %d started", worker_index)
        while True:
            job = await self.queue.get()
            job_id = job.get("job_id") or f"job-{int(asyncio.get_event_loop().time()*1000)}"
            doi = job.get("doi")
            chat_id = job.get("requester_chat_id")
            try:
                async with self.semaphore:
                    logger.info("Worker %d processing job %s (doi=%s)", worker_index, job_id, doi)
                    # attempt download (with retries)
                    for attempt in range(3):
                        try:
                            local_path = await client.download_by_doi(doi, DOWNLOAD_DIR)
                            if local_path:
                                # update state
                                self.state.set_job_result(job_id, {"status":"done","path":str(local_path)})
                                # send file to telegram chat
                                await send_file_to_chat(self.tg_app, chat_id, local_path, caption=f"Article for DOI: {doi}")
                                logger.info("Worker %d finished job %s", worker_index, job_id)
                                break
                        except Exception as e:
                            logger.exception("Attempt %d failed for job %s: %s", attempt+1, job_id, e)
                            await asyncio.sleep(2 ** attempt)
                    else:
                        # all attempts failed
                        self.state.set_job_result(job_id, {"status":"failed", "error":"download_failed"})
                        # notify owner
                        owner = int(os.getenv("OWNER_ID", "0") or 0)
                        if owner:
                            await self.tg_app.bot.send_message(chat_id=owner, text=f"❌ دانلود مقاله ({doi}) برای job {job_id} ناموفق بود.")
            except Exception as e:
                logger.exception("Unhandled error processing job %s: %s", job_id, e)
            finally:
                self.queue.task_done()

