from __future__ import annotations
import asyncio, os, random, logging
from playwright.async_api import Page
from src.utils.stealth import human_sleep, human_move_mouse


class IranPaperClient:
    def __init__(self, username: str, password: str, download_dir: str = "./data"):
        self.username = username
        self.password = password
        self.download_dir = download_dir
        os.makedirs(download_dir, exist_ok=True)

    async def download_by_doi(self, doi: str) -> str:
     
        await asyncio.sleep(1)
        fake_path = os.path.join(self.download_dir, f"{doi.replace('/', '_')}.pdf")

        with open(fake_path, "wb") as f:
            f.write(b"%PDF-1.4\n%Fake PDF content\n%%EOF")

        print(f"[IranPaper] Simulated download complete: {fake_path}")
        return fake_path
    
    async def periodic_relogin(self, page: Page):
        while True:
            wait_time = random.randint(4 * 3600, 6 * 3600)  # بین ۴ تا ۶ ساعت
            logger.info(f"🕒 ورود مجدد بعد از {wait_time // 3600} ساعت.")
            await asyncio.sleep(wait_time)

            try:
                logger.info("🔄 شروع فرآیند خروج و ورود مجدد به IranPaper...")
                await page.goto("https://iranpaper.ir/logout", timeout=30000)
                await asyncio.sleep(3)

                await page.goto("https://iranpaper.ir/login", timeout=30000)
                await page.fill('input[name="email"]', os.getenv("IRANPAPER_USER"))
                await page.fill('input[name="password"]', os.getenv("IRANPAPER_PASS"))
                await page.click('button[type="submit"]')
                await page.wait_for_load_state("networkidle")

                logger.info("✅ ورود مجدد به IranPaper با موفقیت انجام شد.")
                await send_telegram(
                    doi="N/A",
                    title="🔄 IranPaper relogin",
                    year="",
                    journal="System",
                    abstract="ورود مجدد خودکار به IranPaper انجام شد."
                )
            except Exception as e:
                logger.error(f"❌ خطا در ورود مجدد به IranPaper: {e}", exc_info=True)


async def iranpaper_login(page: Page):
    user = os.getenv("IRANPAPER_USER")
    password = os.getenv("IRANPAPER_PASS")

    print("[+] Logging into IranPaper...")

    try:
        await page.goto("https://iranpaper.ir/login", timeout=45000)
        await human_sleep(1, 2)
        await human_move_mouse(page)

        await page.get_by_role("textbox", name="موبایل یا ایمیل (نام‌کاربری)").click()
        await page.get_by_role("textbox", name="موبایل یا ایمیل (نام‌کاربری)").fill(user)
        await human_sleep(0.3, 0.8)
        await page.get_by_role("textbox", name="رمز عبور").click()
        await page.get_by_role("textbox", name="رمز عبور").fill(password)
        await human_sleep(0.3, 0.8)
        await page.get_by_role("button", name="ورود").click()

        await page.wait_for_selector("text=رویا", timeout=30000)
        await human_sleep(1, 2)
        print("[+] Logged into IranPaper successfully!")
        await human_move_mouse(page, times=3)

        await page.context.storage_state(path="session_iranpaper.json")

    except Exception as e:
        print(f"💥 خطای جدی در لاگین ایران‌پیپر: {e}")
        screenshot_path = "login_error.png"
        try:
            await page.screenshot(path=screenshot_path)
            print(f"📸 اسکرین‌شات از خطا در فایل {screenshot_path} ذخیره شد.")
        except Exception as se:
            print(f"🚨 نتوانستیم اسکرین‌شات بگیریم: {se}")
        
        raise


async def iranpaper_download(page: Page, doi: str, download_dir: str = "./data"):
    """Search article by DOI and download PDF from IranPaper"""
    print(f"[+] Searching DOI on IranPaper: {doi}")

    await page.goto("https://iranpaper.ir", wait_until="domcontentloaded")

    await page.wait_for_selector('textarea[aria-label="لینک مقاله، فصل کتاب یا شناسه DOI را وارد کنید"]', timeout=20000)
    await page.fill('textarea[aria-label="لینک مقاله، فصل کتاب یا شناسه DOI را وارد کنید"]', doi)

    await page.locator(".d-inline.pa-3").first.click()

    await page.wait_for_selector('button:has-text("دانلود فایل")', timeout=40000)
    print("[+] Download button detected, starting download...")

    async with page.expect_download() as download_info:
        await page.get_by_role("button", name="دانلود فایل").click()
    download = await download_info.value

    os.makedirs(download_dir, exist_ok=True)
    file_path = os.path.join(download_dir, f"{doi.replace('/', '_')}.pdf")
    await download.save_as(file_path)

    print(f"[+] Article downloaded successfully: {file_path}")
    return file_path


