import logging
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from telegram.ext import ContextTypes
import config  # Импортируем модуль, а не значение (чтобы CHANNEL_ID был always-fresh)
from database import mark_post_published, get_post_by_id, get_scheduled_posts

logger = logging.getLogger(__name__)


def _strip_html(text: str) -> str:
    """Убирает HTML-теги"""
    return re.sub(r'<[^>]+>', '', text)


async def publish_scheduled_post(context: ContextTypes.DEFAULT_TYPE):
    """Callback для публикации запланированного поста"""
    job = context.job
    data = job.data
    post_id = data.get("post_id")
    text = data.get("text", "")
    image_path = data.get("image_path")
    channel_id = data.get("channel_id") or config.CHANNEL_ID
    
    try:
        if not channel_id:
            logger.error("CHANNEL_ID не задан! Невозможно опубликовать.")
            return
        
        if image_path and os.path.exists(image_path):
            try:
                with open(image_path, "rb") as photo:
                    msg = await context.bot.send_photo(
                        chat_id=channel_id, photo=photo,
                        caption=text, parse_mode="HTML"
                    )
            except Exception:
                # HTML ошибка — fallback без разметки
                with open(image_path, "rb") as photo:
                    msg = await context.bot.send_photo(
                        chat_id=channel_id, photo=photo,
                        caption=_strip_html(text)
                    )
        else:
            # Если image_path задан но файл удалён — публикуем текстом
            if image_path and not os.path.exists(image_path):
                logger.warning(f"Файл картинки {image_path} не найден, публикуем текстом")
            try:
                msg = await context.bot.send_message(
                    chat_id=channel_id, text=text, parse_mode="HTML"
                )
            except Exception:
                msg = await context.bot.send_message(
                    chat_id=channel_id, text=_strip_html(text)
                )
        
        if post_id:
            await mark_post_published(post_id)
        
        logger.info(f"Запланированный пост #{post_id} опубликован в {channel_id}")
        
    except Exception as e:
        logger.error(f"Ошибка публикации запланированного поста #{post_id}: {e}")


def schedule_post_job(job_queue, post_id: int, text: str, image_path: str,
                       scheduled_time: datetime, channel_id: str = None):
    """Добавить задачу публикации в очередь"""
    tz = ZoneInfo(config.TIMEZONE)
    
    if scheduled_time.tzinfo is None:
        scheduled_time = scheduled_time.replace(tzinfo=tz)
    
    now = datetime.now(tz)
    
    if scheduled_time <= now:
        logger.warning(f"Время публикации {scheduled_time} уже прошло!")
        return None
    
    job_name = f"post_{post_id}"
    
    job = job_queue.run_once(
        publish_scheduled_post,
        when=scheduled_time,
        data={
            "post_id": post_id,
            "text": text,
            "image_path": image_path,
            "channel_id": channel_id or config.CHANNEL_ID
        },
        name=job_name
    )
    
    logger.info(f"Пост #{post_id} запланирован на {scheduled_time.strftime('%d.%m.%Y %H:%M')} MSK")
    return job


def cancel_scheduled_job(job_queue, post_id: int) -> bool:
    """Отменить запланированную задачу"""
    job_name = f"post_{post_id}"
    jobs = job_queue.get_jobs_by_name(job_name)
    
    if jobs:
        for job in jobs:
            job.schedule_removal()
        logger.info(f"Задача публикации поста #{post_id} отменена")
        return True
    return False


async def recover_scheduled_posts(job_queue):
    """Восстановить запланированные посты из БД после перезапуска бота"""
    try:
        posts = await get_scheduled_posts()
        tz = ZoneInfo(config.TIMEZONE)
        now = datetime.now(tz)
        recovered = 0
        
        for post in posts:
            try:
                scheduled_dt = datetime.fromisoformat(post["scheduled_time"])
                if scheduled_dt.tzinfo is None:
                    scheduled_dt = scheduled_dt.replace(tzinfo=tz)
                
                if scheduled_dt <= now:
                    logger.info(f"Пост #{post['id']}: время прошло, пропускаем")
                    continue
                
                job = schedule_post_job(
                    job_queue,
                    post_id=post["id"],
                    text=post.get("text", ""),
                    image_path=post.get("image_path"),
                    scheduled_time=scheduled_dt,
                    channel_id=config.CHANNEL_ID,
                )
                if job:
                    recovered += 1
            except Exception as e:
                logger.error(f"Ошибка восстановления поста #{post['id']}: {e}")
        
        if recovered:
            logger.info(f"Восстановлено {recovered} запланированных постов из БД")
    except Exception as e:
        logger.error(f"Ошибка при восстановлении постов: {e}")
