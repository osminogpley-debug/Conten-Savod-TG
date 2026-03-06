import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from handlers.start import is_admin
from database import get_setting, set_setting
from config import ADMIN_ID, TIMEZONE
from utils import build_menu_keyboard, back_button

logger = logging.getLogger(__name__)


async def settings_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /settings"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return
    
    await show_settings(update, context)


async def show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать настройки"""
    from config import CHANNEL_ID
    default_time = await get_setting("default_time", "10:00")
    channel_title = await get_setting("channel_title", "")
    hashtags_enabled = await get_setting("hashtags_enabled", "0")
    signature = await get_setting("signature", "")
    
    channel_display = f"<code>{CHANNEL_ID}</code>"
    if channel_title:
        channel_display = f"{channel_title} ({CHANNEL_ID})"
    
    hashtags_status = "✅ вкл" if hashtags_enabled == "1" else "❌ выкл"
    sig_display = f"«{signature[:30]}»" if signature else "не задана"
    
    text = (
        f"⚙️ <b>Настройки</b>\n\n"
        f"📺 Канал: {channel_display if CHANNEL_ID else '❌ не подключён'}\n"
        f"👤 Админ ID: <code>{ADMIN_ID or 'не задан'}</code>\n"
        f"🕐 Таймзона: {TIMEZONE}\n"
        f"⏰ Время по умолчанию: {default_time}\n"
        f"🏷 Хэштеги: {hashtags_status}\n"
        f"✍️ Подпись: {sig_display}"
    )
    
    buttons = [
        [InlineKeyboardButton(
            "📺 Подключить канал" if not CHANNEL_ID else "📺 Сменить канал",
            callback_data="settings_channel"
        )],
        [InlineKeyboardButton("⏰ Время по умолчанию", callback_data="settings_time")],
        [InlineKeyboardButton(
            f"🏷 Хэштеги: {hashtags_status}",
            callback_data="settings_hashtags"
        )],
        [InlineKeyboardButton("✍️ Подпись канала", callback_data="settings_signature")],
    ]
    if CHANNEL_ID:
        buttons.append([InlineKeyboardButton("❌ Отключить канал", callback_data="settings_disconnect")])
    buttons.append([InlineKeyboardButton("🔙 Главное меню", callback_data="settings_back")])
    
    keyboard = InlineKeyboardMarkup(buttons)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопок настроек"""
    query = update.callback_query
    await query.answer()
    
    if not is_admin(query.from_user.id):
        return
    
    action = query.data
    
    if action == "settings_time":
        await query.edit_message_text(
            "⏰ <b>Время по умолчанию</b>\n\n"
            "Отправьте время в формате ЧЧ:ММ\n"
            "Например: <code>10:00</code> или <code>18:30</code>",
            parse_mode="HTML",
            reply_markup=back_button()
        )
        context.user_data["state"] = "awaiting_default_time"
    
    elif action == "settings_channel":
        await query.edit_message_text(
            "📺 Используйте команду /connect для подключения канала.",
            parse_mode="HTML",
            reply_markup=back_button()
        )
    
    elif action == "settings_disconnect":
        from config import set_channel_id
        set_channel_id("")
        await set_setting("channel_id", "")
        await set_setting("channel_title", "")
        await query.edit_message_text(
            "✅ Канал отключён.\n\n/connect — подключить новый",
            parse_mode="HTML",
            reply_markup=back_button()
        )
    
    elif action == "settings_hashtags":
        current = await get_setting("hashtags_enabled", "0")
        new_val = "0" if current == "1" else "1"
        await set_setting("hashtags_enabled", new_val)
        status = "✅ включены" if new_val == "1" else "❌ выключены"
        await query.edit_message_text(
            f"🏷 Хэштеги: <b>{status}</b>\n\n"
            "При автопостинге хэштеги будут добавляться автоматически.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Настройки", callback_data="menu_settings")],
                [InlineKeyboardButton("🏠 Меню", callback_data="menu_back")],
            ])
        )
    
    elif action == "settings_signature":
        signature = await get_setting("signature", "")
        text = "✍️ <b>Подпись канала</b>\n\n"
        if signature:
            text += f"Текущая: <i>{signature}</i>\n\n"
        text += (
            "Отправьте текст подписи, который будет добавляться к постам.\n"
            "Например: <i>📺 @chinaya_channel</i>\n\n"
            "Отправьте <code>-</code> чтобы убрать подпись."
        )
        context.user_data["state"] = "awaiting_signature"
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=back_button())
    
    elif action == "settings_back":
        context.user_data["state"] = None
        keyboard = build_menu_keyboard()
        await query.edit_message_text(
            "👋 <b>Главное меню</b>\n\nВыберите действие:",
            parse_mode="HTML",
            reply_markup=keyboard
        )


async def handle_default_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ввода времени по умолчанию"""
    time_str = update.message.text.strip()
    
    try:
        # Валидация формата
        parts = time_str.split(":")
        if len(parts) != 2:
            raise ValueError
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
        
        await set_setting("default_time", time_str)
        await update.message.reply_text(
            f"✅ Время по умолчанию установлено: <b>{time_str}</b>",
            parse_mode="HTML",
            reply_markup=back_button()
        )
        context.user_data["state"] = None
        
    except ValueError:
        await update.message.reply_text(
            "❌ Неверный формат. Используйте ЧЧ:ММ (например: 10:00)"
        )


async def handle_signature_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ввода подписи канала"""
    text = update.message.text.strip()
    
    if text == "-":
        await set_setting("signature", "")
        await update.message.reply_text(
            "✅ Подпись убрана.",
            reply_markup=back_button()
        )
    else:
        if len(text) > 200:
            await update.message.reply_text("❌ Подпись слишком длинная (макс. 200 символов).")
            return
        await set_setting("signature", text)
        await update.message.reply_text(
            f"✅ Подпись установлена:\n<i>{text}</i>",
            parse_mode="HTML",
            reply_markup=back_button()
        )
    
    context.user_data["state"] = None
