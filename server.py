import logging
import os
import secrets
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
from functools import wraps
from zoneinfo import ZoneInfo

from flask import (Flask, render_template, request,
                   redirect, url_for, session, jsonify)

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

# ==================== БАЗА ДАННЫХ (POSTGRESQL) ====================
def get_db():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise Exception("ОШИБКА: Переменная DATABASE_URL не задана в Render!")
    conn = psycopg2.connect(db_url)
    conn.cursor_factory = RealDictCursor
    return conn

def init_db():
    print("Инициализация базы данных PostgreSQL...")
    conn = get_db()
    c = conn.cursor()

    try:
        c.execute("""
            CREATE TABLE IF NOT EXISTS drivers (
                id SERIAL PRIMARY KEY,
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                balance REAL DEFAULT 0.0,
                online_status TEXT DEFAULT 'offline',
                last_seen TEXT DEFAULT NULL,
                tariff_id INTEGER DEFAULT 1
            )
        """)
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"drivers: {e}")

    all_columns = [
        ("balance",           "REAL DEFAULT 0.0"),
        ("online_status",     "TEXT DEFAULT 'offline'"),
        ("last_seen",         "TEXT DEFAULT NULL"),
        ("tariff_id",         "INTEGER DEFAULT 1"),
        ("can_change_tariff", "INTEGER DEFAULT 0"),
    ]

    for col, definition in all_columns:
        try:
            c.execute(f"ALTER TABLE drivers ADD COLUMN IF NOT EXISTS {col} {definition}")
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"Колонка {col}: {e}")

    try:
        c.execute("""
            CREATE TABLE IF NOT EXISTS tariffs (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                city_rate REAL DEFAULT 2800,
                suburb_rate REAL DEFAULT 3000,
                base_fare REAL DEFAULT 5000,
                wait_rate REAL DEFAULT 500
            )
        """)
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"tariffs: {e}")

    try:
        c.execute("SELECT COUNT(*) as cnt FROM tariffs")
        if c.fetchone()['cnt'] == 0:
            c.execute("""
                INSERT INTO tariffs (name, city_rate, suburb_rate, base_fare, wait_rate) 
                VALUES ('Стандарт', 2800, 3000, 5000, 500)
            """)
            conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"tariffs insert: {e}")

    other_tables = [
        """CREATE TABLE IF NOT EXISTS logs (
            id SERIAL PRIMARY KEY, 
            action TEXT, 
            admin_id INTEGER, 
            driver_id INTEGER, 
            details TEXT, 
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS broadcasts (
            id SERIAL PRIMARY KEY, 
            message TEXT, 
            sent_count INTEGER, 
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS trips (
            id SERIAL PRIMARY KEY, 
            car_number TEXT, 
            price INTEGER, 
            city_distance REAL, 
            suburb_distance REAL, 
            waiting_seconds INTEGER, 
            total_seconds INTEGER, 
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS transactions (
            id SERIAL PRIMARY KEY, 
            car_number TEXT NOT NULL, 
            amount REAL NOT NULL, 
            type TEXT NOT NULL CHECK(type IN ('credit','debit')), 
            description TEXT DEFAULT '', 
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY, 
            value TEXT NOT NULL
        )""",
    ]

    for sql in other_tables:
        try:
            c.execute(sql)
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"Таблица: {e}")

    try:
        c.execute("""
            INSERT INTO app_settings (key, value) 
            VALUES ('allow_custom_tariffs', 'false') 
            ON CONFLICT DO NOTHING
        """)
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"app_settings: {e}")

    conn.close()
    print("База данных PostgreSQL готова")

# ==================== ГЕНЕРАЦИЯ PIN ====================
def generate_pin():
    conn = get_db()
    c = conn.cursor()
    while True:
        pin = str(secrets.randbelow(9000) + 1000)
        c.execute("SELECT COUNT(*) as cnt FROM drivers WHERE pin = %s", (pin,))
        if c.fetchone()['cnt'] == 0:
            conn.close()
            return pin

# ==================== ФУНКЦИИ БД ====================
def add_log(action, admin_id, driver_id, details):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO logs (action, admin_id, driver_id, details) VALUES (%s, %s, %s, %s)", (action, admin_id, driver_id, details))
    conn.commit()
    conn.close()

def add_driver(tg_id, username, full_name, phone, car_number):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO drivers (tg_id, username, full_name, phone, car_number, status)
        VALUES (%s, %s, %s, %s, %s, 'pending')
        ON CONFLICT(car_number) DO UPDATE SET
            tg_id = EXCLUDED.tg_id, username = EXCLUDED.username,
            full_name = EXCLUDED.full_name, phone = EXCLUDED.phone,
            status = 'pending', pin = NULL, pin_created_at = NULL, pin_expires_at = NULL
    """, (tg_id, username, full_name, phone, car_number))
    conn.commit()
    conn.close()

def get_all_drivers():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT d.*, t.name as tariff_name FROM drivers d LEFT JOIN tariffs t ON t.id = d.tariff_id ORDER BY d.created_at DESC")
    drivers = c.fetchall()
    conn.close()
    
    drivers_list = []
    for d in drivers:
        driver_dict = dict(d)
        if not driver_dict.get('tariff_name'): driver_dict['tariff_name'] = 'Стандарт'
        if not driver_dict.get('tariff_id'): driver_dict['tariff_id'] = 1
        
        if driver_dict.get('pin_expires_at'):
            try:
                expires = datetime.strptime(driver_dict['pin_expires_at'], "%Y-%m-%d %H:%M:%S")
                days_left = (expires - datetime.now()).days
                driver_dict['pin_days_left'] = max(0, days_left)
            except:
                driver_dict['pin_days_left'] = 0
        else:
            driver_dict['pin_days_left'] = 0
            
        driver_dict['can_change_tariff'] = driver_dict.get('can_change_tariff', 0)
            
        drivers_list.append(driver_dict)
    return drivers_list

def get_driver_by_car(car_number):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM drivers WHERE car_number = %s", (car_number.upper(),))
    driver = c.fetchone()
    conn.close()
    return driver

def search_drivers(query):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT d.*, t.name as tariff_name FROM drivers d LEFT JOIN tariffs t ON t.id = d.tariff_id WHERE d.full_name LIKE %s OR d.car_number LIKE %s OR d.phone LIKE %s OR d.username LIKE %s", (f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%"))
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
    c.execute("UPDATE drivers SET status = 'approved', pin = %s, pin_created_at = %s, pin_expires_at = %s WHERE car_number = %s", (pin, now.strftime("%Y-%m-%d %H:%M:%S"), expires.strftime("%Y-%m-%d %H:%M:%S"), car_number.upper()))
    conn.commit()
    conn.close()
    return pin

def reject_driver_by_car(car_number):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE drivers SET status = 'rejected' WHERE car_number = %s", (car_number.upper(),))
    conn.commit()
    conn.close()

def block_driver_by_car(car_number):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE drivers SET is_blocked = 1, online_status = 'offline' WHERE car_number = %s", (car_number.upper(),))
    conn.commit()
    conn.close()

def unblock_driver_by_car(car_number):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE drivers SET is_blocked = 0 WHERE car_number = %s", (car_number.upper(),))
    conn.commit()
    conn.close()

def reset_pin_by_car(car_number):
    pin = generate_pin()
    now = datetime.now()
    expires = now + timedelta(days=PIN_EXPIRE_DAYS)
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE drivers SET pin = %s, pin_created_at = %s, pin_expires_at = %s WHERE car_number = %s", (pin, now.strftime("%Y-%m-%d %H:%M:%S"), expires.strftime("%Y-%m-%d %H:%M:%S"), car_number.upper()))
    conn.commit()
    conn.close()
    return pin

def update_online_status(car_number, status):
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("UPDATE drivers SET online_status = %s, last_seen = %s WHERE car_number = %s", (status, now, car_number.upper()))
    conn.commit()
    conn.close()

def get_balance(car_number):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT balance FROM drivers WHERE car_number = %s", (car_number.upper(),))
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
    c.execute("SELECT * FROM trips WHERE car_number = %s ORDER BY created_at DESC LIMIT 100", (car_number.upper(),))
    trips = c.fetchall()
    conn.close()
    return trips

def get_stats():
    conn = get_db()
    c = conn.cursor()
    stats = {}
    c.execute("SELECT COUNT(*) as cnt FROM drivers"); stats['total'] = c.fetchone()['cnt']
    c.execute("SELECT COUNT(*) as cnt FROM drivers WHERE status='approved'"); stats['approved'] = c.fetchone()['cnt']
    c.execute("SELECT COUNT(*) as cnt FROM drivers WHERE status='pending'"); stats['pending'] = c.fetchone()['cnt']
    c.execute("SELECT COUNT(*) as cnt FROM drivers WHERE status='rejected'"); stats['rejected'] = c.fetchone()['cnt']
    c.execute("SELECT COUNT(*) as cnt FROM drivers WHERE is_blocked=1"); stats['blocked'] = c.fetchone()['cnt']
    c.execute("SELECT COUNT(*) as cnt FROM drivers WHERE online_status IN ('online','free','busy')"); stats['online'] = c.fetchone()['cnt']
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("SELECT COUNT(*) as cnt FROM drivers WHERE created_at::text LIKE %s", (f"{today}%",)); stats['today'] = c.fetchone()['cnt']
    c.execute("SELECT COUNT(*) as cnt FROM trips WHERE created_at::text LIKE %s", (f"{today}%",)); stats['trips_today'] = c.fetchone()['cnt']
    c.execute("SELECT COALESCE(SUM(price), 0) as total_sum FROM trips WHERE created_at::text LIKE %s", (f"{today}%",)); stats['earnings_today'] = c.fetchone()['total_sum']
    c.execute("SELECT COUNT(*) as cnt FROM trips"); stats['trips_total'] = c.fetchone()['cnt']
    c.execute("SELECT COALESCE(SUM(price), 0) as total_sum FROM trips"); stats['earnings_total'] = c.fetchone()['total_sum']
    c.execute("SELECT COALESCE(SUM(balance), 0) as total_sum FROM drivers WHERE status='approved'"); stats['total_balance'] = c.fetchone()['total_sum']
    c.execute("SELECT COUNT(*) as cnt FROM drivers WHERE balance < 10000 AND status='approved'"); stats['low_balance_count'] = c.fetchone()['cnt']
    c.execute("SELECT COALESCE(SUM(amount), 0) as total_sum FROM transactions WHERE type='debit' AND created_at::text LIKE %s", (f"{today}%",)); stats['deducted_today'] = c.fetchone()['total_sum']
    c.execute("SELECT COALESCE(SUM(amount), 0) as total_sum FROM transactions WHERE type='credit' AND created_at::text LIKE %s", (f"{today}%",)); stats['topup_today'] = c.fetchone()['total_sum']
    conn.close()
    return stats

def get_logs():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM logs ORDER BY created_at DESC LIMIT 50")
    logs = c.fetchall()
    conn.close()
    return logs

def cleanup_stale_online():
    """Переводит в offline тех, кто не отправлял heartbeat более 2 минут"""
    threshold = (datetime.now() - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE drivers
        SET online_status = 'offline'
        WHERE online_status = 'online'
          AND last_seen IS NOT NULL
          AND last_seen < %s
    """, (threshold,))
    affected = c.rowcount
    conn.commit()
    conn.close()
    if affected > 0:
        logging.info(f"Очистка: {affected} водитель(ей) переведены в offline")

def topup_driver_balance(car_number, amount, description="Пополнение баланса"):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE drivers SET balance = balance + %s WHERE car_number = %s", (amount, car_number.upper()))
    c.execute("INSERT INTO transactions (car_number, amount, type, description) VALUES (%s, %s, 'credit', %s)", (car_number.upper(), amount, description))
    c.execute("SELECT balance FROM drivers WHERE car_number = %s", (car_number.upper(),))
    new_balance = c.fetchone()['balance']
    conn.commit()
    conn.close()
    return new_balance

def deduct_driver_balance(car_number, amount, description="Списание"):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT balance FROM drivers WHERE car_number = %s", (car_number.upper(),))
    row = c.fetchone()
    if not row: conn.close(); return False, "Водитель не найден", 0.0
    if row['balance'] < amount: conn.close(); return False, "Недостаточно средств", row['balance']
    c.execute("UPDATE drivers SET balance = balance - %s WHERE car_number = %s", (amount, car_number.upper()))
    c.execute("INSERT INTO transactions (car_number, amount, type, description) VALUES (%s, %s, 'debit', %s)", (car_number.upper(), amount, description))
    c.execute("SELECT balance FROM drivers WHERE car_number = %s", (car_number.upper(),))
    new_balance = c.fetchone()['balance']
    conn.commit()
    conn.close()
    return True, "OK", new_balance

def get_driver_transactions(car_number, limit=50):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM transactions WHERE car_number = %s ORDER BY created_at DESC LIMIT %s", (car_number.upper(), limit))
    txs = c.fetchall()
    conn.close()
    return txs

def get_all_drivers_balance():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT d.*, COUNT(t.id) as trip_count, COALESCE(SUM(CASE WHEN tx.type='debit' THEN tx.amount ELSE 0 END),0) as total_spent, COALESCE(SUM(CASE WHEN tx.type='credit' THEN tx.amount ELSE 0 END),0) as total_topup FROM drivers d LEFT JOIN trips t ON t.car_number = d.car_number LEFT JOIN transactions tx ON tx.car_number = d.car_number WHERE d.status = 'approved' GROUP BY d.id ORDER BY d.full_name")
    drivers = c.fetchall()
    conn.close()
    return drivers

def get_all_tariffs():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, name, city_rate, suburb_rate, base_fare, wait_rate FROM tariffs ORDER BY id")
    rows = c.fetchall()
    conn.close()
    return rows

def get_driver_tariff(car_number):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT t.id, t.name, t.city_rate, t.suburb_rate, t.base_fare, t.wait_rate FROM drivers d LEFT JOIN tariffs t ON t.id = d.tariff_id WHERE d.car_number = %s", (car_number.upper(),))
    row = c.fetchone()
    conn.close()
    return row

def set_driver_tariff(car_number, tariff_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE drivers SET tariff_id = %s WHERE car_number = %s", (tariff_id, car_number.upper()))
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
        if (request.form.get('username') == ADMIN_USERNAME and request.form.get('password') == ADMIN_PASSWORD):
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
    cleanup_stale_online()
    return render_template('dashboard.html', stats=get_stats())

@flask_app.route('/drivers')
@admin_required
def drivers():
    cleanup_stale_online()
    query = request.args.get('search', '')
    drivers_raw = search_drivers(query) if query else get_all_drivers()
    tariffs = get_all_tariffs()
    return render_template('drivers.html', drivers=drivers_raw, search=query, tariffs=tariffs)

@flask_app.route('/driver/set_tariff', methods=['POST'])
@admin_required
def web_set_tariff():
    car_number = request.form.get('car_number', '').strip().upper()
    tariff_id = request.form.get('tariff_id')
    if not car_number or not tariff_id: return redirect(url_for('drivers'))
    set_driver_tariff(car_number, tariff_id)
    add_log("set_tariff", 0, 0, f"Авто: {car_number} | Тариф ID: {tariff_id}")
    return redirect(url_for('drivers'))

@flask_app.route('/requests')
@admin_required
def requests_page():
    return render_template('requests.html', drivers=get_pending_drivers())

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

@flask_app.route('/extend_pin/<car_number>')
@admin_required
def web_extend_pin(car_number):
    now = datetime.now()
    expires = now + timedelta(days=365)
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE drivers SET pin_expires_at = %s WHERE car_number = %s", (expires.strftime("%Y-%m-%d %H:%M:%S"), car_number.upper()))
    conn.commit()
    conn.close()
    add_log("extend_pin", 0, 0, f"PIN продлен: {car_number} (до {expires.strftime('%Y-%m-%d')})")
    return redirect(url_for('drivers'))

@flask_app.route('/stats')
@admin_required
def stats():
    cleanup_stale_online()
    return render_template('stats.html', stats=get_stats(), logs=get_logs())

@flask_app.route('/trips')
@admin_required
def trips_page():
    car = request.args.get('car', '').strip().upper()
    date = request.args.get('date', '').strip()
    conn = get_db()
    c = conn.cursor()
    if car and date:
        c.execute("SELECT * FROM trips WHERE car_number = %s AND created_at::text LIKE %s ORDER BY created_at DESC", (car, f"{date}%"))
    elif car:
        c.execute("SELECT * FROM trips WHERE car_number = %s ORDER BY created_at DESC", (car,))
    elif date:
        c.execute("SELECT * FROM trips WHERE created_at::text LIKE %s ORDER BY created_at DESC", (f"{date}%",))
    else:
        c.execute("SELECT * FROM trips ORDER BY created_at DESC LIMIT 200")
    trips = c.fetchall()
    conn.close()
    return render_template('trips.html', trips=trips)

@flask_app.route('/broadcast', methods=['GET', 'POST'])
@admin_required
def broadcast():
    return render_template('broadcast.html')

@flask_app.route('/balance')
@admin_required
def balance_page():
    return render_template('balance.html', drivers=get_all_drivers_balance(), stats=get_stats())

@flask_app.route('/balance/topup', methods=['POST'])
@admin_required
def web_topup():
    car_number = request.form.get('car_number', '').strip().upper()
    amount = float(request.form.get('amount', 0))
    description = request.form.get('description', 'Пополнение баланса').strip()
    if not car_number or amount <= 0: return redirect(url_for('balance_page'))
    new_balance = topup_driver_balance(car_number, amount, description)
    add_log("topup", 0, 0, f"Авто: {car_number} | +{amount:,.0f} сум | Баланс: {new_balance:,.0f} сум")
    return redirect(url_for('balance_page'))

@flask_app.route('/balance/deduct', methods=['POST'])
@admin_required
def web_deduct():
    car_number = request.form.get('car_number', '').strip().upper()
    amount = float(request.form.get('amount', 0))
    description = request.form.get('description', 'Списание').strip()
    if not car_number or amount <= 0: return redirect(url_for('balance_page'))
    ok, msg, new_balance = deduct_driver_balance(car_number, amount, description)
    if ok: add_log("deduct", 0, 0, f"Авто: {car_number} | Списано: {amount:,.0f} сум | Баланс: {new_balance:,.0f} сум")
    return redirect(url_for('balance_page'))

@flask_app.route('/balance/history/<car_number>')
@admin_required
def balance_history(car_number):
    return render_template('balance_history.html', driver=get_driver_by_car(car_number), transactions=get_driver_transactions(car_number, limit=100))

# ==================== СТРАНИЦЫ ТАРИФОВ ====================
@flask_app.route('/tariffs')
@admin_required
def tariffs_page():
    return render_template('tariffs.html', tariffs=get_all_tariffs())

@flask_app.route('/tariffs/add', methods=['POST'])
@admin_required
def add_tariff():
    name = request.form.get('name', '').strip()
    city_rate = float(request.form.get('city_rate', 2800))
    suburb_rate = float(request.form.get('suburb_rate', 3000))
    base_fare = float(request.form.get('base_fare', 5000))
    wait_rate = float(request.form.get('wait_rate', 500))
    if not name: return redirect(url_for('tariffs_page'))
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO tariffs (name, city_rate, suburb_rate, base_fare, wait_rate) VALUES (%s, %s, %s, %s, %s)", (name, city_rate, suburb_rate, base_fare, wait_rate))
    conn.commit()
    conn.close()
    add_log("add_tariff", 0, 0, f"Создан тариф: {name}")
    return redirect(url_for('tariffs_page'))

@flask_app.route('/tariffs/edit', methods=['POST'])
@admin_required
def edit_tariff():
    tariff_id = request.form.get('tariff_id')
    name = request.form.get('name', '').strip()
    city_rate = float(request.form.get('city_rate', 2800))
    suburb_rate = float(request.form.get('suburb_rate', 3000))
    base_fare = float(request.form.get('base_fare', 5000))
    wait_rate = float(request.form.get('wait_rate', 500))
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE tariffs SET name=%s, city_rate=%s, suburb_rate=%s, base_fare=%s, wait_rate=%s WHERE id=%s", (name, city_rate, suburb_rate, base_fare, wait_rate, tariff_id))
    conn.commit()
    conn.close()
    if str(tariff_id) == '1':
        TaxiConfig.CITY_RATE = city_rate; TaxiConfig.SUBURB_RATE = suburb_rate; TaxiConfig.BASE_FARE = base_fare; TaxiConfig.WAIT_RATE = wait_rate
    add_log("edit_tariff", 0, 0, f"Тариф ID:{tariff_id} -> {name}")
    return redirect(url_for('tariffs_page'))

@flask_app.route('/tariffs/delete/<int:tariff_id>', methods=['GET'])
@admin_required
def delete_tariff(tariff_id):
    if tariff_id == 1: return redirect(url_for('tariffs_page'))
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE drivers SET tariff_id = 1 WHERE tariff_id = %s", (tariff_id,))
    c.execute("DELETE FROM tariffs WHERE id = %s", (tariff_id,))
    conn.commit()
    conn.close()
    add_log("delete_tariff", 0, 0, f"Удален тариф ID: {tariff_id}")
    return redirect(url_for('tariffs_page'))

# ==================== УПРАВЛЕНИЕ НАСТРОЙКАМИ ====================
@flask_app.route('/api/settings/tariff_lock', methods=['GET'])
def api_get_tariff_lock():
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT value FROM app_settings WHERE key = 'allow_custom_tariffs'")
        row = c.fetchone()
        conn.close()
        is_allowed = row['value'] == 'true' if row else False
        return jsonify({"success": True, "allowed": is_allowed}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@flask_app.route('/admin/toggle_tariffs', methods=['POST'])
@admin_required
def admin_toggle_tariffs():
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT value FROM app_settings WHERE key = 'allow_custom_tariffs'")
        row = c.fetchone()
        current = row['value'] if row else 'false'
        new_value = 'false' if current == 'true' else 'true'
        c.execute("UPDATE app_settings SET value = %s WHERE key = 'allow_custom_tariffs'", (new_value,))
        conn.commit()
        conn.close()
        status = "ВКЛЮЧЕНА" if new_value == 'true' else "ВЫКЛЮЧЕНА"
        add_log("toggle_tariffs", 0, 0, f"Возможность менять тарифы: {status}")
        return jsonify({"success": True, "status": status}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ==================== API ДЛЯ ANDROID APK ====================
@flask_app.route('/api/driver/register', methods=['POST'])
def api_register():
    try:
        data = request.get_json()
        name = data.get('name', '').strip()
        car_number = data.get('car_number', '').strip().upper()
        phone = data.get('phone', '').strip()
        if not name or not car_number: return jsonify({"success": False, "error": "Заполните все поля"}), 400
        driver = get_driver_by_car(car_number)
        if driver:
            if driver['is_blocked']: return jsonify({"success": False, "error": "Аккаунт заблокирован"}), 403
            if driver['status'] == 'pending': return jsonify({"success": False, "error": "Заявка уже отправлена, ожидайте"}), 200
            elif driver['status'] == 'rejected': return jsonify({"success": False, "error": "Ваша заявка отклонена"}), 200
            elif driver['status'] == 'approved':
                add_log("re_register", 0, 0, f"Повторная регистрация: {car_number}")
                return jsonify({"success": False, "error": "Вы уже зарегистрированы. Войдите через PIN"}), 200
        add_driver(tg_id=0, username=name, full_name=name, phone=phone, car_number=car_number)
        return jsonify({"success": True, "message": "Заявка отправлена! Ожидайте одобрения"}), 200
    except Exception as e:
        logging.error(f"Register error: {e}")
        return jsonify({"success": False, "error": "Ошибка сервера"}), 500

@flask_app.route('/api/driver/login', methods=['POST'])
def api_login():
    try:
        data = request.get_json()
        car_number = data.get('car_number', '').strip().upper()
        pin = data.get('pin', '').strip()
        if not car_number or not pin: return jsonify({"success": False, "error": "Заполните все поля"}), 400
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM drivers WHERE car_number = %s AND pin = %s", (car_number, pin))
        driver = c.fetchone()
        conn.close()
        if not driver: return jsonify({"success": False, "error": "Неверный номер авто или PIN"}), 401
        if driver['is_blocked']: return jsonify({"success": False, "error": "Аккаунт заблокирован"}), 403
        if driver['status'] != 'approved': return jsonify({"success": False, "error": "Заявка ещё не одобрена"}), 403
        if driver['pin_expires_at']:
            if datetime.now() > datetime.strptime(driver['pin_expires_at'], "%Y-%m-%d %H:%M:%S"):
                return jsonify({"success": False, "error": "PIN истёк, обратитесь к администратору"}), 403
        update_online_status(car_number, 'online')
        tariff = get_driver_tariff(car_number)
        return jsonify({"success": True, "driver": {"id": driver['id'], "name": driver['full_name'], "car": driver['car_number'], "balance": driver['balance'] or 0.0, "tariff": {"city_rate": tariff['city_rate'] if tariff else 2800, "suburb_rate": tariff['suburb_rate'] if tariff else 3000, "base_fare": tariff['base_fare'] if tariff else 5000, "wait_rate": tariff['wait_rate'] if tariff else 500}}}), 200
    except Exception as e:
        logging.error(f"Login error: {e}")
        return jsonify({"success": False, "error": "Ошибка сервера"}), 500

@flask_app.route('/api/driver/check/<car_number>', methods=['GET'])
def api_check_driver(car_number):
    try:
        driver = get_driver_by_car(car_number)
        if not driver: return jsonify({"success": False, "is_blocked": True, "status": "not_found"}), 404
        return jsonify({"success": True, "is_blocked": bool(driver['is_blocked']), "status": driver['status']}), 200
    except Exception as e:
        logging.error(f"Check error: {e}")
        return jsonify({"success": False, "is_blocked": True}), 500

@flask_app.route('/api/driver/status', methods=['POST'])
def api_update_status():
    try:
        data = request.get_json()
        car_number = data.get('car_number', '').strip().upper()
        status = data.get('status', 'online').strip()
        if status not in ['online', 'offline', 'busy', 'free']: return jsonify({"success": False, "error": "Неверный статус"}), 400
        update_online_status(car_number, status)
        return jsonify({"success": True}), 200
    except Exception as e:
        logging.error(f"Status error: {e}")
        return jsonify({"success": False, "error": "Ошибка сервера"}), 500

@flask_app.route('/api/driver/heartbeat', methods=['POST'])
def api_heartbeat():
    """APK вызывает каждые 30 сек, обновляет last_seen"""
    try:
        data = request.get_json()
        car_number = data.get('car_number', '').strip().upper()

        if not car_number:
            return jsonify({"success": False}), 400

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = get_db()
        c = conn.cursor()
        c.execute("""
            UPDATE drivers
            SET last_seen = %s
            WHERE car_number = %s AND online_status = 'online'
        """, (now, car_number))
        conn.commit()
        conn.close()

        return jsonify({"success": True}), 200

    except Exception as e:
        logging.error(f"Heartbeat error: {e}")
        return jsonify({"success": False}), 500

@flask_app.route('/api/driver/balance', methods=['POST'])
def api_get_balance():
    try:
        data = request.get_json()
        car_number = data.get('car_number', '').strip().upper()
        return jsonify({"success": True, "balance": get_balance(car_number)}), 200
    except Exception as e:
        logging.error(f"Balance error: {e}")
        return jsonify({"success": False, "error": "Ошибка сервера"}), 500

@flask_app.route('/api/driver/balance/detail', methods=['POST'])
def api_balance_detail():
    try:
        data = request.get_json()
        car_number = data.get('car_number', '').strip().upper()
        driver = get_driver_by_car(car_number)
        if not driver: return jsonify({"success": False, "error": "Водитель не найден"}), 404
        txs = get_driver_transactions(car_number, limit=50)
        tx_list = [{"id": tx['id'], "amount": tx['amount'], "type": tx['type'], "description": tx['description'], "date": str(tx['created_at'])} for tx in txs]
        return jsonify({"success": True, "balance": driver['balance'] or 0.0, "car_number": driver['car_number'], "name": driver['full_name'], "transactions": tx_list}), 200
    except Exception as e:
        logging.error(f"Balance detail error: {e}")
        return jsonify({"success": False, "error": "Ошибка сервера"}), 500

@flask_app.route('/api/admin/balance/topup', methods=['POST'])
def api_admin_topup():
    try:
        auth = request.headers.get('X-Admin-Key', '')
        if auth != SECRET_KEY: return jsonify({"success": False, "error": "Не авторизован"}), 401
        data = request.get_json()
        car_number = data.get('car_number', '').strip().upper()
        amount = float(data.get('amount', 0))
        description = data.get('description', 'Пополнение').strip()
        if not car_number or amount <= 0: return jsonify({"success": False, "error": "Неверные данные"}), 400
        new_balance = topup_driver_balance(car_number, amount, description)
        add_log("api_topup", 0, 0, f"Авто: {car_number} | +{amount:,.0f} сум")
        return jsonify({"success": True, "new_balance": new_balance}), 200
    except Exception as e:
        logging.error(f"API topup error: {e}")
        return jsonify({"success": False, "error": "Ошибка сервера"}), 500

@flask_app.route('/api/admin/balance/deduct', methods=['POST'])
def api_admin_deduct():
    try:
        auth = request.headers.get('X-Admin-Key', '')
        if auth != SECRET_KEY: return jsonify({"success": False, "error": "Не авторизован"}), 401
        data = request.get_json()
        car_number = data.get('car_number', '').strip().upper()
        amount = float(data.get('amount', 0))
        description = data.get('description', 'Списание').strip()
        if not car_number or amount <= 0: return jsonify({"success": False, "error": "Неверные данные"}), 400
        ok, msg, new_balance = deduct_driver_balance(car_number, amount, description)
        if not ok: return jsonify({"success": False, "error": msg}), 400
        add_log("api_deduct", 0, 0, f"Авто: {car_number} | -{amount:,.0f} сум")
        return jsonify({"success": True, "new_balance": new_balance}), 200
    except Exception as e:
        logging.error(f"API deduct error: {e}")
        return jsonify({"success": False, "error": "Ошибка сервера"}), 500

@flask_app.route('/api/driver/update', methods=['POST'])
def api_update_driver():
    try:
        data = request.get_json()
        car_number = data.get('car_number', '').strip().upper()
        name = data.get('name', '').strip()
        phone = data.get('phone', '').strip()
        if not car_number or not name or not phone: return jsonify({"success": False, "error": "Не все поля заполнены"}), 400
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE drivers SET full_name = %s, phone = %s WHERE car_number = %s", (name, phone, car_number))
        conn.commit()
        conn.close()
        return jsonify({"success": True}), 200
    except Exception as e:
        logging.error(f"Update driver error: {e}")
        return jsonify({"success": False, "error": "Ошибка сервера"}), 500

@flask_app.route('/api/driver/trip', methods=['POST'])
def api_save_trip():
    try:
        data = request.get_json()
        car_number = data.get('car_number', '').strip().upper()
        price = data.get('price', 0)
        city_distance = data.get('city_distance', 0.0)
        suburb_distance = data.get('suburb_distance', 0.0)
        waiting_seconds = data.get('waiting_seconds', 0)
        total_seconds = data.get('total_seconds', 0)
        tashkent_time = datetime.now(ZoneInfo("Asia/Tashkent")).strftime("%Y-%m-%d %H:%M:%S")
        conn = get_db()
        c = conn.cursor()
        c.execute("INSERT INTO trips (car_number, price, city_distance, suburb_distance, waiting_seconds, total_seconds, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)", (car_number, price, city_distance, suburb_distance, waiting_seconds, total_seconds, tashkent_time))
        conn.commit()
        conn.close()
        add_log("trip", 0, 0, f"Авто: {car_number} | Цена: {price:,} сум | Км: {city_distance:.1f}+{suburb_distance:.1f}")
        return jsonify({"success": True, "created_at": tashkent_time}), 200
    except Exception as e:
        logging.error(f"Trip error: {e}")
        return jsonify({"success": False, "error": "Ошибка сервера"}), 500

@flask_app.route('/api/driver/trips/<car_number>', methods=['GET'])
def api_get_driver_trips(car_number):
    try:
        conn = get_db()
        c = conn.cursor()
        date_from = request.args.get('date_from', '')
        date_to = request.args.get('date_to', '')
        query = "SELECT * FROM trips WHERE car_number = %s"
        params = [car_number.upper()]
        if date_from: query += " AND DATE(created_at) >= DATE(%s)"; params.append(date_from)
        if date_to: query += " AND DATE(created_at) <= DATE(%s)"; params.append(date_to)
        query += " ORDER BY created_at DESC LIMIT 500"
        c.execute(query, params)
        trips = c.fetchall()
        conn.close()
        trips_list = [{"id": t['id'], "price": t['price'], "city_distance": t['city_distance'], "suburb_distance": t['suburb_distance'], "waiting_seconds": t['waiting_seconds'], "total_seconds": t['total_seconds'], "created_at": str(t['created_at'])} for t in trips]
        return jsonify({"success": True, "trips": trips_list}), 200
    except Exception as e:
        logging.error(f"Trips error: {e}")
        return jsonify({"success": False, "error": "Ошибка сервера"}), 500

@flask_app.route('/api/tariffs', methods=['GET'])
def api_get_tariffs():
    try:
        car_number = request.args.get('car_number', '').strip().upper()
        
        conn = get_db()
        c = conn.cursor()
        
        c.execute("SELECT value FROM app_settings WHERE key = 'allow_custom_tariffs'")
        row = c.fetchone()
        global_allowed = row['value'] == 'true' if row else False

        individual_allowed = False
        if car_number and global_allowed:
            c.execute("SELECT can_change_tariff FROM drivers WHERE car_number = %s", (car_number,))
            d_row = c.fetchone()
            if d_row:
                individual_allowed = d_row['can_change_tariff'] == 1

        is_allowed = global_allowed and (not car_number or individual_allowed)

        if not is_allowed and car_number:
            c.execute("""
                SELECT t.city_rate, t.suburb_rate, t.base_fare, t.wait_rate 
                FROM drivers d 
                JOIN tariffs t ON t.id = d.tariff_id 
                WHERE d.car_number = %s
            """, (car_number,))
            tariff_row = c.fetchone()
        else:
            c.execute("SELECT city_rate, suburb_rate, base_fare, wait_rate FROM tariffs WHERE id=1")
            tariff_row = c.fetchone()
            
        conn.close()

        if tariff_row:
            return jsonify({
                "success": True, 
                "city_rate": tariff_row['city_rate'], 
                "suburb_rate": tariff_row['suburb_rate'], 
                "base_fare": tariff_row['base_fare'], 
                "wait_rate": tariff_row['wait_rate'],
                "allowed": is_allowed
            }), 200
        else:
            return jsonify({
                "success": True, 
                "city_rate": TaxiConfig.CITY_RATE, 
                "suburb_rate": TaxiConfig.SUBURB_RATE, 
                "base_fare": TaxiConfig.BASE_FARE, 
                "wait_rate": TaxiConfig.WAIT_RATE,
                "allowed": is_allowed
            }), 200
    except Exception as e:
        logging.error(f"Tariffs error: {e}")
        return jsonify({"success": False}), 500

@flask_app.route('/api/tariffs', methods=['POST'])
def api_save_tariffs():
    try:
        auth = request.headers.get('X-Admin-Key', '')
        if auth != SECRET_KEY: return jsonify({"success": False, "error": "Доступ запрещено: только для админов"}), 403
        data = request.get_json()
        city_rate = float(data.get('city_rate', TaxiConfig.CITY_RATE))
        suburb_rate = float(data.get('suburb_rate', TaxiConfig.SUBURB_RATE))
        base_fare = float(data.get('base_fare', TaxiConfig.BASE_FARE))
        wait_rate = float(data.get('wait_rate', TaxiConfig.WAIT_RATE))
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE tariffs SET city_rate=%s, suburb_rate=%s, base_fare=%s, wait_rate=%s WHERE id=1", (city_rate, suburb_rate, base_fare, wait_rate))
        conn.commit()
        conn.close()
        TaxiConfig.CITY_RATE = city_rate; TaxiConfig.SUBURB_RATE = suburb_rate; TaxiConfig.BASE_FARE = base_fare; TaxiConfig.WAIT_RATE = wait_rate
        add_log("api_save_tariffs", 0, 0, f"Тарифы: город={city_rate} пригород={suburb_rate} мин={base_fare} ожидание={wait_rate}")
        return jsonify({"success": True, "message": "Тарифы сохранены"}), 200
    except Exception as e:
        logging.error(f"api_save_tariffs error: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500

# ==================== УПРАВЛЕНИЕ ДОСТУПОМ ====================
@flask_app.route('/driver/toggle_tariff_access/<car_number>', methods=['POST'])
@admin_required
def web_toggle_tariff_access(car_number):
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT can_change_tariff FROM drivers WHERE car_number = %s", (car_number.upper(),))
        row = c.fetchone()
        if not row:
            conn.close()
            return jsonify({"success": False, "error": "Водитель не найден"}), 404
            
        current = row['can_change_tariff']
        new_val = 0 if current == 1 else 1
        
        c.execute("UPDATE drivers SET can_change_tariff = %s WHERE car_number = %s", (new_val, car_number.upper()))
        conn.commit()
        conn.close()
        
        status = "ВКЛЮЧЕН" if new_val == 1 else "ЗАКРЫТ"
        add_log("toggle_driver_tariff", 0, 0, f"Инд. доступ к тарифам для {car_number}: {status}")
        return jsonify({"success": True, "status": status}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ==================== ЗАПУСК ====================
def init_taxiconfig_from_db():
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT city_rate, suburb_rate, base_fare, wait_rate FROM tariffs WHERE id=1")
        row = c.fetchone()
        conn.close()
        if row:
            TaxiConfig.CITY_RATE = row['city_rate']; TaxiConfig.SUBURB_RATE = row['suburb_rate']
            TaxiConfig.BASE_FARE = row['base_fare']; TaxiConfig.WAIT_RATE = row['wait_rate']
    except Exception as e:
        logging.error(f"init_taxiconfig_from_db error: {e}")

init_db()
init_taxiconfig_from_db()
app = flask_app

if __name__ == "__main__":
    flask_app.run(host='0.0.0.0', port=PORT, debug=False)
