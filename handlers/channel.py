import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ChatMemberUpdated
from telegram.ext import ContextTypes
from telegram.constants import ChatMemberStatus, ChatType
from handlers.start import is_admin
from database import set_setting, get_setting
from config import set_channel_id, set_admin_id, CHANNEL_ID, ADMIN_ID

logger = logging.getLogger(__name__)


async def connect_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /connect — подключить канал"""
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📨 Переслать сообщение из канала", callback_data="channel_forward")],
        [InlineKeyboardButton("✏️ Ввести ID/username канала", callback_data="channel_manual")],
        [InlineKeyboardButton("🔙 Главное меню", callback_data="settings_back")],
    ])

    current = CHANNEL_ID
    status_text = f"✅ Текущий канал: <code>{current}</code>" if current else "❌ Канал не подключён"

    await update.message.reply_text(
        f"📺 <b>Подключение канала</b>\n\n"
        f"{status_text}\n\n"
        f"<b>Инструкция:</b>\n"
        f"1. Добавьте бота в канал как администратора\n"
        f"   (Настройки канала → Администраторы → Добавить → найдите бота)\n"
        f"2. Дайте боту права: <i>публикация сообщений</i>\n"
        f"3. Выберите способ подключения ниже\n\n"
        f"<b>Способ 1</b>: Перешлите любое сообщение из канала боту\n"
        f"<b>Способ 2</b>: Введите ID или @username канала вручную",
        parse_mode="HTML",
        reply_markup=keyboard
    )


async def channel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка inline кнопок подключения канала"""
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    action = query.data

    if action == "channel_forward":
        await query.edit_message_text(
            "📨 <b>Пересылка сообщения</b>\n\n"
            "Перешлите (forward) любое сообщение из вашего канала сюда.\n"
            "Бот автоматически определит ID канала и проверит свои права.",
            parse_mode="HTML"
        )
        context.user_data["state"] = "awaiting_channel_forward"

    elif action == "channel_manual":
        await query.edit_message_text(
            "✏️ <b>Ввод ID канала</b>\n\n"
            "Отправьте ID или username канала.\n\n"
            "Примеры:\n"
            "• <code>@chinaya</code>\n"
            "• <code>-1001234567890</code>\n\n"
            "💡 <i>Чтобы узнать ID: перешлите сообщение из канала боту @userinfobot</i>",
            parse_mode="HTML"
        )
        context.user_data["state"] = "awaiting_channel_id"

    elif action == "channel_disconnect":
        set_channel_id("")
        await set_setting("channel_id", "")
        await query.edit_message_text(
            "✅ Канал отключён.\n\nИспользуйте /connect для подключения нового.",
            parse_mode="HTML"
        )

    elif action == "channel_confirm":
        channel_id = context.user_data.get("pending_channel_id", "")
        channel_title = context.user_data.get("pending_channel_title", "")
        if channel_id:
            set_channel_id(str(channel_id))
            await set_setting("channel_id", str(channel_id))
            await query.edit_message_text(
                f"✅ <b>Канал подключён!</b>\n\n"
                f"📺 {channel_title}\n"
                f"🆔 <code>{channel_id}</code>\n\n"
                f"Теперь вы можете создавать и публиковать посты.\n"
                f"Используйте /start для главного меню.",
                parse_mode="HTML"
            )
            context.user_data.pop("pending_channel_id", None)
            context.user_data.pop("pending_channel_title", None)
            context.user_data["state"] = None
            logger.info(f"Канал подключён: {channel_id} ({channel_title})")

    elif action == "channel_cancel":
        context.user_data.pop("pending_channel_id", None)
        context.user_data.pop("pending_channel_title", None)
        context.user_data["state"] = None
        await query.edit_message_text("❌ Подключение отменено.\n\n/connect — попробовать снова")


async def handle_channel_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка пересланного сообщения из канала"""
    msg = update.message

    # Проверяем что сообщение переслано из канала
    if msg.forward_from_chat and msg.forward_from_chat.type == ChatType.CHANNEL:
        channel = msg.forward_from_chat
        channel_id = channel.id
        channel_title = channel.title or "Без названия"

        # Проверяем права бота в канале
        try:
            bot_member = await context.bot.get_chat_member(channel_id, context.bot.id)
            if bot_member.status in (ChatMemberStatus.ADMINISTRATOR,):
                # Бот уже админ — сразу подключаем
                context.user_data["pending_channel_id"] = channel_id
                context.user_data["pending_channel_title"] = channel_title

                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Подтвердить", callback_data="channel_confirm")],
                    [InlineKeyboardButton("❌ Отмена", callback_data="channel_cancel")],
                ])

                await msg.reply_text(
                    f"📺 <b>Канал найден!</b>\n\n"
                    f"📛 Название: <b>{channel_title}</b>\n"
                    f"🆔 ID: <code>{channel_id}</code>\n"
                    f"✅ Бот — администратор канала\n\n"
                    f"Подтвердите подключение:",
                    parse_mode="HTML",
                    reply_markup=keyboard
                )
            else:
                await msg.reply_text(
                    f"⚠️ <b>Канал найден, но бот не админ!</b>\n\n"
                    f"📛 Канал: <b>{channel_title}</b>\n"
                    f"Статус бота: {bot_member.status}\n\n"
                    f"<b>Добавьте бота как администратора канала</b>, затем повторите попытку.\n\n"
                    f"Настройки канала → Администраторы → Добавить администратора → найдите бота",
                    parse_mode="HTML"
                )
        except Exception as e:
            # Не удалось проверить — предлагаем добавить бота
            context.user_data["pending_channel_id"] = channel_id
            context.user_data["pending_channel_title"] = channel_title

            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Подключить всё равно", callback_data="channel_confirm")],
                [InlineKeyboardButton("❌ Отмена", callback_data="channel_cancel")],
            ])

            await msg.reply_text(
                f"📺 <b>Канал найден!</b>\n\n"
                f"📛 Название: <b>{channel_title}</b>\n"
                f"🆔 ID: <code>{channel_id}</code>\n\n"
                f"⚠️ Не удалось проверить права бота: {e}\n\n"
                f"Убедитесь, что бот добавлен как администратор канала.\n"
                f"Подключить?",
                parse_mode="HTML",
                reply_markup=keyboard
            )
    else:
        await msg.reply_text(
            "❌ Это не сообщение из канала.\n\n"
            "Перешлите (forward) сообщение именно из <b>канала</b>, не из группы или чата.",
            parse_mode="HTML"
        )


async def handle_channel_id_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ввода ID/username канала"""
    user_input = update.message.text.strip()

    # Определяем формат
    channel_id = user_input
    channel_title = user_input

    try:
        # Пробуем получить информацию о канале
        chat = await context.bot.get_chat(channel_id)

        if chat.type != ChatType.CHANNEL:
            await update.message.reply_text(
                f"❌ <code>{user_input}</code> — это не канал (тип: {chat.type}).\n"
                f"Укажите ID или @username именно канала.",
                parse_mode="HTML"
            )
            return

        channel_id = chat.id
        channel_title = chat.title or user_input

        # Проверяем права бота
        try:
            bot_member = await context.bot.get_chat_member(channel_id, context.bot.id)
            is_admin_in_channel = bot_member.status == ChatMemberStatus.ADMINISTRATOR
        except:
            is_admin_in_channel = False

        context.user_data["pending_channel_id"] = channel_id
        context.user_data["pending_channel_title"] = channel_title

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Подтвердить", callback_data="channel_confirm")],
            [InlineKeyboardButton("❌ Отмена", callback_data="channel_cancel")],
        ])

        admin_status = "✅ Бот — администратор" if is_admin_in_channel else "⚠️ Бот НЕ администратор — добавьте его!"

        await update.message.reply_text(
            f"📺 <b>Канал найден!</b>\n\n"
            f"📛 Название: <b>{channel_title}</b>\n"
            f"🆔 ID: <code>{channel_id}</code>\n"
            f"{admin_status}\n\n"
            f"Подтвердите подключение:",
            parse_mode="HTML",
            reply_markup=keyboard
        )

    except Exception as e:
        logger.error(f"Ошибка получения информации о канале {user_input}: {e}")

        # Если не получилось — проверяем формат ID
        if user_input.startswith("-100") or user_input.startswith("@"):
            context.user_data["pending_channel_id"] = user_input
            context.user_data["pending_channel_title"] = user_input

            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Подключить", callback_data="channel_confirm")],
                [InlineKeyboardButton("❌ Отмена", callback_data="channel_cancel")],
            ])

            await update.message.reply_text(
                f"⚠️ Не удалось проверить канал <code>{user_input}</code>.\n\n"
                f"Возможные причины:\n"
                f"• Бот не добавлен в канал\n"
                f"• Неверный ID/username\n\n"
                f"Убедитесь, что бот добавлен как админ.\n"
                f"Подключить всё равно?",
                parse_mode="HTML",
                reply_markup=keyboard
            )
        else:
            await update.message.reply_text(
                f"❌ Неверный формат: <code>{user_input}</code>\n\n"
                f"Используйте:\n"
                f"• <code>@channel_username</code>\n"
                f"• <code>-100xxxxxxxxxx</code> (числовой ID)\n\n"
                f"Или перешлите сообщение из канала.",
                parse_mode="HTML"
            )


async def handle_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка события добавления/удаления бота из канала"""
    my_chat_member: ChatMemberUpdated = update.my_chat_member
    if not my_chat_member:
        return

    chat = my_chat_member.chat
    new_status = my_chat_member.new_chat_member.status
    old_status = my_chat_member.old_chat_member.status

    # Если бот добавлен в канал как админ
    if chat.type == ChatType.CHANNEL:
        if new_status == ChatMemberStatus.ADMINISTRATOR and old_status != ChatMemberStatus.ADMINISTRATOR:
            logger.info(f"Бот добавлен как админ в канал: {chat.title} ({chat.id})")

            # Автоматически запоминаем канал
            set_channel_id(str(chat.id))
            await set_setting("channel_id", str(chat.id))
            await set_setting("channel_title", chat.title or "")

            # Уведомляем админа, если он известен
            from config import ADMIN_ID
            if ADMIN_ID:
                try:
                    await context.bot.send_message(
                        chat_id=ADMIN_ID,
                        text=(
                            f"🎉 <b>Бот подключён к каналу!</b>\n\n"
                            f"📺 {chat.title}\n"
                            f"🆔 <code>{chat.id}</code>\n\n"
                            f"Канал автоматически сохранён. Можете публиковать посты!"
                        ),
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"Не удалось уведомить админа: {e}")

        elif new_status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED) and \
             old_status == ChatMemberStatus.ADMINISTRATOR:
            logger.info(f"Бот удалён из канала: {chat.title} ({chat.id})")

            # Очищаем сохранённый CHANNEL_ID
            set_channel_id("")
            await set_setting("channel_id", "")

            from config import ADMIN_ID
            if ADMIN_ID:
                try:
                    await context.bot.send_message(
                        chat_id=ADMIN_ID,
                        text=(
                            f"⚠️ <b>Бот удалён из канала</b>\n\n"
                            f"📺 {chat.title}\n"
                            f"🆔 <code>{chat.id}</code>\n\n"
                            f"Канал отключён автоматически.\n"
                            f"Используйте /connect для подключения к другому каналу."
                        ),
                        parse_mode="HTML"
                    )
                except:
                    pass
