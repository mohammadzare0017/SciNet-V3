# فایل دستورات ربات - نسخه اولیه
import asyncio
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from functools import wraps

from scinet_bot_fast import is_owner  
from scinet_bot_fast import state, SciNetClient  

def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        uid = update.effective_user.id if update.effective_user else None
        if not is_owner(uid):
            await update.message.reply_text("⛔️ فقط مالک می‌تواند این دستور را اجرا کند.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper


@admin_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    app = context.application
    client = app.bot_data.get("client")
    iran_page = app.bot_data.get("iran_page")
    giga_page = app.bot_data.get("giga_page")
    monitor_task = app.bot_data.get("monitor_task")
    s = []
    s.append("🔎 وضعیت ربات:")
    s.append(f"• فعال: {'بله' if state.enabled else 'خیر'}")
    s.append(f"• DOI فعلی: {state.active or 'هیچ'}")
    s.append(f"• IranPaper tab: {'باز' if iran_page and not iran_page.is_closed() else 'بسته'}")
    s.append(f"• GigaLib tab: {'باز' if giga_page and not giga_page.is_closed() else 'بسته'}")
    s.append(f"• مانیتورینگ: {'درحال اجرا' if monitor_task and not monitor_task.done() else 'غیرفعال'}")
    s.append(f"• آخرین اکشن: {app.bot_data.get('last_action', 'نامشخص')}")
    await update.message.reply_text("\n".join(s))


@admin_only
async def cmd_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        minutes = int(context.args[0])
    except Exception:
        await update.message.reply_text("❗️ شکل درست: /monitor <دقیقه>")
        return

    app = context.application
    if app.bot_data.get("monitor_task") and not app.bot_data["monitor_task"].done():
        await update.message.reply_text("⚠️ مانیتور در حال اجراست.")
        return

    client = app.bot_data.get("client")
    if not client or not getattr(client, "page", None):
        await update.message.reply_text("❌ Playwright آماده نیست.")
        return

    page = client.page
    from scinet_bot_fast import monitor_loop
    app.bot_data["last_action"] = f"Monitor started ({minutes}m)"
    task = asyncio.create_task(monitor_loop(page, minutes * 60))
    app.bot_data["monitor_task"] = task
    await update.message.reply_text(f"📸 مانیتور برای {minutes} دقیقه آغاز شد.")


@admin_only
async def cmd_stop_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    app = context.application
    task = app.bot_data.get("monitor_task")
    if not task or task.done():
        await update.message.reply_text("ℹ️ مانیتور فعال نیست.")
        return
    task.cancel()
    app.bot_data["monitor_task"] = None
    app.bot_data["last_action"] = "Monitor stopped"
    await update.message.reply_text("🛑 مانیتور متوقف شد.")


@admin_only
async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    app = context.application
    client: SciNetClient = app.bot_data.get("client")
    if not client:
        await update.message.reply_text("❌ client پیدا نشد.")
        return
    await update.message.reply_text("♻️ ری‌استارت مرورگر در حال انجام...")
    try:
        await client._launch_browser()
        app.bot_data["client"] = client
        app.bot_data["last_action"] = "Browser restarted"
        await update.message.reply_text("✅ مرورگر با موفقیت ری‌استارت شد.")
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در ری‌استارت: {e}")
