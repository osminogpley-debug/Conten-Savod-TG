"""Контент-календарь — визуальное расписание на неделю"""
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from handlers.start import is_admin
from database import get_scheduled_posts, get_setting
import config
from utils import back_button

logger = logging.getLogger(__name__)

WEEKDAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


async def calendar_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать контент-календарь на неделю"""
    query = update.callback_query
    if query:
        await query.answer()
        if not is_admin(query.from_user.id):
            return

    tz = ZoneInfo(config.TIMEZONE)
    now = datetime.now(tz)
    today = now.date()

    # Получаем запланированные посты
    posts = await get_scheduled_posts()

    # Время автопостинга
    autopost_enabled = await get_setting("autopost_enabled", "0")
    autopost_times = await get_setting("autopost_times", "10:00,18:00")

    # Собираем посты по дням
    week_data = {}
    for day_offset in range(7):
        day = today + timedelta(days=day_offset)
        week_data[day.isoformat()] = {
            "date": day,
            "weekday": WEEKDAYS_RU[day.weekday()],
            "posts": [],
            "is_today": day == today,
        }

    for post in posts:
        try:
            dt = datetime.fromisoformat(post["scheduled_time"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz)
            day_key = dt.date().isoformat()
            if day_key in week_data:
                week_data[day_key]["posts"].append({
                    "time": dt.strftime("%H:%M"),
                    "topic": post["topic"][:30],
                    "id": post["id"],
                })
        except:
            continue

    # Формируем текст
    text = "📅 <b>Контент-календарь</b>\n\n"

    for day_key in sorted(week_data.keys()):
        day = week_data[day_key]
        date_str = day["date"].strftime("%d.%m")
        marker = "👉 " if day["is_today"] else "   "
        header = f"{marker}<b>{day['weekday']} {date_str}</b>"

        if day["posts"]:
            text += f"{header}\n"
            for p in day["posts"]:
                text += f"      ⏰ {p['time']} — {p['topic']}\n"
        else:
            if autopost_enabled == "1":
                slots = autopost_times.split(",")
                text += f"{header}\n"
                for s in slots:
                    text += f"      🤖 {s.strip()} — <i>автопост</i>\n"
            else:
                text += f"{header} — <i>пусто</i>\n"
        text += "\n"

    # Подсказки
    post_count = sum(len(d["posts"]) for d in week_data.values())
    text += f"📊 Запланировано: {post_count} постов\n"
    if autopost_enabled == "1":
        text += "🤖 Автопостинг: включён\n"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить", callback_data="menu_calendar")],
        [InlineKeyboardButton("🏠 Меню", callback_data="menu_back")],
    ])

    if query:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
