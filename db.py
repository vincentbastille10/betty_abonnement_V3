# db.py
from __future__ import annotations
import json, sqlite3, os
from pathlib import Path
from contextlib import contextmanager

def pick_db_path() -> Path:
    env_forced = os.getenv("DB_PATH")
    if env_forced:
        p = Path(env_forced)
    else:
        is_serverless = bool(os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_VERSION"))
        p = Path("/tmp/bots.db") if is_serverless else Path("data/app.db")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p

DB_PATH = pick_db_path()

@contextmanager
def db_connect():
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    try:
        yield con
    finally:
        con.close()

def db_init():
    with db_connect() as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS bots (
            public_id    TEXT PRIMARY KEY,
            bot_key      TEXT NOT NULL,
            pack         TEXT NOT NULL,
            name         TEXT,
            color        TEXT,
            avatar_file  TEXT,
            greeting     TEXT,
            buyer_email  TEXT,
            owner_name   TEXT,
            profile_json TEXT
        )
        """)
        con.commit()

def db_upsert_bot(bot: dict):
    with db_connect() as con:
        con.execute("""
        INSERT INTO bots(public_id, bot_key, pack, name, color, avatar_file, greeting, buyer_email, owner_name, profile_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(public_id) DO UPDATE SET
          pack=excluded.pack,
          name=excluded.name,
          color=excluded.color,
          avatar_file=excluded.avatar_file,
          greeting=excluded.greeting,
          buyer_email=excluded.buyer_email,
          owner_name=excluded.owner_name,
          profile_json=excluded.profile_json
        """, (
            bot.get("public_id"),
            bot.get("bot_key"),
            bot.get("pack"),
            bot.get("name"),
            bot.get("color"),
            bot.get("avatar_file"),
            bot.get("greeting"),
            bot.get("buyer_email"),
            bot.get("owner_name"),
            json.dumps(bot.get("profile") or {}, ensure_ascii=False)
        ))
        con.commit()

def db_get_bot(public_id: str):
    if not public_id:
        return None
    with db_connect() as con:
        row = con.execute("SELECT * FROM bots WHERE public_id = ? LIMIT 1", (public_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["profile"] = {}
    if d.get("profile_json"):
        try:
            d["profile"] = json.loads(d["profile_json"])
        except Exception:
            d["profile"] = {}
    return d
