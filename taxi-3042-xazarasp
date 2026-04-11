import sqlite3
import hashlib
import os
import shutil
import json
import time
import threading
import random
import requests
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template_string, Response, session, redirect, url_for
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'taxi3042secret')

ADMIN_USERNAME  = os.environ.get('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD  = os.environ.get('ADMIN_PASSWORD', 'taxi3042')
DB_PATH         = 'taxi3042.db'
BACKUP_DIR      = 'backups'
TELEGRAM_TOKEN  = "8757251631:AAHMFD4cg1dU9SdZ8-7HMDxy5qDUpSc5TIs"
ADMIN_CHAT_ID   = "1053431273"

# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": ADMIN_CHAT_ID,
            "text": text,
            "parse_mode": "HTML"
        }, timeout=5)
    except Exception as e:
        print(f"Telegram error: {e}")

def answer_telegram(chat_id, text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }, timeout=5)
    except Exception as e:
        print(f"Telegram error: {e}")

# ============================================================
# БАЗА ДАННЫХ
# ============================================================

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS drivers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        car_number TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        phone TEXT,
        pin_hash TEXT,
        balance REAL DEFAULT 0.0,
        rating REAL DEFAULT 0.0,
        rating_count INTEGER DEFAULT 0,
        is_online INTEGER DEFAULT 0,
        is_busy INTEGER DEFAULT 0,
        last_seen DATETIME,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_address TEXT NOT NULL,
        to_address TEXT,
        price REAL DEFAULT 0.0,
        status TEXT DEFAULT 'pending',
        driver_id INTEGER,
        dispatcher_note TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        accepted_at DATETIME,
        completed_at DATETIME,
        cancelled_at DATETIME,
        FOREIGN KEY (driver_id) REFERENCES drivers(id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS pending_pins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        car_number TEXT NOT NULL,
        name TEXT NOT NULL,
        phone TEXT,
        status TEXT DEFAULT 'pending',
        pin_hash TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS balance_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        driver_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        status TEXT DEFAULT 'pending',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (driver_id) REFERENCES drivers(id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        driver_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        type TEXT NOT NULL,
        description TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (driver_id) REFERENCES drivers(id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS shifts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        driver_id INTEGER NOT NULL,
        started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        ended_at DATETIME,
        revenue REAL DEFAULT 0.0,
        FOREIGN KEY (driver_id) REFERENCES drivers(id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        driver_id INTEGER NOT NULL,
        sender TEXT NOT NULL,
        message TEXT NOT NULL,
        is_read INTEGER DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (driver_id) REFERENCES drivers(id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS tariffs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        base_fare REAL DEFAULT 5000.0,
        per_km REAL DEFAULT 1000.0,
        per_minute REAL DEFAULT 200.0,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action TEXT NOT NULL,
        details TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS sse_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        driver_id INTEGER,
        event_type TEXT NOT NULL,
        data TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute('SELECT COUNT(*) FROM tariffs')
    if c.fetchone()[0] == 0:
        c.execute(
            'INSERT INTO tariffs (base_fare, per_km, per_minute) VALUES (5000, 1000, 200)'
        )

    conn.commit()
    conn.close()

def migrate_db():
    conn = get_db()
    c = conn.cursor()
    migrations = [
        "ALTER TABLE drivers ADD COLUMN rating REAL DEFAULT 0.0",
        "ALTER TABLE drivers ADD COLUMN rating_count INTEGER DEFAULT 0",
        "ALTER TABLE drivers ADD COLUMN last_seen DATETIME",
        "ALTER TABLE drivers ADD COLUMN is_busy INTEGER DEFAULT 0",
        "ALTER TABLE orders ADD COLUMN dispatcher_note TEXT",
        "ALTER TABLE orders ADD COLUMN accepted_at DATETIME",
        "ALTER TABLE orders ADD COLUMN completed_at DATETIME",
        "ALTER TABLE orders ADD COLUMN cancelled_at DATETIME",
    ]
    for sql in migrations:
        try:
            c.execute(sql)
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()

def log_action(action, details=None):
    try:
        conn = get_db()
        conn.execute(
            'INSERT INTO logs (action, details) VALUES (?, ?)',
            (action, details)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

def hash_pin(pin):
    return hashlib.sha256(str(pin).encode()).hexdigest()

# ============================================================
# АВТОРИЗАЦИЯ
# ============================================================

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated

# ============================================================
# ФОНОВЫЕ ЗАДАЧИ
# ============================================================

def background_tasks():
    while True:
        try:
            conn = get_db()
            c = conn.cursor()
            now = datetime.now()

            timeout = (now - timedelta(seconds=120)).strftime('%Y-%m-%d %H:%M:%S')
            c.execute('''UPDATE drivers SET is_online=0, is_busy=0
                        WHERE is_online=1 AND (last_seen IS NULL OR last_seen < ?)''',
                     (timeout,))

            order_timeout = (now - timedelta(minutes=60)).strftime('%Y-%m-%d %H:%M:%S')
            c.execute('''UPDATE orders SET status='cancelled', cancelled_at=?
                        WHERE status IN ('pending','accepted') AND created_at < ?''',
                     (now.strftime('%Y-%m-%d %H:%M:%S'), order_timeout))

            old = (now - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
            c.execute("DELETE FROM sse_events WHERE created_at < ?", (old,))

            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Background error: {e}")
        time.sleep(30)

# ============================================================
# CORS
# ============================================================

@app.after_request
def add_cors(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    return response

@app.route('/api/<path:path>', methods=['OPTIONS'])
def options_handler(path):
    return jsonify({'ok': True})

# ============================================================
# ADMIN LOGIN
# ============================================================

LOGIN_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <title>TAXI 3042 - Вход</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin:0; padding:0; box-sizing:border-box; }
        body { background:#1a1a2e; display:flex; align-items:center;
               justify-content:center; height:100vh; font-family:Arial; }
        .box { background:#16213e; padding:40px; border-radius:15px;
               width:350px; box-shadow:0 10px 30px rgba(0,0,0,0.5); }
        h2 { color:#e94560; text-align:center; margin-bottom:30px; font-size:24px; }
        input { width:100%; padding:12px; margin:8px 0;
                border:1px solid #0f3460; border-radius:8px;
                background:#0f3460; color:white; font-size:16px; }
        button { width:100%; padding:12px; background:#e94560; color:white;
                 border:none; border-radius:8px; font-size:16px;
                 cursor:pointer; margin-top:10px; }
        button:hover { background:#c73652; }
        .error { color:#e94560; text-align:center; margin-top:10px; }
    </style>
</head>
<body>
<div class="box">
    <h2>🚕 TAXI 3042</h2>
    <form method="POST">
        <input type="text" name="username" placeholder="Логин" required>
        <input type="password" name="password" placeholder="Пароль" required>
        <button type="submit">Войти</button>
    </form>
    {% if error %}<p class="error">{{ error }}</p>{% endif %}
</div>
</body>
</html>
'''

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    error = None
    if request.method == 'POST':
        u = request.form.get('username')
        p = request.form.get('password')
        if u == ADMIN_USERNAME and p == ADMIN_PASSWORD:
            session['admin'] = True
            return redirect(url_for('admin_dashboard'))
        error = 'Неверный логин или пароль'
    return render_template_string(LOGIN_HTML, error=error)

@app.route('/admin/logout')
def admin_logout():
    session.clear()
    return redirect(url_for('admin_login'))

# ============================================================
# ADMIN DASHBOARD
# ============================================================

DASHBOARD_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <title>TAXI 3042 - Панель</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin:0; padding:0; box-sizing:border-box; }
        body { background:#1a1a2e; color:#eee; font-family:Arial; }
        .sidebar { width:220px; background:#16213e; height:100vh;
                   position:fixed; left:0; top:0; padding:20px 0;
                   overflow-y:auto; }
        .sidebar h2 { color:#e94560; text-align:center;
                      padding:15px; font-size:20px; }
        .sidebar a { display:block; padding:12px 20px; color:#aaa;
                     text-decoration:none; border-left:3px solid transparent;
                     transition:0.2s; }
        .sidebar a:hover, .sidebar a.active {
            color:#fff; border-left-color:#e94560; background:#0f3460; }
        .main { margin-left:220px; padding:20px; }
        .stats { display:grid;
                 grid-template-columns:repeat(auto-fit,minmax(200px,1fr));
                 gap:15px; margin-bottom:20px; }
        .stat-card { background:#16213e; border-radius:10px; padding:20px;
                     border-left:4px solid #e94560; }
        .stat-card h3 { color:#aaa; font-size:14px; margin-bottom:8px; }
        .stat-card .num { color:#fff; font-size:28px; font-weight:bold; }
        .card { background:#16213e; border-radius:10px;
                padding:20px; margin-bottom:20px; }
        .card h3 { color:#e94560; margin-bottom:15px; font-size:18px; }
        table { width:100%; border-collapse:collapse; }
        th { background:#0f3460; padding:10px; text-align:left;
             color:#aaa; font-size:13px; }
        td { padding:10px; border-bottom:1px solid #0f3460; font-size:14px; }
        tr:hover td { background:#0f3460; }
        .btn { padding:6px 14px; border:none; border-radius:6px;
               cursor:pointer; font-size:13px; margin:2px; }
        .btn-green  { background:#27ae60; color:white; }
        .btn-red    { background:#e94560; color:white; }
        .btn-blue   { background:#2980b9; color:white; }
        .btn-orange { background:#f39c12; color:white; }
        .badge { padding:3px 10px; border-radius:20px; font-size:12px; }
        .badge-green  { background:#27ae60; color:white; }
        .badge-red    { background:#e94560; color:white; }
        .badge-orange { background:#f39c12; color:white; }
        .badge-gray   { background:#555; color:white; }
        .tab-content { display:none; }
        .tab-content.active { display:block; }
        input, select, textarea {
            background:#0f3460; border:1px solid #1a4a7a;
            color:white; padding:8px 12px; border-radius:6px;
            font-size:14px; width:100%; }
        .form-row { display:grid; grid-template-columns:1fr 1fr;
                    gap:10px; margin-bottom:10px; }
        .form-group { margin-bottom:10px; }
        .form-group label { display:block; color:#aaa;
                            margin-bottom:5px; font-size:13px; }
        .chat-box { height:300px; overflow-y:auto; background:#0f3460;
                    border-radius:8px; padding:15px; margin-bottom:10px; }
        .msg { margin-bottom:10px; }
        .msg.from-admin { text-align:right; }
        .msg-bubble { display:inline-block; padding:8px 14px;
                      border-radius:10px; max-width:70%; font-size:14px; }
        .from-driver .msg-bubble { background:#1a4a7a; }
        .from-admin  .msg-bubble { background:#e94560; }
        .msg-time { color:#666; font-size:11px; margin-top:3px; }
        .pagination { display:flex; gap:5px; margin-top:15px; }
        .pagination button { padding:6px 12px; background:#0f3460;
                             color:white; border:none; border-radius:5px;
                             cursor:pointer; }
        .pagination button.active { background:#e94560; }
        .modal { display:none; position:fixed; top:0; left:0;
                 width:100%; height:100%; background:rgba(0,0,0,0.7);
                 z-index:1000; align-items:center; justify-content:center; }
        .modal.show { display:flex; }
        .modal-box { background:#16213e; border-radius:12px;
                     padding:30px; width:400px; max-width:90%; }
        .modal-box h3 { color:#e94560; margin-bottom:20px; }
        #notification { position:fixed; top:20px; right:20px;
                        padding:12px 20px; border-radius:8px; color:white;
                        font-size:14px; z-index:9999; display:none; }
        .notif-success { background:#27ae60; }
        .notif-error   { background:#e94560; }
    </style>
</head>
<body>
<div id="notification"></div>
<div class="sidebar">
    <h2>🚕 TAXI 3042</h2>
    <a href="#" class="active" onclick="showTab('dashboard')">📊 Дашборд</a>
    <a href="#" onclick="showTab('orders')">📋 Заказы</a>
    <a href="#" onclick="showTab('drivers')">🚗 Водители</a>
    <a href="#" onclick="showTab('pending')">⏳ Заявки</a>
    <a href="#" onclick="showTab('balance_req')">💰 Пополнения</a>
    <a href="#" onclick="showTab('finance')">📈 Финансы</a>
    <a href="#" onclick="showTab('chat')">💬 Чат</a>
    <a href="#" onclick="showTab('tariffs')">⚙️ Тарифы</a>
    <a href="#" onclick="showTab('backups')">💾 Бэкапы</a>
    <a href="#" onclick="showTab('logs_tab')">📜 Логи</a>
    <a href="/admin/logout" style="color:#e94560;margin-top:20px;">🚪 Выйти</a>
</div>
<div class="main">

    <div id="tab-dashboard" class="tab-content active">
        <h2 style="color:#e94560;margin-bottom:20px;">📊 Дашборд</h2>
        <div class="stats">
            <div class="stat-card">
                <h3>Онлайн водителей</h3>
                <div class="num" id="stat-online">—</div>
            </div>
            <div class="stat-card">
                <h3>Активные заказы</h3>
                <div class="num" id="stat-active">—</div>
            </div>
            <div class="stat-card">
                <h3>Выручка сегодня</h3>
                <div class="num" id="stat-today">—</div>
            </div>
            <div class="stat-card">
                <h3>Всего водителей</h3>
                <div class="num" id="stat-total">—</div>
            </div>
        </div>
        <div class="card">
            <h3>➕ Создать заказ</h3>
            <div class="form-row">
                <div class="form-group">
                    <label>Откуда</label>
                    <input type="text" id="order-from" placeholder="Адрес">
                </div>
                <div class="form-group">
                    <label>Куда</label>
                    <input type="text" id="order-to" placeholder="Адрес">
                </div>
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label>Цена (сум)</label>
                    <input type="number" id="order-price" placeholder="0">
                </div>
                <div class="form-group">
                    <label>Водитель</label>
                    <select id="order-driver">
                        <option value="">— Любой свободный —</option>
                    </select>
                </div>
            </div>
            <div class="form-group">
                <label>Примечание</label>
                <input type="text" id="order-note" placeholder="Примечание">
            </div>
            <button class="btn btn-green" onclick="createOrder()">Создать заказ</button>
        </div>
        <div class="card">
            <h3>🔴 Активные заказы</h3>
            <table>
                <thead><tr>
                    <th>ID</th><th>Откуда</th><th>Куда</th>
                    <th>Цена</th><th>Водитель</th><th>Статус</th><th>Действия</th>
                </tr></thead>
                <tbody id="active-orders-table"></tbody>
            </table>
        </div>
    </div>

    <div id="tab-orders" class="tab-content">
        <h2 style="color:#e94560;margin-bottom:20px;">📋 Все заказы</h2>
        <div class="card">
            <select id="order-filter-status" onchange="loadOrders(1)"
                    style="width:200px;margin-bottom:15px;">
                <option value="">Все статусы</option>
                <option value="pending">Ожидание</option>
                <option value="accepted">Принят</option>
                <option value="completed">Завершён</option>
                <option value="cancelled">Отменён</option>
            </select>
            <table>
                <thead><tr>
                    <th>ID</th><th>Откуда</th><th>Куда</th><th>Цена</th>
                    <th>Водитель</th><th>Статус</th><th>Время</th><th>Действия</th>
                </tr></thead>
                <tbody id="orders-table"></tbody>
            </table>
            <div class="pagination" id="orders-pagination"></div>
        </div>
    </div>

    <div id="tab-drivers" class="tab-content">
        <h2 style="color:#e94560;margin-bottom:20px;">🚗 Водители</h2>
        <div class="card">
            <table>
                <thead><tr>
                    <th>Авто</th><th>Имя</th><th>Телефон</th><th>Баланс</th>
                    <th>Рейтинг</th><th>Статус</th><th>Последний раз</th><th>Действия</th>
                </tr></thead>
                <tbody id="drivers-table"></tbody>
            </table>
            <div class="pagination" id="drivers-pagination"></div>
        </div>
    </div>

    <div id="tab-pending" class="tab-content">
        <h2 style="color:#e94560;margin-bottom:20px;">⏳ Заявки</h2>
        <div class="card">
            <table>
                <thead><tr>
                    <th>Авто</th><th>Имя</th><th>Телефон</th>
                    <th>Дата</th><th>Действия</th>
                </tr></thead>
                <tbody id="pending-table"></tbody>
            </table>
        </div>
    </div>

    <div id="tab-balance_req" class="tab-content">
        <h2 style="color:#e94560;margin-bottom:20px;">💰 Пополнения</h2>
        <div class="card">
            <table>
                <thead><tr>
                    <th>Водитель</th><th>Авто</th><th>Сумма</th>
                    <th>Дата</th><th>Действия</th>
                </tr></thead>
                <tbody id="balance-req-table"></tbody>
            </table>
        </div>
    </div>

    <div id="tab-finance" class="tab-content">
        <h2 style="color:#e94560;margin-bottom:20px;">📈 Финансы</h2>
        <div class="stats">
            <div class="stat-card">
                <h3>Сегодня</h3><div class="num" id="fin-today">—</div>
            </div>
            <div class="stat-card">
                <h3>Неделя</h3><div class="num" id="fin-week">—</div>
            </div>
            <div class="stat-card">
                <h3>Месяц</h3><div class="num" id="fin-month">—</div>
            </div>
        </div>
        <div class="card">
            <h3>Транзакции</h3>
            <table>
                <thead><tr>
                    <th>Водитель</th><th>Сумма</th><th>Тип</th>
                    <th>Описание</th><th>Дата</th>
                </tr></thead>
                <tbody id="transactions-table"></tbody>
            </table>
            <div class="pagination" id="transactions-pagination"></div>
        </div>
    </div>

    <div id="tab-chat" class="tab-content">
        <h2 style="color:#e94560;margin-bottom:20px;">💬 Чат</h2>
        <div style="display:grid;grid-template-columns:250px 1fr;gap:20px;">
            <div class="card">
                <h3>Водители</h3>
                <div id="chat-drivers-list"></div>
            </div>
            <div class="card">
                <h3 id="chat-driver-name">Выберите водителя</h3>
                <div class="chat-box" id="chat-messages"></div>
                <div style="display:flex;gap:10px;">
                    <input type="text" id="chat-input" placeholder="Сообщение..."
                           onkeypress="if(event.key==='Enter') sendMessage()">
                    <button class="btn btn-blue" onclick="sendMessage()">Отправить</button>
                </div>
            </div>
        </div>
    </div>

    <div id="tab-tariffs" class="tab-content">
        <h2 style="color:#e94560;margin-bottom:20px;">⚙️ Тарифы</h2>
        <div class="card" style="max-width:400px;">
            <div class="form-group">
                <label>Посадка (сум)</label>
                <input type="number" id="tariff-base" placeholder="5000">
            </div>
            <div class="form-group">
                <label>За км (сум)</label>
                <input type="number" id="tariff-km" placeholder="1000">
            </div>
            <div class="form-group">
                <label>За минуту (сум)</label>
                <input type="number" id="tariff-min" placeholder="200">
            </div>
            <button class="btn btn-green" onclick="saveTariffs()">Сохранить</button>
        </div>
    </div>

    <div id="tab-backups" class="tab-content">
        <h2 style="color:#e94560;margin-bottom:20px;">💾 Бэкапы</h2>
        <div class="card">
            <button class="btn btn-green" onclick="createBackup()"
                    style="margin-bottom:15px;">➕ Создать бэкап</button>
            <table>
                <thead><tr>
                    <th>Файл</th><th>Размер</th><th>Дата</th>
                </tr></thead>
                <tbody id="backups-table"></tbody>
            </table>
        </div>
    </div>

    <div id="tab-logs_tab" class="tab-content">
        <h2 style="color:#e94560;margin-bottom:20px;">📜 Логи</h2>
        <div class="card">
            <button class="btn btn-blue" onclick="exportReport()"
                    style="margin-bottom:15px;">📥 Экспорт TXT</button>
            <table>
                <thead><tr>
                    <th>Действие</th><th>Детали</th><th>Время</th>
                </tr></thead>
                <tbody id="logs-table"></tbody>
            </table>
            <div class="pagination" id="logs-pagination"></div>
        </div>
    </div>
</div>

<div class="modal" id="modal-balance">
    <div class="modal-box">
        <h3>💰 Пополнить баланс</h3>
        <input type="hidden" id="balance-driver-id">
        <div class="form-group">
            <label>Сумма (сум)</label>
            <input type="number" id="balance-amount" placeholder="10000">
        </div>
        <div style="display:flex;gap:10px;margin-top:15px;">
            <button class="btn btn-green" onclick="addBalance()">Пополнить</button>
            <button class="btn btn-red" onclick="closeModal('modal-balance')">Отмена</button>
        </div>
    </div>
</div>

<div class="modal" id="modal-pin">
    <div class="modal-box">
        <h3>🔑 Новый PIN</h3>
        <p id="new-pin-text"
           style="font-size:32px;text-align:center;color:#e94560;
                  margin:20px 0;letter-spacing:5px;"></p>
        <button class="btn btn-blue" style="width:100%"
                onclick="closeModal('modal-pin')">Закрыть</button>
    </div>
</div>

<script>
let currentTab = 'dashboard';
let selectedDriverId = null;
let chatInterval = null;

function showTab(tab) {
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.sidebar a').forEach(el => el.classList.remove('active'));
    document.getElementById('tab-' + tab).classList.add('active');
    currentTab = tab;
    const loaders = {
        'dashboard':   () => { loadDashboard(); loadActiveOrders(); loadDriversForSelect(); },
        'orders':      () => loadOrders(1),
        'drivers':     () => loadDrivers(1),
        'pending':     () => loadPending(),
        'balance_req': () => loadBalanceRequests(),
        'finance':     () => loadFinance(1),
        'chat':        () => loadChatDrivers(),
        'tariffs':     () => loadTariffs(),
        'backups':     () => loadBackups(),
        'logs_tab':    () => loadLogs(1),
    };
    if (loaders[tab]) loaders[tab]();
}

function notify(msg, type='success') {
    const el = document.getElementById('notification');
    el.textContent = msg;
    el.className = type === 'success' ? 'notif-success' : 'notif-error';
    el.style.display = 'block';
    setTimeout(() => el.style.display = 'none', 3000);
}

async function api(url, method='GET', body=null) {
    const opts = { method, headers: {'Content-Type':'application/json'} };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(url, opts);
    return res.json();
}

async function loadDashboard() {
    const d = await api('/api/admin/dashboard');
    if (d.online_drivers !== undefined) {
        document.getElementById('stat-online').textContent = d.online_drivers;
        document.getElementById('stat-active').textContent = d.active_orders;
        document.getElementById('stat-today').textContent =
            Number(d.today_revenue).toLocaleString() + ' сум';
        document.getElementById('stat-total').textContent = d.total_drivers;
    }
}

async function loadActiveOrders() {
    const d = await api('/api/admin/orders?status=pending,accepted&per_page=50');
    const tbody = document.getElementById('active-orders-table');
    tbody.innerHTML = '';
    (d.orders || []).forEach(o => {
        tbody.innerHTML += `<tr>
            <td>#${o.id}</td><td>${o.from_address}</td>
            <td>${o.to_address || '—'}</td>
            <td>${Number(o.price).toLocaleString()}</td>
            <td>${o.driver_name || '—'}</td>
            <td>${statusBadge(o.status)}</td>
            <td>
                ${o.status !== 'completed' && o.status !== 'cancelled' ?
                    `<button class="btn btn-green" onclick="completeOrder(${o.id})">✓</button>
                     <button class="btn btn-red" onclick="cancelOrder(${o.id})">✗</button>` : ''}
            </td>
        </tr>`;
    });
}

async function loadDriversForSelect() {
    const d = await api('/api/admin/drivers?per_page=100');
    const sel = document.getElementById('order-driver');
    sel.innerHTML = '<option value="">— Любой свободный —</option>';
    (d.drivers || []).forEach(dr => {
        if (dr.is_online) {
            sel.innerHTML +=
                `<option value="${dr.id}">${dr.car_number} — ${dr.name}</option>`;
        }
    });
}

function statusBadge(s) {
    const map = {
        pending:   ['badge-orange', 'Ожидание'],
        accepted:  ['badge-green',  'Принят'],
        completed: ['badge-green',  'Завершён'],
        cancelled: ['badge-red',    'Отменён'],
    };
    const [cls, label] = map[s] || ['badge-gray', s];
    return `<span class="badge ${cls}">${label}</span>`;
}

async function createOrder() {
    const from      = document.getElementById('order-from').value;
    const to        = document.getElementById('order-to').value;
    const price     = document.getElementById('order-price').value;
    const driver_id = document.getElementById('order-driver').value;
    const note      = document.getElementById('order-note').value;
    if (!from) return notify('Укажите адрес', 'error');
    const d = await api('/api/admin/orders', 'POST', {
        from_address: from, to_address: to,
        price: Number(price), driver_id: driver_id || null,
        dispatcher_note: note
    });
    if (d.success) {
        notify('Заказ создан!');
        document.getElementById('order-from').value  = '';
        document.getElementById('order-to').value    = '';
        document.getElementById('order-price').value = '';
        document.getElementById('order-note').value  = '';
        loadActiveOrders();
    } else notify(d.error || 'Ошибка', 'error');
}

async function completeOrder(id) {
    const d = await api(`/api/admin/orders/${id}/complete`, 'POST');
    if (d.success) { notify('Завершён!'); loadActiveOrders(); loadDashboard(); }
    else notify(d.error || 'Ошибка', 'error');
}

async function cancelOrder(id) {
    const d = await api(`/api/admin/orders/${id}/cancel`, 'POST');
    if (d.success) { notify('Отменён!'); loadActiveOrders(); loadDashboard(); }
    else notify(d.error || 'Ошибка', 'error');
}

async function loadOrders(page=1) {
    const status = document.getElementById('order-filter-status').value;
    let url = `/api/admin/orders?page=${page}&per_page=20`;
    if (status) url += `&status=${status}`;
    const d = await api(url);
    const tbody = document.getElementById('orders-table');
    tbody.innerHTML = '';
    (d.orders || []).forEach(o => {
        tbody.innerHTML += `<tr>
            <td>#${o.id}</td><td>${o.from_address}</td>
            <td>${o.to_address || '—'}</td>
            <td>${Number(o.price).toLocaleString()}</td>
            <td>${o.driver_name || '—'}</td>
            <td>${statusBadge(o.status)}</td>
            <td>${o.created_at}</td>
            <td>
                ${o.status === 'pending' || o.status === 'accepted' ?
                    `<button class="btn btn-green" onclick="completeOrder(${o.id})">✓</button>
                     <button class="btn btn-red" onclick="cancelOrder(${o.id})">✗</button>` : ''}
            </td>
        </tr>`;
    });
    renderPagination('orders-pagination', page, d.total_pages, loadOrders);
}

async function loadDrivers(page=1) {
    const d = await api(`/api/admin/drivers?page=${page}&per_page=20`);
    const tbody = document.getElementById('drivers-table');
    tbody.innerHTML = '';
    (d.drivers || []).forEach(dr => {
        tbody.innerHTML += `<tr>
            <td>${dr.car_number}</td><td>${dr.name}</td>
            <td>${dr.phone || '—'}</td>
            <td>${Number(dr.balance).toLocaleString()} сум</td>
            <td>⭐ ${Number(dr.rating || 0).toFixed(1)}</td>
            <td><span class="badge ${dr.is_online ? 'badge-green':'badge-gray'}">
                ${dr.is_online ? 'Онлайн':'Офлайн'}</span></td>
            <td>${dr.last_seen || '—'}</td>
            <td>
                <button class="btn btn-blue"
                    onclick="openBalanceModal(${dr.id})">💰</button>
                <button class="btn btn-orange"
                    onclick="resetPin(${dr.id})">🔑</button>
                <button class="btn btn-red"
                    onclick="deleteDriver(${dr.id})">🗑</button>
            </td>
        </tr>`;
    });
    renderPagination('drivers-pagination', page, d.total_pages, loadDrivers);
}

function openBalanceModal(id) {
    document.getElementById('balance-driver-id').value = id;
    document.getElementById('balance-amount').value = '';
    document.getElementById('modal-balance').classList.add('show');
}

async function addBalance() {
    const id     = document.getElementById('balance-driver-id').value;
    const amount = document.getElementById('balance-amount').value;
    if (!amount || amount <= 0) return notify('Введите сумму', 'error');
    const d = await api(`/api/admin/drivers/${id}/balance`, 'POST',
        { amount: Number(amount) });
    if (d.success) {
        notify('Пополнено!');
        closeModal('modal-balance');
        loadDrivers();
    } else notify(d.error || 'Ошибка', 'error');
}

async function resetPin(id) {
    const d = await api(`/api/admin/drivers/${id}/reset_pin`, 'POST');
    if (d.success) {
        document.getElementById('new-pin-text').textContent = d.new_pin;
        document.getElementById('modal-pin').classList.add('show');
    } else notify(d.error || 'Ошибка', 'error');
}

async function deleteDriver(id) {
    if (!confirm('Удалить водителя?')) return;
    const d = await api(`/api/admin/drivers/${id}`, 'DELETE');
    if (d.success) { notify('Удалён!'); loadDrivers(); }
    else notify(d.error || 'Ошибка', 'error');
}

async function loadPending() {
    const d = await api('/api/admin/pending');
    const tbody = document.getElementById('pending-table');
    tbody.innerHTML = '';
    (d.pending || []).forEach(p => {
        tbody.innerHTML += `<tr>
            <td>${p.car_number}</td><td>${p.name}</td>
            <td>${p.phone || '—'}</td><td>${p.created_at}</td>
            <td>
                <button class="btn btn-green"
                    onclick="approvePending(${p.id})">✅ Одобрить</button>
                <button class="btn btn-red"
                    onclick="rejectPending(${p.id})">❌ Отклонить</button>
            </td>
        </tr>`;
    });
}

async function approvePending(id) {
    const d = await api(`/api/admin/pending/${id}/approve`, 'POST');
    if (d.success) { notify(`PIN: ${d.pin}`); loadPending(); }
    else notify(d.error || 'Ошибка', 'error');
}

async function rejectPending(id) {
    const d = await api(`/api/admin/pending/${id}/reject`, 'POST');
    if (d.success) { notify('Отклонён!'); loadPending(); }
    else notify(d.error || 'Ошибка', 'error');
}

async function loadBalanceRequests() {
    const d = await api('/api/admin/balance_requests');
    const tbody = document.getElementById('balance-req-table');
    tbody.innerHTML = '';
    (d.requests || []).forEach(r => {
        tbody.innerHTML += `<tr>
            <td>${r.driver_name}</td><td>${r.car_number}</td>
            <td>${Number(r.amount).toLocaleString()} сум</td>
            <td>${r.created_at}</td>
            <td>
                <button class="btn btn-green"
                    onclick="approveBalanceReq(${r.id})">✅</button>
                <button class="btn btn-red"
                    onclick="rejectBalanceReq(${r.id})">❌</button>
            </td>
        </tr>`;
    });
}

async function approveBalanceReq(id) {
    const d = await api(`/api/admin/balance_requests/${id}/approve`, 'POST');
    if (d.success) { notify('Пополнено!'); loadBalanceRequests(); }
    else notify(d.error || 'Ошибка', 'error');
}

async function rejectBalanceReq(id) {
    const d = await api(`/api/admin/balance_requests/${id}/reject`, 'POST');
    if (d.success) { notify('Отклонено!'); loadBalanceRequests(); }
    else notify(d.error || 'Ошибка', 'error');
}

async function loadFinance(page=1) {
    const d = await api(`/api/admin/finance?page=${page}&per_page=20`);
    document.getElementById('fin-today').textContent =
        Number(d.today || 0).toLocaleString() + ' сум';
    document.getElementById('fin-week').textContent =
        Number(d.week || 0).toLocaleString() + ' сум';
    document.getElementById('fin-month').textContent =
        Number(d.month || 0).toLocaleString() + ' сум';
    const tbody = document.getElementById('transactions-table');
    tbody.innerHTML = '';
    (d.transactions || []).forEach(t => {
        tbody.innerHTML += `<tr>
            <td>${t.driver_name || '—'}</td>
            <td style="color:${t.amount>=0?'#27ae60':'#e94560'}">
                ${t.amount>=0?'+':''}${Number(t.amount).toLocaleString()} сум
            </td>
            <td>${t.type}</td><td>${t.description || '—'}</td>
            <td>${t.created_at}</td>
        </tr>`;
    });
    renderPagination('transactions-pagination', page, d.total_pages, loadFinance);
}

async function loadChatDrivers() {
    const d = await api('/api/admin/drivers?per_page=100');
    const list = document.getElementById('chat-drivers-list');
    list.innerHTML = '';
    (d.drivers || []).forEach(dr => {
        list.innerHTML += `<div style="padding:10px;cursor:pointer;
            border-radius:6px;border-bottom:1px solid #0f3460;"
            onclick="selectChatDriver(${dr.id},'${dr.name} (${dr.car_number})')">
            <span class="badge ${dr.is_online?'badge-green':'badge-gray'}"
                style="margin-right:5px;">●</span>
            ${dr.name} — ${dr.car_number}
        </div>`;
    });
}

function selectChatDriver(id, name) {
    selectedDriverId = id;
    document.getElementById('chat-driver-name').textContent = '💬 ' + name;
    loadMessages();
    if (chatInterval) clearInterval(chatInterval);
    chatInterval = setInterval(loadMessages, 3000);
}

async function loadMessages() {
    if (!selectedDriverId) return;
    const d = await api(`/api/admin/chat/${selectedDriverId}`);
    const box = document.getElementById('chat-messages');
    box.innerHTML = '';
    (d.messages || []).forEach(m => {
        box.innerHTML += `<div class="msg from-${m.sender}">
            <div class="msg-bubble">${m.message}</div>
            <div class="msg-time">${m.created_at}</div>
        </div>`;
    });
    box.scrollTop = box.scrollHeight;
}

async function sendMessage() {
    if (!selectedDriverId) return notify('Выберите водителя', 'error');
    const input = document.getElementById('chat-input');
    const msg = input.value.trim();
    if (!msg) return;
    const d = await api(`/api/admin/chat/${selectedDriverId}`, 'POST',
        { message: msg });
    if (d.success) { input.value = ''; loadMessages(); }
    else notify(d.error || 'Ошибка', 'error');
}

async function loadTariffs() {
    const d = await api('/api/admin/tariffs');
    if (d.tariff) {
        document.getElementById('tariff-base').value = d.tariff.base_fare;
        document.getElementById('tariff-km').value   = d.tariff.per_km;
        document.getElementById('tariff-min').value  = d.tariff.per_minute;
    }
}

async function saveTariffs() {
    const d = await api('/api/admin/tariffs', 'POST', {
        base_fare:  Number(document.getElementById('tariff-base').value),
        per_km:     Number(document.getElementById('tariff-km').value),
        per_minute: Number(document.getElementById('tariff-min').value),
    });
    if (d.success) notify('Сохранено!');
    else notify(d.error || 'Ошибка', 'error');
}

async function loadBackups() {
    const d = await api('/api/admin/backups');
    const tbody = document.getElementById('backups-table');
    tbody.innerHTML = '';
    (d.backups || []).forEach(b => {
        tbody.innerHTML += `<tr>
            <td>${b.name}</td><td>${b.size}</td><td>${b.date}</td>
        </tr>`;
    });
}

async function createBackup() {
    const d = await api('/api/admin/backups', 'POST');
    if (d.success) { notify('Бэкап создан!'); loadBackups(); }
    else notify(d.error || 'Ошибка', 'error');
}

async function loadLogs(page=1) {
    const d = await api(`/api/admin/logs?page=${page}&per_page=20`);
    const tbody = document.getElementById('logs-table');
    tbody.innerHTML = '';
    (d.logs || []).forEach(l => {
        tbody.innerHTML += `<tr>
            <td>${l.action}</td><td>${l.details || '—'}</td>
            <td>${l.created_at}</td>
        </tr>`;
    });
    renderPagination('logs-pagination', page, d.total_pages, loadLogs);
}

function exportReport() { window.open('/api/admin/export', '_blank'); }

function renderPagination(containerId, current, total, callback) {
    const el = document.getElementById(containerId);
    el.innerHTML = '';
    if (!total || total <= 1) return;
    for (let i = 1; i <= total; i++) {
        const btn = document.createElement('button');
        btn.textContent = i;
        if (i === current) btn.classList.add('active');
        btn.onclick = () => callback(i);
        el.appendChild(btn);
    }
}

function closeModal(id) {
    document.getElementById(id).classList.remove('show');
}

showTab('dashboard');
setInterval(() => {
    if (currentTab === 'dashboard') {
        loadDashboard();
        loadActiveOrders();
    }
}, 10000);
</script>
</body>
</html>
'''

# ============================================================
# ADMIN ROUTES
# ============================================================

@app.route('/')
@app.route('/admin')
@admin_required
def admin_dashboard():
    return render_template_string(DASHBOARD_HTML)

@app.route('/api/admin/dashboard')
@admin_required
def api_dashboard():
    conn = get_db()
    c = conn.cursor()
    today = datetime.now().strftime('%Y-%m-%d')
    online  = c.execute('SELECT COUNT(*) FROM drivers WHERE is_online=1').fetchone()[0]
    active  = c.execute('SELECT COUNT(*) FROM orders WHERE status IN ("pending","accepted")').fetchone()[0]
    revenue = c.execute(
        'SELECT COALESCE(SUM(price),0) FROM orders WHERE status="completed" AND DATE(completed_at)=?',
        (today,)
    ).fetchone()[0]
    total = c.execute('SELECT COUNT(*) FROM drivers').fetchone()[0]
    conn.close()
    return jsonify({
        'online_drivers': online,
        'active_orders':  active,
        'today_revenue':  revenue,
        'total_drivers':  total
    })

@app.route('/api/admin/orders', methods=['GET', 'POST'])
@admin_required
def api_admin_orders():
    if request.method == 'GET':
        page          = int(request.args.get('page', 1))
        per_page      = int(request.args.get('per_page', 20))
        status_filter = request.args.get('status', '')
        offset        = (page - 1) * per_page
        conn   = get_db()
        c      = conn.cursor()
        where  = ''
        params = []
        if status_filter:
            statuses     = status_filter.split(',')
            placeholders = ','.join('?' * len(statuses))
            where        = f'WHERE o.status IN ({placeholders})'
            params       = statuses
        total = c.execute(f'SELECT COUNT(*) FROM orders o {where}', params).fetchone()[0]
        rows  = c.execute(f'''
            SELECT o.*, d.name as driver_name, d.car_number
            FROM orders o
            LEFT JOIN drivers d ON o.driver_id = d.id
            {where} ORDER BY o.id DESC LIMIT ? OFFSET ?
        ''', params + [per_page, offset]).fetchall()
        conn.close()
        return jsonify({
            'orders':      [dict(r) for r in rows],
            'total_pages': max(1, (total + per_page - 1) // per_page)
        })

    data      = request.json or {}
    from_addr = data.get('from_address', '').strip()
    if not from_addr:
        return jsonify({'success': False, 'error': 'Укажите адрес'})
    conn = get_db()
    c    = conn.cursor()
    c.execute('''INSERT INTO orders
                 (from_address, to_address, price, driver_id, dispatcher_note)
                 VALUES (?, ?, ?, ?, ?)''',
              (from_addr, data.get('to_address'),
               data.get('price', 0), data.get('driver_id'),
               data.get('dispatcher_note')))
    order_id  = c.lastrowid
    driver_id = data.get('driver_id')
    c.execute('INSERT INTO sse_events (driver_id, event_type, data) VALUES (?,?,?)',
              (driver_id, 'new_order', json.dumps({'order_id': order_id})))
    conn.commit()
    log_action('Создан заказ', f'ID:{order_id}')
    conn.close()

    # ✅ Уведомление в Telegram
    send_telegram(
        f"📦 <b>Новый заказ #{order_id}</b>\n\n"
        f"📍 Откуда: {from_addr}\n"
        f"🏁 Куда: {data.get('to_address') or '—'}\n"
        f"💰 Цена: {data.get('price', 0):,.0f} сум"
    )

    return jsonify({'success': True, 'order_id': order_id})

@app.route('/api/admin/orders/<int:order_id>/complete', methods=['POST'])
@admin_required
def api_complete_order(order_id):
    conn  = get_db()
    c     = conn.cursor()
    order = c.execute('SELECT * FROM orders WHERE id=?', (order_id,)).fetchone()
    if not order:
        conn.close()
        return jsonify({'success': False, 'error': 'Не найден'})
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.execute('UPDATE orders SET status="completed", completed_at=? WHERE id=?', (now, order_id))
    if order['driver_id'] and order['price']:
        c.execute('UPDATE drivers SET balance=balance+?, is_busy=0 WHERE id=?',
                  (order['price'], order['driver_id']))
        c.execute('INSERT INTO transactions (driver_id, amount, type, description) VALUES (?,?,"order",?)',
                  (order['driver_id'], order['price'], f'Заказ #{order_id}'))
        c.execute('UPDATE shifts SET revenue=revenue+? WHERE driver_id=? AND ended_at IS NULL',
                  (order['price'], order['driver_id']))
    conn.commit()
    log_action('Заказ завершён', f'ID:{order_id}')
    conn.close()
    return jsonify({'success': True})

@app.route('/api/admin/orders/<int:order_id>/cancel', methods=['POST'])
@admin_required
def api_cancel_order(order_id):
    now   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn  = get_db()
    c     = conn.cursor()
    order = c.execute('SELECT * FROM orders WHERE id=?', (order_id,)).fetchone()
    if order and order['driver_id']:
        c.execute('UPDATE drivers SET is_busy=0 WHERE id=?', (order['driver_id'],))
    c.execute('UPDATE orders SET status="cancelled", cancelled_at=? WHERE id=?', (now, order_id))
    conn.commit()
    log_action('Заказ отменён', f'ID:{order_id}')
    conn.close()
    return jsonify({'success': True})

@app.route('/api/admin/drivers', methods=['GET'])
@admin_required
def api_admin_drivers():
    page     = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))
    offset   = (page - 1) * per_page
    conn     = get_db()
    c        = conn.cursor()
    total    = c.execute('SELECT COUNT(*) FROM drivers').fetchone()[0]
    rows     = c.execute(
        'SELECT * FROM drivers ORDER BY is_online DESC, name LIMIT ? OFFSET ?',
        (per_page, offset)
    ).fetchall()
    conn.close()
    return jsonify({
        'drivers':     [dict(r) for r in rows],
        'total_pages': max(1, (total + per_page - 1) // per_page)
    })

@app.route('/api/admin/drivers/<int:driver_id>/balance', methods=['POST'])
@admin_required
def api_add_balance(driver_id):
    amount = request.json.get('amount', 0)
    if amount <= 0:
        return jsonify({'success': False, 'error': 'Неверная сумма'})
    conn = get_db()
    conn.execute('UPDATE drivers SET balance=balance+? WHERE id=?', (amount, driver_id))
    conn.execute('INSERT INTO transactions (driver_id, amount, type, description) VALUES (?,?,"topup","Пополнение диспетчером")',
                 (driver_id, amount))
    conn.commit()
    log_action('Пополнение', f'Водитель:{driver_id} сумма:{amount}')
    conn.close()
    return jsonify({'success': True})

@app.route('/api/admin/drivers/<int:driver_id>/reset_pin', methods=['POST'])
@admin_required
def api_reset_pin(driver_id):
    new_pin = str(random.randint(1000, 9999))
    conn    = get_db()
    conn.execute('UPDATE drivers SET pin_hash=? WHERE id=?', (hash_pin(new_pin), driver_id))
    conn.commit()
    log_action('Сброс PIN', f'Водитель:{driver_id}')
    conn.close()

    # ✅ Уведомление в Telegram
    send_telegram(f"🔑 PIN сброшен для водителя ID:{driver_id}\nНовый PIN: <b>{new_pin}</b>")

    return jsonify({'success': True, 'new_pin': new_pin})

@app.route('/api/admin/drivers/<int:driver_id>', methods=['DELETE'])
@admin_required
def api_delete_driver(driver_id):
    conn = get_db()
    conn.execute('DELETE FROM drivers WHERE id=?', (driver_id,))
    conn.commit()
    log_action('Удалён водитель', f'ID:{driver_id}')
    conn.close()
    return jsonify({'success': True})

@app.route('/api/admin/pending', methods=['GET'])
@admin_required
def api_admin_pending():
    conn = get_db()
    rows = conn.execute(
        'SELECT * FROM pending_pins WHERE status="pending" ORDER BY id DESC'
    ).fetchall()
    conn.close()
    return jsonify({'pending': [dict(r) for r in rows]})

@app.route('/api/admin/pending/<int:pid>/approve', methods=['POST'])
@admin_required
def api_approve_pending(pid):
    conn = get_db()
    c    = conn.cursor()
    p    = c.execute('SELECT * FROM pending_pins WHERE id=?', (pid,)).fetchone()
    if not p:
        conn.close()
        return jsonify({'success': False, 'error': 'Не найдено'})
    pin = str(random.randint(1000, 9999))
    c.execute('INSERT OR IGNORE INTO drivers (car_number, name, phone, pin_hash) VALUES (?,?,?,?)',
              (p['car_number'], p['name'], p['phone'], hash_pin(pin)))
    c.execute('UPDATE pending_pins SET status="approved", pin_hash=? WHERE id=?',
              (hash_pin(pin), pid))
    conn.commit()
    log_action('Заявка одобрена', f'{p["car_number"]} PIN:{pin}')
    conn.close()

    # ✅ Уведомление в Telegram
    send_telegram(
        f"✅ Водитель одобрен!\n\n"
        f"🚗 Авто: {p['car_number']}\n"
        f"👤 Имя: {p['name']}\n"
        f"🔑 PIN: <b>{pin}</b>"
    )

    return jsonify({'success': True, 'pin': pin})

@app.route('/api/admin/pending/<int:pid>/reject', methods=['POST'])
@admin_required
def api_reject_pending(pid):
    conn = get_db()
    conn.execute('UPDATE pending_pins SET status="rejected" WHERE id=?', (pid,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/admin/balance_requests', methods=['GET'])
@admin_required
def api_admin_balance_requests():
    conn = get_db()
    rows = conn.execute('''
        SELECT br.*, d.name as driver_name, d.car_number
        FROM balance_requests br
        JOIN drivers d ON br.driver_id = d.id
        WHERE br.status="pending" ORDER BY br.id DESC
    ''').fetchall()
    conn.close()
    return jsonify({'requests': [dict(r) for r in rows]})

@app.route('/api/admin/balance_requests/<int:rid>/approve', methods=['POST'])
@admin_required
def api_approve_balance_req(rid):
    conn = get_db()
    c    = conn.cursor()
    req  = c.execute('SELECT * FROM balance_requests WHERE id=?', (rid,)).fetchone()
    if not req:
        conn.close()
        return jsonify({'success': False, 'error': 'Не найдено'})
    c.execute('UPDATE drivers SET balance=balance+? WHERE id=?',
              (req['amount'], req['driver_id']))
    c.execute('UPDATE balance_requests SET status="approved" WHERE id=?', (rid,))
    c.execute('INSERT INTO transactions (driver_id, amount, type, description) VALUES (?,?,"topup","Пополнение по запросу")',
              (req['driver_id'], req['amount']))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/admin/balance_requests/<int:rid>/reject', methods=['POST'])
@admin_required
def api_reject_balance_req(rid):
    conn = get_db()
    conn.execute('UPDATE balance_requests SET status="rejected" WHERE id=?', (rid,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/admin/finance', methods=['GET'])
@admin_required
def api_admin_finance():
    page     = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))
    offset   = (page - 1) * per_page
    now      = datetime.now()
    conn     = get_db()
    c        = conn.cursor()
    today_rev = c.execute(
        'SELECT COALESCE(SUM(price),0) FROM orders WHERE status="completed" AND DATE(completed_at)=?',
        (now.strftime('%Y-%m-%d'),)
    ).fetchone()[0]
    week_rev = c.execute(
        'SELECT COALESCE(SUM(price),0) FROM orders WHERE status="completed" AND completed_at>=?',
        ((now - timedelta(days=7)).strftime('%Y-%m-%d'),)
    ).fetchone()[0]
    month_rev = c.execute(
        'SELECT COALESCE(SUM(price),0) FROM orders WHERE status="completed" AND completed_at>=?',
        ((now - timedelta(days=30)).strftime('%Y-%m-%d'),)
    ).fetchone()[0]
    total = c.execute('SELECT COUNT(*) FROM transactions').fetchone()[0]
    rows  = c.execute('''
        SELECT t.*, d.name as driver_name FROM transactions t
        LEFT JOIN drivers d ON t.driver_id = d.id
        ORDER BY t.id DESC LIMIT ? OFFSET ?
    ''', (per_page, offset)).fetchall()
    conn.close()
    return jsonify({
        'today':        today_rev,
        'week':         week_rev,
        'month':        month_rev,
        'transactions': [dict(r) for r in rows],
        'total_pages':  max(1, (total + per_page - 1) // per_page)
    })

@app.route('/api/admin/chat/<int:driver_id>', methods=['GET', 'POST'])
@admin_required
def api_admin_chat(driver_id):
    conn = get_db()
    if request.method == 'GET':
        rows = conn.execute(
            'SELECT * FROM chat_messages WHERE driver_id=? ORDER BY id DESC LIMIT 50',
            (driver_id,)
        ).fetchall()
        conn.execute(
            'UPDATE chat_messages SET is_read=1 WHERE driver_id=? AND sender="driver"',
            (driver_id,)
        )
        conn.commit()
        conn.close()
        return jsonify({'messages': [dict(r) for r in reversed(rows)]})
    msg = request.json.get('message', '').strip()
    if not msg:
        conn.close()
        return jsonify({'success': False, 'error': 'Пустое сообщение'})
    conn.execute('INSERT INTO chat_messages (driver_id, sender, message) VALUES (?, "admin", ?)',
                 (driver_id, msg))
    conn.execute('INSERT INTO sse_events (driver_id, event_type, data) VALUES (?,?,?)',
                 (driver_id, 'chat', json.dumps({'message': msg, 'sender': 'admin'})))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/admin/tariffs', methods=['GET', 'POST'])
@admin_required
def api_admin_tariffs():
    conn = get_db()
    if request.method == 'GET':
        row = conn.execute('SELECT * FROM tariffs ORDER BY id DESC LIMIT 1').fetchone()
        conn.close()
        return jsonify({'tariff': dict(row) if row else {}})
    data = request.json or {}
    conn.execute('DELETE FROM tariffs')
    conn.execute('INSERT INTO tariffs (base_fare, per_km, per_minute) VALUES (?,?,?)',
                 (data.get('base_fare', 5000), data.get('per_km', 1000),
                  data.get('per_minute', 200)))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/admin/backups', methods=['GET', 'POST'])
@admin_required
def api_admin_backups():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    if request.method == 'POST':
        name = f'backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db'
        shutil.copy2(DB_PATH, os.path.join(BACKUP_DIR, name))
        log_action('Бэкап', name)
        return jsonify({'success': True, 'name': name})
    backups = []
    for f in sorted(os.listdir(BACKUP_DIR), reverse=True):
        if f.endswith('.db'):
            path  = os.path.join(BACKUP_DIR, f)
            size  = os.path.getsize(path)
            mtime = datetime.fromtimestamp(os.path.getmtime(path))
            backups.append({
                'name': f,
                'size': f'{size // 1024} KB',
                'date': mtime.strftime('%Y-%m-%d %H:%M')
            })
    return jsonify({'backups': backups})

@app.route('/api/admin/logs', methods=['GET'])
@admin_required
def api_admin_logs():
    page     = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))
    offset   = (page - 1) * per_page
    conn     = get_db()
    c        = conn.cursor()
    total    = c.execute('SELECT COUNT(*) FROM logs').fetchone()[0]
    rows     = c.execute(
        'SELECT * FROM logs ORDER BY id DESC LIMIT ? OFFSET ?',
        (per_page, offset)
    ).fetchall()
    conn.close()
    return jsonify({
        'logs':        [dict(r) for r in rows],
        'total_pages': max(1, (total + per_page - 1) // per_page)
    })

@app.route('/api/admin/export')
@admin_required
def api_export():
    conn = get_db()
    c    = conn.cursor()
    now  = datetime.now()
    lines = [f'ОТЧЁТ TAXI 3042 — {now.strftime("%Y-%m-%d %H:%M")}', '='*50]
    today_rev = c.execute(
        'SELECT COALESCE(SUM(price),0) FROM orders WHERE status="completed" AND DATE(completed_at)=?',
        (now.strftime('%Y-%m-%d'),)
    ).fetchone()[0]
    lines.append(f'Выручка сегодня: {today_rev:,.0f} сум')
    total_orders = c.execute(
        'SELECT COUNT(*) FROM orders WHERE status="completed"'
    ).fetchone()[0]
    lines.append(f'Завершённых заказов: {total_orders}')
    lines.append('='*50)
    lines.append('ВОДИТЕЛИ:')
    drivers = c.execute('SELECT * FROM drivers ORDER BY balance DESC').fetchall()
    for dr in drivers:
        lines.append(f'  {dr["car_number"]} — {dr["name"]} | Баланс: {dr["balance"]:,.0f} сум')
    conn.close()
    return Response('\n'.join(lines), mimetype='text/plain',
                    headers={'Content-Disposition': 'attachment; filename=report.txt'})

# ============================================================
# TELEGRAM WEBHOOK
# ============================================================

@app.route(f'/telegram/{TELEGRAM_TOKEN}', methods=['POST'])
def telegram_webhook():
    data     = request.json or {}
    message  = data.get('message', {})
    chat_id  = message.get('chat', {}).get('id')
    text     = message.get('text', '').strip()

    if not chat_id or not text:
        return jsonify({'ok': True})

    if text == '/start':
        answer_telegram(chat_id,
            "🚕 <b>TAXI 3042 — Панель диспетчера</b>\n\n"
            "Доступные команды:\n"
            "/stats — статистика\n"
            "/orders — активные заказы\n"
            "/drivers — онлайн водители\n"
            "/pending — заявки водителей\n"
            "/neworder — создать заказ"
        )

    elif text == '/stats':
        conn  = get_db()
        c     = conn.cursor()
        today = datetime.now().strftime('%Y-%m-%d')
        online  = c.execute('SELECT COUNT(*) FROM drivers WHERE is_online=1').fetchone()[0]
        active  = c.execute('SELECT COUNT(*) FROM orders WHERE status IN ("pending","accepted")').fetchone()[0]
        revenue = c.execute(
            'SELECT COALESCE(SUM(price),0) FROM orders WHERE status="completed" AND DATE(completed_at)=?',
            (today,)
        ).fetchone()[0]
        total_orders = c.execute('SELECT COUNT(*) FROM orders WHERE status="completed"').fetchone()[0]
        conn.close()
        answer_telegram(chat_id,
            f"📊 <b>Статистика TAXI 3042</b>\n\n"
            f"🟢 Онлайн водителей: {online}\n"
            f"📋 Активных заказов: {active}\n"
            f"💰 Выручка сегодня: {revenue:,.0f} сум\n"
            f"✅ Всего завершено: {total_orders} заказов"
        )

    elif text == '/orders':
        conn = get_db()
        rows = conn.execute('''
            SELECT o.*, d.name as driver_name FROM orders o
            LEFT JOIN drivers d ON o.driver_id = d.id
            WHERE o.status IN ("pending","accepted")
            ORDER BY o.id DESC LIMIT 10
        ''').fetchall()
        conn.close()
        if not rows:
            answer_telegram(chat_id, "📋 Активных заказов нет")
        else:
            msg = "📋 <b>Активные заказы:</b>\n\n"
            for o in rows:
                msg += (
                    f"#{o['id']} | {o['status']}\n"
                    f"📍 {o['from_address']}\n"
                    f"🏁 {o['to_address'] or '—'}\n"
                    f"💰 {o['price']:,.0f} сум\n"
                    f"🚗 {o['driver_name'] or 'Не назначен'}\n\n"
                )
            answer_telegram(chat_id, msg)

    elif text == '/drivers':
        conn = get_db()
        rows = conn.execute(
            'SELECT * FROM drivers WHERE is_online=1 ORDER BY name'
        ).fetchall()
        conn.close()
        if not rows:
            answer_telegram(chat_id, "🚗 Нет онлайн водителей")
        else:
            msg = "🚗 <b>Онлайн водители:</b>\n\n"
            for d in rows:
                status = "🔴 Занят" if d['is_busy'] else "🟢 Свободен"
                msg += f"{d['car_number']} — {d['name']}\n{status} | 💰 {d['balance']:,.0f} сум\n\n"
            answer_telegram(chat_id, msg)

    elif text == '/pending':
        conn = get_db()
        rows = conn.execute(
            'SELECT * FROM pending_pins WHERE status="pending" ORDER BY id DESC'
        ).fetchall()
        conn.close()
        if not rows:
            answer_telegram(chat_id, "⏳ Новых заявок нет")
        else:
            msg = "⏳ <b>Заявки на регистрацию:</b>\n\n"
            for p in rows:
                msg += (
                    f"#{p['id']} | {p['car_number']}\n"
                    f"👤 {p['name']} | 📞 {p['phone'] or '—'}\n"
                    f"Одобрить: /approve_{p['id']}\n"
                    f"Отклонить: /reject_{p['id']}\n\n"
                )
            answer_telegram(chat_id, msg)

    elif text.startswith('/approve_'):
        try:
            pid  = int(text.split('_')[1])
            conn = get_db()
            c    = conn.cursor()
            p    = c.execute('SELECT * FROM pending_pins WHERE id=?', (pid,)).fetchone()
            if not p:
                answer_telegram(chat_id, "❌ Заявка не найдена")
            else:
                pin = str(random.randint(1000, 9999))
                c.execute('INSERT OR IGNORE INTO drivers (car_number, name, phone, pin_hash) VALUES (?,?,?,?)',
                          (p['car_number'], p['name'], p['phone'], hash_pin(pin)))
                c.execute('UPDATE pending_pins SET status="approved" WHERE id=?', (pid,))
                conn.commit()
                conn.close()
                answer_telegram(chat_id,
                    f"✅ Водитель одобрен!\n\n"
                    f"🚗 Авто: {p['car_number']}\n"
                    f"👤 Имя: {p['name']}\n"
                    f"🔑 PIN: <b>{pin}</b>\n\n"
                    f"Сообщите PIN водителю!"
                )
        except Exception as e:
            answer_telegram(chat_id, f"❌ Ошибка: {e}")

    elif text.startswith('/reject_'):
        try:
            pid  = int(text.split('_')[1])
            conn = get_db()
            conn.execute('UPDATE pending_pins SET status="rejected" WHERE id=?', (pid,))
            conn.commit()
            conn.close()
            answer_telegram(chat_id, "❌ Заявка отклонена")
        except Exception as e:
            answer_telegram(chat_id, f"❌ Ошибка: {e}")

    elif text == '/neworder':
        answer_telegram(chat_id,
            "➕ <b>Создать заказ</b>\n\n"
            "Отправьте в формате:\n"
            "/order Откуда | Куда | Цена\n\n"
            "Пример:\n"
            "/order Центр | Аэропорт | 50000"
        )

    elif text.startswith('/order '):
        try:
            parts     = text.replace('/order ', '').split('|')
            from_addr = parts[0].strip()
            to_addr   = parts[1].strip() if len(parts) > 1 else ''
            price     = float(parts[2].strip()) if len(parts) > 2 else 0
            conn = get_db()
            c    = conn.cursor()
            c.execute('INSERT INTO orders (from_address, to_address, price, dispatcher_note) VALUES (?,?,?,?)',
                      (from_addr, to_addr, price, 'Создан через Telegram'))
            order_id = c.lastrowid
            c.execute('INSERT INTO sse_events (driver_id, event_type, data) VALUES (NULL,"new_order",?)',
                      (json.dumps({'order_id': order_id}),))
            conn.commit()
            conn.close()
            answer_telegram(chat_id,
                f"✅ Заказ #{order_id} создан!\n\n"
                f"📍 Откуда: {from_addr}\n"
                f"🏁 Куда: {to_addr or '—'}\n"
                f"💰 Цена: {price:,.0f} сум"
            )
        except Exception as e:
            answer_telegram(chat_id, f"❌ Ошибка: {e}")

    else:
        answer_telegram(chat_id,
            "❓ Неизвестная команда\nНапишите /start"
        )

    return jsonify({'ok': True})

# ============================================================
# DRIVER API
# ============================================================

@app.route('/api/driver/register', methods=['POST'])
def driver_register():
    data  = request.json or {}
    car   = data.get('car_number', '').strip().upper()
    name  = data.get('name', '').strip()
    phone = data.get('phone', '').strip()

    if not car or not name:
        return jsonify({'success': False, 'error': 'Заполните все поля'})

    conn = get_db()
    c    = conn.cursor()

    existing = c.execute('SELECT id FROM drivers WHERE car_number=?', (car,)).fetchone()
    if existing:
        conn.close()
        return jsonify({'success': False, 'error': 'Вы уже зарегистрированы! Введите ваш PIN.'})

    if c.execute('SELECT id FROM pending_pins WHERE car_number=? AND status="pending"', (car,)).fetchone():
        conn.close()
        return jsonify({'success': False, 'error': 'Заявка уже отправлена. Ожидайте PIN.'})

    c.execute('INSERT INTO pending_pins (car_number, name, phone) VALUES (?,?,?)', (car, name, phone))
    conn.commit()
    conn.close()

    # ✅ Уведомление в Telegram
    send_telegram(
        f"🆕 <b>Новая заявка!</b>\n\n"
        f"🚗 Авто: {car}\n"
        f"👤 Имя: {name}\n"
        f"📞 Телефон: {phone}\n\n"
        f"Одобрить через бот: /pending"
    )

    return jsonify({'success': True, 'message': 'Заявка отправлена. Ожидайте одобрения.'})

@app.route('/api/driver/login', methods=['POST'])
def driver_login():
    data = request.json or {}
    car  = data.get('car_number', '').strip().upper()
    pin  = str(data.get('pin', '')).strip()

    if not car or not pin:
        return jsonify({'success': False, 'error': 'Введите данные'})

    conn   = get_db()
    driver = conn.execute(
        'SELECT * FROM drivers WHERE car_number=? AND pin_hash=?',
        (car, hash_pin(pin))
    ).fetchone()
    conn.close()

    if not driver:
        return jsonify({'success': False, 'error': 'Неверный номер или PIN'})

    return jsonify({
        'success': True,
        'driver': {
            'id':         driver['id'],
            'name':       driver['name'],
            'car_number': driver['car_number'],
            'phone':      driver['phone'],
            'balance':    driver['balance'],
            'rating':     driver['rating'],
            'is_online':  driver['is_online'],
        }
    })

@app.route('/api/driver/heartbeat', methods=['POST'])
def driver_heartbeat():
    data      = request.json or {}
    driver_id = data.get('driver_id')
    if not driver_id:
        return jsonify({'success': False, 'error': 'Нет driver_id'})
    now  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn = get_db()
    conn.execute('UPDATE drivers SET last_seen=? WHERE id=?', (now, driver_id))
    conn.commit()
    events = conn.execute(
        'SELECT * FROM sse_events WHERE driver_id=? OR driver_id IS NULL ORDER BY id DESC LIMIT 5',
        (driver_id,)
    ).fetchall()
    conn.close()
    return jsonify({'success': True, 'events': [dict(e) for e in events]})

@app.route('/api/driver/status', methods=['POST'])
def driver_status():
    data      = request.json or {}
    driver_id = data.get('driver_id')
    is_online = data.get('is_online', False)
    if not driver_id:
        return jsonify({'success': False, 'error': 'Нет driver_id'})
    now  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn = get_db()
    c    = conn.cursor()
    c.execute('UPDATE drivers SET is_online=?, last_seen=? WHERE id=?',
              (1 if is_online else 0, now, driver_id))
    if is_online:
        active = c.execute('SELECT id FROM shifts WHERE driver_id=? AND ended_at IS NULL', (driver_id,)).fetchone()
        if not active:
            c.execute('INSERT INTO shifts (driver_id) VALUES (?)', (driver_id,))
    else:
        c.execute('UPDATE shifts SET ended_at=? WHERE driver_id=? AND ended_at IS NULL', (now, driver_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/driver/order', methods=['GET'])
def driver_get_order():
    driver_id = request.args.get('driver_id')
    if not driver_id:
        return jsonify({'success': False, 'error': 'Нет driver_id'})
    conn  = get_db()
    order = conn.execute('''
        SELECT * FROM orders
        WHERE (driver_id=? OR driver_id IS NULL)
        AND status IN ("pending","accepted")
        ORDER BY id DESC LIMIT 1
    ''', (driver_id,)).fetchone()
    conn.close()
    if not order:
        return jsonify({'success': True, 'order': None})
    return jsonify({'success': True, 'order': dict(order)})

@app.route('/api/driver/order/accept', methods=['POST'])
def driver_accept_order():
    data      = request.json or {}
    driver_id = data.get('driver_id')
    order_id  = data.get('order_id')
    conn  = get_db()
    c     = conn.cursor()
    now   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    order = c.execute('SELECT * FROM orders WHERE id=?', (order_id,)).fetchone()
    if not order or order['status'] != 'pending':
        conn.close()
        return jsonify({'success': False, 'error': 'Заказ недоступен'})
    c.execute('UPDATE orders SET status="accepted", driver_id=?, accepted_at=? WHERE id=?',
              (driver_id, now, order_id))
    c.execute('UPDATE drivers SET is_busy=1 WHERE id=?', (driver_id,))
    conn.commit()
    log_action('Заказ принят', f'Водитель:{driver_id}')
    conn.close()
    return jsonify({'success': True})

@app.route('/api/driver/order/complete', methods=['POST'])
def driver_complete_order():
    data      = request.json or {}
    driver_id = data.get('driver_id')
    order_id  = data.get('order_id')
    conn  = get_db()
    c     = conn.cursor()
    now   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    order = c.execute('SELECT * FROM orders WHERE id=? AND driver_id=?',
                      (order_id, driver_id)).fetchone()
    if not order:
        conn.close()
        return jsonify({'success': False, 'error': 'Заказ не найден'})
    c.execute('UPDATE orders SET status="completed", completed_at=? WHERE id=?', (now, order_id))
    c.execute('UPDATE drivers SET is_busy=0, balance=balance+? WHERE id=?',
              (order['price'], driver_id))
    c.execute('INSERT INTO transactions (driver_id, amount, type, description) VALUES (?,?,"order",?)',
              (driver_id, order['price'], f'Заказ #{order_id}'))
    c.execute('UPDATE shifts SET revenue=revenue+? WHERE driver_id=? AND ended_at IS NULL',
              (order['price'], driver_id))
    conn.commit()
    log_action('Заказ завершён водителем', f'Водитель:{driver_id}')
    conn.close()
    return jsonify({'success': True})

@app.route('/api/driver/order/cancel', methods=['POST'])
def driver_cancel_order():
    data      = request.json or {}
    driver_id = data.get('driver_id')
    order_id  = data.get('order_id')
    now  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn = get_db()
    conn.execute('UPDATE orders SET status="cancelled", cancelled_at=? WHERE id=? AND driver_id=?',
                 (now, order_id, driver_id))
    conn.execute('UPDATE drivers SET is_busy=0 WHERE id=?', (driver_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/driver/balance', methods=['GET'])
def driver_balance():
    driver_id = request.args.get('driver_id')
    if not driver_id:
        return jsonify({'success': False, 'error': 'Нет driver_id'})
    conn   = get_db()
    driver = conn.execute('SELECT balance FROM drivers WHERE id=?', (driver_id,)).fetchone()
    conn.close()
    if not driver:
        return jsonify({'success': False, 'error': 'Не найден'})
    return jsonify({'success': True, 'balance': driver['balance']})

@app.route('/api/driver/balance/request', methods=['POST'])
def driver_balance_request():
    data      = request.json or {}
    driver_id = data.get('driver_id')
    amount    = data.get('amount', 0)
    if not driver_id or amount <= 0:
        return jsonify({'success': False, 'error': 'Неверные данные'})
    conn = get_db()
    conn.execute('INSERT INTO balance_requests (driver_id, amount) VALUES (?,?)',
                 (driver_id, amount))
    conn.commit()
    conn.close()

    # ✅ Уведомление в Telegram
    send_telegram(
        f"💰 <b>Запрос пополнения!</b>\n\n"
        f"Водитель ID: {driver_id}\n"
        f"Сумма: {amount:,.0f} сум\n\n"
        f"Одобрить в админке"
    )

    return jsonify({'success': True, 'message': 'Запрос отправлен'})

@app.route('/api/driver/chat', methods=['GET', 'POST'])
def driver_chat():
    if request.method == 'GET':
        driver_id = request.args.get('driver_id')
        if not driver_id:
            return jsonify({'success': False, 'error': 'Нет driver_id'})
        conn = get_db()
        rows = conn.execute(
            'SELECT * FROM chat_messages WHERE driver_id=? ORDER BY id DESC LIMIT 50',
            (driver_id,)
        ).fetchall()
        conn.execute('UPDATE chat_messages SET is_read=1 WHERE driver_id=? AND sender="admin"',
                     (driver_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'messages': [dict(r) for r in reversed(rows)]})

    data      = request.json or {}
    driver_id = data.get('driver_id')
    msg       = data.get('message', '').strip()
    if not driver_id or not msg:
        return jsonify({'success': False, 'error': 'Неверные данные'})
    conn = get_db()
    conn.execute('INSERT INTO chat_messages (driver_id, sender, message) VALUES (?, "driver", ?)',
                 (driver_id, msg))
    conn.commit()
    conn.close()

    # ✅ Уведомление в Telegram
    send_telegram(f"💬 <b>Сообщение от водителя</b>\n\nID: {driver_id}\n{msg}")

    return jsonify({'success': True})

# ============================================================
# ЗАПУСК
# ============================================================

init_db()
migrate_db()

t = threading.Thread(target=background_tasks, daemon=True)
t.start()

if __name__ == '__main__':
    os.makedirs(BACKUP_DIR, exist_ok=True)
    app.run(host='0.0.0.0', port=5000, debug=False)
