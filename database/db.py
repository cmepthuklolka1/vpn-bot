import sqlite3
import os
import json
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bot_data.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS operators (
            telegram_id INTEGER PRIMARY KEY,
            name TEXT DEFAULT '',
            added_by INTEGER,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS client_config (
            email TEXT PRIMARY KEY,
            uuid TEXT DEFAULT '',
            monthly_traffic_gb INTEGER DEFAULT NULL,
            speed_base_mbps REAL DEFAULT NULL,
            speed_80pct_mbps REAL DEFAULT NULL,
            speed_95pct_mbps REAL DEFAULT NULL,
            device_limit INTEGER DEFAULT NULL,
            speed_override INTEGER DEFAULT 0,
            is_unlimited INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS traffic_archive (
            email TEXT,
            period TEXT,
            upload BIGINT DEFAULT 0,
            download BIGINT DEFAULT 0,
            archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (email, period)
        );

        CREATE TABLE IF NOT EXISTS notification_state (
            key TEXT PRIMARY KEY,
            notified_at TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS status_messages (
            chat_id INTEGER PRIMARY KEY,
            message_id INTEGER
        );

        CREATE TABLE IF NOT EXISTS bot_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    conn.commit()

    # Migrations
    try:
        conn.execute("ALTER TABLE client_config ADD COLUMN inbound_id INTEGER DEFAULT NULL")
        conn.commit()
    except Exception:
        pass  # Column already exists

    conn.close()


# --- Operators ---

def is_admin(telegram_id: int, config: dict) -> bool:
    return telegram_id == config["telegram"]["admin_id"]


def is_operator(telegram_id: int) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM operators WHERE telegram_id = ?", (telegram_id,)).fetchone()
    conn.close()
    return row is not None


def is_authorized(telegram_id: int, config: dict) -> bool:
    return is_admin(telegram_id, config) or is_operator(telegram_id)


def add_operator(telegram_id: int, name: str, added_by: int):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO operators (telegram_id, name, added_by) VALUES (?, ?, ?)",
        (telegram_id, name, added_by)
    )
    conn.commit()
    conn.close()


def remove_operator(telegram_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM operators WHERE telegram_id = ?", (telegram_id,))
    conn.commit()
    conn.close()


def list_operators():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM operators ORDER BY added_at").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Client Config ---

def get_client_config(email: str) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM client_config WHERE email = ?", (email,)).fetchone()
    conn.close()
    return dict(row) if row else None


def upsert_client_config(email: str, **kwargs):
    conn = get_conn()
    existing = conn.execute("SELECT 1 FROM client_config WHERE email = ?", (email,)).fetchone()
    if existing:
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [email]
        conn.execute(f"UPDATE client_config SET {sets} WHERE email = ?", vals)
    else:
        kwargs["email"] = email
        cols = ", ".join(kwargs.keys())
        placeholders = ", ".join("?" for _ in kwargs)
        conn.execute(f"INSERT INTO client_config ({cols}) VALUES ({placeholders})", list(kwargs.values()))
    conn.commit()
    conn.close()


def get_all_client_configs() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM client_config ORDER BY created_at").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_client_config(email: str):
    conn = get_conn()
    conn.execute("DELETE FROM client_config WHERE email = ?", (email,))
    conn.commit()
    conn.close()


def get_client_inbound_id(email: str) -> int | None:
    cfg = get_client_config(email)
    return cfg["inbound_id"] if cfg else None


def get_effective_config(email: str, defaults: dict) -> dict:
    """Get effective config for a client, falling back to defaults."""
    client = get_client_config(email)
    if not client:
        return {
            "monthly_traffic_gb": defaults["monthly_traffic_gb"],
            "speed_base_mbps": defaults["speed_base_mbps"],
            "speed_80pct_mbps": defaults["speed_80pct_mbps"],
            "speed_95pct_mbps": defaults["speed_95pct_mbps"],
            "device_limit": defaults["device_limit"],
            "speed_override": 0,
            "is_unlimited": 0,
        }

    return {
        "monthly_traffic_gb": client["monthly_traffic_gb"] if client["monthly_traffic_gb"] is not None else defaults["monthly_traffic_gb"],
        "speed_base_mbps": client["speed_base_mbps"] if client["speed_base_mbps"] is not None else defaults["speed_base_mbps"],
        "speed_80pct_mbps": client["speed_80pct_mbps"] if client["speed_80pct_mbps"] is not None else defaults["speed_80pct_mbps"],
        "speed_95pct_mbps": client["speed_95pct_mbps"] if client["speed_95pct_mbps"] is not None else defaults["speed_95pct_mbps"],
        "device_limit": client["device_limit"] if client["device_limit"] is not None else defaults["device_limit"],
        "speed_override": client["speed_override"],
        "is_unlimited": client["is_unlimited"],
    }


# --- Traffic Archive ---

def archive_traffic(email: str, period: str, upload: int, download: int):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO traffic_archive (email, period, upload, download) VALUES (?, ?, ?, ?)",
        (email, period, upload, download)
    )
    conn.commit()
    conn.close()


def get_archive(email: str = None, period: str = None) -> list[dict]:
    conn = get_conn()
    query = "SELECT * FROM traffic_archive WHERE 1=1"
    params = []
    if email:
        query += " AND email = ?"
        params.append(email)
    if period:
        query += " AND period = ?"
        params.append(period)
    rows = conn.execute(query + " ORDER BY period DESC", params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Notification State ---

def is_notified(key: str) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM notification_state WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row is not None


def set_notified(key: str):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO notification_state (key, notified_at) VALUES (?, ?)",
        (key, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def clear_notifications(prefix: str = ""):
    conn = get_conn()
    if prefix:
        conn.execute("DELETE FROM notification_state WHERE key LIKE ?", (f"{prefix}%",))
    else:
        conn.execute("DELETE FROM notification_state")
    conn.commit()
    conn.close()


# --- Status Messages ---

def get_status_message(chat_id: int) -> int | None:
    conn = get_conn()
    row = conn.execute("SELECT message_id FROM status_messages WHERE chat_id = ?", (chat_id,)).fetchone()
    conn.close()
    return row["message_id"] if row else None


def set_status_message(chat_id: int, message_id: int):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO status_messages (chat_id, message_id) VALUES (?, ?)",
        (chat_id, message_id)
    )
    conn.commit()
    conn.close()
