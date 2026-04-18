from functools import wraps
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from database import db


def require_auth(func):
    """Decorator: reject unauthorized users."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        query = update.callback_query
        user_id = query.from_user.id if query else update.effective_user.id
        config = context.bot_data["config"]
        if not db.is_authorized(user_id, config):
            if query:
                await query.answer("⛔ Доступ запрещён", show_alert=True)
            else:
                await update.message.reply_text("⛔ Доступ запрещён.")
            return ConversationHandler.END
        return await func(update, context, *args, **kwargs)
    return wrapper


def require_admin(func):
    """Decorator: reject non-admin users."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        query = update.callback_query
        user_id = query.from_user.id if query else update.effective_user.id
        config = context.bot_data["config"]
        if not db.is_admin(user_id, config):
            if query:
                await query.answer("⛔ Только для администратора", show_alert=True)
            else:
                await update.message.reply_text("⛔ Только для администратора.")
            return ConversationHandler.END
        return await func(update, context, *args, **kwargs)
    return wrapper


def main_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("➕ Создать подключение", callback_data="create_client")],
        [InlineKeyboardButton("📊 Статус клиентов", callback_data="status")],
        [InlineKeyboardButton("👤 Управление клиентом", callback_data="manage_client")],
        [InlineKeyboardButton("⚙️ Базовый конфиг", callback_data="edit_defaults")],
        [InlineKeyboardButton("🚫 Баны", callback_data="bans")],
    ]
    if is_admin:
        buttons.append([InlineKeyboardButton("🔄 Сброс трафика", callback_data="reset_traffic")])
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


@require_admin
async def reset_traffic_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show confirmation dialog before resetting all traffic."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🔄 <b>Сброс трафика</b>\n\n"
        "Будет выполнен полный сброс трафика всех клиентов на всех inbound'ах "
        "(архивирование текущих значений + обнуление в панели + очистка лимитов скорости).\n\n"
        "⚠️ Это действие необратимо. Продолжить?",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Да, сбросить", callback_data="reset_traffic_do"),
                InlineKeyboardButton("❌ Отмена", callback_data="main_menu"),
            ]
        ]),
        parse_mode="HTML"
    )


@require_admin
async def reset_traffic_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Actually run the traffic reset after confirmation."""
    query = update.callback_query
    await query.answer()

    api = context.bot_data["api"]
    config = context.bot_data["config"]
    is_admin = db.is_admin(query.from_user.id, config)

    await query.edit_message_text(
        "⏳ <b>Сброс трафика...</b>\n\nАрхивирую и сбрасываю данные. Это может занять несколько секунд.",
        parse_mode="HTML"
    )

    from services.traffic_monitor import monthly_reset
    try:
        success = await monthly_reset(api, config)
    except Exception as e:
        await query.edit_message_text(
            f"❌ <b>Ошибка сброса</b>\n\n<code>{e}</code>",
            reply_markup=main_menu_keyboard(is_admin),
            parse_mode="HTML"
        )
        return

    if success:
        await query.edit_message_text(
            "✅ <b>Сброс выполнен</b>\n\n"
            "Трафик всех клиентов обнулён, ограничения скорости очищены.",
            reply_markup=main_menu_keyboard(is_admin),
            parse_mode="HTML"
        )
    else:
        await query.edit_message_text(
            "⚠️ <b>Сброс не удался</b>\n\n"
            "Панель вернула ошибку при сбросе трафика. Детали см. в логах бота "
            "(<code>journalctl -u vpn-bot</code>).",
            reply_markup=main_menu_keyboard(is_admin),
            parse_mode="HTML"
        )
