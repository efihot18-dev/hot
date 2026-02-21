"""
Scheduler que roda em loop verificando assinaturas expiradas
e chamando o bot para remover os membros do grupo.
"""

import asyncio
import logging
import time

import config
from database import init_db

logger = logging.getLogger(__name__)


def run_scheduler(bot_app):
    """
    Executa o loop de verificacao de expiracoes em uma thread separada.
    bot_app: instancia da Application do python-telegram-bot
    """
    interval = config.CHECK_INTERVAL_MINUTES * 60

    async def _loop():
        from bot import kick_expired, warn_expiring
        while True:
            logger.info("Verificando assinaturas expiradas e avisos...")
            try:
                await warn_expiring(bot_app.bot)
            except Exception as e:
                logger.error("Erro ao enviar avisos: %s", e)
            try:
                await kick_expired(bot_app.bot)
            except Exception as e:
                logger.error("Erro no scheduler: %s", e)
            await asyncio.sleep(interval)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_loop())
