"""
Painel web Flask para gerenciar links de assinatura do Telegram.
"""

import secrets
import threading
import asyncio
from datetime import datetime, timezone
from functools import wraps

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    jsonify,
)

import config
from database import (
    get_conn, add_log, init_db, get_setting, set_setting,
    list_groups, get_group, list_products, get_product,
    list_api_keys, get_api_key,
)

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

# ---------------------------------------------------------------------------
# Autenticacao simples
# ---------------------------------------------------------------------------

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if (
            request.form["username"] == config.ADMIN_USER
            and request.form["password"] == config.ADMIN_PASS
        ):
            session["logged_in"] = True
            return redirect(url_for("dashboard"))
        flash("Usuario ou senha incorretos.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Dashboard principal
# ---------------------------------------------------------------------------

@app.route("/")
@login_required
def dashboard():
    conn = get_conn()
    total      = conn.execute("SELECT COUNT(*) FROM invite_links").fetchone()[0]
    available  = conn.execute("SELECT COUNT(*) FROM invite_links WHERE used=0").fetchone()[0]
    active     = conn.execute("SELECT COUNT(*) FROM invite_links WHERE used=1 AND kicked=0").fetchone()[0]
    expired    = conn.execute("SELECT COUNT(*) FROM invite_links WHERE kicked=1").fetchone()[0]
    lifetime   = conn.execute("SELECT COUNT(*) FROM invite_links WHERE days=0").fetchone()[0]
    recent     = conn.execute(
        "SELECT * FROM invite_links ORDER BY created_at DESC LIMIT 10"
    ).fetchall()
    logs       = conn.execute(
        "SELECT * FROM logs ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
    conn.close()

    return render_template(
        "dashboard.html",
        total=total,
        available=available,
        active=active,
        expired=expired,
        lifetime=lifetime,
        recent=recent,
        logs=logs,
        base_url=config.BASE_URL,
        group_id=config.TELEGRAM_GROUP_ID,
    )


# ---------------------------------------------------------------------------
# Gerar links
# ---------------------------------------------------------------------------

@app.route("/generate", methods=["GET", "POST"])
@login_required
def generate():
    groups = list_groups(only_active=True)
    products = list_products(only_active=True)

    if request.method == "POST":
        # Verifica se foi usando um produto pre-configurado
        product_id = request.form.get("product_id")
        if product_id:
            prod = get_product(int(product_id))
            if not prod:
                flash("Produto nao encontrado.", "danger")
                return redirect(url_for("generate"))
            days     = prod["days"]
            note     = prod["name"]
            group_id = prod["group_id"]
            lifetime = (days == 0)
        else:
            lifetime = request.form.get("lifetime") == "1"
            days     = 0 if lifetime else int(request.form.get("days", 30))
            note     = request.form.get("note", "")
            gid_str  = request.form.get("group_id", "")
            group_id = int(gid_str) if gid_str else None

        quantity = int(request.form.get("quantity", 1))
        if quantity < 1 or quantity > 500:
            flash("Quantidade deve ser entre 1 e 500.", "warning")
            return redirect(url_for("generate"))

        conn = get_conn()
        tokens = []
        for _ in range(quantity):
            token = secrets.token_urlsafe(20)
            conn.execute(
                "INSERT INTO invite_links (token, days, note, group_id) VALUES (?, ?, ?, ?)",
                (token, days, note, group_id),
            )
            tokens.append(token)
        conn.commit()
        conn.close()

        label = "vitalicio" if lifetime else f"{days} dias"
        add_log("GENERATED", f"qty={quantity} days={label} note={note} group_id={group_id}")
        flash(f"{quantity} link(s) gerado(s) com sucesso!", "success")

        links = [f"{config.BASE_URL}/acesso/{t}" for t in tokens]
        return render_template("generate.html", links=links, days=days,
                               group_id=config.TELEGRAM_GROUP_ID, groups=groups, products=products)

    return render_template("generate.html", links=None, group_id=config.TELEGRAM_GROUP_ID,
                           groups=groups, products=products)


# ---------------------------------------------------------------------------
# Listar todos os links
# ---------------------------------------------------------------------------

@app.route("/links")
@login_required
def links():
    status_filter = request.args.get("status", "all")
    conn = get_conn()

    query = "SELECT * FROM invite_links"
    if status_filter == "available":
        query += " WHERE used=0"
    elif status_filter == "active":
        query += " WHERE used=1 AND kicked=0"
    elif status_filter == "expired":
        query += " WHERE kicked=1"
    elif status_filter == "lifetime":
        query += " WHERE days=0"

    query += " ORDER BY created_at DESC"
    rows = conn.execute(query).fetchall()
    conn.close()

    return render_template(
        "links.html",
        rows=rows,
        status_filter=status_filter,
        base_url=config.BASE_URL,
        group_id=config.TELEGRAM_GROUP_ID,
    )


# ---------------------------------------------------------------------------
# Revogar assinatura manualmente
# ---------------------------------------------------------------------------

@app.route("/revoke/<int:link_id>", methods=["POST"])
@login_required
def revoke(link_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM invite_links WHERE id=?", (link_id,)
    ).fetchone()

    if not row:
        flash("Link nao encontrado.", "danger")
        conn.close()
        return redirect(url_for("links"))

    if not row["used"] or row["kicked"]:
        flash("Este link nao possui assinatura ativa para revogar.", "warning")
        conn.close()
        return redirect(url_for("links"))

    # Kick via bot (executa em thread de evento asyncio separada)
    uid = row["telegram_user_id"]
    token = row["token"]
    # Resolver grupo correto para o link
    tg_group_id = config.TELEGRAM_GROUP_ID
    if row["group_id"]:
        g = get_group(row["group_id"])
        if g:
            tg_group_id = g["telegram_group_id"]

    def do_kick():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _kick():
            from telegram import Bot
            bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
            try:
                await bot.ban_chat_member(chat_id=tg_group_id, user_id=uid)
                await bot.unban_chat_member(
                    chat_id=tg_group_id, user_id=uid, only_if_banned=True
                )
            except Exception as e:
                add_log("KICK_ERROR", f"user_id={uid} err={e}")

        loop.run_until_complete(_kick())
        loop.close()

    t = threading.Thread(target=do_kick, daemon=True)
    t.start()
    t.join(timeout=10)

    conn.execute(
        "UPDATE invite_links SET kicked=1 WHERE id=?", (link_id,)
    )
    conn.commit()
    conn.close()

    add_log("MANUAL_REVOKE", f"link_id={link_id} user_id={uid} token={token}")
    flash(f"Assinatura do usuario {row['telegram_username']} revogada.", "success")
    return redirect(url_for("links"))


# ---------------------------------------------------------------------------
# Deletar link nao usado
# ---------------------------------------------------------------------------

@app.route("/delete/<int:link_id>", methods=["POST"])
@login_required
def delete_link(link_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM invite_links WHERE id=?", (link_id,)
    ).fetchone()

    if not row:
        flash("Link nao encontrado.", "danger")
    elif row["used"] and not row["kicked"]:
        flash("Nao e possivel deletar um link com assinatura ativa. Revogue primeiro.", "warning")
    else:
        conn.execute("DELETE FROM invite_links WHERE id=?", (link_id,))
        conn.commit()
        add_log("DELETED", f"link_id={link_id} token={row['token']}")
        flash("Link deletado.", "success")

    conn.close()
    return redirect(url_for("links"))


# ---------------------------------------------------------------------------
# Endpoint de acesso (o lead clica neste link e e redirecionado para o bot)
# ---------------------------------------------------------------------------

@app.route("/acesso/<token>")
def acesso(token):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM invite_links WHERE token=?", (token,)
    ).fetchone()
    conn.close()

    if not row:
        return render_template("invalid.html", reason="Link invalido."), 404

    if row["used"]:
        return render_template("invalid.html", reason="Este link ja foi utilizado."), 410

    # Redireciona para o bot do Telegram passando o token como parametro /start
    bot_username = _get_bot_username()
    telegram_url = f"https://t.me/{bot_username}?start={token}"
    return render_template("redirect.html", telegram_url=telegram_url, days=row["days"])


def _get_bot_username():
    """Cache simples do username do bot."""
    if not hasattr(_get_bot_username, "_cache"):
        try:
            import asyncio
            from telegram import Bot

            async def _fetch():
                bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
                me = await bot.get_me()
                return me.username

            loop = asyncio.new_event_loop()
            username = loop.run_until_complete(_fetch())
            loop.close()
            _get_bot_username._cache = username
        except Exception:
            _get_bot_username._cache = "SeuBot"
    return _get_bot_username._cache


# ---------------------------------------------------------------------------
# Configuracoes de aviso de expiracao
# ---------------------------------------------------------------------------

@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        set_setting("warn_enabled",     "1" if request.form.get("warn_enabled") else "0")
        set_setting("warn_days_before", request.form.get("warn_days_before", "3").strip())
        set_setting("warn_message",     request.form.get("warn_message", "").strip())
        add_log("SETTINGS_SAVED", "Configuracoes de aviso atualizadas")
        flash("Configuracoes salvas com sucesso!", "success")
        return redirect(url_for("settings"))

    return render_template(
        "settings.html",
        warn_enabled=get_setting("warn_enabled"),
        warn_days_before=get_setting("warn_days_before"),
        warn_message=get_setting("warn_message"),
        group_id=config.TELEGRAM_GROUP_ID,
    )


# ---------------------------------------------------------------------------
# API simples para consulta externa (opcional)
# ---------------------------------------------------------------------------

@app.route("/api/links", methods=["GET"])
@login_required
def api_links():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM invite_links ORDER BY created_at DESC").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ===========================================================================
# GRUPOS DO TELEGRAM
# ===========================================================================

@app.route("/groups")
@login_required
def groups():
    return render_template("groups.html", groups=list_groups(), group_id=config.TELEGRAM_GROUP_ID)


@app.route("/groups/add", methods=["POST"])
@login_required
def group_add():
    name     = request.form.get("name", "").strip()
    tg_id    = request.form.get("telegram_group_id", "").strip()
    desc     = request.form.get("description", "").strip()
    if not name or not tg_id:
        flash("Nome e ID do grupo sao obrigatorios.", "danger")
        return redirect(url_for("groups"))
    try:
        tg_id = int(tg_id)
    except ValueError:
        flash("ID do grupo deve ser um numero (ex: -1001234567890).", "danger")
        return redirect(url_for("groups"))
    conn = get_conn()
    conn.execute(
        "INSERT INTO groups (name, telegram_group_id, description) VALUES (?, ?, ?)",
        (name, tg_id, desc),
    )
    conn.commit()
    conn.close()
    add_log("GROUP_ADDED", f"name={name} tg_id={tg_id}")
    flash(f"Grupo '{name}' adicionado!", "success")
    return redirect(url_for("groups"))


@app.route("/groups/toggle/<int:gid>", methods=["POST"])
@login_required
def group_toggle(gid):
    conn = get_conn()
    conn.execute("UPDATE groups SET active = CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?", (gid,))
    conn.commit()
    conn.close()
    return redirect(url_for("groups"))


@app.route("/groups/delete/<int:gid>", methods=["POST"])
@login_required
def group_delete(gid):
    conn = get_conn()
    conn.execute("DELETE FROM groups WHERE id=?", (gid,))
    conn.commit()
    conn.close()
    add_log("GROUP_DELETED", f"id={gid}")
    flash("Grupo removido.", "success")
    return redirect(url_for("groups"))


# ===========================================================================
# PRODUTOS
# ===========================================================================

@app.route("/products")
@login_required
def products():
    return render_template(
        "products.html",
        products=list_products(),
        groups=list_groups(only_active=True),
        group_id=config.TELEGRAM_GROUP_ID,
    )


@app.route("/products/add", methods=["POST"])
@login_required
def product_add():
    name     = request.form.get("name", "").strip()
    days_str = request.form.get("days", "30")
    lifetime = request.form.get("lifetime") == "1"
    gid_str  = request.form.get("group_id", "")
    note     = request.form.get("note", "").strip()
    if not name:
        flash("Nome e obrigatorio.", "danger")
        return redirect(url_for("products"))
    days     = 0 if lifetime else int(days_str)
    group_id = int(gid_str) if gid_str else None
    conn = get_conn()
    conn.execute(
        "INSERT INTO products (name, days, group_id, note) VALUES (?, ?, ?, ?)",
        (name, days, group_id, note),
    )
    conn.commit()
    conn.close()
    add_log("PRODUCT_ADDED", f"name={name} days={days}")
    flash(f"Produto '{name}' criado!", "success")
    return redirect(url_for("products"))


@app.route("/products/toggle/<int:pid>", methods=["POST"])
@login_required
def product_toggle(pid):
    conn = get_conn()
    conn.execute("UPDATE products SET active = CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    return redirect(url_for("products"))


@app.route("/products/delete/<int:pid>", methods=["POST"])
@login_required
def product_delete(pid):
    conn = get_conn()
    conn.execute("DELETE FROM products WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    add_log("PRODUCT_DELETED", f"id={pid}")
    flash("Produto removido.", "success")
    return redirect(url_for("products"))


# ===========================================================================
# API KEYS
# ===========================================================================

@app.route("/api-keys")
@login_required
def apikeys():
    return render_template("apikeys.html", keys=list_api_keys(), group_id=config.TELEGRAM_GROUP_ID)


@app.route("/api-keys/add", methods=["POST"])
@login_required
def apikey_add():
    import secrets as _s
    label = request.form.get("label", "").strip() or "Sem nome"
    key   = "sk_" + _s.token_urlsafe(32)
    conn  = get_conn()
    conn.execute("INSERT INTO api_keys (key, label) VALUES (?, ?)", (key, label))
    conn.commit()
    conn.close()
    add_log("APIKEY_CREATED", f"label={label}")
    flash(f"Chave criada: {key}", "success")
    return redirect(url_for("apikeys"))


@app.route("/api-keys/revoke/<int:kid>", methods=["POST"])
@login_required
def apikey_revoke(kid):
    conn = get_conn()
    conn.execute("UPDATE api_keys SET active=0 WHERE id=?", (kid,))
    conn.commit()
    conn.close()
    add_log("APIKEY_REVOKED", f"id={kid}")
    flash("Chave revogada.", "success")
    return redirect(url_for("apikeys"))


@app.route("/api-keys/delete/<int:kid>", methods=["POST"])
@login_required
def apikey_delete(kid):
    conn = get_conn()
    conn.execute("DELETE FROM api_keys WHERE id=?", (kid,))
    conn.commit()
    conn.close()
    add_log("APIKEY_DELETED", f"id={kid}")
    flash("Chave deletada.", "success")
    return redirect(url_for("apikeys"))


# ===========================================================================
# WEBHOOK — POST /api/purchase
# Payload JSON:
#   { "product_id": 1, "discord_user": "User#1234", "quantity": 1 }
# Header obrigatorio:
#   Authorization: Bearer <api_key>
# ===========================================================================

@app.route("/api/purchase", methods=["POST"])
def api_purchase():
    # Autenticacao por API key
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return jsonify({"error": "Unauthorized"}), 401
    raw_key = auth.split(" ", 1)[1].strip()
    if not get_api_key(raw_key):
        return jsonify({"error": "Invalid or revoked API key"}), 401

    data = request.get_json(silent=True) or {}
    product_id   = data.get("product_id")
    discord_user = data.get("discord_user", "discord")
    quantity     = int(data.get("quantity", 1))

    if not product_id:
        return jsonify({"error": "product_id is required"}), 400

    prod = get_product(int(product_id))
    if not prod:
        return jsonify({"error": "Product not found"}), 404

    if quantity < 1 or quantity > 100:
        return jsonify({"error": "quantity must be 1..100"}), 400

    conn = get_conn()
    tokens = []
    for _ in range(quantity):
        token = secrets.token_urlsafe(20)
        conn.execute(
            "INSERT INTO invite_links (token, days, note, group_id) VALUES (?, ?, ?, ?)",
            (token, prod["days"], f"[Webhook] {discord_user} — {prod['name']}", prod["group_id"]),
        )
        tokens.append(token)
    conn.commit()
    conn.close()

    label = "vitalicio" if prod["days"] == 0 else f"{prod['days']} dias"
    add_log("WEBHOOK_PURCHASE", f"product={prod['name']} user={discord_user} qty={quantity}")

    links = [f"{config.BASE_URL}/acesso/{t}" for t in tokens]
    return jsonify({
        "ok": True,
        "product": prod["name"],
        "days": prod["days"],
        "quantity": quantity,
        "links": links,
    }), 201


# ===========================================================================
# Pagina de documentacao do webhook (no painel)
# ===========================================================================

@app.route("/webhook-docs")
@login_required
def webhook_docs():
    keys = list_api_keys()
    prods = list_products(only_active=True)
    return render_template(
        "webhook_docs.html",
        keys=keys,
        products=prods,
        base_url=config.BASE_URL,
        group_id=config.TELEGRAM_GROUP_ID,
    )
