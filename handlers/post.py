"""Создание и публикация постов — ядро бота"""
import asyncio
import logging
import os
import re as _re
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from handlers.start import is_admin
from services.text_gen import generate_text, generate_image_prompt, generate_hashtags, get_last_text_provider
from services.image_gen import generate_image
from config import MAX_POST_LENGTH
from database import add_post_history, get_setting
from utils import (
    smart_truncate, fit_caption, strip_html, back_button,
    CAPTION_MAX, PREVIEW_PREFIX, PREVIEW_UPD_PREFIX, PREVIEW_IMG_PREFIX, TEST_PREFIX,
)

logger = logging.getLogger(__name__)


def _provider_line(context: ContextTypes.DEFAULT_TYPE) -> str:
    provider = context.user_data.get("draft_ai_provider", "")
    return f"🤖 <b>ИИ:</b> {provider}\n\n" if provider else ""


# ============================================================
# Стандартная клавиатура превью
# ============================================================

def _preview_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Опубликовать", callback_data="post_publish"),
            InlineKeyboardButton("❌ Отменить", callback_data="post_cancel"),
        ],
        [
            InlineKeyboardButton("🔄 Перегенерировать", callback_data="post_regen_text"),
            InlineKeyboardButton("🖼 Сменить картинку", callback_data="post_regen_image"),
        ],
        [
            InlineKeyboardButton("✏️ Редактировать", callback_data="post_edit"),
            InlineKeyboardButton("🏷 Хэштеги", callback_data="post_hashtags"),
        ],
        [
            InlineKeyboardButton("📊 + Опрос", callback_data="post_poll"),
            InlineKeyboardButton("👍 + Реакции", callback_data="post_reactions"),
        ],
        [
            InlineKeyboardButton("⏰ Запланировать", callback_data="post_schedule"),
            InlineKeyboardButton("🧪 Тест", callback_data="post_test"),
        ],
    ])


# ============================================================
# Команда /newpost
# ============================================================

async def newpost_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /newpost"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return

    from config import STYLE_NAMES
    rows = []
    for key, name in STYLE_NAMES.items():
        rows.append([InlineKeyboardButton(name, callback_data=f"style_{key}")])
    rows.append([InlineKeyboardButton("🏠 Меню", callback_data="menu_back")])
    await update.message.reply_text(
        "📝 <b>Создание поста</b>\n\nВыберите стиль:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(rows)
    )


async def style_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор стиля → ожидание темы"""
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return

    style_key = query.data.replace("style_", "")
    context.user_data["post_style"] = style_key

    from config import STYLE_NAMES
    name = STYLE_NAMES.get(style_key, "Стандартный")

    await query.edit_message_text(
        f"📝 Стиль: <b>{name}</b>\n\n"
        "Отправьте тему для поста.\n"
        "Например: <i>5 фраз для путешествий в Китай</i>",
        parse_mode="HTML",
        reply_markup=back_button()
    )
    context.user_data["state"] = "awaiting_post_topic"


async def handle_post_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка темы для нового поста"""
    topic = update.message.text.strip()
    style = context.user_data.get("post_style", "default")

    loading_msg = await update.message.reply_text("⏳ Loading поста... Генерирую текст и картинку.")

    try:
        text, img_prompt = await asyncio.gather(
            generate_text(topic, style=style),
            generate_image_prompt(topic),
        )
        provider = get_last_text_provider() or "неизвестно"
        context.user_data["draft_ai_provider"] = provider

        text = fit_caption(text, PREVIEW_PREFIX)
        image_path = await generate_image(img_prompt)

        context.user_data["draft_text"] = text
        context.user_data["draft_image"] = image_path
        context.user_data["draft_topic"] = topic
        context.user_data["draft_img_prompt"] = img_prompt
        context.user_data["state"] = "draft_ready"

        await loading_msg.edit_text(f"✅ Пост сгенерирован. 🤖 ИИ: {provider}")

        await _show_preview(update, context, text, image_path)

    except Exception as e:
        logger.error(f"Ошибка генерации поста: {e}", exc_info=True)
        try:
            await loading_msg.edit_text("❌ Ошибка генерации поста.")
        except Exception:
            pass
        await update.message.reply_text(
            f"❌ Ошибка при генерации: {e}\n\nПопробуйте ещё раз /newpost",
            reply_markup=back_button()
        )
        context.user_data["state"] = None


# ============================================================
# Превью
# ============================================================

async def _show_preview(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         text: str, image_path: str | None):
    """Показать превью поста с кнопками"""
    keyboard = _preview_keyboard()
    chat_id = update.effective_chat.id

    if image_path and os.path.exists(image_path):
        caption = f"{PREVIEW_PREFIX}{_provider_line(context)}{text}"
        if len(caption) > CAPTION_MAX:
            text = fit_caption(text, PREVIEW_PREFIX)
            caption = f"{PREVIEW_PREFIX}{_provider_line(context)}{text}"
        with open(image_path, "rb") as photo:
            msg = await context.bot.send_photo(
                chat_id=chat_id, photo=photo,
                caption=caption, parse_mode="HTML",
                reply_markup=keyboard
            )
    else:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=f"{PREVIEW_PREFIX}{_provider_line(context)}{text}",
            parse_mode="HTML",
            reply_markup=keyboard
        )
    # Сохраняем message_id для inline-редактирования
    context.user_data["preview_msg_id"] = msg.message_id


# ============================================================
# Inline кнопки поста
# ============================================================

async def post_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка inline кнопок поста"""
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    action = query.data

    if action == "post_publish":
        await _publish_post(update, context)
    elif action == "post_cancel":
        _clear_draft(context)
        menu_kb = back_button()
        try:
            if query.message.photo:
                await query.edit_message_caption(caption="❌ Пост отменён.", reply_markup=menu_kb)
            else:
                await query.edit_message_text("❌ Пост отменён.", reply_markup=menu_kb)
        except Exception:
            await query.message.reply_text("❌ Пост отменён.", reply_markup=menu_kb)
    elif action == "post_regen_text":
        await _regenerate_text(update, context)
    elif action == "post_regen_image":
        await _regenerate_image(update, context)
    elif action == "post_edit":
        await _start_edit(update, context)
    elif action == "post_hashtags":
        await _add_hashtags(update, context)
    elif action == "post_poll":
        await _add_poll(update, context)
    elif action == "post_reactions":
        await _toggle_reactions(update, context)
    elif action == "post_schedule":
        context.user_data["state"] = "awaiting_schedule_time"
        cancel_kb = back_button()
        msg = (
            "⏰ Укажите дату и время публикации.\n\n"
            "Формат: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>\n"
            "Например: <code>15.03.2026 14:30</code>\n\n"
            "Таймзона: Europe/Moscow"
        )
        await query.message.reply_text(msg, parse_mode="HTML", reply_markup=cancel_kb)
    elif action == "post_test":
        await _test_post(update, context)


# ============================================================
# Публикация
# ============================================================

async def _publish_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Публикация поста в канал"""
    query = update.callback_query
    text = context.user_data.get("draft_text", "")
    image_path = context.user_data.get("draft_image")
    topic = context.user_data.get("draft_topic", "")
    add_reactions = context.user_data.get("draft_reactions", False)

    from config import CHANNEL_ID
    if not CHANNEL_ID:
        await query.message.reply_text(
            "❌ Канал не подключён!\nИспользуйте /connect",
            reply_markup=back_button()
        )
        return

    try:
        # Хэштеги и подпись
        final_text = await _finalize_text(text, context)

        if image_path and os.path.exists(image_path):
            if len(final_text) <= CAPTION_MAX:
                with open(image_path, "rb") as photo:
                    msg = await _safe_send_photo(context.bot, CHANNEL_ID, photo, final_text)
            else:
                with open(image_path, "rb") as photo:
                    await context.bot.send_photo(chat_id=CHANNEL_ID, photo=photo)
                msg = await _safe_send_message(context.bot, CHANNEL_ID, final_text)
        else:
            msg = await _safe_send_message(context.bot, CHANNEL_ID, final_text)

        # Реакции (inline кнопки под постом в канале)
        if add_reactions and msg:
            try:
                await context.bot.edit_message_reply_markup(
                    chat_id=CHANNEL_ID,
                    message_id=msg.message_id,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔥", callback_data="react_fire"),
                        InlineKeyboardButton("👍", callback_data="react_like"),
                        InlineKeyboardButton("😂", callback_data="react_laugh"),
                        InlineKeyboardButton("🤔", callback_data="react_think"),
                    ]])
                )
            except Exception:
                pass

        # Опрос
        poll_question = context.user_data.get("draft_poll_question")
        poll_options = context.user_data.get("draft_poll_options")
        if poll_question and poll_options:
            try:
                await context.bot.send_poll(
                    chat_id=CHANNEL_ID,
                    question=poll_question,
                    options=poll_options,
                    is_anonymous=True,
                )
            except Exception as e:
                logger.warning(f"Не удалось отправить опрос: {e}")

        # Сохраняем в историю
        hashtags = context.user_data.get("draft_hashtags", "")
        style = context.user_data.get("post_style", "default")
        await add_post_history(topic, text, "", str(CHANNEL_ID), msg.message_id)

        # Успех
        success_kb = back_button()
        try:
            if query.message.photo:
                await query.edit_message_caption(
                    caption="✅ Пост опубликован в канал!",
                    reply_markup=success_kb
                )
            else:
                await query.edit_message_text(
                    "✅ Пост опубликован в канал!",
                    reply_markup=success_kb
                )
        except:
            pass

        _clear_draft(context)
        logger.info(f"Пост опубликован: {topic[:50]}")

    except Exception as e:
        logger.error(f"Ошибка публикации: {e}", exc_info=True)
        await query.message.reply_text(
            f"❌ Ошибка публикации: {e}",
            reply_markup=back_button()
        )


async def _finalize_text(text: str, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Добавляет хэштеги и подпись к тексту"""
    hashtags = context.user_data.get("draft_hashtags", "")
    signature = await get_setting("signature", "")

    result = text.rstrip()
    if hashtags:
        result += "\n\n" + hashtags
    if signature:
        result += "\n\n" + signature

    # Подгоняем под лимит
    return fit_caption(result, "")


# ============================================================
# Редактирование текста (P0.1)
# ============================================================

async def _start_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет текст для ручного редактирования"""
    query = update.callback_query
    text = context.user_data.get("draft_text", "")

    context.user_data["state"] = "awaiting_post_edit"

    await query.message.reply_text(
        "✏️ <b>Редактирование текста</b>\n\n"
        "Отредактируйте текст и отправьте обратно.\n\n"
        f"<code>{strip_html(text)}</code>",
        parse_mode="HTML",
        reply_markup=back_button()
    )


async def handle_post_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка отредактированного текста"""
    new_text = update.message.text.strip()

    if len(new_text) < 50:
        await update.message.reply_text("❌ Текст слишком короткий (мин. 50 символов).")
        return

    new_text = fit_caption(new_text, PREVIEW_PREFIX)
    context.user_data["draft_text"] = new_text
    context.user_data["state"] = "draft_ready"

    image_path = context.user_data.get("draft_image")
    await _show_preview(update, context, new_text, image_path)


# ============================================================
# Хэштеги (P1.7)
# ============================================================

async def _add_hashtags(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Генерация хэштегов"""
    query = update.callback_query
    text = context.user_data.get("draft_text", "")

    if query.message.photo:
        await query.edit_message_caption(caption="⏳ Генерирую хэштеги...")
    else:
        await query.edit_message_text("⏳ Генерирую хэштеги...")

    try:
        tags = await generate_hashtags(text)
        if tags:
            context.user_data["draft_hashtags"] = tags
            result_text = f"🏷 <b>Хэштеги добавлены:</b>\n{tags}"
        else:
            result_text = "⚠️ Не удалось сгенерировать хэштеги."

        keyboard = _preview_keyboard()
        if query.message.photo:
            cap = fit_caption(text, PREVIEW_PREFIX)
            if tags:
                cap = fit_caption(text, PREVIEW_PREFIX + "(+🏷) ")
            await query.edit_message_caption(
                caption=f"{PREVIEW_PREFIX}{cap}\n\n{tags}" if tags else f"{PREVIEW_PREFIX}{cap}",
                parse_mode="HTML", reply_markup=keyboard
            )
        else:
            await query.edit_message_text(
                f"{PREVIEW_PREFIX}{text}\n\n{tags}" if tags else f"{PREVIEW_PREFIX}{text}",
                parse_mode="HTML", reply_markup=keyboard
            )
    except Exception as e:
        await query.message.reply_text(f"❌ Ошибка: {e}")


# ============================================================
# Опрос (P2.12)
# ============================================================

async def _add_poll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Генерация опроса по теме поста"""
    query = update.callback_query
    topic = context.user_data.get("draft_topic", "")

    if query.message.photo:
        await query.edit_message_caption(caption="⏳ Генерирую опрос...")
    else:
        await query.edit_message_text("⏳ Генерирую опрос...")

    try:
        from services.text_gen import generate_text_with_prompt
        prompt = (
            f"Сгенерируй опрос для Telegram на тему: {topic}\n"
            f"Формат СТРОГО:\n"
            f"ВОПРОС: <текст вопроса>\n"
            f"1. <вариант 1>\n"
            f"2. <вариант 2>\n"
            f"3. <вариант 3>\n"
            f"4. <вариант 4>\n"
            f"Только опрос, без пояснений."
        )
        result = await generate_text_with_prompt(prompt)

        if result:
            lines = [l.strip() for l in result.strip().split("\n") if l.strip()]
            question = ""
            options = []
            for line in lines:
                if line.upper().startswith("ВОПРОС:") or line.upper().startswith("ВОПРОС :"):
                    question = line.split(":", 1)[1].strip()
                elif _re.match(r'^\d+[\.\)]\s*', line):
                    opt = _re.sub(r'^\d+[\.\)]\s*', '', line).strip()
                    if opt:
                        options.append(opt)

            if question and len(options) >= 2:
                context.user_data["draft_poll_question"] = question
                context.user_data["draft_poll_options"] = options[:10]

                poll_text = f"📊 <b>Опрос добавлен:</b>\n\n{question}\n"
                for i, o in enumerate(options[:10], 1):
                    poll_text += f"{i}. {o}\n"

                keyboard = _preview_keyboard()
                if query.message.photo:
                    text = context.user_data.get("draft_text", "")
                    cap = fit_caption(text, PREVIEW_PREFIX)
                    await query.edit_message_caption(
                        caption=f"{PREVIEW_PREFIX}{cap}",
                        parse_mode="HTML", reply_markup=keyboard
                    )
                else:
                    text = context.user_data.get("draft_text", "")
                    await query.edit_message_text(
                        f"{PREVIEW_PREFIX}{text}",
                        parse_mode="HTML", reply_markup=keyboard
                    )
                await query.message.reply_text(poll_text, parse_mode="HTML")
                return

        await query.message.reply_text("⚠️ Не удалось сгенерировать опрос.")
        # Восстанавливаем превью
        text = context.user_data.get("draft_text", "")
        image_path = context.user_data.get("draft_image")
        keyboard = _preview_keyboard()
        if query.message.photo:
            cap = fit_caption(text, PREVIEW_PREFIX)
            await query.edit_message_caption(
                caption=f"{PREVIEW_PREFIX}{cap}",
                parse_mode="HTML", reply_markup=keyboard
            )
        else:
            await query.edit_message_text(
                f"{PREVIEW_PREFIX}{text}",
                parse_mode="HTML", reply_markup=keyboard
            )

    except Exception as e:
        logger.error(f"Ошибка генерации опроса: {e}")
        await query.message.reply_text(f"❌ Ошибка: {e}")


# ============================================================
# Реакции (P2.13)
# ============================================================

async def _toggle_reactions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Включить/выключить кнопки реакций"""
    query = update.callback_query
    current = context.user_data.get("draft_reactions", False)
    context.user_data["draft_reactions"] = not current

    status = "✅ включены" if not current else "❌ выключены"
    text = context.user_data.get("draft_text", "")
    keyboard = _preview_keyboard()

    try:
        if query.message.photo:
            cap = fit_caption(text, PREVIEW_PREFIX)
            await query.edit_message_caption(
                caption=f"{PREVIEW_PREFIX}{cap}\n\n👍 Реакции: {status}",
                parse_mode="HTML", reply_markup=keyboard
            )
        else:
            await query.edit_message_text(
                f"{PREVIEW_PREFIX}{text}\n\n👍 Реакции: {status}",
                parse_mode="HTML", reply_markup=keyboard
            )
    except:
        pass


# ============================================================
# Перегенерация (inline edit)
# ============================================================

async def _regenerate_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Перегенерация текста — inline edit вместо нового сообщения"""
    query = update.callback_query
    topic = context.user_data.get("draft_topic", "")
    style = context.user_data.get("post_style", "default")

    if query.message.photo:
        await query.edit_message_caption(caption="⏳ Loading поста... Перегенерирую текст...")
    else:
        await query.edit_message_text("⏳ Loading поста... Перегенерирую текст...")

    try:
        new_text = await generate_text(topic, style=style)
        provider = get_last_text_provider() or "неизвестно"
        context.user_data["draft_ai_provider"] = provider
        new_text = fit_caption(new_text, PREVIEW_UPD_PREFIX)
        context.user_data["draft_text"] = new_text

        keyboard = _preview_keyboard()

        if query.message.photo:
            cap = fit_caption(new_text, PREVIEW_UPD_PREFIX)
            await query.edit_message_caption(
                caption=f"{PREVIEW_UPD_PREFIX}{_provider_line(context)}{cap}",
                parse_mode="HTML", reply_markup=keyboard
            )
        else:
            await query.edit_message_text(
                text=f"{PREVIEW_UPD_PREFIX}{_provider_line(context)}{new_text}",
                parse_mode="HTML", reply_markup=keyboard
            )
    except Exception as e:
        await query.message.reply_text(f"❌ Ошибка перегенерации: {e}")


async def _regenerate_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Перегенерация картинки"""
    query = update.callback_query
    text = context.user_data.get("draft_text", "")
    img_prompt = context.user_data.get("draft_img_prompt", "")

    # Отправляем статус через reply — нельзя edit photo на другое фото
    await query.message.reply_text("⏳ Генерирую новую картинку...")

    try:
        new_image = await generate_image(img_prompt)
        context.user_data["draft_image"] = new_image

        keyboard = _preview_keyboard()
        chat_id = update.effective_chat.id

        if new_image and os.path.exists(new_image):
            cap = fit_caption(text, PREVIEW_IMG_PREFIX)
            with open(new_image, "rb") as photo:
                msg = await context.bot.send_photo(
                    chat_id=chat_id, photo=photo,
                    caption=f"{PREVIEW_IMG_PREFIX}{cap}",
                    parse_mode="HTML", reply_markup=keyboard
                )
            context.user_data["preview_msg_id"] = msg.message_id
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ Не удалось. Пост без картинки.\n\n{text}",
                parse_mode="HTML", reply_markup=keyboard
            )
    except Exception as e:
        await query.message.reply_text(f"❌ Ошибка: {e}")


async def _test_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправка тестового поста в ЛС"""
    query = update.callback_query
    text = context.user_data.get("draft_text", "")
    image_path = context.user_data.get("draft_image")
    user_id = query.from_user.id

    try:
        final_text = await _finalize_text(text, context)
        test_text = fit_caption(final_text, TEST_PREFIX)
        test_text = f"{TEST_PREFIX}{test_text}"

        if image_path and os.path.exists(image_path):
            with open(image_path, "rb") as photo:
                await context.bot.send_photo(
                    chat_id=user_id, photo=photo,
                    caption=test_text, parse_mode="HTML"
                )
        else:
            await context.bot.send_message(
                chat_id=user_id, text=test_text, parse_mode="HTML"
            )

        await query.message.reply_text("🧪 Тестовый пост отправлен в ЛС!")

    except Exception as e:
        await query.message.reply_text(f"❌ Ошибка: {e}\nНачните чат с ботом.")


# ============================================================
# Утилиты
# ============================================================

def _clear_draft(context: ContextTypes.DEFAULT_TYPE):
    """Очистить черновик"""
    context.user_data["state"] = None
    for key in ["draft_text", "draft_image", "draft_topic", "draft_img_prompt",
                 "draft_hashtags", "draft_reactions", "draft_poll_question",
                 "draft_poll_options", "post_style", "preview_msg_id"]:
        context.user_data.pop(key, None)


async def _safe_send_message(bot, chat_id, text):
    """Отправка сообщения с fallback при ошибке HTML"""
    try:
        return await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
    except Exception:
        return await bot.send_message(chat_id=chat_id, text=strip_html(text))


async def _safe_send_photo(bot, chat_id, photo, caption):
    """Отправка фото с fallback при ошибке HTML"""
    try:
        return await bot.send_photo(chat_id=chat_id, photo=photo, caption=caption, parse_mode="HTML")
    except Exception:
        photo.seek(0)
        return await bot.send_photo(chat_id=chat_id, photo=photo, caption=strip_html(caption))
