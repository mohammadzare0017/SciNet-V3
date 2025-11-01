import asyncio
import aiohttp
import os

class IranPaperClient:
    def __init__(self, username: str, password: str, download_dir: str = "./data"):
        self.username = username
        self.password = password
        self.download_dir = download_dir
        os.makedirs(download_dir, exist_ok=True)

    async def login(self):
        # فعلاً شبیه‌سازی لاگین
        print(f"[IranPaper] Login simulated for user {self.username}")
        await asyncio.sleep(0.5)
        return True

    async def download_by_doi(self, doi: str) -> str:
        """
        شبیه‌سازی دانلود مقاله از سایت ایران‌پیپر
        در نسخه واقعی اینجا باید:
         1. وارد سایت شوی
         2. DOI را جستجو کنی
         3. فایل را بگیری
        """
        await asyncio.sleep(1)
        fake_path = os.path.join(self.download_dir, f"{doi.replace('/', '_')}.pdf")

        # ساخت فایل فرضی برای تست
        with open(fake_path, "wb") as f:
            f.write(b"%PDF-1.4\n%Fake PDF content\n%%EOF")

        print(f"[IranPaper] Simulated download complete: {fake_path}")
        return fake_path
