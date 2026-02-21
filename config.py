import os

# ------------------------------------------------------------------
# BOT DO TELEGRAM
# ------------------------------------------------------------------
# Token do bot (obtenha com @BotFather no Telegram)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8222157726:AAEAqDgor3QgiLoCnmYJfge3Ry8iDk_PbjU")

# ID numerico do grupo/canal onde os assinantes ficam
# Para descobrir: adicione @userinfobot no grupo e envie uma mensagem
TELEGRAM_GROUP_ID = int(os.getenv("TELEGRAM_GROUP_ID", "-1003898242457"))

# ------------------------------------------------------------------
# PAINEL WEB
# ------------------------------------------------------------------
# Chave secreta do Flask (troque em producao!)
SECRET_KEY = os.getenv("SECRET_KEY", "troque-esta-chave-em-producao")

# Login e senha do painel admin
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "admin123")

# Porta onde o painel sobe
PORT = int(os.getenv("PORT", 5000))

# URL base do painel (usado para mostrar o link completo de acesso)
# Ex: "http://meuservidor.com" ou "http://localhost:5000"
BASE_URL = os.getenv("BASE_URL", f"http://localhost:{PORT}")

# ------------------------------------------------------------------
# SCHEDULER
# ------------------------------------------------------------------
# Intervalo (em minutos) para verificar assinaturas expiradas
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", 30))
