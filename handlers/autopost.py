"""Очередь тем + Автопостинг"""
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from handlers.start import is_admin
from database import (
    add_topics_bulk, get_topic_queue, clear_topic_queue,
    remove_topic_from_queue, get_next_topic, mark_topic_used,
    get_setting, set_setting,
)
from services.text_gen import generate_text, generate_image_prompt
from services.image_gen import generate_image
from services.scheduler import schedule_post_job
from database import add_scheduled_post
import config
from utils import smart_truncate, back_button

logger = logging.getLogger(__name__)

# ============================================================
# Очередь тем
# ============================================================

async def queue_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать очередь тем"""
    query = update.callback_query
    if query:
        await query.answer()
        if not is_admin(query.from_user.id):
            return

    topics = await get_topic_queue()

    if not topics:
        text = "📚 <b>Очередь тем пуста</b>\n\nОтправьте темы (каждая с новой строки)."
    else:
        text = f"📚 <b>Очередь тем ({len(topics)}):</b>\n\n"
        for i, t in enumerate(topics[:15], 1):
            text += f"{i}. {t['topic'][:50]}\n"
        if len(topics) > 15:
            text += f"\n... и ещё {len(topics) - 15}"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить темы", callback_data="queue_add")],
        [InlineKeyboardButton("🗑 Очистить очередь", callback_data="queue_clear")],
        [InlineKeyboardButton("🏠 Меню", callback_data="menu_back")],
    ])

    if query:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def queue_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопок очереди"""
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return

    action = query.data

    if action == "queue_add":
        context.user_data["state"] = "awaiting_queue_topics"
        await query.edit_message_text(
            "📚 <b>Добавление тем</b>\n\n"
            "Отправьте темы — <b>каждая с новой строки</b>.\n\n"
            "Пример:\n"
            "<i>Китайский чай\n"
            "Великая стена\n"
            "Бизнес в Шэньчжэне</i>",
            parse_mode="HTML",
            reply_markup=back_button()
        )

    elif action == "queue_clear":
        await clear_topic_queue()
        await query.edit_message_text(
            "✅ Очередь тем очищена.",
            reply_markup=back_button()
        )


async def handle_queue_topics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ввода тем для очереди"""
    raw = update.message.text.strip()
    topics = [t.strip() for t in raw.split("\n") if t.strip()]

    if not topics:
        await update.message.reply_text("❌ Не найдено тем. Отправьте каждую тему с новой строки.")
        return

    count = await add_topics_bulk(topics)
    context.user_data["state"] = None

    await update.message.reply_text(
        f"✅ Добавлено <b>{count}</b> тем в очередь.",
        parse_mode="HTML",
        reply_markup=back_button()
    )


# ============================================================
# Автопостинг
# ============================================================

async def autopost_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню автопостинга"""
    query = update.callback_query
    if query:
        await query.answer()
        if not is_admin(query.from_user.id):
            return

    enabled = await get_setting("autopost_enabled", "0")
    times = await get_setting("autopost_times", "10:00,18:00")
    style = await get_setting("autopost_style", "default")

    from config import STYLE_NAMES
    style_name = STYLE_NAMES.get(style, style)
    status = "✅ Включён" if enabled == "1" else "❌ Выключен"

    text = (
        f"🤖 <b>Автопостинг</b>\n\n"
        f"Статус: {status}\n"
        f"Время: <code>{times}</code>\n"
        f"Стиль: {style_name}\n\n"
        f"Бот берёт темы из очереди и публикует автоматически."
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "❌ Выключить" if enabled == "1" else "✅ Включить",
            callback_data="autopost_toggle"
        )],
        [InlineKeyboardButton("🕐 Изменить время", callback_data="autopost_times")],
        [InlineKeyboardButton("🎨 Изменить стиль", callback_data="autopost_style")],
        [InlineKeyboardButton("🏠 Меню", callback_data="menu_back")],
    ])

    if query:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def autopost_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопок автопостинга"""
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return

    action = query.data

    if action == "autopost_toggle":
        current = await get_setting("autopost_enabled", "0")
        new_val = "0" if current == "1" else "1"
        await set_setting("autopost_enabled", new_val)

        if new_val == "1":
            _setup_autopost_jobs(context.job_queue)
            await query.edit_message_text(
                "✅ Автопостинг <b>включён</b>.\n"
                "Темы будут браться из очереди.",
                parse_mode="HTML", reply_markup=back_button()
            )
        else:
            _remove_autopost_jobs(context.job_queue)
            await query.edit_message_text(
                "❌ Автопостинг <b>выключен</b>.",
                parse_mode="HTML", reply_markup=back_button()
            )

    elif action == "autopost_times":
        context.user_data["state"] = "awaiting_autopost_times"
        await query.edit_message_text(
            "🕐 <b>Время автопостинга</b>\n\n"
            "Отправьте время через запятую.\n"
            "Пример: <code>10:00,14:00,18:00</code>\n\n"
            f"Таймзона: {config.TIMEZONE}",
            parse_mode="HTML", reply_markup=back_button()
        )

    elif action == "autopost_style":
        from config import STYLE_NAMES
        rows = []
        for key, name in STYLE_NAMES.items():
            rows.append([InlineKeyboardButton(name, callback_data=f"autopost_setstyle_{key}")])
        rows.append([InlineKeyboardButton("🏠 Меню", callback_data="menu_back")])
        await query.edit_message_text(
            "🎨 <b>Стиль автопостинга</b>\n\nВыберите стиль:",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows)
        )

    elif action.startswith("autopost_setstyle_"):
        style_key = action.replace("autopost_setstyle_", "")
        await set_setting("autopost_style", style_key)
        from config import STYLE_NAMES
        name = STYLE_NAMES.get(style_key, style_key)
        await query.edit_message_text(
            f"✅ Стиль автопостинга: <b>{name}</b>",
            parse_mode="HTML", reply_markup=back_button()
        )


async def handle_autopost_times(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ввода времени автопостинга"""
    raw = update.message.text.strip()
    parts = [p.strip() for p in raw.split(",")]

    # Валидация
    valid = []
    for p in parts:
        try:
            h, m = p.split(":")
            h, m = int(h), int(m)
            if 0 <= h <= 23 and 0 <= m <= 59:
                valid.append(f"{h:02d}:{m:02d}")
        except:
            pass

    if not valid:
        await update.message.reply_text("❌ Неверный формат. Пример: 10:00,18:00")
        return

    times_str = ",".join(valid)
    await set_setting("autopost_times", times_str)
    context.user_data["state"] = None

    # Перезапускаем джобы
    _remove_autopost_jobs(context.job_queue)
    enabled = await get_setting("autopost_enabled", "0")
    if enabled == "1":
        _setup_autopost_jobs(context.job_queue)

    await update.message.reply_text(
        f"✅ Время автопостинга: <b>{times_str}</b>",
        parse_mode="HTML", reply_markup=back_button()
    )


# ============================================================
# Автопостинг — job
# ============================================================

async def _autopost_job(context: ContextTypes.DEFAULT_TYPE):
    """Job для автопостинга: берёт тему из очереди, генерирует и публикует"""
    try:
        enabled = await get_setting("autopost_enabled", "0")
        if enabled != "1":
            return

        topic_row = await get_next_topic()
        if not topic_row:
            logger.info("Автопостинг: очередь тем пуста")
            # Уведомляем админа
            if config.ADMIN_ID:
                try:
                    await context.bot.send_message(
                        config.ADMIN_ID,
                        "⚠️ <b>Автопостинг</b>: очередь тем пуста.\n"
                        "Добавьте темы через меню 📚 Очередь тем.",
                        parse_mode="HTML"
                    )
                except:
                    pass
            return

        topic = topic_row["topic"]
        logger.info(f"Автопостинг: генерация поста на тему «{topic}»")

        style = await get_setting("autopost_style", "default")
        text = await generate_text(topic, style=style)

        if not text or len(text) < 100:
            logger.error(f"Автопостинг: не удалось сгенерировать текст для «{topic}»")
            return

        # Генерация картинки
        img_prompt = await generate_image_prompt(topic)
        image_path = await generate_image(img_prompt)

        channel_id = config.CHANNEL_ID
        if not channel_id:
            logger.error("Автопостинг: CHANNEL_ID не задан")
            return

        # Публикация
        from utils import fit_caption
        text = fit_caption(text, "")

        # Хэштеги
        hashtags_enabled = await get_setting("hashtags_enabled", "0")
        if hashtags_enabled == "1":
            from services.text_gen import generate_hashtags
            tags = await generate_hashtags(text)
            if tags:
                text = text.rstrip() + "\n\n" + tags

        # Подпись
        signature = await get_setting("signature", "")
        if signature:
            text = text.rstrip() + "\n\n" + signature

        text = fit_caption(text, "")

        if image_path:
            import os
            if os.path.exists(image_path):
                with open(image_path, "rb") as photo:
                    try:
                        await context.bot.send_photo(
                            chat_id=channel_id, photo=photo,
                            caption=text, parse_mode="HTML"
                        )
                    except:
                        photo.seek(0)
                        from utils import strip_html
                        await context.bot.send_photo(
                            chat_id=channel_id, photo=photo,
                            caption=strip_html(text)
                        )
            else:
                try:
                    await context.bot.send_message(
                        chat_id=channel_id, text=text, parse_mode="HTML"
                    )
                except:
                    from utils import strip_html
                    await context.bot.send_message(
                        chat_id=channel_id, text=strip_html(text)
                    )
        else:
            try:
                await context.bot.send_message(
                    chat_id=channel_id, text=text, parse_mode="HTML"
                )
            except:
                from utils import strip_html
                await context.bot.send_message(
                    chat_id=channel_id, text=strip_html(text)
                )

        # Отмечаем тему как использованную
        await mark_topic_used(topic_row["id"])

        # Сохраняем в историю
        from database import add_post_history
        await add_post_history(topic, text, "", channel_id, 0)

        logger.info(f"Автопостинг: опубликован пост «{topic[:40]}»")

    except Exception as e:
        logger.error(f"Автопостинг: ошибка — {e}", exc_info=True)


def _setup_autopost_jobs(job_queue):
    """Настроить ежедневные джобы автопостинга"""
    import asyncio

    async def _get_times():
        return await get_setting("autopost_times", "10:00,18:00")

    # Синхронно получить времена нельзя — используем фиксированный интервал
    # Джоб проверяет каждый час, публикует если время подошло
    _remove_autopost_jobs(job_queue)

    job_queue.run_repeating(
        _autopost_check_job,
        interval=1800,  # каждые 30 мин
        first=30,
        name="autopost_check"
    )
    logger.info("Автопостинг: джоб запущен (проверка каждые 30 мин)")


async def _autopost_check_job(context: ContextTypes.DEFAULT_TYPE):
    """Проверяет, пора ли публиковать автопост"""
    try:
        enabled = await get_setting("autopost_enabled", "0")
        if enabled != "1":
            return

        times_str = await get_setting("autopost_times", "10:00,18:00")
        times = [t.strip() for t in times_str.split(",")]

        tz = ZoneInfo(config.TIMEZONE)
        now = datetime.now(tz)
        current_time = now.strftime("%H:%M")

        # Проверяем, совпадает ли текущее время (с точностью 30мин)
        for t in times:
            try:
                target_h, target_m = t.split(":")
                target_h, target_m = int(target_h), int(target_m)
                target_dt = now.replace(hour=target_h, minute=target_m, second=0, microsecond=0)
                diff = abs((now - target_dt).total_seconds())

                if diff <= 900:  # ±15 мин
                    # Проверяем, не публиковали ли уже сегодня в это время
                    last_key = f"autopost_last_{t}"
                    last_date = await get_setting(last_key, "")
                    today = now.strftime("%Y-%m-%d")

                    if last_date != today:
                        await set_setting(last_key, today)
                        await _autopost_job(context)
                        return  # Один пост за проверку
            except:
                continue
    except Exception as e:
        logger.error(f"Автопостинг check: {e}")


def _remove_autopost_jobs(job_queue):
    """Убрать джобы автопостинга"""
    jobs = job_queue.get_jobs_by_name("autopost_check")
    for j in jobs:
        j.schedule_removal()


async def setup_autopost_on_start(job_queue):
    """Вызывается при старте бота"""
    enabled = await get_setting("autopost_enabled", "0")
    if enabled == "1":
        _setup_autopost_jobs(job_queue)
        logger.info("Автопостинг восстановлен при старте")
