import asyncio
from playwright.async_api import async_playwright
from downloader.gigalib import gigalib_login

async def test_gigalib():
    doi = "10.1016/j.cell.2020.04.015"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        # مرحله ۱: لاگین
        await gigalib_login(page)

        # مرحله ۲: رفتن به صفحه‌ی اصلی و بخش DOI
        print("[+] Navigating to DOI access page...")
        await page.goto("http://gigalib.org/index.aspx", timeout=60000)

        await page.get_by_role("link", name="دسترسی مقاله با DOI").click()
        await asyncio.sleep(2)

        # مرحله ۳: وارد کردن DOI و ارسال درخواست
        print(f"[+] Submitting DOI request: {doi}")
        await page.locator("#ContentPlaceHolder1_txt_SearchKey").click()
        await page.locator("#ContentPlaceHolder1_txt_SearchKey").fill(doi)
        await page.get_by_role("button", name="درخواست مقاله").click()

        # مرحله ۴: کمی صبر برای واکنش سایت
        await asyncio.sleep(5)

        # مرحله ۵: گرفتن اسکرین‌شات از نتیجه
        await page.screenshot(path="gigalib_doi_result.png")
        print("📸 Screenshot saved: gigalib_doi_result.png")

        print("✅ DOI test finished. مرورگر باز ماند برای بررسی دستی.")
        await asyncio.Event().wait()  # مرورگر باز می‌ماند تا بستن دستی

asyncio.run(test_gigalib())
