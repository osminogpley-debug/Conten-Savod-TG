"""История постов и статистика"""
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from handlers.start import is_admin
from database import get_post_history, get_history_post_by_id, get_stats_summary
from utils import smart_truncate, back_button

logger = logging.getLogger(__name__)


# ============================================================
# История постов
# ============================================================

async def history_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать историю постов"""
    query = update.callback_query
    if query:
        await query.answer()
        if not is_admin(query.from_user.id):
            return

    page = context.user_data.get("history_page", 0)
    posts = await get_post_history(limit=5, offset=page * 5)

    if not posts:
        text = "📊 <b>История постов пуста</b>"
        kb = back_button()
    else:
        text = f"📊 <b>История постов</b> (стр. {page + 1}):\n\n"
        for p in posts:
            date = p.get("posted_at", "")[:16].replace("T", " ")
            topic = smart_truncate(p.get("topic", "—"), 40)
            text += f"🆔 #{p['id']} | {date}\n📝 {topic}\n"
            if p.get("style"):
                text += f"🎨 {p['style']}\n"
            text += "\n"

        rows = []
        detail_row = []
        for p in posts[:3]:
            detail_row.append(
                InlineKeyboardButton(f"#{p['id']}", callback_data=f"hist_{p['id']}")
            )
        if detail_row:
            rows.append(detail_row)

        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("⬅️", callback_data="hist_prev"))
        nav_row.append(InlineKeyboardButton(f"стр. {page + 1}", callback_data="hist_noop"))
        if len(posts) == 5:
            nav_row.append(InlineKeyboardButton("➡️", callback_data="hist_next"))
        rows.append(nav_row)
        rows.append([InlineKeyboardButton("🏠 Меню", callback_data="menu_back")])
        kb = InlineKeyboardMarkup(rows)

    if query:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def history_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопок истории"""
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return

    data = query.data

    if data == "hist_prev":
        page = context.user_data.get("history_page", 0)
        context.user_data["history_page"] = max(0, page - 1)
        await history_menu(update, context)

    elif data == "hist_next":
        page = context.user_data.get("history_page", 0)
        context.user_data["history_page"] = page + 1
        await history_menu(update, context)

    elif data == "hist_noop":
        pass

    elif data.startswith("hist_"):
        try:
            post_id = int(data.replace("hist_", ""))
        except ValueError:
            return
        post = await get_history_post_by_id(post_id)
        if not post:
            await query.edit_message_text("❌ Пост не найден.", reply_markup=back_button())
            return

        text_preview = smart_truncate(post.get("text", "—"), 800)
        date = post.get("posted_at", "")[:16].replace("T", " ")
        topic = post.get("topic", "—")

        text = (
            f"📊 <b>Пост #{post['id']}</b>\n\n"
            f"📝 Тема: {topic}\n"
            f"🕐 Дата: {date}\n"
        )
        if post.get("hashtags"):
            text += f"🏷 {post['hashtags']}\n"
        text += f"\n{text_preview}"

        await query.edit_message_text(
            text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="menu_history")],
                [InlineKeyboardButton("🏠 Меню", callback_data="menu_back")],
            ])
        )


# ============================================================
# Статистика
# ============================================================

async def stats_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать статистику"""
    query = update.callback_query
    if query:
        await query.answer()
        if not is_admin(query.from_user.id):
            return

    stats = await get_stats_summary(days=7)

    text = (
        f"📈 <b>Статистика</b>\n\n"
        f"📝 Постов за 7 дней: <b>{stats['posts_period']}</b>\n"
        f"📝 Всего постов: <b>{stats['total_posts']}</b>\n"
        f"📚 В очереди тем: <b>{stats['queue_size']}</b>\n"
        f"⏰ Запланировано: <b>{stats['scheduled']}</b>\n"
    )

    if stats["api_stats"]:
        text += "\n<b>API за 7 дней:</b>\n"
        for s in stats["api_stats"][:8]:
            ok = s.get("ok", 0)
            total = s.get("cnt", 0)
            avg_ms = int(s.get("avg_ms", 0) or 0)
            pct = int(ok / total * 100) if total else 0
            text += (
                f"• {s['api_name']} ({s['gen_type']}): "
                f"{total} вызовов, {pct}% OK"
            )
            if avg_ms:
                text += f", ~{avg_ms}мс"
            text += "\n"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 30 дней", callback_data="stats_30")],
        [InlineKeyboardButton("🏠 Меню", callback_data="menu_back")],
    ])

    if query:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Расширенная статистика"""
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return

    if query.data == "stats_30":
        stats = await get_stats_summary(days=30)
        text = (
            f"📈 <b>Статистика за 30 дней</b>\n\n"
            f"📝 Постов: <b>{stats['posts_period']}</b>\n"
            f"📝 Всего: <b>{stats['total_posts']}</b>\n"
        )
        if stats["api_stats"]:
            text += "\n<b>API:</b>\n"
            for s in stats["api_stats"][:10]:
                ok = s.get("ok", 0)
                total = s.get("cnt", 0)
                pct = int(ok / total * 100) if total else 0
                text += f"• {s['api_name']}: {total} ({pct}% OK)\n"

        await query.edit_message_text(
            text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📈 7 дней", callback_data="menu_stats")],
                [InlineKeyboardButton("🏠 Меню", callback_data="menu_back")],
            ])
        )
