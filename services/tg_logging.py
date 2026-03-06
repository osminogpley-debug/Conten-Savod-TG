"""Telegram logging handler — отправляет ERROR-логи админу в ЛС"""
import logging
import asyncio
from datetime import datetime, timedelta


class TelegramLogHandler(logging.Handler):
    """Отправляет логи ERROR+ уровня прямо в Telegram админу."""

    def __init__(self, bot, admin_id: int, throttle_seconds: int = 60):
        super().__init__(level=logging.ERROR)
        self.bot = bot
        self.admin_id = admin_id
        self.throttle_seconds = throttle_seconds
        self._last_sent: dict[str, datetime] = {}
        self._loop = None

    def emit(self, record: logging.LogRecord):
        if not self.admin_id:
            return

        # Throttle: не спамим одну и ту же ошибку
        key = f"{record.name}:{record.lineno}:{record.getMessage()[:50]}"
        now = datetime.now()
        last = self._last_sent.get(key)
        if last and (now - last).total_seconds() < self.throttle_seconds:
            return
        self._last_sent[key] = now

        # Чистим старые ключи
        cutoff = now - timedelta(minutes=10)
        self._last_sent = {
            k: v for k, v in self._last_sent.items() if v > cutoff
        }

        text = (
            f"🚨 <b>ERROR</b>\n"
            f"<code>{record.name}</code>\n"
            f"{record.getMessage()[:500]}"
        )

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._send(text))
        except RuntimeError:
            pass  # No event loop — skip

    async def _send(self, text: str):
        try:
            await self.bot.send_message(
                chat_id=self.admin_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception:
            pass  # Не крашим бот из-за логирования


def setup_telegram_logging(bot, admin_id: int):
    """Подключает TelegramLogHandler к root logger."""
    if not admin_id:
        return
    handler = TelegramLogHandler(bot, admin_id)
    logging.getLogger().addHandler(handler)
    logging.getLogger(__name__).info("Telegram logging подключён")
