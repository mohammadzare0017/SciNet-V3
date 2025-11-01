# scinet_bot_fast.py


from __future__ import annotations
import asyncio, json, logging, os, sys, functools, time, html, random
from dataclasses import dataclass, field, asdict
from pathlib import Path
from textwrap import dedent
from typing import Dict, Any, Optional
from urllib.parse import urljoin, quote
from src.downloader.iranpaper import iranpaper_download

from src.worker import WorkerPool
import uuid
sys.path.append(os.path.dirname(__file__))

import base64, re

from src.downloader.gigalib import gigalib_login, gigalib_download
from src.utils.stealth import human_sleep, human_type
from src.downloader.iranpaper import iranpaper_login, IranPaperClient
from src.pdf_cleaner import clean_pdf_watermarks_async


import aiohttp
from dotenv import load_dotenv
from playwright.async_api import async_playwright, Page
from pythonjsonlogger import jsonlogger
from logging.handlers import RotatingFileHandler
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    AIORateLimiter, Application, CallbackQueryHandler,
    CommandHandler, ContextTypes
)
load_dotenv()
from src.config.download_policy import get_policy
POLICY = get_policy()
DRY_RUN = POLICY.dry_run

# ── متغیرهای محیطی ──────────────────────────────────────────

DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "./downloads"))
SCINET_URL   = "https://sci-net.xyz/"
STATE_FILE   = Path(os.getenv("SCINET_STATE_FILE", "state.json"))

TG_TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT      = int(os.getenv("SCINET_GROUP_CHAT_ID", "0"))
OWNER_ID     = int(os.getenv("OWNER_ID", "0"))
OWNER_ID2    = int(os.getenv("OWNER_ID2", "0"))

SCINET_USER  = os.getenv("SCINET_USERNAME")
SCINET_PASS  = os.getenv("SCINET_PASSWORD")

IRANPAPER_USER = os.getenv("IRANPAPER_USER")  
IRANPAPER_PASS = os.getenv("IRANPAPER_PASS")

HEADFUL      = os.getenv("HEADFUL", "0") == "1"     




required = [TG_TOKEN, TG_CHAT, SCINET_USER, SCINET_PASS]
if not DRY_RUN:
    required += [IRANPAPER_USER, IRANPAPER_PASS]


if not all(required):
    raise RuntimeError(
        "⚠️ .env ناقص است: حداقل TG_TOKEN, SCINET_GROUP_CHAT_ID, SCINET_USERNAME, SCINET_PASSWORD لازم‌اند؛ "
        "در حالت غیر DRY، IRANPAPER_USER/IRANPAPER_PASS هم اجباری است."
    )

KEEP_LOCAL_PDFS = os.getenv("KEEP_LOCAL_PDFS", "0") == "1"
_cout = os.getenv("CLEAN_OUTPUT_DIR", "").strip()
CLEAN_OUTPUT_DIR = Path(_cout).resolve() if _cout else None
if CLEAN_OUTPUT_DIR:
    CLEAN_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── لاگ‌ها ──────────────────────────────────────────────────
DEBUG_MODE = os.getenv("DEBUG", "0") == "1"

logger = logging.getLogger("scinet_fast")
logger.setLevel(logging.DEBUG if DEBUG_MODE else logging.INFO)

# کنسول
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(jsonlogger.JsonFormatter("%(levelname)s %(message)s"))
logger.addHandler(stream_handler)

# فایل اصلی: INFO+
file_handler = RotatingFileHandler(
    "scinet_fast.log", maxBytes=2_000_000, backupCount=3
)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(
    jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(message)s")
)
logger.addHandler(file_handler)

# فایل DEBUG جدا
if DEBUG_MODE:
    dbg_file = RotatingFileHandler(
        "scinet_fast.debug.log", maxBytes=2_000_000, backupCount=3
    )
    dbg_file.setLevel(logging.DEBUG)
    dbg_file.setFormatter(
        jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(message)s")
    )
    logger.addHandler(dbg_file)

# ── دکوراتور ردگیری ────────────────────────────────────────
def dbg(fn):
    if not DEBUG_MODE:
        return fn
    if asyncio.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def _wrap(*a, **kw):
            t0 = time.perf_counter()
            logger.debug("▶️ %s args=%s kw=%s", fn.__name__, a[1:], kw)
            try:
                res = await fn(*a, **kw)
                logger.debug("⏹ %s duration=%.3fs", fn.__name__, time.perf_counter()-t0)
                return res
            except Exception:
                logger.exception(" %s failed", fn.__name__)
                raise
        return _wrap
    else:
        @functools.wraps(fn)
        def _wrap(*a, **kw):
            t0 = time.perf_counter()
            logger.debug("▶️ %s args=%s kw=%s", fn.__name__, a[1:], kw)
            try:
                res = fn(*a, **kw)
                logger.debug("⏹ %s duration=%.3fs", fn.__name__, time.perf_counter()-t0)
                return res
            except Exception:
                logger.exception("💥 %s failed", fn.__name__)
                raise
        return _wrap

# ── مجوز مالک ──────────────────────────────────────────────
def is_owner(uid:int)->bool:
    return OWNER_ID==0 or uid==OWNER_ID or uid==OWNER_ID2

# ── State ───────────────────────────────────────────────────
@dataclass(slots=True)
class BotState:
    skip: list[str] = field(default_factory=list)
    active: Optional[str] = None
    initialized: bool = False
    enabled: bool = True
    def save(self): STATE_FILE.write_text(json.dumps(asdict(self), ensure_ascii=False))
    @classmethod
    def load(cls):
        try:
            return cls(**json.loads(STATE_FILE.read_text())) if STATE_FILE.exists() else cls()
        except Exception:
            return cls()
state = BotState.load()

# ── متادیتا Crossref/OpenAlex ───────────────────────────────
@dbg
async def xref(sess: aiohttp.ClientSession, doi: str):
    try:
        async with sess.get(f"https://api.crossref.org/works/{doi}", timeout=8) as r:
            # روش A: فقط 2xx
            if not (200 <= r.status < 300):
                return "", "", None, "", ""
            m = (await r.json())["message"]
            return (
                m.get("title", [""])[0],
                m.get("container-title", [""])[0],
                (m.get("issued", {}).get("date-parts", [[None]])[0][0]),
                m.get("abstract", ""),
                m.get("type", "")
            )
    except Exception as e:
        logger.debug("⚠️ Crossref error %s", e)
        return "", "", None, "", ""

@dbg
async def oalex(sess: aiohttp.ClientSession, doi: str):
    try:
        async with sess.get(f"https://api.openalex.org/works/doi:{doi}", timeout=8) as r:
            if not (200 <= r.status < 300):
                return "", "", None, "", ""
            m = await r.json()
            return (
                m.get("title", ""),
                m.get("primary_location", {}).get("source", {}).get("display_name", ""),
                m.get("publication_year"),
                m.get("abstract_inverted_index", ""),
                m.get("type", "")
            )
    except Exception as e:
        logger.debug("⚠️ OpenAlex error %s", e)
        return "", "", None, "", ""

def _openalex_abs_to_text(inv):
    """abstract_inverted_index را به متن خوانا تبدیل می‌کند."""
    if not isinstance(inv, dict):
        return inv or ""
    # طول را از بیشترین اندیس‌ها استنباط می‌کنیم
    size = max((max(pos_list) for pos_list in inv.values() if pos_list), default=-1) + 1
    arr = [""] * max(size, 0)
    for word, positions in inv.items():
        for i in positions:
            if 0 <= i < len(arr):
                arr[i] = word
    return " ".join(w for w in arr if w)
@dbg

async def metadata(doi:str) -> Dict[str, Any]:
    async with aiohttp.ClientSession(headers={"User-Agent":"doi-bot/fast"}) as s:
        cr, oa = await asyncio.gather(xref(s, doi), oalex(s, doi))

    abs_cr = cr[3] if cr and len(cr) >= 4 else ""
    abs_oa_raw = oa[3] if oa and len(oa) >= 4 else ""
    abs_oa = _openalex_abs_to_text(abs_oa_raw)

    type_val = (cr[4] or oa[4]) if (cr and oa) else (cr[4] if cr else (oa[4] if oa else ""))

    return {
        "title":   (cr[0] or oa[0] or "—"),
        "journal": (cr[1] or oa[1]),
        "year":    (cr[2] or oa[2]),
        "abstract": (abs_cr or abs_oa or ""),
        "type":    (type_val or ""),
    }


class SciNetClient:
    def __init__(self):
        self.page: Page | None = None
        self._pw = None
        self._browser = None
        self._cdp = None
        self._seen_ids: set[str] = set()
        self._seen_dois: set[str] = set()
        self._doi_re = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.I)
        self._keepalive_task: asyncio.Task | None = None
        # لاگ تشخیص‌ها را حتی در حالت غیر DEBUG هم می‌توان روشن گذاشت
        self._detect_log_enabled = os.getenv("DETECT_LOG", "1") == "1"

    def _log_detect(self, *, src: str, url: str | None = None,
                    doi: str | None = None, note: str | None = None,
                    preview: str | None = None):
        """لاگ ساختاریافته برای تشخیص DOI از منابع مختلف (CDP/Ws/Observer/Request/Response)."""
        if not self._detect_log_enabled:
            return
        try:
            msg = {
                "event": "DETECT",
                "src": src,
                "doi": doi or "",
                "url": url or "",
                "note": note or "",
                "preview": (preview[:300] + "…") if (preview and len(preview) > 300) else (preview or "")
            }
            logger.info(json.dumps(msg, ensure_ascii=False))
        except Exception:
            logger.info("[DETECT] src=%s doi=%s url=%s note=%s",
                        src, doi or "-", url or "-", note or "-")

    # --- startup ---------------------------------------------------------
    @dbg
    async def start(self):
        self._pw = await async_playwright().start()
        await self._launch_browser()

    @dbg
    async def _launch_browser(self):
        ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/121.0.0.0 Safari/537.36")

        self._browser = await self._pw.chromium.launch(
            headless=not HEADFUL,
            args=["--disable-extensions"] + ([] if HEADFUL else ["--disable-gpu"])
        )
        session_file = Path("session_giga_iran.json")
        ctx_kwargs = dict(user_agent=ua, bypass_csp=True)
        if session_file.exists():
            ctx_kwargs["storage_state"] = str(session_file)

        ctx = await self._browser.new_context(**ctx_kwargs)

        if DEBUG_MODE:
            ctx.on("console", lambda m: logger.debug(" JS: %s", m.text))

            def _req_failed(r):
                try:
                    fr = r.failure or {}
                    if isinstance(fr, dict):
                        err = fr.get("errorText") or fr.get("error_text") or fr
                    else:
                        err = getattr(fr, "errorText", None) or getattr(fr, "error_text", None) or fr or "unknown"
                    logger.debug(" FAIL %s %s | err=%s", r.method, r.url, err)
                except Exception:
                    logger.debug(" FAIL %s %s", r.method, r.url)

            ctx.on("requestfailed", _req_failed)


       


        self.page = await ctx.new_page()
        self.page.on("crash", lambda *_: asyncio.create_task(self._recover("page crash")))
        self.page.on("close", lambda *_: asyncio.create_task(self._recover("page closed")))
        await self.page.expose_function("__notify_py", self._notify_py)

        try:
            await self._login()

            # شنود فوق‌سریع شبکه با CDP (میلی‌ثانیه‌ای)
            await self._enable_ultrafast_request_listener()

            # فالو‌بک: هوک سمت کلاینت (رویداد داخلی و wrap کردن arequest)
            await self._inject_observer()

            self._start_keepalive()

            # فقط وقتی DRY روشنه، لاگر DRY را فعال کن
            if DRY_RUN:
                await self._enable_request_dryrun()

            logger.info("Playwright ready | headful=%s", HEADFUL)
        except Exception:
            logger.exception("Playwright startup failed")
            await self._recover("startup error")

    @dbg
    async def _enable_request_dryrun(self):
        """
        DRY-RUN: فقط پاسخ‌های /request را پایش و گزارش می‌کند؛
        هیچ رزروی انجام نمی‌دهد.
        """
        p = self.page; assert p

        async def on_response(resp):
            try:
                url = resp.url or ""
                if "/request" not in url:
                    return

                method = resp.request.method
                status = resp.status
                headers = resp.headers or {}
                ctype = headers.get("content-type", "")

                doi = title = _id = created = ""
                reward = None
                body_preview = ""

                if "application/json" in ctype.lower():
                    try:
                        data = await resp.json()
                    except Exception:
                        try:
                            txt = await resp.text()
                            body_preview = (txt[:300] + "...") if len(txt) > 300 else txt
                        except Exception:
                            body_preview = ""
                    else:
                        node = (data.get("success") or {}).get("data") or data.get("data") or {}
                        doi     = (node.get("doi") or "").strip()
                        _id     = (node.get("_id") or "")
                        created = (node.get("createdAt") or "")
                        title   = (node.get("title") or "")[:120]
                        reward  = (node.get("request") or {}).get("reward")
                        body_preview = json.dumps(
                            {"doi": doi, "_id": _id, "reward": reward, "createdAt": created, "title": title},
                            ensure_ascii=False
                        )

                logger.info("DRYRUN /request | %s %s -> %s | %s", method, url, status, body_preview)

                if 'bot_app' in globals() and getattr(bot_app, 'bot', None) is not None \
                and os.getenv("DRYRUN_TG", "1") == "1":
                    parts = [
                        "👀 <b>DRY-RUN: درخواست جدید شناسایی شد</b>",
                        f"<b>URL:</b> {html.escape(url)}",
                        f"<b>Method:</b> {method}  <b>Status:</b> {status}",
                    ]
                    if doi:     parts.append(f"<b>DOI:</b> <code>{html.escape(doi)}</code>")
                    if title:   parts.append(f"<b>Title:</b> {html.escape(title)}")
                    if reward not in ("", None): parts.append(f"<b>Reward:</b> {reward}")
                    if _id:     parts.append(f"<b>ID:</b> <code>{html.escape(str(_id))}</code>")
                    if created: parts.append(f"<b>CreatedAt:</b> {html.escape(str(created))}")
                    if not doi and body_preview:
                        parts.append(f"<b>Preview:</b> {html.escape(body_preview[:200])}")

                    await bot_app.bot.send_message(
                        TG_CHAT, "\n".join(parts),
                        parse_mode="HTML", disable_web_page_preview=True
                    )

            except Exception:
                logger.exception("dryrun listener failed")

        p.on("response", lambda r: asyncio.create_task(on_response(r)))

    # --- recovery --------------------------------------------------------
    @dbg
    async def _recover(self, reason: str):
        logger.warning("🚑 Browser recovery: %s", reason)
        self._cancel_keepalive()
        try:
            if self.page and not self.page.is_closed():
                await self.page.context.close()
            if self._browser:
                await self._browser.close()
        except Exception:
            pass
        delay = 5
        while True:
            try:
                await asyncio.sleep(delay)
                # ریستِ دیده‌ها تا تداخل نشه
                self._seen_ids.clear()
                self._seen_dois.clear()
                await self._launch_browser()
                break
            except Exception:
                logger.exception("Recovery retry failed; next in %ds", delay)
                delay = min(delay * 2, 60)

    # --- internals -------------------------------------------------------
    @dbg
    async def _login(self):
        p = self.page; assert p
        await p.goto(SCINET_URL)
        if await p.locator('input[name="user"]').count() > 0:
            await p.fill('input[name="user"]', SCINET_USER)
            await p.fill('input[name="pass"]', SCINET_PASS)
            await p.press("form", "Enter")

        await p.wait_for_selector(".requests", timeout=30_000)

    @dbg
    async def _enable_ultrafast_request_listener(self):
        """
        شنود CDP با هماهنگ‌سازی responseReceived + loadingFinished.
        فقط روی /request و /requests کار می‌کند؛ هیچ regex عمومی روی HTML اجرا نمی‌شود.
        """
        import asyncio, json, base64, time as _t
        p = self.page; assert p
        self._cdp = await p.context.new_cdp_session(p)
        await self._cdp.send("Network.enable", {})

        dry = DRY_RUN
        self._pending: dict[str, dict] = {}

        async def _process_body(request_id: str, url: str, body_text: str):
            try:
                if "/request" not in url and "/requests" not in url:
                    return

                if "/requests" in url:
                    try:
                        data = json.loads(body_text) or {}
                        docs = data.get("docs") or []
                        if isinstance(docs, list):
                            for doc in docs:
                                await self._handle_new_request_payload(doc, dry=dry, is_doc=True)
                    except Exception:
                        pass
                    return

                if "/request" in url:
                    try:
                        data = json.loads(body_text) or {}
                        node = (data.get("success") or {}).get("data") or data.get("data")
                        if isinstance(node, dict):
                            await self._handle_new_request_payload(node, dry=dry, is_doc=False)
                    except Exception:
                        pass
            except Exception:
                if DEBUG_MODE:
                    logger.exception("cdp _process_body failed")

        async def _stage_on_response(params: dict):
            try:
                resp = params.get("response") or {}
                url  = resp.get("url") or ""
                rid  = params.get("requestId")
                if not rid or ("/request" not in url and "/requests" not in url):
                    return
                self._pending[rid] = {"url": url, "ts": _t.time()}
            except Exception:
                if DEBUG_MODE:
                    logger.exception("ultrafast listener on_response staging failed")

        async def _on_loading_finished(params: dict):
            rid = params.get("requestId")
            entry = self._pending.pop(rid, None)
            if not entry:
                return
            url = entry["url"]
            try:
                body_res = await self._cdp.send("Network.getResponseBody", {"requestId": rid})
                body = body_res.get("body") or ""
                if body_res.get("base64Encoded"):
                    try:
                        body = base64.b64decode(body).decode("utf-8", "ignore")
                    except Exception:
                        body = ""
                if body:
                    await _process_body(rid, url, body)
            except Exception as e:
                # خطاهای «resource not found / no data» رو ساکت رد کن
                msg = str(e).lower()
                if "no resource with given identifier" in msg or "no data found" in msg:
                    return
                if DEBUG_MODE:
                    logger.exception("ultrafast listener getResponseBody failed")

        # اتصال رویدادهای CDP
        self._cdp.on("Network.responseReceived", lambda p: asyncio.create_task(_stage_on_response(p)))
        self._cdp.on("Network.loadingFinished", lambda p: asyncio.create_task(_on_loading_finished(p)))

        # فالو‌بک Playwright برای وقتی CDP چیزی نده
        async def _pw_on_response(resp):
            try:
                url = resp.url or ""
                if "/request" not in url and "/requests" not in url:
                    return
                ctype = (resp.headers or {}).get("content-type", "")
                if "application/json" not in ctype.lower():
                    return
                txt = await resp.text()
                await _process_body("pw", url, txt)
            except Exception:
                if DEBUG_MODE:
                    logger.exception("fallback page.on(response) failed")

        p.on("response", lambda r: asyncio.create_task(_pw_on_response(r)))

    @dbg
    async def _inject_observer(self):
        """
        Hook سمت کلاینت: رویداد داخلی سایت و wrap کردن arequest
        (fallback سریع‌تر از MutationObserver).
        """
        p = self.page; assert p
        dry = DRY_RUN
        skip_json = json.dumps(state.skip, ensure_ascii=False)
        enabled_js = "true" if state.enabled else "false"

        js = dedent(f"""
        (() => {{
          const DRY = {str(dry).lower()};
          window.skipSet = new Set({skip_json});
          window.busy = false;
          window.enabled = {enabled_js};

          const seenDois = new Set();

          function doiFrom(doc) {{
            let d = (doc && (doc.doi || doc.DOI || doc.id)) || "";
            return (typeof d === 'string') ? d.trim() : "";
          }}

          function precheckTitle(title) {{
            const t = String(title || "");
            const words = t.trim().split(/\\s+/).filter(Boolean);
            if (words.length > 0 && words.length < 5) return "short_title_pre";
            // "book" یا "ebook/e-book" به صورت کلمه‌ی مستقل (notebook را نمی‌گیرد)
            if (/\\b(?:e-?book|book)\\b/i.test(t)) return "book_in_title_pre";
            return null; // اوکی
          }}

          async function handleDoc(doc) {{
            const doi = doiFrom(doc);
            if (!doi) return;
            if (seenDois.has(doi) || window.skipSet.has(doi)) return;
            seenDois.add(doi);

            const request = (doc && doc.request) || {{}};
            const payload = {{
              doi,
              detail: (doc && (doc.detail || doc.url)) || ("/" + doi),
              requester: (request && request.from) || "",
              reward: String((request && request.reward) ?? "")
            }};

            if (!window.enabled) return;

            // DRY: فقط اعلان
            if (DRY) {{
              try {{
                await window.__notify_py(Object.assign({{}}, payload, {{__src:"js_observer"}}));
              }} catch (e) {{}}
              return;
            }}

            // ⬇️ پیش‌سنجی قبل از take فقط بر اساس عنوان
            const title = String((doc && (doc.title || doc.Title)) || "");
            const reason = precheckTitle(title);
            if (reason) {{
              try {{ await window.__notify_py({{ doi, reason }}); }} catch (e) {{}}
              return; // رد شد: take نزن
            }}

            if (window.busy) return;
            window.busy = true;
            try {{
              const res = await fetch('/take/' + encodeURIComponent(doi), {{
                method: 'GET', credentials: 'include', redirect: 'manual'
              }});
              const final = new URL(res.url, location.href);
              const ok = res.redirected || res.ok ||
                         final.pathname.startsWith('/work/') || final.pathname.startsWith('/requests/');
              if (ok) {{
                window.skipSet.add(doi);
                try {{
                  await window.__notify_py(Object.assign({{}}, payload, {{__src:"js_observer"}}));
                }} catch (e) {{}}
              }} else {{
                window.busy = false;
                try {{ await window.__notify_py({{ doi, reason: "competitor_won" }}); }} catch (e) {{}}

              }}
            }} catch (e) {{
              window.busy = false;
            }}
          }}

          // 1) Hook به events.request
          try {{
            window.events ||= {{}};
            window.events.request ||= [];
            const _push = window.events.request.push.bind(window.events.request);
            if (!window.__scinet_req_listener_installed) {{
              _push(handleDoc);
              window.__scinet_req_listener_installed = true;
              try {{ console.debug("observer: events.request hook installed"); }} catch(e){{}}
            }}
            window.events.request.push = function(fn) {{ return _push(fn); }};
          }} catch (e) {{}}

          // 2) Wrap arequest('requests', ...)
          try {{
            const _arequest = window.arequest && window.arequest.bind(window);
            if (_arequest && !window.__scinet_arequest_wrapped) {{
              window.arequest = function(endpoint, cb, params) {{
                return _arequest(endpoint, function(resp) {{
                  try {{
                    if (endpoint === 'requests' && resp && Array.isArray(resp.docs)) {{
                      try {{ console.debug("observer: arequest('requests') intercepted, docs=", resp.docs.length); }} catch(e){{}}
                      for (const d of resp.docs) handleDoc(d);
                    }}
                  }} catch (e) {{}}
                  return cb && cb(resp);
                }}, params);
              }};
              window.__scinet_arequest_wrapped = true;
              try {{ console.debug("observer: arequest wrapper installed"); }} catch(e){{}}
            }}
          }} catch (e) {{}}

          window.__observerAlive = true;
          try {{ console.debug("observer: injected and alive"); }} catch(e){{}}
        }})();
        """)
        await p.add_init_script(js)
        await p.goto(SCINET_URL)

    def _cancel_keepalive(self):
        """تسکِ پینگ را اگر در حال اجراست، متوقف می‌کند."""
        try:
            if self._keepalive_task and not self._keepalive_task.done():
                self._keepalive_task.cancel()
        except Exception:
            pass
        self._keepalive_task = None

    def _start_keepalive(self):
        """یک تسکِ پس‌زمینه برای پینگ دوره‌ای راه می‌اندازد (اگر قبلاً نبود)."""
        self._cancel_keepalive()
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    @dbg
    async def _keepalive_loop(self):
        """
        هر ~20 تا ~40 ثانیه، یک درخواست سبک به سایت می‌زند تا اتصال گرم بماند.
        وقتی busy باشد یا صفحه بسته باشد، کاری نمی‌کند/خارج می‌شود.
        """
        while True:
            try:
                p = self.page
                if not p or p.is_closed():
                    return

                # اگر ربات غیرفعال/یا مشغول است، پینگ نزن
                enabled = await p.evaluate("typeof window.enabled === 'undefined' ? true : Boolean(window.enabled)")
                busy    = await p.evaluate("Boolean(window.busy)")
                if enabled and not busy:
                    # از همان کانتکستِ تب، یک fetch سبک بزن (کوکی‌ها همان سشن هستند)
                    await p.evaluate("""
                        async () => {
                            const doFetch = async (url, opts) => {
                                const ctl = new AbortController();
                                const t = setTimeout(() => ctl.abort(), 800);
                                try { return await fetch(url, { ...opts, signal: ctl.signal }); }
                                finally { clearTimeout(t); }
                            };
                            try {
                                // اول HEAD /
                                const r = await doFetch('/', { method: 'HEAD', credentials: 'include' });
                                if (r && r.ok) return 1;
                            } catch (e) {}
                            try {
                                // بعد GET /favicon.ico با cache bust سبک
                                const r2 = await doFetch('/favicon.ico', { method: 'GET', credentials: 'include', cache: 'no-store' });
                                return r2 && r2.ok ? 1 : 0;
                            } catch (e2) { return 0; }
                        }
                    """)
            except asyncio.CancelledError:
                return
            except Exception as e:
                if DEBUG_MODE:
                    logger.debug("keepalive tick failed: %s", e)

            # فاصله‌ی تصادفی بین 20 تا 40 ثانیه
            await asyncio.sleep(random.uniform(20, 40))

    @dbg
    async def _handle_new_request_payload(self, node_or_payload: dict, dry: bool = False, is_doc: bool = True):
        """
        به‌محض کشف درخواست (از CDP یا کلاینت):
        - اگر DRY: فقط notify
        - اگر عادی: پیش‌سنجی عنوان → تلاش فوری برای /take → notify
        """
        # استخراج فیلدها از داکیومنت یا payload
        src_hint = node_or_payload.get("__src") if isinstance(node_or_payload, dict) else None
        if is_doc:
            doi = (node_or_payload.get("doi") or node_or_payload.get("DOI") or node_or_payload.get("id") or "").strip()
            req = node_or_payload.get("request") or {}
            payload = {
                "doi": doi,
                "detail": node_or_payload.get("detail") or node_or_payload.get("url") or (f"/{doi}" if doi else ""),
                "requester": req.get("from") or "",
                "reward": str((req.get("reward") if isinstance(req, dict) else "") or ""),
            }
            _id = node_or_payload.get("_id")
            # ضدتکرار
            if (_id and _id in self._seen_ids) or (doi and doi in self._seen_dois):
                return
            if _id: self._seen_ids.add(_id)
            if doi: self._seen_dois.add(doi)
        else:
            doi = (node_or_payload.get("doi") or "").strip()
            payload = {
                "doi": doi,
                "detail": node_or_payload.get("detail") or f"/{doi}",
                "requester": (node_or_payload.get("request") or {}).get("from") or "",
                "reward": str(((node_or_payload.get("request") or {}).get("reward")) or ""),
            }

        if not doi:
            return

        # لاگ کشف (اولین نقطه)
        self._log_detect(src=src_hint or "handler",
                         url=payload.get("detail"), doi=doi,
                         note="handle_new_request_payload")

        # --- PRE-TAKE: فقط روی عنوان، بدون Crossref ---
        title = ""
        if isinstance(node_or_payload, dict):
            title = (node_or_payload.get("title") or node_or_payload.get("Title") or "").strip()

        if title:
            # عنوان کوتاه؟
            if len(title.split()) < 5:
                await self._notify_py({
                    "doi": doi,
                    "detail": payload.get("detail", ""),
                    "requester": payload.get("requester", ""),
                    "reward": payload.get("reward", ""),
                    "reason": "short_title_pre",
                })
                return

            # book / ebook / e-book به‌صورت کلمهٔ مستقل
            if re.search(r"\b(?:e-?book|book)\b", title, re.IGNORECASE):
                await self._notify_py({
                    "doi": doi,
                    "detail": payload.get("detail", ""),
                    "requester": payload.get("requester", ""),
                    "reward": payload.get("reward", ""),
                    "reason": "book_in_title_pre",
                })
                return
        # --- پایان PRE-TAKE ---

        # DRY: فقط اعلان
        if dry:
            await self._notify_py(payload)
            return

        # حالت عادی: فوراً از همان سشن صفحه رزرو کن
        ok = await self.page.evaluate("""
          async (d) => {
            try {
              const r = await fetch('/take/' + encodeURIComponent(d), {
                method: 'GET', credentials: 'include', redirect: 'manual'
              });
              const final = new URL(r.url, location.href);
              const success = r.redirected || r.ok ||
                              final.pathname.startsWith('/work/') || final.pathname.startsWith('/requests/');
              if (success) {
                (window.skipSet ||= new Set()).add(d);
                window.busy = true;
                return 1;
              }
              return 0;
            } catch { return 0; }
          }
        """, doi)

        if not ok:
            return  # شخص دیگری جلوتر رزرو کرده

        await self._notify_py(payload)

    # --- JS→Python bridge -----------------------------------------------
    @dbg
    async def _notify_py(self, payload: Dict[str, str]):
        doi = payload.get("doi", "")
        reason = payload.get("reason")

        if doi and doi not in state.skip:
            state.skip.append(doi)
        state.active = doi or None
        state.save()

        logger.debug("NOTIFY doi=%s skipSize=%d", doi, len(state.skip))
        if 'bot_app' not in globals() or getattr(bot_app, 'bot', None) is None:
            logger.warning("bot_app not ready yet; dropping notify for %s", doi)
            return

        # اگر از سمت اعتبارسنجی/پیش‌سنجی رد شده
        if reason:
            reason_text = {
                "contains_book": "⚠️ DOI شامل عبارت book یا ebook است.",
                "invalid_crossref": "🚫 DOI در CrossRef معتبر نیست.",
                "invalid_format": "🚫 قالب DOI معتبر نیست.",
                # دلایل جدید پیش از رزرو (فقط عنوان)
                "short_title_pre": "⛔️ رد شد (قبل از رزرو): عنوان کمتر از ۵ کلمه است.",
                "book_in_title_pre": "⛔️ رد شد (قبل از رزرو): عبارت book/ebook در عنوان است.",
                "competitor_won": "⏱️ رزرو توسط رقیب انجام شد (دیر رسیدیم).",
            }.get(reason, "⚠️ علت ناشناخته")

            msg = f"📭 درخواست نادیده گرفته شد:\nDOI: <code>{doi}</code>\nدلیل: {reason_text}"
            await bot_app.bot.send_message(TG_CHAT, msg, parse_mode="HTML")
            logger.info(f"📭 Skipped DOI: {doi} | Reason: {reason_text}")

            # آزادسازی busy و افزودن به skipSet صفحه (احتیاط)
            try:
                page = bot_app.bot_data.get("client").page
                if page and doi:
                    await page.evaluate("(d)=>{window.busy=false;(window.skipSet ||= new Set()).add(d);}", doi)
            except Exception:
                pass
            return

        # متادیتا و فیلترهای پس از رزرو (باقی می‌مانند به‌عنوان Safety Net)
        meta = await metadata(doi)
        title_words = (meta.get("title") or "").split()
        if len(title_words) < 5:
            msg = (
                f"📚 درخواست لغو شد چون عنوان کمتر از ۵ کلمه است:\n"
                f"<code>{doi}</code>\n"
                f"عنوان: <b>{html.escape(meta.get('title', 'نامشخص'))}</b>"
            )
            await bot_app.bot.send_message(TG_CHAT, msg, parse_mode="HTML")
            # آزادسازی busy
            try:
                page = bot_app.bot_data.get("client").page
                if page and doi:
                    await page.evaluate("(d)=>{window.busy=false;(window.skipSet ||= new Set()).add(d);}", doi)
            except Exception:
                pass
            logger.info(f"⛔️ DOI {doi} رد شد چون عنوان خیلی کوتاه بود ({len(title_words)} کلمه).")
            return

        if "book" in (meta.get("type") or "").lower():
            msg = f"📚 درخواست لغو شد چون DOI مربوط به کتاب است:\n<code>{doi}</code>"
            await bot_app.bot.send_message(TG_CHAT, msg, parse_mode="HTML")
            # آزادسازی busy
            try:
                page = bot_app.bot_data.get("client").page
                if page and doi:
                    await page.evaluate("(d)=>{window.busy=false;(window.skipSet ||= new Set()).add(d);}", doi)
            except Exception:
                pass
            logger.info(f"⛔️ DOI {doi} مربوط به کتاب بود، درخواست لغو شد.")
            return

        # === DRY-RUN: فقط پیام، بدون دانلود/آپلود ===
        if DRY_RUN:
            await send_telegram(
                doi=doi, title=meta["title"], year=meta["year"], journal=meta["journal"],
                abstract=meta["abstract"], reward=payload.get("reward", ""),
                requester=payload.get("requester", ""),
                detail=urljoin(SCINET_URL, payload.get("detail", ""))
            )
            # آزادسازی busy (اگر قبل‌تر ست شده باشد)
            try:
                page = bot_app.bot_data.get("client").page
                if page and doi:
                    await page.evaluate("(d)=>{window.busy=false;(window.skipSet ||= new Set()).add(d);}", doi)
            except Exception:
                pass
            return

        # حالت عادی: پیام + شروع فرآیند دانلود/آپلود
        await send_telegram(
            doi=doi, title=meta["title"], year=meta["year"], journal=meta["journal"],
            abstract=meta["abstract"], reward=payload.get("reward", ""),
            requester=payload.get("requester", ""),
            detail=urljoin(SCINET_URL, payload.get("detail", ""))
        )
        asyncio.create_task(start_download_process(bot_app, payload, meta))


# ── Telegram helpers ───────────────────────────────────────
@dbg
async def send_telegram(**kw):
    esc = html.escape
    parts = [
        "📌 <b>درخواست جدید Sci-Net</b>",
        f"<b>عنوان:</b> {esc(kw['title'])}",
        f"<b>DOI:</b> <code>{esc(kw['doi'])}</code>"
    ]
    src = " — ".join(filter(bool, [str(kw['year'] or ""), kw['journal']]))
    if src: parts.append(f"<b>منبع:</b> {esc(src)}")
    if kw['reward']:    parts.append(f"<b>جایزه:</b> {esc(kw['reward'])}")
    if kw['requester']: parts.append(f"<b>درخواست‌کننده:</b> {esc(kw['requester'])}")
    parts.append(f"<b>لینک:</b> {esc(kw['detail'])}")
    if kw['abstract']:
        parts.append("\n" + esc(str(kw['abstract'])))

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("تموم شد", callback_data="done")],
        [InlineKeyboardButton("فعال‌سازی", callback_data="on"),
         InlineKeyboardButton("غیرفعال‌سازی", callback_data="off")]
    ])
    await bot_app.bot.send_message(
        TG_CHAT, "\n".join(parts), parse_mode="HTML",
        disable_web_page_preview=True, reply_markup=kb
    )

# ── فعال/غیرفعال ───────────────────────────────────────────
@dbg
async def enable_bot(flag:bool):
    state.enabled = flag; state.save()
    client:SciNetClient = bot_app.bot_data["client"]
    # آرگومان را مستقیم به JS می‌فرستیم؛ امن و بدون استرینگ‌سازی
    await client.page.evaluate("(f) => { window.enabled = f; }", flag)

@dbg
async def toggle_cb(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.callback_query.from_user.id):
        await update.callback_query.answer(" اجازه ندارید", show_alert=True); return
    enable = update.callback_query.data == "on"
    await enable_bot(enable)
    await update.callback_query.answer("OK")
    logger.info("toggle | enabled=%s", enable)
    await context.bot.send_message(
        TG_CHAT, " فعال شد ✅" if enable else "⏸ غیرفعال شد ⏸"
    )

# ── done ───────────────────────────────────────────────────
@dbg
async def done_cb(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.callback_query.from_user.id):
        await update.callback_query.answer(" فقط مالک می‌تواند", show_alert=True); return
    doi = state.active
    state.active = None
    if doi and doi not in state.skip:
        state.skip.append(doi)
    state.save()
    logger.debug("DONE doi=%s skipSize=%d", doi, len(state.skip))
    client:SciNetClient = context.application.bot_data["client"]
    await client.page.evaluate(f"""
        window.busy=false;
        window.skipSet.add({json.dumps(doi)});
    """)
    await update.callback_query.answer()
    await update.callback_query.edit_message_reply_markup(None)
    await context.bot.send_message(
        TG_CHAT, f"✅ درخواست <code>{doi}</code> بسته شد.", parse_mode="HTML"
    )

# ── /start ─────────────────────────────────────────────────
@dbg
async def start_cmd(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("فعال‌سازی", callback_data="on"),
         InlineKeyboardButton("غیرفعال‌سازی", callback_data="off")]
    ])
    await update.message.reply_text("ربات سریع Sci-Net آماده است.", reply_markup=kb)

# ── Heartbeat ──────────────────────────────────────────────
@dbg
async def heartbeat():
    while True:
        await asyncio.sleep(24 * 3600)
        ok_browser = False
        try:
            p = bot_app.bot_data["client"].page
            if p and not p.is_closed():
                ok_browser = await p.evaluate("typeof window.__observerAlive !== 'undefined'")
        except Exception as e:
            logger.warning("heartbeat check failed", exc_info=True)
        text = "✅ ربات فعال است." if ok_browser else "⚠️ مرورگر Crash؛ در حال بازیابی…"
        try:
            await bot_app.bot.send_message(TG_CHAT, text)
        except Exception as e:
            logger.error("heartbeat telegram", exc_info=True)
        if not ok_browser:
            await bot_app.bot_data["client"]._recover("heartbeat failure")
# ── main ───────────────────────────────────────────────────
@dbg
async def main():
    global bot_app
    bot_app = (Application.builder().token(TG_TOKEN).rate_limiter(AIORateLimiter()).build())
    client = SciNetClient(); await client.start()

    sources = POLICY.sources()
    if not DRY_RUN and not sources:
        logger.warning("هیچ منبع دانلودی فعال نیست (Policy). فقط رزرو/اعلان انجام می‌شود.")
    print("[+] SciNet login completed.")

    iran_page = None
    giga_page = None

    if not DRY_RUN:
        if "iranpaper" in sources:
            iran_page = await client.page.context.new_page()
            print("[+] IranPaper tab opened successfully!")
            try:
                await iranpaper_login(iran_page)
                ipc = IranPaperClient(IRANPAPER_USER, IRANPAPER_PASS, download_dir=str(DOWNLOAD_DIR))
                asyncio.create_task(ipc.periodic_relogin(iran_page, notify=send_telegram))
            except Exception:
                logger.exception("[IranPaper] login failed")
                await iran_page.screenshot(path="iranpaper_login_error.png")
                print("💥 IranPaper login failed; continuing without it.")


        if "gigalib" in sources:
            giga_page = await client.page.context.new_page()
            print("[+] GigaLib tab opened successfully!")
            try:
                await gigalib_login(giga_page)
                print("[+] GigaLib login completed successfully!")
            except Exception as e:
                cur_url = giga_page.url
                if "block.aspx" in cur_url:
                    logger.error("[GigaLib] BLOCKED by site (URL=%s). Skipping GigaLib, bot continues.", cur_url)
                else:
                    logger.exception("[GigaLib] login failed", exc_info=True)
                await giga_page.screenshot(path="gigalib_error_screenshot.png")
                print("[+] Screenshot saved as 'gigalib_error_screenshot.png' for debugging purposes.")
    else:
        logger.info("DRY-RUN فعال است: IranPaper/GigaLib ساخته نمی‌شوند.")

    await client.page.context.storage_state(path="session_giga_iran.json")

    from bot.setup import register_commands
    register_commands(bot_app)
    bot_app.bot_data["client"] = client
    bot_app.bot_data["iran_page"] = iran_page
    bot_app.bot_data["giga_page"] = giga_page
    bot_app.bot_data["state"] = state

    bot_app.add_handler(CommandHandler("start", start_cmd))
    bot_app.add_handler(CallbackQueryHandler(done_cb, pattern="^done$"))
    bot_app.add_handler(CallbackQueryHandler(toggle_cb, pattern="^(on|off)$"))
    bot_app.add_handler(CommandHandler("testdoi", test_doi_cmd))
    bot_app.add_handler(CommandHandler("monitor", monitor_cmd))
    bot_app.add_handler(CommandHandler("diag", diag_cmd))


    logger.info("Bot started | DEBUG=%s | headful=%s | DRY_RUN=%s | sources=%s",
                DEBUG_MODE, HEADFUL, DRY_RUN, sources)

    asyncio.create_task(heartbeat())
    print("[+] Telegram bot started ✅")
    # به‌جای await bot_app.run_polling()
    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling()
    logger.info("Polling started and running inside existing event loop")

    try:
        # لوپ را زنده نگه می‌داریم (تا وقتی Ctrl+C بزنی)
        await asyncio.Future()
    finally:
        # خاموش‌سازی تمیز
        await bot_app.updater.stop()
        await bot_app.stop()
        await bot_app.shutdown()



    
# ── اد شده توسط ممد ────────────────────────────────────────────
# ── فرآیند دانلود و آپلود ───────────────────────────────────
async def start_download_process(bot_app, payload: dict, meta: dict):
    """
    انتها-به-انتها برای یک DOI بر اساس Policy:
      - ترتیب و انتخاب منابع از POLICY.sources() می‌آید.
      - در DRY_RUN فقط اعلان می‌فرستیم و busy را آزاد می‌کنیم.
    """
    if DRY_RUN:
        doi_dbg = html.escape(payload.get("doi", "") or "")
        try:
            await bot_app.bot.send_message(
                TG_CHAT,
                f"👀 DRY-RUN فعال است: برای DOI زیر دانلود/آپلود انجام نمی‌شود:\n<code>{doi_dbg}</code>",
                parse_mode="HTML"
            )
        except Exception:
            pass
        # آزادسازی busy (اگر قبلاً ست شده)
        try:
            client = bot_app.bot_data.get("client")
            page = getattr(client, "page", None)
            doi_val = payload.get("doi", "")
            if page and doi_val:
                await page.evaluate("(d)=>{window.busy=false;(window.skipSet ||= new Set()).add(d);}", doi_val)
        except Exception:
            pass
        return

    doi = payload.get("doi")
    detail_url = urljoin(SCINET_URL, payload.get("detail", ""))

    # صفحات منابع (ممکن است None باشند اگر Policy اجازه نداده)
    scinet_page: Page = bot_app.bot_data["client"].page
    iran_page:   Page = bot_app.bot_data.get("iran_page")
    giga_page:   Page = bot_app.bot_data.get("giga_page")

    logger.info(f"شروع فرآیند برای DOI: {doi}")
    await bot_app.bot.send_message(
        TG_CHAT, f"⏳ شروع دانلود مقاله:\n<code>{doi}</code>", parse_mode="HTML"
    )

    async def try_iranpaper() -> Optional[str]:
        if not iran_page:
            return None
        logger.info(f"[{doi}] تلاش از IranPaper ...")
        return await iranpaper_download(iran_page, doi, download_dir=str(DOWNLOAD_DIR))


    async def try_gigalib() -> Optional[str]:
        if not giga_page:
            return None
        logger.info(f"[{doi}] تلاش از GigaLib ...")
        # اطمینان از لاگین (ممکن است سشن پریده باشد)
        try:
            await gigalib_login(giga_page)
        except Exception:
            pass
        return await gigalib_download(giga_page, doi, download_dir=str(DOWNLOAD_DIR))

    downloaded_file_path: Optional[str] = None
    errors: list[str] = []

    # ترتیب منابع را از Policy بگیر
    for src in POLICY.sources():
        try:
            if src == "iranpaper":
                downloaded_file_path = await try_iranpaper()
            elif src == "gigalib":
                downloaded_file_path = await try_gigalib()
            else:
                logger.warning("منبع ناشناخته در Policy: %s", src)
                continue

            if downloaded_file_path:
                await bot_app.bot.send_message(
                    TG_CHAT,
                    f"✅ دانلود موفق از {src}:\n<code>{doi}</code>\nمسیر: <code>{html.escape(downloaded_file_path)}</code>",
                    parse_mode="HTML"
                )
                break  # موفق؛ از حلقه خارج شو

        except Exception as e:
            logger.warning(f"[{doi}] منبع {src} ناموفق بود.", exc_info=True)
            errors.append(src)

    if not downloaded_file_path:
        # هیچ منبعی موفق نشد
        await bot_app.bot.send_message(
            TG_CHAT,
            "❌ فایل یافت/دانلود نشد "
            + (f"(تلاش‌شده‌ها: {', '.join(errors)})" if errors else ""),
            parse_mode="HTML"
        )

        # تلاش برای لغو روی سایت؛ اگر لغو نشود، لینک را بفرست و busy را آزاد نکن
        try:
            ok_cancel = await cancel_scinet_request(scinet_page, detail_url, doi)
            if ok_cancel:
                await bot_app.bot.send_message(
                    TG_CHAT,
                    f"✅ درخواست کنسل شد:\n<code>{html.escape(doi)}</code>",
                    parse_mode="HTML"
                )
                # cancel_scinet_request خودش busy=false می‌کند و DOI را به skipSet می‌افزاید
            else:
                await bot_app.bot.send_message(
                    TG_CHAT,
                    f"⚠️ نتوانستم درخواست را کنسل کنم. لطفاً دستی اقدام کن؛ "
                    f"تا لغو نشود، ربات درخواست جدیدی بررسی نمی‌کند.\n"
                    f"<code>{html.escape(doi)}</code>\n"
                    f'🔗 <a href="{html.escape(detail_url)}">صفحهٔ درخواست</a>',
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
                # عمداً busy را آزاد نکن
        except Exception as e:
            logger.exception("cancel after download-fail")
            await bot_app.bot.send_message(
                TG_CHAT,
                f"⚠️ خطا در تلاش برای کنسل‌کردن درخواست:\n"
                f"<code>{html.escape(doi)}</code>\n"
                f"{html.escape(str(e))}\n"
                f'🔗 <a href="{html.escape(detail_url)}">صفحهٔ درخواست</a>',
                parse_mode="HTML",
                disable_web_page_preview=True
            )
            # عمداً busy را آزاد نکن
        return

    # ← در این نقطه downloaded_file_path داریم (دانلود موفق بوده) و می‌خواهیم قبل از آپلود، تمیزش کنیم
    to_upload_path = downloaded_file_path
    cleaned_file_path = None
    try:
        cleaned_file_path = await clean_pdf_watermarks_async(
            downloaded_file_path,

            output_path=cleaned_file_path,
            overwrite_original=True,
            header_height_pt=70,            
            include_first_page=True,
            keywords=["downloaded from","iranpaper","tarjomano","joopy","ترجمانو"],
            remove_images_in_header=True,
            img_max_h_pt=95,
            img_max_w_ratio=0.85,
            min_repetition_ratio=0.40
            
            
        )
        if cleaned_file_path and cleaned_file_path != downloaded_file_path:
            to_upload_path = cleaned_file_path
            try:
                await bot_app.bot.send_message(
                    TG_CHAT, "🧼 فایل قبل از آپلود پاک‌سازی شد.", parse_mode="HTML"
                )
            except Exception:
                pass
    except Exception as e:
        logger.warning("PDF cleaning failed: %s", e, exc_info=True)
      

    # ـــــــــــــــــــــــ 2) آپلود به SciNet ـــــــــــــــــــــــ
    try:
        logger.info(f"[{doi}] شروع آپلود به SciNet: {detail_url}")
        await upload_to_scinet(scinet_page, detail_url, to_upload_path)
        logger.info(f"[{doi}] آپلود موفق بود.")
        await bot_app.bot.send_message(
            TG_CHAT,
            f"🎉 آپلود برای درخواست مرتبط با DOI زیر انجام شد:\n<code>{html.escape(doi)}</code>",
            parse_mode="HTML"
        )

        try:
            import os
            from pathlib import Path  # اگر بالاتر ایمپورت نشده
            if not KEEP_LOCAL_PDFS:
                if cleaned_file_path and Path(cleaned_file_path).exists():
                    os.remove(cleaned_file_path)
                    logger.info(f"🧹 فایل تمیزشده پاک شد: {cleaned_file_path}")
                if downloaded_file_path and Path(downloaded_file_path).exists() and downloaded_file_path != cleaned_file_path:
                    os.remove(downloaded_file_path)
                    logger.info(f"🧹 فایل اصلی پاک شد: {downloaded_file_path}")
            else:
                logger.info(
                    "🔒 KEEP_LOCAL_PDFS=1 → فایل‌ها نگه داشته شدند.\n"
                    f" - cleaned: {cleaned_file_path or '-'}\n"
                    f" - original: {downloaded_file_path}"
                )
        except Exception as e:
            logger.warning(f"نتوانستم فایل‌ها را پاک/نگه دارم: {e}")
        # ⬆️⬆️ پایان بلاک پاک‌سازی/نگه‌داری ⬆️⬆️

        # رسیدگی موفق → busy را آزاد کن
        try:
            await scinet_page.evaluate(
                "(d)=>{window.busy=false;(window.skipSet ||= new Set()).add(d);}", doi
            )
        except Exception:
            pass

    except Exception as upload_err:
        logger.error(f"[{doi}] خطا در آپلود به SciNet: {upload_err}", exc_info=True)
        await bot_app.bot.send_message(
            TG_CHAT,
            f"❌ خطا در آپلود به SciNet برای DOI:\n"
            f"<code>{html.escape(doi)}</code>\n\n"
            f"علت: {html.escape(str(upload_err))}",
            parse_mode="HTML"
        )

        # تلاش برای لغو؛ اگر نشد لینک بده و busy را آزاد نکن
        try:
            ok_cancel = await cancel_scinet_request(scinet_page, detail_url, doi)
            if ok_cancel:
                await bot_app.bot.send_message(
                    TG_CHAT,
                    f"✅ درخواست کنسل شد:\n<code>{html.escape(doi)}</code>",
                    parse_mode="HTML"
                )
                # cancel_scinet_request خودش busy=false می‌کند
            else:
                await bot_app.bot.send_message(
                    TG_CHAT,
                    f"⚠️ نتوانستم درخواست را کنسل کنم. لطفاً دستی اقدام کن؛ "
                    f"تا لغو نشود، ربات درخواست جدیدی بررسی نمی‌کند.\n"
                    f"<code>{html.escape(doi)}</code>\n"
                    f'🔗 <a href="{html.escape(detail_url)}">صفحهٔ درخواست</a>',
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
                # عمداً busy را آزاد نکن
        except Exception as e:
            logger.exception("cancel after upload-fail")
            await bot_app.bot.send_message(
                TG_CHAT,
                f"⚠️ خطا در تلاش برای کنسل‌کردن درخواست:\n"
                f"<code>{html.escape(doi)}</code>\n"
                f"{html.escape(str(e))}\n"
                f'🔗 <a href="{html.escape(detail_url)}">صفحهٔ درخواست</a>',
                parse_mode="HTML",
                disable_web_page_preview=True
            )
            # عمداً busy را آزاد نکن




async def upload_to_scinet(page: Page, detail_url: str, file_path: str):
    
    try:
        logger.info(f"📤 باز کردن صفحه درخواست SciNet: {detail_url}")
        await page.goto(detail_url, timeout=60000)
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)


        upload_input = await page.query_selector('input[type="file"]')
        if not upload_input:
            raise Exception("فیلد آپلود پیدا نشد!")
        await upload_input.set_input_files(file_path)
        logger.info(f"✅ فایل {file_path} انتخاب و آماده آپلود است.")

        await page.locator("#progress").click()
        await page.locator("div").filter(has_text="remove signatures →").nth(4).click()
        await page.locator(".clean > .button").first.click()
        logger.info("🧹 فایل تمیز شد (remove signatures).")

        await page.get_by_role("link", name="submit").click()
        logger.info("🚀 فایل ارسال شد، منتظر تأیید سرور...")

        await asyncio.sleep(5)
        logger.info(f"🎉 آپلود و ارسال فایل برای درخواست {detail_url} با موفقیت انجام شد.")

    except Exception as e:
        logger.error(f"❌ خطا در آپلود به SciNet برای {detail_url}: {e}", exc_info=True)
        screenshot_path = f"scinet_upload_error_{Path(file_path).stem}.png"
        await page.screenshot(path=screenshot_path)
        logger.info(f"📸 اسکرین‌شات خطا در {screenshot_path} ذخیره شد.")
        raise

@dbg
async def cancel_scinet_request(page: Page, detail_url: str, doi: str) -> bool:
    """
    تلاش چندمرحله‌ای برای لغو:
      1) کلیک روی <a.button href="/refuse/<doi>">X</a> با چند واریانت href
      2) fallback: fetch('/refuse/<doi>') در context صفحه
      3) fallback نهایی: page.goto('/refuse/<doi>')
    هر مرحله در صورت موفقیت True برمی‌گرداند.
    """
    try:
        # 0) برو صفحهٔ درخواست
        await page.goto(detail_url, timeout=60000)
        await page.wait_for_load_state("domcontentloaded")

        # 1) تلاش برای کلیک دکمه
        raw = f"/refuse/{doi}"
        enc_all = f"/refuse/{quote(doi, safe='')}"          # همه حروف encode
        enc_slash = f"/refuse/{doi.replace('/', '%2F')}"    # فقط اسلش‌ها

        selectors = [
            f'a.button[href="{raw}"]',
            f'a.button[href="{enc_all}"]',
            f'a.button[href="{enc_slash}"]',
            'a.button[href^="/refuse/"]',                    # عام‌ترین
            page.locator('a.button', has_text="X")           # با متن X
        ]

        clicked = False

        for sel in selectors:
            loc = sel if hasattr(sel, "click") else page.locator(sel)
            first = loc.first
            try:
                # سریع ببین چیزی هست
                if not await first.count():
                    continue

                # اول attach بعد visible (visible ممکنه تایم‌اوت بده، اشکال ندارد)
                try:
                    await first.wait_for(state="attached", timeout=500)
                except Exception:
                    pass

                await first.wait_for(state="visible", timeout=1500)
                await first.click(timeout=2000)
                clicked = True
                break

            except Exception as e:
                # اگر دوست داری فقط Timeout را بی‌صدا رد کنی:
                # from playwright._impl._errors import TimeoutError
                # if isinstance(e, TimeoutError): continue
                logger.debug("click attempt failed for %s: %s", getattr(sel, "selector", sel), e)
                continue

        if clicked:
            # کمی صبر کن تا ریدایرکت‌ها/آپدیت انجام شود
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            try:
                await page.evaluate("(d)=>{window.busy=false;(window.skipSet ||= new Set()).add(d);}", doi)
            except Exception:
                pass
            return True

        # 2) fallback: fetch مستقیم در context صفحه
        ok = await page.evaluate("""
            async (d) => {
                try {
                    const r = await fetch('/refuse/' + encodeURIComponent(d), {
                        method: 'GET', credentials: 'include', redirect: 'manual'
                    });
                    const u = new URL(r.url, location.href);
                    const success = r.redirected || r.ok || u.pathname.startsWith('/requests');
                    if (success) {
                        (window.skipSet ||= new Set()).add(d);
                        window.busy = false;
                        return 1;
                    }
                    return 0;
                } catch { return 0; }
            }
        """, doi)
        if ok:
            return True

        # 3) fallback نهایی: رفتن مستقیم به URL لغو
        try:
            await page.goto(urljoin(page.url, f"/refuse/{quote(doi, safe='')}"), timeout=15000)
            try:
                await page.wait_for_load_state("networkidle", timeout=4000)
            except Exception:
                pass
            try:
                await page.evaluate("(d)=>{window.busy=false;(window.skipSet ||= new Set()).add(d);}", doi)
            except Exception:
                pass
            return True
        except Exception:
            pass

        # اگر همهٔ مراحل شکست خورد:
        
        return False

    except Exception:
        logger.exception("cancel_scinet_request failed for %s", doi)
        
        return False

# ── /doi ──────────────────────────────────
@dbg
async def test_doi_cmd(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return

    try:
        doi_to_test = context.args[0]
    except IndexError:
        await update.message.reply_text("لطفاً یک DOI وارد کنید.\nمثال: /testdoi 10.1234/fake.doi")
        return

    fake_payload = {
        "doi": doi_to_test,
        "requester": "تست دستی",
        "detail": "",
        "reward": "100"
    }
    fake_meta = {
        "title": "عنوان مقاله‌ی تستی",
        "journal": "مجله‌ی تست",
        "year": 2025,
        "abstract": "این یک تست دستی برای شروع فرآیند دانلود است."
    }

    await update.message.reply_text(f"✅ شبیه‌سازی دریافت DOI از ساینت...\nشروع فرآیند برای: {doi_to_test}")
    asyncio.create_task(start_download_process(context.application, fake_payload, fake_meta))

@dbg
async def monitor_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("⛔️ فقط مالک می‌تواند این دستور را اجرا کند.")
        return

    # خواندن مدت از آرگومان دستور
    try:
        minutes = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("❗️ لطفاً مدت مانیتورینگ را بر حسب دقیقه بنویسید. مثال:\n/monitor 30")
        return

    duration_seconds = minutes * 60

    client: SciNetClient = context.application.bot_data["client"]
    page = client.page

    # اطلاع به کاربر
    await update.message.reply_text(f"📸 مانیتورینگ به مدت {minutes} دقیقه شروع شد (I Love Abbas btw).")

    # اجرای وظیفه در پس‌زمینه
    asyncio.create_task(monitor_loop(page, duration_seconds))

# ── /diag ──────────────────────────────────
@dbg
async def diag_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return

    app = context.application
    state_obj = app.bot_data.get("state")
    client: SciNetClient = app.bot_data.get("client")
    page = getattr(client, "page", None)

    # اطلاعات سمت JS در تب Sci-Net
    js_info = {}
    if page:
        try:
            js_info = await page.evaluate("""
                () => ({
                    url: location.href,
                    enabled: Boolean(window.enabled),
                    busy: Boolean(window.busy),
                    observerAlive: Boolean(window.__observerAlive),
                    hasArequest: !!window.arequest,
                    eventsReqLen: (window.events && window.events.request && window.events.request.length) || 0,
                    skipSetSize: (window.skipSet && window.skipSet.size) || 0,
                    ts: new Date().toISOString()
                })
            """)
        except Exception as e:
            js_info = {"js_error": str(e)}
    else:
        js_info = {"page": "missing"}

    # اطلاعات سمت سرور/بات
    srv_info = {
        "DRY_RUN": DRY_RUN,
        "DEBUG_MODE": DEBUG_MODE,
        "HEADFUL": HEADFUL,
        "state_enabled": getattr(state_obj, "enabled", None),
        "state_active": getattr(state_obj, "active", None),
        "state_skip_len": len(getattr(state_obj, "skip", []) or []),
        "seen_dois_len": len(getattr(client, "_seen_dois", set()) or []),
        "seen_ids_len": len(getattr(client, "_seen_ids", set()) or []),
    }

    payload = {"server": srv_info, "client_js": js_info}
    text = "diag:\n<code>" + html.escape(json.dumps(payload, ensure_ascii=False, indent=2)) + "</code>"

    try:
        # ترجیحاً جواب را در همانی که دستور را زدی بفرست
        if update.effective_chat:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode="HTML")
        else:
            await update.message.reply_text(text, parse_mode="HTML")
    except Exception:
        #fallback کوتاه
        await update.message.reply_text("diag: " + str(payload))



@dbg
async def monitor_loop(page: Page, duration_seconds: int):
    start_time = time.time()
    monitor_dir = Path("./monitor")
    monitor_dir.mkdir(exist_ok=True)

    logger.info(f"📷 شروع مانیتورینگ SciNet برای {duration_seconds} ثانیه...")

    counter = 0
    while time.time() - start_time < duration_seconds:
        try:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = monitor_dir / f"sci_{timestamp}_{counter:04d}.png"
            await page.screenshot(path=filename, full_page=True)
            counter += 1
        except Exception as e:
            logger.error(f"❌ خطا در گرفتن اسکرین‌شات مانیتورینگ: {e}", exc_info=True)
        await asyncio.sleep(1)  # هر ثانیه یک عکس

    logger.info(f"🛑 مانیتورینگ {duration_seconds//60} دقیقه‌ای تمام شد ({counter} تصویر گرفته شد).")


# ── entry point ────────────────────────────────────────────
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass





