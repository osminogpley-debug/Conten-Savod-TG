"""Общий aiohttp.ClientSession для всех API-вызовов (P2.11)"""
import aiohttp
import logging
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

_session: aiohttp.ClientSession | None = None


async def get_session() -> aiohttp.ClientSession:
    """Получить или создать глобальный ClientSession."""
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=120),
            connector=aiohttp.TCPConnector(limit=20, limit_per_host=5),
        )
        logger.info("Создан новый aiohttp.ClientSession")
    return _session


@asynccontextmanager
async def shared_session():
    """Async context manager — drop-in замена для aiohttp.ClientSession().
    НЕ закрывает сессию при выходе из блока."""
    session = await get_session()
    yield session
    # Не закрываем — сессия общая


async def close_session():
    """Закрыть глобальный ClientSession."""
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None
        logger.info("aiohttp.ClientSession закрыт")
