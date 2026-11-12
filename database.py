import sqlite3
import json
import hashlib
from datetime import datetime

DB_FILE = "orders.db"

def hash_password(password: str) -> str:
    return hashlib.sha256(password.strip().encode("utf-8")).hexdigest()

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Таблица заказов
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        order_id INTEGER PRIMARY KEY,
        created_at TEXT,
        client_name TEXT DEFAULT 'ООО «СтройГрупп»',
        discount_percent REAL DEFAULT 0.0,
        items_json TEXT,
        total_sum REAL DEFAULT 0.0
    )
    """)
    
    # Таблица пользователей (логины и пароли)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        full_name TEXT NOT NULL,
        is_admin INTEGER DEFAULT 0,
        created_at TEXT
    )
    """)
    
    # Создаем админа по умолчанию, если таблица пуста
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        now = datetime.now().strftime("%d.%m.%Y")
        cursor.execute("""
        INSERT INTO users (username, password_hash, full_name, is_admin, created_at)
        VALUES (?, ?, ?, ?, ?)
        """, ("admin", hash_password("admin12345"), "Главный администратор", 1, now))
    
    conn.commit()
    conn.close()

init_db()

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

# ----------------- ПОЛЬЗОВАТЕЛИ -----------------

def verify_user_login(username: str, password: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    pwd_hash = hash_password(password)
    cursor.execute("SELECT * FROM users WHERE username = ? AND password_hash = ?", (username.strip().lower(), pwd_hash))
    user = cursor.fetchone()
    conn.close()
    if user:
        return dict(user)
    return None

def create_user(username: str, password: str, full_name: str, is_admin: int = 0):
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now().strftime("%d.%m.%Y")
    try:
        cursor.execute("""
        INSERT INTO users (username, password_hash, full_name, is_admin, created_at)
        VALUES (?, ?, ?, ?, ?)
        """, (username.strip().lower(), hash_password(password), full_name.strip(), is_admin, now))
        conn.commit()
        conn.close()
        return True, "Пользователь успешно создан"
    except sqlite3.IntegrityError:
        conn.close()
        return False, "Логин уже занят другим клиентом"

def list_all_users():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, full_name, is_admin, created_at FROM users ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def delete_user_by_id(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE id = ? AND is_admin = 0", (user_id,))
    conn.commit()
    conn.close()

# ----------------- ЗАКАЗЫ -----------------

def save_order(order_data: dict):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    order_id = order_data["order_id"]
    created_at = order_data.get("created_at") or datetime.now().strftime("%d.%m.%Y %H:%M")
    client_name = order_data.get("client_name", "ООО «СтройГрупп»")
    discount = float(order_data.get("discount_percent", 0.0))
    items_json = json.dumps(order_data["items"], ensure_ascii=False)
    
    raw_sum = sum(
        it["selected_item"]["price"] * it["qty"]
        for it in order_data["items"]
        if it.get("selected_item")
    )
    total_sum = round(raw_sum * (1 - discount / 100), 2)

    cursor.execute("""
    INSERT OR REPLACE INTO orders (order_id, created_at, client_name, discount_percent, items_json, total_sum)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (order_id, created_at, client_name, discount, items_json, total_sum))
    
    conn.commit()
    conn.close()
    
    order_data["created_at"] = created_at
    order_data["client_name"] = client_name
    order_data["discount_percent"] = discount
    order_data["total_sum"] = total_sum
    return order_data

def get_order(order_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return None
        
    return {
        "order_id": row["order_id"],
        "created_at": row["created_at"],
        "client_name": row["client_name"],
        "discount_percent": row["discount_percent"],
        "items": json.loads(row["items_json"]),
        "total_sum": row["total_sum"]
    }

def list_recent_orders(limit: int = 15):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT order_id, created_at, client_name, discount_percent, total_sum, items_json FROM orders ORDER BY order_id DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    
    orders = []
    for r in rows:
        items = json.loads(r["items_json"])
        has_ambiguous = any(it.get("status") == "AMBIGUOUS" for it in items)
        has_not_found = any(it.get("status") == "NOT_FOUND" for it in items)
        
        status_label = "READY"
        if has_ambiguous or has_not_found:
            status_label = "ATTENTION"

        orders.append({
            "order_id": r["order_id"],
            "created_at": r["created_at"],
            "client_name": r["client_name"],
            "discount_percent": r["discount_percent"],
            "total_sum": r["total_sum"],
            "status_label": status_label,
            "items_count": len(items)
        })
    return orders