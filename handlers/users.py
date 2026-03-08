import logging
from html import escape
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from database import db
from handlers.menu import back_button, require_admin

logger = logging.getLogger(__name__)

ADD_OPERATOR_ID = 20
ADD_OPERATOR_NAME = 21


async def show_operators(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    config = context.bot_data["config"]

    if not db.is_admin(query.from_user.id, config):
        await query.answer("⛔ Только для администратора", show_alert=True)
        return

    await query.answer()
    operators = db.list_operators()

    text = "👑 <b>Управление операторами</b>\n\n"
    if operators:
        for i, op in enumerate(operators, 1):
            name = op["name"] or "Без имени"
            text += f"{i}. <b>{escape(name)}</b> (ID: <code>{op['telegram_id']}</code>)\n"
    else:
        text += "Операторов нет.\n"

    text += f"\n👤 Администратор: <code>{config['telegram']['admin_id']}</code>"

    buttons = [
        [InlineKeyboardButton("➕ Добавить оператора", callback_data="add_operator")],
    ]

    if operators:
        for op in operators:
            name = op["name"] or str(op["telegram_id"])
            buttons.append([InlineKeyboardButton(
                f"🗑 Удалить {name}", callback_data=f"del_operator:{op['telegram_id']}"
            )])

    buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="main_menu")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")


async def add_operator_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    config = context.bot_data["config"]

    if not db.is_admin(query.from_user.id, config):
        await query.answer("⛔ Только для администратора", show_alert=True)
        return

    await query.answer()
    await query.edit_message_text(
        "➕ <b>Добавление оператора</b>\n\n"
        "Введите Telegram ID нового оператора.\n"
        "Пользователь может узнать свой ID через @userinfobot",
        parse_mode="HTML"
    )
    return ADD_OPERATOR_ID


@require_admin
async def add_operator_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        telegram_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ ID должен быть числом. Попробуйте снова:")
        return ADD_OPERATOR_ID

    config = context.bot_data["config"]
    if telegram_id == config["telegram"]["admin_id"]:
        await update.message.reply_text("❌ Это ID администратора. Введите другой:")
        return ADD_OPERATOR_ID

    if db.is_operator(telegram_id):
        await update.message.reply_text("❌ Этот оператор уже добавлен. Введите другой ID:")
        return ADD_OPERATOR_ID

    context.user_data["new_operator_id"] = telegram_id
    await update.message.reply_text("Введите имя оператора (для удобства):")
    return ADD_OPERATOR_NAME


@require_admin
async def add_operator_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    telegram_id = context.user_data.get("new_operator_id")

    db.add_operator(telegram_id, name, update.effective_user.id)

    await update.message.reply_text(
        f"✅ Оператор добавлен: <b>{escape(name)}</b> (ID: <code>{telegram_id}</code>)",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("👑 К операторам", callback_data="manage_operators")],
            [InlineKeyboardButton("◀️ Меню", callback_data="main_menu")],
        ]),
        parse_mode="HTML"
    )
    return ConversationHandler.END


async def delete_operator(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    config = context.bot_data["config"]

    if not db.is_admin(query.from_user.id, config):
        await query.answer("⛔ Только для администратора", show_alert=True)
        return

    telegram_id = int(query.data.split(":")[1])
    operators = db.list_operators()
    name = next((op["name"] for op in operators if op["telegram_id"] == telegram_id), str(telegram_id))

    db.remove_operator(telegram_id)

    await query.answer()
    await query.edit_message_text(
        f"🗑 Оператор <b>{escape(name)}</b> удалён.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("👑 К операторам", callback_data="manage_operators")],
            [InlineKeyboardButton("◀️ Меню", callback_data="main_menu")],
        ]),
        parse_mode="HTML"
    )


async def add_operator_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❌ Отменено.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Меню", callback_data="main_menu")]
        ])
    )
    return ConversationHandler.END
