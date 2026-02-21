import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "subscriptions.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()

    # Tabela de links de convite
    c.execute("""
        CREATE TABLE IF NOT EXISTS invite_links (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            token       TEXT UNIQUE NOT NULL,
            days        INTEGER NOT NULL,           -- duracao da assinatura em dias
            used        INTEGER NOT NULL DEFAULT 0, -- 0 = disponivel, 1 = usado
            telegram_user_id  INTEGER,              -- quem usou o link
            telegram_username TEXT,
            used_at     TEXT,                       -- datetime ISO
            expires_at  TEXT,                       -- datetime ISO (usado_at + days)
            kicked      INTEGER NOT NULL DEFAULT 0, -- 0 = ainda no grupo, 1 = removido
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            note        TEXT                        -- anotacao livre (ex: "Produto Mensal Discord")
        )
    """)

    # Colunas extras (podem ja existir em BDs antigos)
    for alter_sql in [
        "ALTER TABLE invite_links ADD COLUMN warned INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE invite_links ADD COLUMN group_id INTEGER",  # multi-grupo
    ]:
        try:
            c.execute(alter_sql)
        except Exception:
            pass  # coluna ja existe

    # Tabela de log de acoes
    c.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            action     TEXT NOT NULL,
            detail     TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # Tabela de configuracoes (chave/valor)
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    # Tabela de grupos do Telegram (multi-grupo)
    c.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            name             TEXT NOT NULL,
            telegram_group_id INTEGER NOT NULL,
            description      TEXT,
            active           INTEGER NOT NULL DEFAULT 1,
            created_at       TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # Tabela de produtos (planos pre-configurados)
    c.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            days       INTEGER NOT NULL,           -- 0 = vitalicio
            group_id   INTEGER,                    -- FK groups.id (nulo = grupo padrao)
            note       TEXT,
            active     INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # Tabela de API keys (para webhook do Discord)
    c.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            key        TEXT UNIQUE NOT NULL,
            label      TEXT NOT NULL,
            active     INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # Valores padrao
    defaults = [
        ("warn_days_before", "3"),
        ("warn_enabled",     "1"),
        (
            "warn_message",
            "Ola {nome}! Sua assinatura expira em {dias} dia(s), no dia {data}. "
            "Renove para continuar com acesso ao grupo.",
        ),
    ]
    for key, value in defaults:
        c.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )

    conn.commit()
    conn.close()


def get_setting(key: str) -> str:
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else ""


def set_setting(key: str, value: str):
    conn = get_conn()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def add_log(action: str, detail: str = ""):
    conn = get_conn()
    conn.execute("INSERT INTO logs (action, detail) VALUES (?, ?)", (action, detail))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Helpers de grupos
# ---------------------------------------------------------------------------

def list_groups(only_active: bool = False):
    conn = get_conn()
    q = "SELECT * FROM groups"
    if only_active:
        q += " WHERE active=1"
    q += " ORDER BY name"
    rows = conn.execute(q).fetchall()
    conn.close()
    return rows


def get_group(group_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM groups WHERE id=?", (group_id,)).fetchone()
    conn.close()
    return row


# ---------------------------------------------------------------------------
# Helpers de produtos
# ---------------------------------------------------------------------------

def list_products(only_active: bool = False):
    conn = get_conn()
    q = """
        SELECT p.*, g.name AS group_name, g.telegram_group_id
        FROM products p
        LEFT JOIN groups g ON p.group_id = g.id
    """
    if only_active:
        q += " WHERE p.active=1"
    q += " ORDER BY p.name"
    rows = conn.execute(q).fetchall()
    conn.close()
    return rows


def get_product(product_id: int):
    conn = get_conn()
    row = conn.execute(
        """
        SELECT p.*, g.telegram_group_id
        FROM products p
        LEFT JOIN groups g ON p.group_id = g.id
        WHERE p.id=?
        """,
        (product_id,),
    ).fetchone()
    conn.close()
    return row


# ---------------------------------------------------------------------------
# Helpers de API keys
# ---------------------------------------------------------------------------

def list_api_keys():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM api_keys ORDER BY created_at DESC").fetchall()
    conn.close()
    return rows


def get_api_key(key: str):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM api_keys WHERE key=? AND active=1", (key,)
    ).fetchone()
    conn.close()
    return row
