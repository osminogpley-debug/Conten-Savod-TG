import aiosqlite
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "chinaya.db")


async def init_db():
    """Инициализация базы данных"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                text TEXT,
                image_url TEXT,
                image_path TEXT,
                scheduled_time TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                job_id TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS post_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT,
                text TEXT,
                image_url TEXT,
                posted_at TEXT DEFAULT CURRENT_TIMESTAMP,
                channel_id TEXT,
                message_id INTEGER,
                style TEXT DEFAULT '',
                hashtags TEXT DEFAULT ''
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        # Очередь тем для автопостинга
        await db.execute("""
            CREATE TABLE IF NOT EXISTS topic_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                used_at TEXT
            )
        """)
        # Каналы (мультиканал)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT NOT NULL UNIQUE,
                title TEXT DEFAULT '',
                is_active INTEGER DEFAULT 1,
                added_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Кэш промптов картинок
        await db.execute("""
            CREATE TABLE IF NOT EXISTS image_cache (
                prompt_hash TEXT PRIMARY KEY,
                image_path TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Статистика генерации
        await db.execute("""
            CREATE TABLE IF NOT EXISTS gen_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                api_name TEXT NOT NULL,
                gen_type TEXT DEFAULT 'text',
                success INTEGER DEFAULT 1,
                duration_ms INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Миграция: добавляем колонки если их нет
        try:
            await db.execute("ALTER TABLE post_history ADD COLUMN style TEXT DEFAULT ''")
        except:
            pass
        try:
            await db.execute("ALTER TABLE post_history ADD COLUMN hashtags TEXT DEFAULT ''")
        except:
            pass
        await db.commit()
        logger.info("База данных инициализирована")


async def add_scheduled_post(topic: str, text: str, image_url: str,
                              image_path: str, scheduled_time: str, job_id: str = None) -> int:
    """Добавить запланированный пост"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO scheduled_posts (topic, text, image_url, image_path, scheduled_time, job_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (topic, text, image_url, image_path, scheduled_time, job_id)
        )
        await db.commit()
        post_id = cursor.lastrowid
        logger.info(f"Запланирован пост #{post_id} на {scheduled_time}")
        return post_id


async def get_scheduled_posts():
    """Получить все ожидающие посты"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM scheduled_posts WHERE status = 'pending' ORDER BY scheduled_time"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def cancel_scheduled_post(post_id: int) -> bool:
    """Отменить запланированный пост"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE scheduled_posts SET status = 'cancelled' WHERE id = ? AND status = 'pending'",
            (post_id,)
        )
        await db.commit()
        if cursor.rowcount > 0:
            logger.info(f"Пост #{post_id} отменён")
            return True
        return False


async def mark_post_published(post_id: int):
    """Отметить пост как опубликованный"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE scheduled_posts SET status = 'published' WHERE id = ?",
            (post_id,)
        )
        await db.commit()


async def add_post_history(topic: str, text: str, image_url: str,
                           channel_id: str, message_id: int,
                           style: str = "", hashtags: str = ""):
    """Добавить пост в историю"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO post_history (topic, text, image_url, channel_id, message_id, style, hashtags)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (topic, text, image_url, channel_id, message_id, style, hashtags)
        )
        await db.commit()


async def get_setting(key: str, default: str = None) -> str:
    """Получить настройку"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return row[0] if row else default


async def set_setting(key: str, value: str):
    """Сохранить настройку"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )
        await db.commit()


async def get_post_by_id(post_id: int):
    """Получить пост по ID"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM scheduled_posts WHERE id = ?", (post_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


# ============================================================
# Очередь тем
# ============================================================

async def add_topic_to_queue(topic: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO topic_queue (topic) VALUES (?)", (topic,)
        )
        await db.commit()
        return cursor.lastrowid


async def add_topics_bulk(topics: list[str]) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            "INSERT INTO topic_queue (topic) VALUES (?)",
            [(t,) for t in topics]
        )
        await db.commit()
        return len(topics)


async def get_next_topic() -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM topic_queue WHERE status='pending' ORDER BY id LIMIT 1"
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def mark_topic_used(topic_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE topic_queue SET status='used', used_at=? WHERE id=?",
            (datetime.now().isoformat(), topic_id)
        )
        await db.commit()


async def get_topic_queue():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM topic_queue WHERE status='pending' ORDER BY id"
        )
        return [dict(r) for r in await cursor.fetchall()]


async def clear_topic_queue():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM topic_queue WHERE status='pending'")
        await db.commit()


async def remove_topic_from_queue(topic_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM topic_queue WHERE id=? AND status='pending'", (topic_id,)
        )
        await db.commit()
        return cursor.rowcount > 0


# ============================================================
# Мультиканал
# ============================================================

async def add_channel(channel_id: str, title: str = "") -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT OR REPLACE INTO channels (channel_id, title, is_active) VALUES (?, ?, 1)",
            (channel_id, title)
        )
        await db.commit()
        return cursor.lastrowid


async def get_active_channels():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM channels WHERE is_active=1 ORDER BY id"
        )
        return [dict(r) for r in await cursor.fetchall()]


async def remove_channel(channel_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE channels SET is_active=0 WHERE channel_id=?", (channel_id,)
        )
        await db.commit()


# ============================================================
# Кэш картинок
# ============================================================

async def get_image_cache(prompt_hash: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT image_path FROM image_cache WHERE prompt_hash=?", (prompt_hash,)
        )
        row = await cursor.fetchone()
        if row and row[0] and os.path.exists(row[0]):
            return row[0]
        return None


async def set_image_cache(prompt_hash: str, image_path: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO image_cache (prompt_hash, image_path, created_at) VALUES (?, ?, ?)",
            (prompt_hash, image_path, datetime.now().isoformat())
        )
        await db.commit()


async def clean_image_cache(max_age_hours: int = 24):
    async with aiosqlite.connect(DB_PATH) as db:
        cutoff = datetime.now().isoformat()
        await db.execute(
            "DELETE FROM image_cache WHERE created_at < datetime(?, '-' || ? || ' hours')",
            (cutoff, max_age_hours)
        )
        await db.commit()


# ============================================================
# Статистика
# ============================================================

async def log_gen_stat(api_name: str, gen_type: str = "text",
                       success: bool = True, duration_ms: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO gen_stats (api_name, gen_type, success, duration_ms) VALUES (?, ?, ?, ?)",
            (api_name, gen_type, 1 if success else 0, duration_ms)
        )
        await db.commit()


async def get_stats_summary(days: int = 7) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Посты за период
        cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM post_history WHERE posted_at >= datetime('now', '-' || ? || ' days')",
            (days,)
        )
        row = await cursor.fetchone()
        posts_count = row["cnt"] if row else 0

        # По API
        cursor = await db.execute(
            "SELECT api_name, gen_type, COUNT(*) as cnt, SUM(success) as ok, AVG(duration_ms) as avg_ms "
            "FROM gen_stats WHERE created_at >= datetime('now', '-' || ? || ' days') "
            "GROUP BY api_name, gen_type ORDER BY cnt DESC",
            (days,)
        )
        api_stats = [dict(r) for r in await cursor.fetchall()]

        # Всего постов
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM post_history")
        row = await cursor.fetchone()
        total_posts = row["cnt"] if row else 0

        # В очереди
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM topic_queue WHERE status='pending'")
        row = await cursor.fetchone()
        queue_size = row["cnt"] if row else 0

        # Запланировано
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM scheduled_posts WHERE status='pending'")
        row = await cursor.fetchone()
        scheduled = row["cnt"] if row else 0

        return {
            "posts_period": posts_count,
            "total_posts": total_posts,
            "api_stats": api_stats,
            "queue_size": queue_size,
            "scheduled": scheduled,
            "days": days,
        }


# ============================================================
# История постов
# ============================================================

async def get_post_history(limit: int = 10, offset: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM post_history ORDER BY posted_at DESC LIMIT ? OFFSET ?",
            (limit, offset)
        )
        return [dict(r) for r in await cursor.fetchall()]


async def get_history_post_by_id(post_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM post_history WHERE id=?", (post_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
