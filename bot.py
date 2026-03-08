#!/usr/bin/env python3
"""
VPN Management Telegram Bot for 3X-UI
"""

import json
import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

from database import db
from services.xui_api import XUIApi
from services import speed_limiter
from handlers import menu, clients, config_template, bans, users, status
from scheduler import jobs

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(
            os.path.join(os.path.dirname(__file__), "bot.log"),
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        ),
    ]
)
logger = logging.getLogger(__name__)


def load_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    with open(config_path) as f:
        return json.load(f)


async def sync_existing_clients(api: XUIApi, config: dict):
    """
    Sync existing 3X-UI clients into our database.
    Existing clients not in our DB get is_unlimited=1.
    """
    clients_list = await api.sync_existing_clients()
    if not clients_list:
        logger.info("No existing clients found in 3X-UI")
        return

    synced = 0
    for client in clients_list:
        email = client.get("email", "")
        if not email:
            continue

        existing = db.get_client_config(email)
        if not existing:
            db.upsert_client_config(
                email=email,
                uuid=client.get("id", ""),
                is_unlimited=1,
                device_limit=client.get("limitIp", 2),
            )
            synced += 1
            logger.debug(f"Synced existing client: {email} (unlimited)")

    logger.info(f"Sync complete: {synced} new clients added, {len(clients_list)} total in 3X-UI")


async def post_init(application: Application):
    """Called after bot starts — init DB, API, sync clients, start scheduler."""
    # Set bot commands (menu button in Telegram)
    from telegram import BotCommand
    await application.bot.set_my_commands([
        BotCommand("start", "Главное меню"),
        BotCommand("menu", "Главное меню"),
        BotCommand("status", "Статус клиентов"),
        BotCommand("cancel", "Отменить действие"),
    ])
    config = application.bot_data["config"]
    api = application.bot_data["api"]

    # Init database
    db.init_db()

    # Login to 3X-UI
    if not await api.login():
        logger.error("Failed to login to 3X-UI panel! Check config.")
        return

    # Sync existing clients
    await sync_existing_clients(api, config)

    # Init traffic control
    await speed_limiter.init_tc()

    # Schedule jobs
    job_queue = application.job_queue

    check_interval = config.get("monitoring", {}).get("check_interval_minutes", 5)
    status_interval = config.get("monitoring", {}).get("status_update_minutes", 60)

    # Traffic monitoring every N minutes
    job_queue.run_repeating(
        jobs.traffic_check_job,
        interval=check_interval * 60,
        first=60,  # Start after 1 minute
        name="traffic_check"
    )

    # Status update every hour
    job_queue.run_repeating(
        jobs.status_update_job,
        interval=status_interval * 60,
        first=120,
        name="status_update"
    )

    # Monthly reset check — run daily at 00:05
    job_queue.run_daily(
        jobs.monthly_reset_job,
        time=__import__("datetime").time(0, 5),
        name="monthly_reset"
    )

    logger.info("Bot initialized successfully")


async def post_shutdown(application: Application):
    """Cleanup on shutdown."""
    api = application.bot_data.get("api")
    if api:
        await api.close()
    await speed_limiter.clear_all_limits()
    logger.info("Bot shut down")


async def error_handler(update: object, context) -> None:
    """Global error handler — logs exceptions and notifies user."""
    logger.error(f"Exception while handling an update: {context.error}", exc_info=context.error)
    if update and hasattr(update, "effective_user"):
        try:
            if hasattr(update, "callback_query") and update.callback_query:
                await update.callback_query.answer(
                    "❌ Произошла ошибка. Попробуйте снова.", show_alert=True
                )
            elif hasattr(update, "message") and update.message:
                await update.message.reply_text(
                    "❌ Произошла ошибка. Попробуйте /start для возврата в меню."
                )
        except Exception:
            pass


def main():
    config = load_config()

    if config["telegram"]["bot_token"] == "YOUR_BOT_TOKEN_HERE":
        print("❌ Укажите bot_token в config.json")
        sys.exit(1)

    if config["telegram"]["admin_id"] == 0:
        print("❌ Укажите admin_id в config.json")
        sys.exit(1)

    api = XUIApi(config)

    app = (
        Application.builder()
        .token(config["telegram"]["bot_token"])
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.bot_data["config"] = config
    app.bot_data["api"] = api

    # --- Conversation: Create Client ---
    create_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(clients.create_client_start, pattern="^create_client$")],
        states={
            clients.ENTER_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, clients.create_client_name)
            ],
        },
        fallbacks=[CommandHandler("cancel", clients.create_client_cancel)],
        per_message=False,
        conversation_timeout=300,
    )

    # --- Conversation: Edit Client Field ---
    edit_client_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(clients.client_edit_action, pattern=r"^cedit:(email|traffic|devices|speeds):"),
        ],
        states={
            clients.EDIT_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, clients.client_edit_value)
            ],
        },
        fallbacks=[CommandHandler("cancel", clients.create_client_cancel)],
        per_message=False,
        conversation_timeout=300,
    )

    # --- Conversation: Edit Defaults ---
    defaults_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(config_template.edit_default_start, pattern=r"^def:")],
        states={
            config_template.EDIT_DEFAULT_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, config_template.edit_default_value)
            ],
        },
        fallbacks=[CommandHandler("cancel", clients.create_client_cancel)],
        per_message=False,
        conversation_timeout=300,
    )

    # --- Conversation: Add Operator ---
    operator_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(users.add_operator_start, pattern="^add_operator$")],
        states={
            users.ADD_OPERATOR_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, users.add_operator_id)
            ],
            users.ADD_OPERATOR_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, users.add_operator_name)
            ],
        },
        fallbacks=[CommandHandler("cancel", users.add_operator_cancel)],
        per_message=False,
        conversation_timeout=300,
    )

    # Register handlers (order matters!)
    app.add_handler(CommandHandler("start", menu.start))
    app.add_handler(CommandHandler("menu", menu.start))
    app.add_handler(CommandHandler("status", menu.cmd_status))

    # Conversations first (they have priority)
    app.add_handler(create_conv)
    app.add_handler(edit_client_conv)
    app.add_handler(defaults_conv)
    app.add_handler(operator_conv)

    # Quick actions for clients (no conversation needed)
    app.add_handler(CallbackQueryHandler(
        clients.client_edit_action,
        pattern=r"^cedit:(toggle_override|toggle_enable|reset_traffic|delete|confirm_delete|show_key):"
    ))

    # Menu navigation
    app.add_handler(CallbackQueryHandler(menu.menu_callback, pattern="^main_menu$"))
    app.add_handler(CallbackQueryHandler(status.show_status, pattern="^status$"))
    app.add_handler(CallbackQueryHandler(status.refresh_status, pattern="^refresh_status$"))
    app.add_handler(CallbackQueryHandler(status.pin_status, pattern="^pin_status$"))
    app.add_handler(CallbackQueryHandler(status.refresh_pinned, pattern="^refresh_pinned$"))
    app.add_handler(CallbackQueryHandler(clients.manage_client_list, pattern="^manage_client$"))
    app.add_handler(CallbackQueryHandler(clients.client_detail, pattern=r"^client_detail:"))
    app.add_handler(CallbackQueryHandler(config_template.show_defaults, pattern="^edit_defaults$"))
    app.add_handler(CallbackQueryHandler(bans.show_bans, pattern="^bans$"))
    app.add_handler(CallbackQueryHandler(bans.unban_action, pattern=r"^unban"))
    app.add_handler(CallbackQueryHandler(users.show_operators, pattern="^manage_operators$"))
    app.add_handler(CallbackQueryHandler(users.delete_operator, pattern=r"^del_operator:"))

    # Global error handler
    app.add_error_handler(error_handler)

    # Start
    logger.info("Starting bot...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
