"""
Ponto de entrada principal.
Sobe em paralelo:
  1. Bot do Telegram (polling) + Scheduler (via JobQueue do PTB)
  2. Painel web Flask (thread daemon)
"""

import threading
import logging

from database import init_db
import config

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def start_flask():
    from app import app
    logger.info("Painel web em http://localhost:%s", config.PORT)
    app.run(host="0.0.0.0", port=config.PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    init_db()

    # Flask em thread daemon
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()

    # Bot + scheduler rodam no event loop principal gerenciado pelo PTB
    from bot import build_app
    bot_app = build_app()
    logger.info("Bot do Telegram iniciado (polling)...")
    bot_app.run_polling()
