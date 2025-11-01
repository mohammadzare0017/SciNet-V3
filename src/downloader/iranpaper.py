# iranpaper.py

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
from pathlib import Path
from typing import Optional, Awaitable, Callable

from playwright.async_api import Page
from src.utils.stealth import human_sleep, human_move_mouse  # شاید بعداً استفاده شود

DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "./downloads"))

logger = logging.getLogger(__name__)

NotifyFn = Callable[..., Awaitable[None]]


class IranPaperClient:
    def __init__(self, username: str, password: str, download_dir: str = str(DOWNLOAD_DIR)):
        self.username = username
        self.password = password
        self.download_dir = download_dir  # string ok
        os.makedirs(download_dir, exist_ok=True)

    async def login(self, page: Page) -> None:
        """
        لاگین به IranPaper با استفاده از credentialهای خود شیء.
        منطق قبلی iranpaper_login اینجا آورده شده است.
        """
        user = self.username
        password = self.password

        print("[+] Logging into IranPaper...")

        async def _wait_challenge(p: Page, total_ms=20000):
            # انتظار فعال برای چک مرورگر/turnstile
            t0 = time.time()
            while (time.time() - t0) * 1000 < total_ms:
                html = (await p.content()).lower()
                if any(s in html for s in ["checking your browser", "turnstile", "cf-chl", "cloudflare"]):
                    await asyncio.sleep(1.0)
                    continue
                # اگر فرم را دیدیم، خارج شو
                if await p.locator('input[type="email"], input[name="email"], input[placeholder*="ایمیل"]').count() > 0 \
                   or await p.locator('input[type="password"], input[name="password"]').count() > 0:
                    return
                await asyncio.sleep(0.5)

        try:
            # مستقیم به صفحهٔ لاگین برو
            await page.goto("https://iranpaper.ir/login", timeout=60000, wait_until="domcontentloaded")
            await _wait_challenge(page, total_ms=25000)

            # اگر هنوز فرم بیرون نیامد، یک بار رفرش
            if await page.locator('input[name="email"], input[type="email"]').count() == 0:
                await page.reload(wait_until="domcontentloaded")
                await _wait_challenge(page, total_ms=15000)

            # بستن بنرهای کوکی/مودال
            for sel in [
                'button:has-text("قبول")',
                'button:has-text("باشه")',
                'button:has-text("موافقم")',
                '#cookie-accept', '.cookie-accept', 'button[aria-label="close"]'
            ]:
                try:
                    loc = page.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible():
                        await loc.click(timeout=1500)
                except Exception:
                    pass

            # تلاش برای پر کردن فرم در صفحهٔ اصلی
            async def fill_form(root):
                email = root.locator(
                    'input[name="email"], input[type="email"], input[placeholder*="ایمیل"], '
                    'input[placeholder*="نام\u200cکاربری"], input[placeholder*="کاربری"]'
                ).first
                await email.wait_for(state="visible", timeout=15000)
                await email.click()
                await email.fill(user)

                pwd = root.locator(
                    'input[name="password"], input[type="password"], input[placeholder*="رمز"], '
                    'input[placeholder*="گذرواژه"]'
                ).first
                await pwd.wait_for(state="visible", timeout=15000)
                await pwd.click()
                await pwd.fill(password)

                # ارسال
                try:
                    btn = root.get_by_role("button", name=re.compile(r"(ورود|login|sign ?in|ورود به حساب)", re.I))
                    await btn.click(timeout=4000)
                except Exception:
                    await root.locator('button[type="submit"], input[type="submit"]').first.click(timeout=4000)

            # 1) سعی مستقیم
            try:
                await fill_form(page)
            except Exception:
                # 2) اگر داخل iframe باشد
                filled = False
                for f in page.frames:
                    try:
                        await fill_form(f)
                        filled = True
                        break
                    except Exception:
                        continue
                if not filled:
                    await page.screenshot(path="login_error.png", full_page=True)
                    raise RuntimeError("Login form not found (after challenge).")

            # منتظر لاگین و نشانه‌های آن
            try:
                await page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass

            # تأیید لاگین: وجود لینک خروج یا منوی کاربر
            if not await _iranpaper_is_logged_in(page):
                await page.screenshot(path="login_error.png", full_page=True)
                raise RuntimeError("Login not confirmed (no logout/user markers).")

            try:
                await page.context.storage_state(path="session_iranpaper.json")
            except Exception:
                pass
            print("[+] Logged into IranPaper successfully!")

        except Exception as e:
            print(f"💥 خطای جدی در لاگین ایران‌پیپر: {e}")
            try:
                await page.screenshot(path="login_error.png", full_page=True)
                print("📸 اسکرین‌شات از خطا در فایل login_error.png ذخیره شد.")
            except Exception:
                pass
            raise

    async def download_by_doi(self, doi: str) -> str:
        """
        شبیه‌ساز دانلود (اگر جایی در تست‌ها لازم باشد).
        """
        await asyncio.sleep(1)
        fake_path = os.path.join(self.download_dir, f"{doi.replace('/', '_')}.pdf")
        with open(fake_path, "wb") as f:
            f.write(b"%PDF-1.4\n%Fake PDF content\n%%EOF")
        print(f"[IranPaper] Simulated download complete: {fake_path}")
        return fake_path

    async def periodic_relogin(self, page: Page, notify: Optional[NotifyFn] = None):
        """
        لاگین دوره‌ای با استفاده از credentialهای شیء (نه ENV).
        """
        while True:
            wait_time = random.randint(4 * 3600, 6 * 3600)
            logger.info(f"🕒 ورود مجدد بعد از {wait_time // 3600} ساعت.")
            await asyncio.sleep(wait_time)

            try:
                logger.info("🔄 شروع فرآیند خروج و ورود مجدد به IranPaper...")
                await page.goto("https://iranpaper.ir/logout", timeout=30000)
                await asyncio.sleep(3)

                await page.goto("https://iranpaper.ir/login", timeout=30000)
                await page.fill('input[name="email"]', self.username)
                await page.fill('input[name="password"]', self.password)
                await page.click('button[type="submit"]')
                await page.wait_for_load_state("networkidle")

                # تأیید لاگین
                if not await _iranpaper_is_logged_in(page):
                    raise RuntimeError("Re-login verification failed")

                logger.info("✅ ورود مجدد به IranPaper با موفقیت انجام شد.")

                if notify is not None:
                    await notify(
                        doi="N/A",
                        title="🔄 IranPaper relogin",
                        year="",
                        journal="System",
                        abstract="ورود مجدد خودکار به IranPaper انجام شد."
                    )
            except Exception as e:
                logger.error(f"❌ خطا در ورود مجدد به IranPaper: {e}", exc_info=True)
                # گزینهٔ ساده: کمی صبر کن و یک تلاش دیگر بکن
                try:
                    await asyncio.sleep(random.randint(30, 90))
                    logger.info("🔁 تلاش مجدد برای ورود دوره‌ای...")
                    await page.goto("https://iranpaper.ir/login", timeout=30000)
                    await page.fill('input[name="email"]', self.username)
                    await page.fill('input[name="password"]', self.password)
                    await page.click('button[type="submit"]')
                    await page.wait_for_load_state("networkidle")
                    if not await _iranpaper_is_logged_in(page):
                        raise RuntimeError("Re-login retry failed")
                    logger.info("✅ ورود مجدد (دفعهٔ دوم) موفق بود.")
                except Exception:
                    logger.error("⛔️ ورود مجدد (دفعهٔ دوم) هم شکست خورد.", exc_info=True)


async def _iranpaper_is_logged_in(page: Page) -> bool:
    """
    اگر لاگین باشیم، یکی از این نشانه‌ها در هدر دیده می‌شود:
      - لینک/دکمه «خروج»
    (واکنش به نام کاربری خاص حذف شد تا پایدار بماند)
    """
    markers = [
        'a[href*="logout"]',
        'a:has-text("خروج")',
        'button:has-text("خروج")',
    ]
    for sel in markers:
        try:
            if await page.locator(sel).count() > 0:
                return True
        except Exception:
            pass
    return False


async def iranpaper_login(page: Page, username: Optional[str] = None, password: Optional[str] = None) -> None:
    """
    Thin-wrapper برای سازگاری با کدهای قبلی.
    اگر username/password داده نشود، از ENV می‌خواند؛
    سپس از متد IranPaperClient.login استفاده می‌کند.
    """
    if username is None:
        username = os.getenv("IRANPAPER_USER", "")
    if password is None:
        password = os.getenv("IRANPAPER_PASS", "")

    ipc = IranPaperClient(username, password, download_dir=str(DOWNLOAD_DIR))
    await ipc.login(page)


async def iranpaper_download(page: Page, doi: str, download_dir: str = str(DOWNLOAD_DIR)) -> str:
    """
    سرچ DOI در ایران‌پیپر با سلکتورهای مقاوم:
      - اگر باکس جستجو نیامد، روی «لینک مقاله با DOI» کلیک می‌کنیم
      - هم input و هم textarea پوشش داده می‌شوند
      - اگر دکمه‌ی جستجو پیدا نشد، Enter می‌زنیم
      - سپس روی «دانلود فایل» دانلود مستقیم یا پاپ‌آپ را هندل می‌کنیم
    """
    import os as _os
    from pathlib import Path as _Path
    from urllib.parse import urljoin

    doi = doi.strip()
    print(f"[+] Searching DOI on IranPaper: {doi}")

    await page.goto("https://iranpaper.ir", timeout=60000, wait_until="load")

    # 1) اگر باکس جستجو آماده نبود، روی «لینک مقاله با DOI» کلیک کن
    try:
        # گاهی این آیکون باید فعال شود تا باکس زیری برای DOI در فوکوس قرار گیرد
        tile = page.locator('button:has-text("لینک\u200cمقاله با DOI"), button:has-text("لینک مقاله با DOI")')
        if await tile.count() > 0 and await tile.first.is_visible():
            await tile.first.click(timeout=2000)
    except Exception:
        pass

    # 2) تکست‌باکس را با سلکتورهای جایگزین پیدا کن (role/name/placeholder و هر دو input/textarea)
    search_locators = [
        # role-based (مطمئن‌تر)
        lambda p: p.get_by_role("textbox", name=re.compile(r"لینک.*شناسه\s*DOI", re.S)),
        # aria-label فارسی (input یا textarea)
        lambda p: p.locator('input[aria-label*="شناسه DOI"], textarea[aria-label*="شناسه DOI"]'),
        # placeholder فارسی
        lambda p: p.locator('input[placeholder*="شناسه DOI"], textarea[placeholder*="شناسه DOI"]'),
        # fallback عمومی‌تر
        lambda p: p.locator('input[type="text"], textarea').first,
    ]

    box = None
    for maker in search_locators:
        try:
            cand = maker(page)
            await cand.wait_for(state="visible", timeout=4000)
            box = cand
            break
        except Exception:
            continue
    if box is None:
        # برای دیباگ: اسکرین‌شات بگیر و خطا بده
        await page.screenshot(path="iranpaper_no_searchbox.png", full_page=True)
        raise RuntimeError("Search box for DOI not found on IranPaper (selectors outdated).")

    # 3) DOI را وارد کن و جستجو را بزن
    await box.click()
    await box.fill(doi)

    # دکمه جستجو (چند احتمال)
    search_btns = [
        lambda p: p.locator(".d-inline.pa-3").first,
        lambda p: p.get_by_role("button", name=re.compile(r"(جستجو|search)", re.I)),
        lambda p: p.locator('button[type="submit"]').first,
    ]
    clicked = False
    for maker in search_btns:
        try:
            btn = maker(page)
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click(timeout=1500)
                clicked = True
                break
        except Exception:
            continue
    if not clicked:
        # اگر دکمه پیدا نشد، Enter بزن
        try:
            await box.press("Enter")
            clicked = True
        except Exception:
            pass

    # 4) انتظار برای دکمه «دانلود فایل»
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_selector('button:has-text("دانلود فایل"), a:has-text("دانلود فایل")', timeout=60000)
    btn = page.locator('button:has-text("دانلود فایل"), a:has-text("دانلود فایل")').first

    # 5) کلیک و RACE بین دانلود و پاپ‌آپ — فقط یک کلیک (DOM)، نه دو تا!
    ctx = page.context
    pre_pages = set(ctx.pages)

    # دو سناریو را همزمان رصد می‌کنیم
    dl_task = asyncio.create_task(ctx.wait_for_event("download", timeout=40000))
    pop_task = asyncio.create_task(ctx.wait_for_event("page", timeout=40000))

    # ❗️مهم: فقط همین یک‌بار کلیک می‌کنیم تا دابل‌تب اتفاق نیافتد
    await btn.evaluate("""
    (el) => {
      el.style.pointerEvents = 'none';
      setTimeout(() => { el.style.pointerEvents = ''; }, 1500);
      el.click();
    }
    """)

    done, pending = await asyncio.wait({dl_task, pop_task}, return_when=asyncio.FIRST_COMPLETED, timeout=45)

    # --- حالت A: دانلود مستقیم در هر تب/کانتکست ---
    if dl_task in done:
        download = await dl_task
        safe = doi.replace("/", "_").replace(":", "_")
        out = _Path(download_dir) / f"{safe}.pdf"
        await download.save_as(out)

        # تب‌های جدیدی که با کلیک باز شده‌اند را ببند تا شلوغ نشود
        new_pages = [p for p in ctx.pages if p not in pre_pages]
        for p in new_pages:
            try:
                await p.close()
            except Exception:
                pass

        for t in pending:
            t.cancel()
        print(f"[+] Article downloaded (context-level): {out}")
        return str(out)

    # --- حالت B: پاپ‌آپ/ویوِر باز شده است ---
    popup = await pop_task
    for t in pending:
        t.cancel()

    # اگر بیش از یک تب باز شده، فقط تبِ «viewer/PDF» را نگه داریم
    await asyncio.sleep(0.6)  # کمی فرصت برای گرفتن url/title
    new_pages = [p for p in ctx.pages if p not in pre_pages]
    if len(new_pages) > 1:
        keep = None
        for p in new_pages:
            try:
                u = (p.url or "").lower()
                t = (await p.title() or "").lower()
                if u.endswith(".pdf") or "viewer" in u or "pdf" in u or "pdf" in t:
                    keep = p
                    break
            except Exception:
                pass
        if keep is None:
            keep = popup
        for p in new_pages:
            if p is not keep:
                try:
                    await p.close()
                except Exception:
                    pass
        popup = keep

    await popup.wait_for_load_state("domcontentloaded")

    # 6) تلاش برای دکمه‌ی Download داخل ویوِر (pdf.js و مشابه)
    async def try_viewer_button() -> Optional[str]:
        selectors = [
            'button.gsr-flat-btn[aria-label="Download"]',
            'button[aria-label="Download"]',
            '#download',
            'a[download]'
        ]
        for sel in selectors:
            try:
                loc = popup.locator(sel).first
                if await loc.count() == 0 or not await loc.is_visible():
                    continue

                # ریس دوباره: یا دانلود می‌آید یا href داریم
                dl_f = asyncio.create_task(ctx.wait_for_event("download", timeout=20000))
                try:
                    await loc.click()
                except Exception:
                    pass

                try:
                    dld = await dl_f
                    safe = doi.replace("/", "_").replace(":", "_")
                    out = _Path(download_dir) / f"{safe}.pdf"
                    await dld.save_as(out)
                    await popup.close()
                    return str(out)
                except Exception:
                    # اگر دانلود نیامد، شاید href داشته باشد
                    try:
                        href = await loc.get_attribute("href")
                        if href:
                            return await download_via_http(href)
                    except Exception:
                        pass
            except Exception:
                continue
        return None

    # Helperها
    def _dispo_name(headers: dict) -> Optional[str]:
        cd = (headers or {}).get("content-disposition") or (headers or {}).get("Content-Disposition") or ""
        m = re.search(r'filename\*?=(?:UTF-8\'\'|\"?)([^\";]+)\"?', cd)
        return m.group(1) if m else None

    async def download_via_http(pdf_url: str) -> str:
        pdf_url = urljoin(popup.url, pdf_url)
        resp = await popup.context.request.get(pdf_url, headers={"Referer": popup.url})
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} for {pdf_url}")
        name = _dispo_name(resp.headers) or (pdf_url.split("/")[-1] or "file.pdf")
        if not name.lower().endswith(".pdf"):
            name += ".pdf"
        name = re.sub(r'[\\/:*?"<>|]+', "_", name)
        out = _Path(download_dir) / name
        out.write_bytes(await resp.body())
        await popup.close()
        return str(out)

    # 6-a) دکمه‌ی داخل ویوِر
    got = await try_viewer_button()
    if got:
        return got

    # 6-b) iframe/embed → src را بگیر و HTTP دانلود کن
    try:
        await popup.wait_for_selector("embed[src], iframe[src]", timeout=15000)
        src = await popup.locator("embed[src], iframe[src]").first.get_attribute("src")
        if src:
            return await download_via_http(src)
    except Exception:
        pass

    # 6-c) اگر popup خودش مستقیم PDF بود یا در URL مشخص است
    try:
        if popup.url.lower().endswith(".pdf"):
            return await download_via_http(popup.url)
    except Exception:
        pass

    # 6-d) آخرین تلاش: کمی صبر و اگر باز هم نشد، اسکرین‌شات برای دیباگ
    await popup.screenshot(path=f"iranpaper_viewer_error_{doi.replace('/', '_')}.png", full_page=True)
    raise RuntimeError("Could not obtain PDF from viewer or context download.")
