"""
Bot do Telegram responsavel por:
  1. Receber o /start com o token do link de convite
  2. Registrar quem entrou (user_id + username) e marcar o link como usado
  3. Ser adicionado como ADMIN no grupo (para poder banir/desbanir membros)
  4. Ser chamado pelo scheduler para remover membros expirados
"""

import os
import logging
from datetime import datetime, timedelta, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

from database import get_conn, add_log, init_db, get_setting, get_group
import config

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def resolve_tg_group(row) -> int:
    """Retorna o telegram_group_id real para um link, usando o grupo associado ou o default."""
    if row["group_id"]:
        g = get_group(row["group_id"])
        if g:
            return g["telegram_group_id"]
    return config.TELEGRAM_GROUP_ID


# ---------------------------------------------------------------------------
# /start  ->  chamado quando o lead clica no link t.me/BotName?start=TOKEN
# ---------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args

    if not args:
        await update.message.reply_text(
            "Ola! Use o link fornecido apos sua compra para ativar seu acesso."
        )
        return

    token = args[0]
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM invite_links WHERE token = ?", (token,)
    ).fetchone()

    if not row:
        await update.message.reply_text("Link invalido. Verifique com o suporte.")
        conn.close()
        return

    if row["used"]:
        await update.message.reply_text(
            "Este link ja foi utilizado. Cada link e de uso unico.\n"
            "Entre em contato com o suporte se precisar de ajuda."
        )
        conn.close()
        return

    # Registrar uso
    now = datetime.now(timezone.utc)
    is_lifetime = (row["days"] == 0)
    expires_at = None if is_lifetime else (now + timedelta(days=row["days"]))

    conn.execute(
        """UPDATE invite_links
           SET used=1, telegram_user_id=?, telegram_username=?,
               used_at=?, expires_at=?
           WHERE token=?""",
        (
            user.id,
            user.username or user.first_name,
            now.isoformat(),
            expires_at.isoformat() if expires_at else None,
            token,
        ),
    )
    conn.commit()
    conn.close()

    add_log(
        "LINK_USED",
        f"token={token} user_id={user.id} username={user.username} expires={'vitalicio' if is_lifetime else expires_at.date()}",
    )

    # Gerar link de convite unico do grupo (1 uso, sem expirar pelo Telegram)
    tg_group_id = resolve_tg_group(row)
    try:
        invite = await context.bot.create_chat_invite_link(
            chat_id=tg_group_id,
            member_limit=1,
        )
        link = invite.invite_link
    except Exception as e:
        logger.error("Erro ao criar link de convite do grupo: %s", e)
        await update.message.reply_text(
            "Houve um erro ao gerar seu acesso. Contate o suporte."
        )
        return

    expire_str = expires_at.strftime("%d/%m/%Y") if expires_at else None
    if expire_str:
        msg = (
            f"Tudo certo, {user.first_name}!\n\n"
            f"Clique no link abaixo para entrar no grupo:\n{link}\n\n"
            f"Seu acesso expira em: *{expire_str}*\n"
            f"(_Apos essa data voce sera removido automaticamente_)"
        )
    else:
        msg = (
            f"Tudo certo, {user.first_name}!\n\n"
            f"Clique no link abaixo para entrar no grupo:\n{link}\n\n"
            f"Seu acesso e *vitalicio*. Aproveite! 🎉"
        )
    await update.message.reply_text(msg, parse_mode="Markdown")


# ---------------------------------------------------------------------------
# /status  ->  o proprio usuario confere quando expira
# ---------------------------------------------------------------------------
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM invite_links WHERE telegram_user_id = ? AND kicked = 0",
        (user.id,),
    ).fetchone()
    conn.close()

    if not row:
        await update.message.reply_text(
            "Nao encontrei nenhuma assinatura ativa para voce."
        )
        return

    if row["days"] == 0:
        await update.message.reply_text(
            "Sua assinatura e *vitalicia*. Sem data de expiracao.",
            parse_mode="Markdown",
        )
        return

    expires_at = datetime.fromisoformat(row["expires_at"])
    now = datetime.now(timezone.utc)
    remaining = (expires_at - now).days

    await update.message.reply_text(
        f"Sua assinatura expira em *{expires_at.strftime('%d/%m/%Y')}*.\n"
        f"Restam aproximadamente *{remaining} dia(s)*.",
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# Funcao chamada pelo scheduler para remover expirados
# ---------------------------------------------------------------------------
async def kick_expired(bot):
    """Remove do grupo todos os membros com assinatura expirada (ignora vitalicios)."""
    now = datetime.now(timezone.utc).isoformat()
    conn = get_conn()
    rows = conn.execute(
        """SELECT * FROM invite_links
           WHERE used=1 AND kicked=0 AND days > 0 AND expires_at <= ?""",
        (now,),
    ).fetchall()

    for row in rows:
        uid = row["telegram_user_id"]
        tg_group_id = resolve_tg_group(row)
        try:
            await bot.ban_chat_member(
                chat_id=tg_group_id, user_id=uid
            )
            # Desbanir logo apos kick para o usuario poder ser convidado novamente se renovar
            await bot.unban_chat_member(
                chat_id=tg_group_id, user_id=uid, only_if_banned=True
            )
            conn.execute(
                "UPDATE invite_links SET kicked=1 WHERE id=?", (row["id"],)
            )
            add_log(
                "KICKED",
                f"user_id={uid} username={row['telegram_username']} token={row['token']}",
            )
            logger.info("Removido user_id=%s do grupo (expirado)", uid)
        except Exception as e:
            logger.error("Erro ao remover user_id=%s: %s", uid, e)

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Funcao chamada pelo scheduler para avisar quem esta perto de expirar
# ---------------------------------------------------------------------------
async def warn_expiring(bot):
    """Envia mensagem de aviso aos membros cuja assinatura expira em breve."""
    if get_setting("warn_enabled") != "1":
        return

    try:
        days_before = int(get_setting("warn_days_before") or "3")
    except ValueError:
        days_before = 3

    message_template = get_setting("warn_message")

    # Janela: expira entre agora e (agora + days_before dias)
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(days=days_before)

    conn = get_conn()
    rows = conn.execute(
        """
        SELECT * FROM invite_links
        WHERE used=1 AND kicked=0 AND warned=0 AND days > 0
          AND expires_at > ? AND expires_at <= ?
        """,
        (now.isoformat(), window_end.isoformat()),
    ).fetchall()

    for row in rows:
        uid = row["telegram_user_id"]
        expires_at = datetime.fromisoformat(row["expires_at"])
        remaining = max(0, (expires_at - now).days)

        text = message_template.format(
            nome=row["telegram_username"] or "amigo",
            dias=remaining,
            data=expires_at.strftime("%d/%m/%Y"),
        )

        try:
            await bot.send_message(chat_id=uid, text=text)
            conn.execute(
                "UPDATE invite_links SET warned=1 WHERE id=?", (row["id"],)
            )
            add_log(
                "WARN_SENT",
                f"user_id={uid} username={row['telegram_username']} expires={expires_at.date()}",
            )
            logger.info("Aviso enviado para user_id=%s", uid)
        except Exception as e:
            logger.error("Erro ao avisar user_id=%s: %s", uid, e)

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Job periodico do scheduler (roda dentro do event loop do PTB)
# ---------------------------------------------------------------------------
async def scheduler_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Scheduler: verificando avisos e expiracoes...")
    await warn_expiring(context.bot)
    await kick_expired(context.bot)


# ---------------------------------------------------------------------------
# Iniciar o bot (usado pelo main.py)
# ---------------------------------------------------------------------------
def build_app():
    init_db()
    application = ApplicationBuilder().token(config.TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))

    # Agenda o scheduler usando o JobQueue nativo do PTB
    interval = config.CHECK_INTERVAL_MINUTES * 60
    application.job_queue.run_repeating(
        scheduler_job,
        interval=interval,
        first=15,  # primeira execucao 15s apos o inicio
    )

    return application
