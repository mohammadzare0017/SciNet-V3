import os
from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder
from bot.setup import register_commands

load_dotenv()

# تابع معمولی (دیگه async نیست)
def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("❌ BOT_TOKEN در فایل .env پیدا نشد.")
        return

    # ساخت اپلیکیشن تلگرام
    app = ApplicationBuilder().token(token).build()

    # ثبت کامندها
    register_commands(app)

    # داده‌های ساختگی برای تست (بدون نیاز به Playwright)
    class DummyPage:
        def __init__(self): self.closed = False
        def is_closed(self): return self.closed
    dummy_client = type("DummyClient", (), {"page": DummyPage()})()

    app.bot_data["client"] = dummy_client
    app.bot_data["iran_page"] = DummyPage()
    app.bot_data["giga_page"] = DummyPage()
    app.bot_data["last_action"] = "Idle"

    print("🤖 Telegram test bot started (standalone mode)")
    # اجرای ربات بدون async/await
    app.run_polling()

if __name__ == "__main__":
    main()
