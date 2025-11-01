# bot/setup.py


from telegram.ext import CommandHandler
from .commands import cmd_status, cmd_monitor, cmd_stop_monitor, cmd_restart

def register_commands(app):
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("monitor", cmd_monitor))
    app.add_handler(CommandHandler("stop_monitor", cmd_stop_monitor))
    app.add_handler(CommandHandler("restart", cmd_restart))

