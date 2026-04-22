import logging
import sqlite3
import secrets
from datetime import datetime, timedelta
from functools import wraps
from zoneinfo import ZoneInfo

from flask import (Flask, render_template, request,
                   redirect, url_for, session, jsonify)
import os

# ==================== НАСТРОЙКИ ====================
ADMIN_USERNAME  = os.environ.get("ADMIN_USERNAME", "admin").strip()
ADMIN_PASSWORD  = os.environ.get("ADMIN_PASSWORD", "admin123").strip()
SECRET_KEY      = os.environ.get("SECRET_KEY", "taxi2024secret").strip()
PIN_EXPIRE_DAYS = 30
PORT            = int(os.environ.get("PORT", 5000))

# ==================== ТАРИФЫ (ГЛОБАЛЬНЫЕ) ====================
class TaxiConfig:
    BASE_FARE   = 5000.0
    CITY_RATE   = 2800.0
    SUBURB_RATE = 3000.0
    WAIT_RATE   = 500.0

# ==================== БАЗА ДАННЫХ ====================
def get_db():
    conn = sqlite3.connect("taxi.db")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    print("🔧 Инициализация базы данных...")
    conn = get_db()
    c = conn.cursor()

    # ✅ ВОДИТЕЛИ
    c.execute("""
        CREATE TABLE IF NOT EXISTS drivers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER DEFAULT 0,
            username TEXT,
            full_name TEXT,
            phone TEXT,
            car_number TEXT UNIQUE,
            pin TEXT,
            pin_created_at TEXT,
            pin_expires_at TEXT,
            status TEXT DEFAULT 'pending',
            is_blocked INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            balance REAL DEFAULT 0.0,
            online_status TEXT DEFAULT 'offline',
            last_seen TEXT DEFAULT NULL,
            tariff_id INTEGER DEFAULT 1
        )
    """)

    # ✅ ДОБАВЛЯЕМ ПОЛЯ ЕСЛИ НЕ СУЩЕСТВУЕТ
    for col, definition in [
        ("balance",       "REAL DEFAULT 0.0"),
        ("online_status", "TEXT DEFAULT 'offline'"),
        ("last_seen",     "TEXT DEFAULT NULL"),
        ("tariff_id",     "INTEGER DEFAULT 1"),
    ]:
        try:
            c.execute(f"ALTER TABLE drivers ADD COLUMN {col} {definition}")
        except:
            pass

    # ✅ ТАРИФЫ
    c.execute("""
        CREATE TABLE IF NOT EXISTS tariffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            city_rate REAL DEFAULT 2800,
            suburb_rate REAL DEFAULT 3000,
            base_fare REAL DEFAULT 5000,
            wait_rate REAL DEFAULT 500
        )
    """)

    # ✅ СТАНДАРТНЫЙ ТАРИФ
    c.execute("SELECT COUNT(*) FROM tariffs")
    if c.fetchone()[0] == 0:
        c.execute("""
            INSERT INTO tariffs (name, city_rate, suburb_rate, base_fare, wait_rate)
            VALUES ('Стандарт', 2800, 3000, 5000, 500)
        """)

    # ✅ ЛОГИ
    c.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT,
            admin_id INTEGER,
            driver_id INTEGER,
            details TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ✅ BROADCASTS
    c.execute("""
        CREATE TABLE IF NOT EXISTS broadcasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message TEXT,
            sent_count INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ✅ ПОЕЗДКИ
    c.execute("""
        CREATE TABLE IF NOT EXISTS trips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            car_number TEXT,
            price INTEGER,
            city_distance REAL,
            suburb_distance REAL,
            waiting_seconds INTEGER,
            total_seconds INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ✅ ТРАНЗАКЦИИ
    c.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            car_number TEXT NOT NULL,
            amount REAL NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('credit','debit')),
            description TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()
    print("✅ База данных готова")

# ==================== ГЕНЕРАЦИЯ PIN ====================
def generate_pin():
    conn = get_db()
    c = conn.cursor()
    while True:
        pin = str(secrets.randbelow(9000) + 1000)
        c.execute("SELECT COUNT(*) FROM drivers WHERE pin = ?", (pin,))
        if c.fetchone()[0] == 0:
            conn.close()
            return pin

# ==================== ФУНКЦИИ БД ====================
def add_log(action, admin_id, driver_id, details):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO logs (action, admin_id, driver_id, details)
        VALUES (?, ?, ?, ?)
    """, (action, admin_id, driver_id, details))
    conn.commit()
    conn.close()

def add_driver(tg_id, username, full_name, phone, car_number):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO drivers
        (tg_id, username, full_name, phone, car_number, status)
        VALUES (?, ?, ?, ?, ?, 'pending')
        ON CONFLICT(car_number) DO UPDATE SET
            tg_id = excluded.tg_id,
            username = excluded.username,
            full_name = excluded.full_name,
            phone = excluded.phone,
            status = 'pending',
            pin = NULL,
            pin_created_at = NULL,
            pin_expires_at = NULL
    """, (tg_id, username, full_name, phone, car_number))
    conn.commit()
    conn.close()

def get_all_drivers():
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT d.*, t.name as tariff_name
        FROM drivers d
        LEFT JOIN tariffs t ON t.id = d.tariff_id
        ORDER BY d.created_at DESC
    """)
    drivers = c.fetchall()
    conn.close()
    return drivers

def get_driver_by_car(car_number):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM drivers WHERE car_number = ?", (car_number.upper(),))
    driver = c.fetchone()
    conn.close()
    return driver

def search_drivers(query):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT d.*, t.name as tariff_name
        FROM drivers d
        LEFT JOIN tariffs t ON t.id = d.tariff_id
        WHERE d.full_name LIKE ? OR d.car_number LIKE ?
        OR d.phone LIKE ? OR d.username LIKE ?
    """, (f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%"))
    drivers = c.fetchall()
    conn.close()
    return drivers

def get_pending_drivers():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM drivers WHERE status = 'pending' AND is_blocked = 0")
    drivers = c.fetchall()
    conn.close()
    return drivers

def approve_driver_by_car(car_number):
    pin = generate_pin()
    now = datetime.now()
    expires = now + timedelta(days=PIN_EXPIRE_DAYS)
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE drivers
        SET status = 'approved',
            pin = ?,
            pin_created_at = ?,
            pin_expires_at = ?
        WHERE car_number = ?
    """, (pin, now.strftime("%Y-%m-%d %H:%M:%S"),
          expires.strftime("%Y-%m-%d %H:%M:%S"),
          car_number.upper()))
    conn.commit()
    conn.close()
    return pin

def reject_driver_by_car(car_number):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE drivers SET status = 'rejected' WHERE car_number = ?",
              (car_number.upper(),))
    conn.commit()
    conn.close()

def block_driver_by_car(car_number):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE drivers
        SET is_blocked = 1, online_status = 'offline'
        WHERE car_number = ?
    """, (car_number.upper(),))
    conn.commit()
    conn.close()

def unblock_driver_by_car(car_number):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE drivers SET is_blocked = 0 WHERE car_number = ?",
              (car_number.upper(),))
    conn.commit()
    conn.close()

def reset_pin_by_car(car_number):
    pin = generate_pin()
    now = datetime.now()
    expires = now + timedelta(days=PIN_EXPIRE_DAYS)
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE drivers
        SET pin = ?, pin_created_at = ?, pin_expires_at = ?
        WHERE car_number = ?
    """, (pin, now.strftime("%Y-%m-%d %H:%M:%S"),
          expires.strftime("%Y-%m-%d %H:%M:%S"),
          car_number.upper()))
    conn.commit()
    conn.close()
    return pin

def update_online_status(car_number, status):
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("""
        UPDATE drivers
        SET online_status = ?, last_seen = ?
        WHERE car_number = ?
    """, (status, now, car_number.upper()))
    conn.commit()
    conn.close()

def get_balance(car_number):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT balance FROM drivers WHERE car_number = ?",
              (car_number.upper(),))
    row = c.fetchone()
    conn.close()
    return row['balance'] if row else 0.0

def get_all_trips():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM trips ORDER BY created_at DESC LIMIT 200")
    trips = c.fetchall()
    conn.close()
    return trips

def get_driver_trips(car_number):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM trips
        WHERE car_number = ?
        ORDER BY created_at DESC
        LIMIT 100
    """, (car_number.upper(),))
    trips = c.fetchall()
    conn.close()
    return trips

def search_drivers(query):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM drivers
        WHERE full_name LIKE ? OR car_number LIKE ?
        OR phone LIKE ? OR username LIKE ?
    """, (f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%"))
    drivers = c.fetchall()
    conn.close()
    return drivers

def get_stats():
    conn = get_db()
    c = conn.cursor()
    stats = {}
    c.execute("SELECT COUNT(*) FROM drivers")
    stats['total'] = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM drivers WHERE status='approved'")
    stats['approved'] = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM drivers WHERE status='pending'")
    stats['pending'] = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM drivers WHERE status='rejected'")
    stats['rejected'] = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM drivers WHERE is_blocked=1")
    stats['blocked'] = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM drivers WHERE online_status IN ('online','free','busy')")
    stats['online'] = c.fetchone()[0]
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("SELECT COUNT(*) FROM drivers WHERE created_at LIKE ?", (f"{today}%",))
    stats['today'] = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM trips WHERE created_at LIKE ?", (f"{today}%",))
    stats['trips_today'] = c.fetchone()[0]
    c.execute("SELECT COALESCE(SUM(price), 0) FROM trips WHERE created_at LIKE ?",
              (f"{today}%",))
    stats['earnings_today'] = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM trips")
    stats['trips_total'] = c.fetchone()[0]
    c.execute("SELECT COALESCE(SUM(price), 0) FROM trips")
    stats['earnings_total'] = c.fetchone()[0]
    c.execute("SELECT COALESCE(SUM(balance), 0) FROM drivers WHERE status='approved'")
    stats['total_balance'] = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM drivers WHERE balance < 10000 AND status='approved'")
    stats['low_balance_count'] = c.fetchone()[0]
    c.execute("""
        SELECT COALESCE(SUM(amount), 0) FROM transactions
        WHERE type='debit' AND created_at LIKE ?
    """, (f"{today}%",))
    stats['deducted_today'] = c.fetchone()[0]
    c.execute("""
        SELECT COALESCE(SUM(amount), 0) FROM transactions
        WHERE type='credit' AND created_at LIKE ?
    """, (f"{today}%",))
    stats['topup_today'] = c.fetchone()[0]
    conn.close()
    return stats

def get_logs():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM logs ORDER BY created_at DESC LIMIT 50")
    logs = c.fetchall()
    conn.close()
    return logs

# ==================== БАЛАНС ====================
def topup_driver_balance(car_number, amount, description="Пополнение баланса"):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE drivers SET balance = balance + ?
        WHERE car_number = ?
    """, (amount, car_number.upper()))
    c.execute("""
        INSERT INTO transactions (car_number, amount, type, description)
        VALUES (?, ?, 'credit', ?)
    """, (car_number.upper(), amount, description))
    c.execute("SELECT balance FROM drivers WHERE car_number = ?",
              (car_number.upper(),))
    row = c.fetchone()
    new_balance = row['balance'] if row else 0.0
    conn.commit()
    conn.close()
    return new_balance

def deduct_driver_balance(car_number, amount, description="Списание"):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT balance FROM drivers WHERE car_number = ?",
              (car_number.upper(),))
    row = c.fetchone()
    if not row:
        conn.close()
        return False, "Водитель не найден", 0.0
    current = row['balance']
    if current < amount:
        conn.close()
        return False, "Недостаточно средств", current
    c.execute("""
        UPDATE drivers SET balance = balance - ?
        WHERE car_number = ?
    """, (amount, car_number.upper()))
    c.execute("""
        INSERT INTO transactions (car_number, amount, type, description)
        VALUES (?, ?, 'debit', ?)
    """, (car_number.upper(), amount, description))
    c.execute("SELECT balance FROM drivers WHERE car_number = ?",
              (car_number.upper(),))
    new_balance = c.fetchone()['balance']
    conn.commit()
    conn.close()
    return True, "OK", new_balance

def get_driver_transactions(car_number, limit=50):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM transactions
        WHERE car_number = ?
        ORDER BY created_at DESC
        LIMIT ?
    """, (car_number.upper(), limit))
    txs = c.fetchall()
    conn.close()
    return txs

def get_all_drivers_balance():
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT d.*,
               COUNT(t.id) as trip_count,
               COALESCE(SUM(CASE WHEN tx.type='debit' THEN tx.amount ELSE 0 END),0) as total_spent,
               COALESCE(SUM(CASE WHEN tx.type='credit' THEN tx.amount ELSE 0 END),0) as total_topup
        FROM drivers d
        LEFT JOIN trips t ON t.car_number = d.car_number
        LEFT JOIN transactions tx ON tx.car_number = d.car_number
        WHERE d.status = 'approved'
        GROUP BY d.id
        ORDER BY d.full_name
    """)
    drivers = c.fetchall()
    conn.close()
    return drivers

# ==================== ТАРИФЫ ====================
def get_all_tariffs():
    """Получить все тарифы из БД в виде списка словарей"""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, name, city_rate, suburb_rate, base_fare, wait_rate FROM tariffs ORDER BY id")
    rows = c.fetchall()
    conn.close()
    
    # Преобразуем tuple в dict для удобства в шаблоне
    result = []
    for r in rows:
        result.append({
            'id':          r[0],
            'name':        r[1],
            'city_rate':   r[2],
            'suburb_rate': r[3],
            'base_fare':   r[4],
            'wait_rate':   r[5]
        })
    return result

def get_driver_tariff(car_number):
    """Получить тариф конкретного водителя"""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT t.id, t.name, t.city_rate, t.suburb_rate, t.base_fare, t.wait_rate
        FROM drivers d
        LEFT JOIN tariffs t ON t.id = d.tariff_id
        WHERE d.car_number = ?
    """, (car_number.upper(),))
    row = c.fetchone()
    conn.close()
    
    if row:
        return {
            'id':          row[0],
            'name':        row[1],
            'city_rate':   row[2],
            'suburb_rate': row[3],
            'base_fare':   row[4],
            'wait_rate':   row[5]
        }
    return None

def set_driver_tariff(car_number, tariff_id):
    """Назначить тариф водителю"""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE drivers
        SET tariff_id = ?
        WHERE car_number = ?
    """, (tariff_id, car_number.upper()))
    conn.commit()
    affected = c.rowcount
    conn.close()
    return affected > 0

# ==================== FLASK ====================
flask_app = Flask(__name__, template_folder='.')
flask_app.secret_key = SECRET_KEY

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'admin' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ==================== ADMIN ROUTES ====================
@flask_app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if (request.form.get('username') == ADMIN_USERNAME and
                request.form.get('password') == ADMIN_PASSWORD):
            session['admin'] = True
            return redirect(url_for('dashboard'))
        return render_template('login.html', error="Неверный логин или пароль")
    return render_template('login.html')

@flask_app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@flask_app.route('/')
@admin_required
def dashboard():
    return render_template('dashboard.html', stats=get_stats())

# ✅ DRIVERS С ТАРИФАМИ
@flask_app.route('/drivers')
@admin_required
def drivers():
    query       = request.args.get('search', '')
    drivers_raw = search_drivers(query) if query else get_all_drivers()
    tariffs     = get_all_tariffs()

    drivers_list = []
    for d in drivers_raw:
        driver_dict = dict(d)
        if not driver_dict.get('tariff_name'):
            driver_dict['tariff_name'] = 'Стандарт'
        if not driver_dict.get('tariff_id'):
            driver_dict['tariff_id'] = 1
        drivers_list.append(driver_dict)

    return render_template(
        'drivers.html',
        drivers=drivers_list,
        search=query,
        tariffs=tariffs
    )
# ✅ СМЕНА ТАРИФА
@flask_app.route('/driver/set_tariff', methods=['POST'])
@admin_required
def web_set_tariff():
    car_number = request.form.get('car_number', '').strip().upper()
    tariff_id  = request.form.get('tariff_id')

    if not car_number or not tariff_id:
        return redirect(url_for('drivers'))

    set_driver_tariff(car_number, tariff_id)
    add_log("set_tariff", 0, 0,
            f"Авто: {car_number} | Тариф ID: {tariff_id}")

    return redirect(url_for('drivers'))

@flask_app.route('/requests')
@admin_required
def requests_page():
    pending = get_pending_drivers()
    return render_template('requests.html', drivers=pending)

@flask_app.route('/approve/<car_number>')
@admin_required
def web_approve(car_number):
    pin = approve_driver_by_car(car_number)
    add_log("approve", 0, 0, f"Авто: {car_number} PIN: {pin}")
    return redirect(url_for('requests_page'))

@flask_app.route('/reject/<car_number>')
@admin_required
def web_reject(car_number):
    reject_driver_by_car(car_number)
    add_log("reject", 0, 0, f"Авто: {car_number}")
    return redirect(url_for('requests_page'))

@flask_app.route('/approve_direct/<car_number>')
@admin_required
def web_approve_direct(car_number):
    pin = approve_driver_by_car(car_number)
    add_log("approve", 0, 0, f"Авто: {car_number} PIN: {pin}")
    return redirect(url_for('drivers'))

@flask_app.route('/block/<car_number>')
@admin_required
def web_block(car_number):
    block_driver_by_car(car_number)
    add_log("block", 0, 0, f"Авто: {car_number}")
    return redirect(url_for('drivers'))

@flask_app.route('/unblock/<car_number>')
@admin_required
def web_unblock(car_number):
    unblock_driver_by_car(car_number)
    add_log("unblock", 0, 0, f"Авто: {car_number}")
    return redirect(url_for('drivers'))

@flask_app.route('/reset_pin/<car_number>')
@admin_required
def web_reset_pin(car_number):
    pin = reset_pin_by_car(car_number)
    add_log("reset_pin", 0, 0, f"Авто: {car_number} PIN: {pin}")
    return redirect(url_for('drivers'))

@flask_app.route('/stats')
@admin_required
def stats():
    return render_template('stats.html', stats=get_stats(), logs=get_logs())

@flask_app.route('/trips')
@admin_required
def trips_page():
    car  = request.args.get('car',  '').strip().upper()
    date = request.args.get('date', '').strip()
    conn = get_db()
    c    = conn.cursor()
    if car and date:
        c.execute("""
            SELECT * FROM trips
            WHERE car_number = ? AND created_at LIKE ?
            ORDER BY created_at DESC
        """, (car, f"{date}%"))
    elif car:
        c.execute("""
            SELECT * FROM trips
            WHERE car_number = ?
            ORDER BY created_at DESC
        """, (car,))
    elif date:
        c.execute("""
            SELECT * FROM trips
            WHERE created_at LIKE ?
            ORDER BY created_at DESC
        """, (f"{date}%",))
    else:
        c.execute("SELECT * FROM trips ORDER BY created_at DESC LIMIT 200")
    trips = c.fetchall()
    conn.close()
    return render_template('trips.html', trips=trips)

@flask_app.route('/broadcast', methods=['GET', 'POST'])
@admin_required
def broadcast():
    return render_template('broadcast.html')

# ==================== БАЛАНС ROUTES ====================
@flask_app.route('/balance')
@admin_required
def balance_page():
    drivers_list = get_all_drivers_balance()
    stats        = get_stats()
    return render_template('balance.html',
                           drivers=drivers_list,
                           stats=stats)

@flask_app.route('/balance/topup', methods=['POST'])
@admin_required
def web_topup():
    car_number  = request.form.get('car_number', '').strip().upper()
    amount      = float(request.form.get('amount', 0))
    description = request.form.get('description', 'Пополнение баланса').strip()

    if not car_number or amount <= 0:
        return redirect(url_for('balance_page'))

    new_balance = topup_driver_balance(car_number, amount, description)
    add_log("topup", 0, 0,
            f"Авто: {car_number} | Сумма: {amount:,.0f} сум | "
            f"Баланс: {new_balance:,.0f} сум")
    return redirect(url_for('balance_page'))

@flask_app.route('/balance/deduct', methods=['POST'])
@admin_required
def web_deduct():
    car_number  = request.form.get('car_number', '').strip().upper()
    amount      = float(request.form.get('amount', 0))
    description = request.form.get('description', 'Списание').strip()

    if not car_number or amount <= 0:
        return redirect(url_for('balance_page'))

    ok, msg, new_balance = deduct_driver_balance(car_number, amount, description)
    if ok:
        add_log("deduct", 0, 0,
                f"Авто: {car_number} | Списано: {amount:,.0f} сум | "
                f"Баланс: {new_balance:,.0f} сум")
    return redirect(url_for('balance_page'))

@flask_app.route('/balance/history/<car_number>')
@admin_required
def balance_history(car_number):
    driver = get_driver_by_car(car_number)
    txs    = get_driver_transactions(car_number, limit=100)
    return render_template('balance_history.html',
                           driver=driver,
                           transactions=txs)

# ==================== API для APK ====================
@flask_app.route('/api/driver/register', methods=['POST'])
def api_register():
    try:
        data       = request.get_json()
        name       = data.get('name', '').strip()
        car_number = data.get('car_number', '').strip().upper()
        phone      = data.get('phone', '').strip()

        if not name or not car_number:
            return jsonify({"success": False,
                            "error": "Заполните все поля"}), 400

        driver = get_driver_by_car(car_number)

        if driver:
            if driver['is_blocked']:
                return jsonify({"success": False,
                                "error": "Аккаунт заблокирован"}), 403
            if driver['status'] == 'pending':
                return jsonify({"success": False,
                                "error": "Заявка уже отправлена, ожидайте"}), 200
            elif driver['status'] == 'rejected':
                return jsonify({"success": False,
                                "error": "Ваша заявка отклонена"}), 200
            elif driver['status'] == 'approved':
                reset_driver(car_number)
                add_log("reset", 0, 0, f"Переустановка APK: {car_number}")

        add_driver(tg_id=0, username=name, full_name=name,
                   phone=phone, car_number=car_number)

        return jsonify({"success": True,
                        "message": "Заявка отправлена! Ожидайте одобрения"}), 200

    except Exception as e:
        logging.error(f"Register error: {e}")
        return jsonify({"success": False, "error": "Ошибка сервера"}), 500


@flask_app.route('/api/driver/login', methods=['POST'])
def api_login():
    try:
        data       = request.get_json()
        car_number = data.get('car_number', '').strip().upper()
        pin        = data.get('pin', '').strip()

        if not car_number or not pin:
            return jsonify({"success": False,
                            "error": "Заполните все поля"}), 400

        conn = get_db()
        c = conn.cursor()
        c.execute(
            "SELECT * FROM drivers WHERE car_number = ? AND pin = ?",
            (car_number, pin)
        )
        driver = c.fetchone()
        conn.close()

        if not driver:
            return jsonify({"success": False,
                            "error": "Неверный номер авто или PIN"}), 401
        if driver['is_blocked']:
            return jsonify({"success": False,
                            "error": "Аккаунт заблокирован"}), 403
        if driver['status'] != 'approved':
            return jsonify({"success": False,
                            "error": "Заявка ещё не одобрена"}), 403

        if driver['pin_expires_at']:
            expires = datetime.strptime(driver['pin_expires_at'],
                                        "%Y-%m-%d %H:%M:%S")
            if datetime.now() > expires:
                return jsonify({"success": False,
                                "error": "PIN истёк, обратитесь к администратору"}), 403

        update_online_status(car_number, 'online')

        # ✅ ПОЛУЧАЕМ ТАРИФ ВОДИТЕЛЯ
        tariff = get_driver_tariff(car_number)

        return jsonify({
            "success": True,
            "driver": {
                "id":      driver['id'],
                "name":    driver['full_name'],
                "car":     driver['car_number'],
                "balance": driver['balance'] or 0.0,
                # ✅ ТАРИФ ПЕРЕДАЁТСЯ В APK
                "tariff": {
                    "city_rate":   tariff['city_rate']   if tariff else 2800,
                    "suburb_rate": tariff['suburb_rate'] if tariff else 3000,
                    "base_fare":   tariff['base_fare']   if tariff else 5000,
                    "wait_rate":   tariff['wait_rate']   if tariff else 500
                }
            }
        }), 200

    except Exception as e:
        logging.error(f"Login error: {e}")
        return jsonify({"success": False, "error": "Ошибка сервера"}), 500


@flask_app.route('/api/driver/check/<car_number>', methods=['GET'])
def api_check_driver(car_number):
    try:
        driver = get_driver_by_car(car_number)
        if not driver:
            return jsonify({
                "success":    False,
                "is_blocked": True,
                "status":     "not_found"
            }), 404

        return jsonify({
            "success":    True,
            "is_blocked": bool(driver['is_blocked']),
            "status":     driver['status']
        }), 200

    except Exception as e:
        logging.error(f"Check error: {e}")
        return jsonify({"success": False, "is_blocked": True}), 500


@flask_app.route('/api/driver/status', methods=['POST'])
def api_update_status():
    try:
        data       = request.get_json()
        car_number = data.get('car_number', '').strip().upper()
        status     = data.get('status', 'online').strip()

        if status not in ['online', 'offline', 'busy', 'free']:
            return jsonify({"success": False,
                            "error": "Неверный статус"}), 400

        update_online_status(car_number, status)
        return jsonify({"success": True}), 200

    except Exception as e:
        logging.error(f"Status error: {e}")
        return jsonify({"success": False, "error": "Ошибка сервера"}), 500


@flask_app.route('/api/driver/balance', methods=['POST'])
def api_get_balance():
    try:
        data       = request.get_json()
        car_number = data.get('car_number', '').strip().upper()
        balance    = get_balance(car_number)
        return jsonify({"success": True, "balance": balance}), 200

    except Exception as e:
        logging.error(f"Balance error: {e}")
        return jsonify({"success": False, "error": "Ошибка сервера"}), 500


@flask_app.route('/api/driver/balance/detail', methods=['POST'])
def api_balance_detail():
    try:
        data       = request.get_json()
        car_number = data.get('car_number', '').strip().upper()

        driver = get_driver_by_car(car_number)
        if not driver:
            return jsonify({"success": False, "error": "Водитель не найден"}), 404

        txs = get_driver_transactions(car_number, limit=50)

        tx_list = []
        for tx in txs:
            tx_list.append({
                "id":          tx['id'],
                "amount":      tx['amount'],
                "type":        tx['type'],
                "description": tx['description'],
                "date":        tx['created_at']
            })

        return jsonify({
            "success":      True,
            "balance":      driver['balance'] or 0.0,
            "car_number":   driver['car_number'],
            "name":         driver['full_name'],
            "transactions": tx_list
        }), 200

    except Exception as e:
        logging.error(f"Balance detail error: {e}")
        return jsonify({"success": False, "error": "Ошибка сервера"}), 500


@flask_app.route('/api/admin/balance/topup', methods=['POST'])
def api_admin_topup():
    try:
        auth = request.headers.get('X-Admin-Key', '')
        if auth != SECRET_KEY:
            return jsonify({"success": False, "error": "Не авторизован"}), 401

        data        = request.get_json()
        car_number  = data.get('car_number', '').strip().upper()
        amount      = float(data.get('amount', 0))
        description = data.get('description', 'Пополнение').strip()

        if not car_number or amount <= 0:
            return jsonify({"success": False, "error": "Неверные данные"}), 400

        new_balance = topup_driver_balance(car_number, amount, description)
        add_log("api_topup", 0, 0,
                f"Авто: {car_number} | +{amount:,.0f} сум")

        return jsonify({
            "success":     True,
            "new_balance": new_balance
        }), 200

    except Exception as e:
        logging.error(f"API topup error: {e}")
        return jsonify({"success": False, "error": "Ошибка сервера"}), 500


@flask_app.route('/api/admin/balance/deduct', methods=['POST'])
def api_admin_deduct():
    try:
        auth = request.headers.get('X-Admin-Key', '')
        if auth != SECRET_KEY:
            return jsonify({"success": False, "error": "Не авторизован"}), 401

        data        = request.get_json()
        car_number  = data.get('car_number', '').strip().upper()
        amount      = float(data.get('amount', 0))
        description = data.get('description', 'Списание').strip()

        if not car_number or amount <= 0:
            return jsonify({"success": False, "error": "Неверные данные"}), 400

        ok, msg, new_balance = deduct_driver_balance(car_number, amount, description)

        if not ok:
            return jsonify({"success": False, "error": msg}), 400

        add_log("api_deduct", 0, 0,
                f"Авто: {car_number} | -{amount:,.0f} сум")

        return jsonify({
            "success":     True,
            "new_balance": new_balance
        }), 200

    except Exception as e:
        logging.error(f"API deduct error: {e}")
        return jsonify({"success": False, "error": "Ошибка сервера"}), 500


@flask_app.route('/api/driver/update', methods=['POST'])
def api_update_driver():
    try:
        data       = request.get_json()
        car_number = data.get('car_number', '').strip().upper()
        name       = data.get('name', '').strip()
        phone      = data.get('phone', '').strip()

        if not car_number or not name or not phone:
            return jsonify({"success": False,
                            "error": "Не все поля заполнены"}), 400

        conn = get_db()
        c = conn.cursor()
        c.execute("""
            UPDATE drivers
            SET full_name = ?, phone = ?
            WHERE car_number = ?
        """, (name, phone, car_number))
        conn.commit()
        conn.close()

        return jsonify({"success": True}), 200

    except Exception as e:
        logging.error(f"Update driver error: {e}")
        return jsonify({"success": False, "error": "Ошибка сервера"}), 500


@flask_app.route('/api/driver/trip', methods=['POST'])
def api_save_trip():
    try:
        data            = request.get_json()
        car_number      = data.get('car_number', '').strip().upper()
        price           = data.get('price', 0)
        city_distance   = data.get('city_distance', 0.0)
        suburb_distance = data.get('suburb_distance', 0.0)
        waiting_seconds = data.get('waiting_seconds', 0)
        total_seconds   = data.get('total_seconds', 0)

        tashkent_time = datetime.now(
            ZoneInfo("Asia/Tashkent")
        ).strftime("%Y-%m-%d %H:%M:%S")

        conn = get_db()
        c    = conn.cursor()
        c.execute("""
            INSERT INTO trips
            (car_number, price, city_distance, suburb_distance,
             waiting_seconds, total_seconds, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (car_number, price, city_distance, suburb_distance,
              waiting_seconds, total_seconds, tashkent_time))
        conn.commit()
        conn.close()

        add_log("trip", 0, 0,
                f"Авто: {car_number} | "
                f"Цена: {price:,} сум | "
                f"Км: {city_distance:.1f}+{suburb_distance:.1f}")

        return jsonify({"success": True,
                        "created_at": tashkent_time}), 200

    except Exception as e:
        logging.error(f"Trip error: {e}")
        return jsonify({"success": False, "error": "Ошибка сервера"}), 500


@flask_app.route('/api/driver/trips/<car_number>', methods=['GET'])
def api_get_driver_trips(car_number):
    try:
        conn = get_db()
        c    = conn.cursor()

        date_from = request.args.get('date_from', '')
        date_to   = request.args.get('date_to',   '')

        query  = "SELECT * FROM trips WHERE car_number = ?"
        params = [car_number.upper()]

        if date_from:
            query += " AND DATE(created_at) >= DATE(?)"
            params.append(date_from)

        if date_to:
            query += " AND DATE(created_at) <= DATE(?)"
            params.append(date_to)

        query += " ORDER BY created_at DESC LIMIT 500"

        c.execute(query, params)
        trips = c.fetchall()
        conn.close()

        trips_list = []
        for trip in trips:
            trips_list.append({
                "id":              trip['id'],
                "price":           trip['price'],
                "city_distance":   trip['city_distance'],
                "suburb_distance": trip['suburb_distance'],
                "waiting_seconds": trip['waiting_seconds'],
                "total_seconds":   trip['total_seconds'],
                "created_at":      trip['created_at']
            })

        return jsonify({
            "success": True,
            "trips":   trips_list
        }), 200

    except Exception as e:
        logging.error(f"Trips error: {e}")
        return jsonify({"success": False, "error": "Ошибка сервера"}), 500


@flask_app.route('/api/tariffs', methods=['GET'])
def api_get_tariffs():
    try:
        # ✅ Берём из БД, а не из TaxiConfig
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT city_rate, suburb_rate, base_fare, wait_rate FROM tariffs WHERE id=1")
        row = c.fetchone()
        conn.close()

        if row:
            return jsonify({
                "success":     True,
                "city_rate":   row[0],
                "suburb_rate": row[1],
                "base_fare":   row[2],
                "wait_rate":   row[3]
            }), 200
        else:
            return jsonify({
                "success":     True,
                "city_rate":   TaxiConfig.CITY_RATE,
                "suburb_rate": TaxiConfig.SUBURB_RATE,
                "base_fare":   TaxiConfig.BASE_FARE,
                "wait_rate":   TaxiConfig.WAIT_RATE
            }), 200

    except Exception as e:
        logging.error(f"Tariffs error: {e}")
        return jsonify({"success": False}), 500


@flask_app.route('/api/tariffs', methods=['POST'])
def api_save_tariffs():
    try:
        data = request.get_json()
        
        print(f"📥 Получены данные: {data}")  # 🔍 Логирование

        city_rate   = float(data.get('city_rate',   TaxiConfig.CITY_RATE))
        suburb_rate = float(data.get('suburb_rate', TaxiConfig.SUBURB_RATE))
        base_fare   = float(data.get('base_fare',   TaxiConfig.BASE_FARE))
        wait_rate   = float(data.get('wait_rate',   TaxiConfig.WAIT_RATE))
        
        print(f"📊 Параметры: city={city_rate}, suburb={suburb_rate}, base={base_fare}, wait={wait_rate}")

        # ✅ Сохраняем в БД
        conn = get_db()
        c = conn.cursor()
        
        # 🔍 Проверяем что есть в БД
        c.execute("SELECT * FROM tariffs WHERE id=1")
        existing = c.fetchone()
        print(f"📦 Текущие тарифы в БД: {existing}")
        
        c.execute("""
            UPDATE tariffs
            SET city_rate=?, suburb_rate=?, base_fare=?, wait_rate=?
            WHERE id=1
        """, (city_rate, suburb_rate, base_fare, wait_rate))
        
        print(f"✅ Обновлено строк: {c.rowcount}")
        
        conn.commit()
        conn.close()

        # ✅ Обновляем TaxiConfig в памяти
        TaxiConfig.CITY_RATE   = city_rate
        TaxiConfig.SUBURB_RATE = suburb_rate
        TaxiConfig.BASE_FARE   = base_fare
        TaxiConfig.WAIT_RATE   = wait_rate

        add_log("api_save_tariffs", 0, 0,
                f"Тарифы: город={city_rate} пригород={suburb_rate} мин={base_fare} ожидание={wait_rate}")

        print(f"✅ api_save_tariffs успешно завершён")
        
        return jsonify({
            "success": True, 
            "message": "Тарифы сохранены",
            "saved": {
                "city_rate": city_rate,
                "suburb_rate": suburb_rate,
                "base_fare": base_fare,
                "wait_rate": wait_rate
            }
        }), 200

    except Exception as e:
        print(f"❌ api_save_tariffs error: {e}")
        logging.error(f"api_save_tariffs error: {e}", exc_info=True)
        return jsonify({
            "success": False, 
            "message": str(e)
        }), 500


@flask_app.route('/tariffs/edit', methods=['POST'])
@admin_required
def edit_tariff():
    tariff_id   = request.form.get('tariff_id')
    name        = request.form.get('name', '').strip()
    city_rate   = float(request.form.get('city_rate',   2800))
    suburb_rate = float(request.form.get('suburb_rate', 3000))
    base_fare   = float(request.form.get('base_fare',   5000))
    wait_rate   = float(request.form.get('wait_rate',    500))

    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE tariffs
        SET name=?, city_rate=?, suburb_rate=?, base_fare=?, wait_rate=?
        WHERE id=?
    """, (name, city_rate, suburb_rate, base_fare, wait_rate, tariff_id))
    conn.commit()
    conn.close()

    # ✅ Если редактируем ID=1 — обновляем TaxiConfig
    if str(tariff_id) == '1':
        TaxiConfig.CITY_RATE   = city_rate
        TaxiConfig.SUBURB_RATE = suburb_rate
        TaxiConfig.BASE_FARE   = base_fare
        TaxiConfig.WAIT_RATE   = wait_rate

    add_log("edit_tariff", 0, 0, f"Тариф ID:{tariff_id} → {name}")
    return redirect(url_for('tariffs_page'))


# ==================== ЗАПУСК ====================
def init_taxiconfig_from_db():
    """При старте загружаем тарифы из БД в TaxiConfig"""
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT city_rate, suburb_rate, base_fare, wait_rate FROM tariffs WHERE id=1")
        row = c.fetchone()
        conn.close()

        if row:
            TaxiConfig.CITY_RATE   = row[0]
            TaxiConfig.SUBURB_RATE = row[1]
            TaxiConfig.BASE_FARE   = row[2]
            TaxiConfig.WAIT_RATE   = row[3]
            logging.info(f"✅ TaxiConfig загружен из БД: город={row[0]} пригород={row[1]}")
    except Exception as e:
        logging.error(f"❌ init_taxiconfig_from_db error: {e}")

init_db()
init_taxiconfig_from_db()  # ← ДОБАВЬ ЭТУ СТРОКУ
app = flask_app

if __name__ == "__main__":
    flask_app.run(host='0.0.0.0', port=PORT, debug=False)
