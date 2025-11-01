# gigalib.py
from __future__ import annotations
import asyncio
import os
from dotenv import load_dotenv
load_dotenv()
from playwright.async_api import Page

async def gigalib_login(page: Page):
    user = os.getenv("GIGALIB_USER")
    password = os.getenv("GIGALIB_PASS")

    print("[+] Logging into GigaLib...")
    try:
        await page.goto("http://gigalib.org", timeout=45000)
        await page.locator("#txtUser").click()
        await page.locator("#txtUser").fill(user)
        await page.locator("#txtPass").click()
        await page.locator("#txtPass").fill(password)
        page.once("dialog", lambda dialog: asyncio.create_task(dialog.dismiss()))
        await page.get_by_role("button", name="ورود به پروفایل").click()

        await asyncio.sleep(3)
        print("[+] Logged into GigaLib successfully!")

    except Exception as e:
        print(f"❌ GigaLib login failed: {e}")
        screenshot_path = "gigalib_login_error.png"
        await page.screenshot(path=screenshot_path)
        print(f"📸 Screenshot saved: {screenshot_path}")
        raise


async def gigalib_download(page: Page, doi: str, download_dir: str = "./downloads") -> str:
    import time
    from pathlib import Path

    print(f"[GigaLib] Searching DOI: {doi}")
    Path(download_dir).mkdir(exist_ok=True)

    try:
        await page.goto("http://gigalib.org", timeout=30000)
        await page.locator("#ContentPlaceHolder1_txt_SearchKey").click()
        await page.locator("#ContentPlaceHolder1_txt_SearchKey").fill(doi)
        await page.get_by_role("button", name="درخواست مقاله").click()
        await asyncio.sleep(3)
        await page.get_by_role("button", name=doi).click()
        async with page.expect_download(timeout=60000) as dl_info:
            pass
        download = await dl_info.value
        safe_name = doi.replace('/', '_').replace(':', '_')
        file_path = os.path.join(download_dir, f"{safe_name}.pdf")
        await download.save_as(file_path)

        print(f"[+] GigaLib article downloaded: {file_path}")
        return file_path
    
    except Exception as e:
        print(f"⚠️ GigaLib download failed for DOI {doi}: {e}")
        screenshot_path = f"gigalib_error_{int(time.time())}.png"
        await page.screenshot(path=screenshot_path)
        print(f"📸 Error screenshot saved: {screenshot_path}")
        raise

    except Exception as e:
        print(f"⚠️ GigaLib failed for DOI {doi}: {e}")
        raise
