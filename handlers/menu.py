from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import db


def main_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("➕ Создать подключение", callback_data="create_client")],
        [InlineKeyboardButton("📊 Статус клиентов", callback_data="status")],
        [InlineKeyboardButton("👤 Управление клиентом", callback_data="manage_client")],
        [InlineKeyboardButton("⚙️ Базовый конфиг", callback_data="edit_defaults")],
        [InlineKeyboardButton("🚫 Баны", callback_data="bans")],
    ]
    if is_admin:
        buttons.append([InlineKeyboardButton("👑 Управление операторами", callback_data="manage_operators")])
    return InlineKeyboardMarkup(buttons)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = context.bot_data["config"]
    user_id = update.effective_user.id

    if not db.is_authorized(user_id, config):
        await update.message.reply_text("⛔ Доступ запрещён. Обратитесь к администратору.")
        return

    is_admin = db.is_admin(user_id, config)
    await update.message.reply_text(
        "📱 <b>Главное меню</b>\nВыберите действие:",
        reply_markup=main_menu_keyboard(is_admin),
        parse_mode="HTML"
    )


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    config = context.bot_data["config"]
    user_id = query.from_user.id

    if not db.is_authorized(user_id, config):
        await query.answer("⛔ Доступ запрещён", show_alert=True)
        return

    if query.data == "main_menu":
        is_admin = db.is_admin(user_id, config)
        await query.edit_message_text(
            "📱 <b>Главное меню</b>\nВыберите действие:",
            reply_markup=main_menu_keyboard(is_admin),
            parse_mode="HTML"
        )
        await query.answer()


def back_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Назад", callback_data="main_menu")]
    ])


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command directly."""
    config = context.bot_data["config"]
    user_id = update.effective_user.id

    if not db.is_authorized(user_id, config):
        await update.message.reply_text("⛔ Доступ запрещён.")
        return

    api = context.bot_data["api"]
    from services.traffic_monitor import get_status_data
    from utils.formatting import format_status

    msg = await update.message.reply_text("⏳ Собираю данные...")
    data = await get_status_data(api, config)
    text = format_status(data)

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    await msg.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Обновить", callback_data="refresh_status")],
            [InlineKeyboardButton("📌 Закрепить статус", callback_data="pin_status")],
            [InlineKeyboardButton("◀️ Меню", callback_data="main_menu")],
        ]),
        parse_mode="HTML"
    )
