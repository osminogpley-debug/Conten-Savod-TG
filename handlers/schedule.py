import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from handlers.start import is_admin
from services.text_gen import generate_text, generate_image_prompt
from services.image_gen import generate_image
from services.scheduler import schedule_post_job, cancel_scheduled_job
from database import (
    add_scheduled_post, get_scheduled_posts,
    cancel_scheduled_post as db_cancel_post
)
from config import TIMEZONE, MAX_POST_LENGTH
from utils import smart_truncate, fit_caption, PREVIEW_PREFIX

logger = logging.getLogger(__name__)


async def schedule_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /schedule"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return
    
    await update.message.reply_text(
        "⏰ <b>Планирование поста</b>\n\n"
        "Отправьте тему для поста.\n"
        "После генерации вы сможете выбрать время публикации.",
        parse_mode="HTML"
    )
    context.user_data["state"] = "awaiting_schedule_topic"


async def handle_schedule_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка темы для запланированного поста"""
    topic = update.message.text.strip()
    
    await update.message.reply_text("⏳ Генерирую пост для планирования...")
    
    try:
        text = await generate_text(topic)
        text = fit_caption(text, PREVIEW_PREFIX)
        
        img_prompt = await generate_image_prompt(topic)
        image_path = await generate_image(img_prompt)
        
        context.user_data["draft_text"] = text
        context.user_data["draft_image"] = image_path
        context.user_data["draft_topic"] = topic
        context.user_data["draft_img_prompt"] = img_prompt
        context.user_data["state"] = "awaiting_schedule_time"
        
        # Показываем текст и просим время
        preview = smart_truncate(text, 300)
        cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="menu_back")]])
        img_status = "🖼 Картинка сгенерирована" if image_path else "⚠️ Без картинки"
        await update.message.reply_text(
            f"📋 <b>Пост готов:</b>\n\n{preview}\n\n"
            f"{img_status}\n\n"
            f"⏰ Укажите дату и время публикации:\n"
            f"Формат: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>\n"
            f"Например: <code>15.03.2026 14:30</code>\n"
            f"Таймзона: {TIMEZONE}",
            parse_mode="HTML",
            reply_markup=cancel_kb
        )
        
    except Exception as e:
        logger.error(f"Ошибка генерации: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка: {e}")
        context.user_data["state"] = None


async def handle_schedule_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка времени для планирования"""
    time_str = update.message.text.strip()
    
    try:
        tz = ZoneInfo(TIMEZONE)
        scheduled_dt = datetime.strptime(time_str, "%d.%m.%Y %H:%M")
        scheduled_dt = scheduled_dt.replace(tzinfo=tz)
        
        now = datetime.now(tz)
        if scheduled_dt <= now:
            await update.message.reply_text(
                "❌ Указанное время уже прошло. Укажите будущее время."
            )
            return
        
        text = context.user_data.get("draft_text", "")
        image_path = context.user_data.get("draft_image")
        topic = context.user_data.get("draft_topic", "")
        
        # Сохраняем в БД
        post_id = await add_scheduled_post(
            topic=topic,
            text=text,
            image_url="",
            image_path=image_path or "",
            scheduled_time=scheduled_dt.isoformat(),
            job_id=f"post_{topic[:20]}"
        )
        
        # Добавляем задачу в планировщик
        from config import CHANNEL_ID
        job = schedule_post_job(
            context.job_queue,
            post_id=post_id,
            text=text,
            image_path=image_path,
            scheduled_time=scheduled_dt,
            channel_id=CHANNEL_ID
        )
        
        if job:
            # Форматируем разницу во времени
            diff = scheduled_dt - now
            hours = diff.seconds // 3600
            minutes = (diff.seconds % 3600) // 60
            days = diff.days
            
            time_left = ""
            if days > 0:
                time_left = f"{days} дн. {hours} ч. {minutes} мин."
            elif hours > 0:
                time_left = f"{hours} ч. {minutes} мин."
            else:
                time_left = f"{minutes} мин."
            
            await update.message.reply_text(
                f"✅ <b>Пост запланирован!</b>\n\n"
                f"📋 Тема: {topic}\n"
                f"🕐 Время: {scheduled_dt.strftime('%d.%m.%Y %H:%M')} MSK\n"
                f"⏳ Через: {time_left}\n"
                f"🆔 ID: #{post_id}\n\n"
                f"Для отмены: /cancel {post_id}",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text("❌ Не удалось запланировать (время в прошлом?).")
        
        # Очищаем черновик
        context.user_data["state"] = None
        context.user_data.pop("draft_text", None)
        context.user_data.pop("draft_image", None)
        
    except ValueError:
        await update.message.reply_text(
            "❌ Неверный формат даты.\n"
            "Используйте: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>\n"
            "Например: <code>15.03.2026 14:30</code>",
            parse_mode="HTML"
        )


async def list_scheduled_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /list — список запланированных постов"""
    # Может вызываться как через команду, так и через callback
    if update.callback_query:
        query = update.callback_query
        if not is_admin(query.from_user.id):
            return
    else:
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Нет доступа.")
            return
    
    posts = await get_scheduled_posts()
    
    if not posts:
        text = "📋 <b>Запланированных постов нет</b>"
    else:
        text = "📋 <b>Запланированные посты:</b>\n\n"
        for post in posts:
            try:
                dt = datetime.fromisoformat(post["scheduled_time"])
                time_str = dt.strftime("%d.%m.%Y %H:%M")
            except:
                time_str = post["scheduled_time"]
            
            topic = post["topic"][:40]
            text += f"🆔 #{post['id']} | 🕐 {time_str}\n📝 {topic}\n\n"
        
        text += "Для отмены: /cancel <ID>"
    
    menu_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="menu_back")]])
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=menu_kb)
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=menu_kb)


async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /cancel <ID> — отмена запланированного поста"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return
    
    args = context.args
    if not args:
        await update.message.reply_text(
            "Использование: /cancel <ID поста>\n"
            "Посмотреть список: /list"
        )
        return
    
    try:
        post_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ ID должен быть числом.")
        return
    
    # Отменяем в БД
    cancelled = await db_cancel_post(post_id)
    
    # Отменяем задачу
    cancel_scheduled_job(context.job_queue, post_id)
    
    if cancelled:
        await update.message.reply_text(f"✅ Пост #{post_id} отменён.")
    else:
        await update.message.reply_text(f"❌ Пост #{post_id} не найден или уже отменён.")
