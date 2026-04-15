import asyncio
import logging
import sqlite3
import threading
import secrets
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, render_template, request,
                   redirect, url_for, session, jsonify)
import os

# ==================== НАСТРОЙКИ ====================
ADMIN_USERNAME  = os.environ.get("ADMIN_USERNAME", "admin").strip()
ADMIN_PASSWORD  = os.environ.get("ADMIN_PASSWORD", "admin123").strip()
SECRET_KEY      = os.environ.get("SECRET_KEY", "taxi2024secret").strip()
PIN_EXPIRE_DAYS = 30
PORT            = int(os.environ.get("PORT", 5000))

# ==================== ТАРИФЫ ====================
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
            last_seen TEXT DEFAULT NULL
        )
    """)

    for col, definition in [
        ("balance",       "REAL DEFAULT 0.0"),
        ("online_status", "TEXT DEFAULT 'offline'"),
        ("last_seen",     "TEXT DEFAULT NULL"),
    ]:
        try:
            c.execute(f"ALTER TABLE drivers ADD COLUMN {col} {definition}")
        except:
            pass

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

    c.execute("""
        CREATE TABLE IF NOT EXISTS broadcasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message TEXT,
            sent_count INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

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

    conn.commit()
    conn.close()
    print("✅ База данных готова")

# ==================== ГЕНЕРАЦИЯ PIN ====================
def generate_pin():
    """Генерирует уникальный PIN для каждого водителя"""
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

def reset_driver(car_number):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE drivers
        SET status = 'pending',
            pin = NULL,
            pin_created_at = NULL,
            pin_expires_at = NULL,
            online_status = 'offline'
        WHERE car_number = ?
    """, (car_number.upper(),))
    conn.commit()
    conn.close()

def get_driver_by_car(car_number):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM drivers WHERE car_number = ?", (car_number.upper(),))
    driver = c.fetchone()
    conn.close()
    return driver

def get_all_drivers():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM drivers ORDER BY created_at DESC")
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

# ✅ Одобрить по car_number — каждый получает свой уникальный PIN
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

def get_driver_trips(car_number):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM trips
        WHERE car_number = ?
        ORDER BY created_at DESC
        LIMIT 50
    """, (car_number.upper(),))
    trips = c.fetchall()
    conn.close()
    return trips

def get_all_trips():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM trips ORDER BY created_at DESC LIMIT 100")
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
    conn.close()
    return stats

def get_logs():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM logs ORDER BY created_at DESC LIMIT 50")
    logs = c.fetchall()
    conn.close()
    return logs

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

@flask_app.route('/drivers')
@admin_required
def drivers():
    query = request.args.get('search', '')
    drivers_list = search_drivers(query) if query else get_all_drivers()
    return render_template('drivers.html', drivers=drivers_list, search=query)

@flask_app.route('/requests')
@admin_required
def requests_page():
    pending = get_pending_drivers()
    return render_template('requests.html', drivers=pending)

# ✅ ИСПРАВЛЕНО: одобряем только конкретного водителя по car_number
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
    return render_template('trips.html', trips=get_all_trips(), stats=get_stats())

@flask_app.route('/broadcast', methods=['GET', 'POST'])
@admin_required
def broadcast():
    return render_template('broadcast.html')

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

        # ✅ Ищем по car_number И pin одновременно
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

        return jsonify({
            "success": True,
            "driver": {
                "id":            driver['id'],
                "name":          driver['full_name'],
                "car":           driver['car_number'],
                "balance":       driver['balance'] if driver['balance'] else 0.0,
                "online_status": "online",
                "last_seen":     datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        }), 200

    except Exception as e:
        logging.error(f"Login error: {e}")
        return jsonify({"success": False, "error": "Ошибка сервера"}), 500


# ✅ Проверка статуса водителя (блокировка)
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

        # ✅ Ищем по car_number И pin одновременно
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

        return jsonify({
            "success": True,
            "driver": {
                "id":            driver['id'],
                "name":          driver['full_name'],
                "car":           driver['car_number'],
                "balance":       driver['balance'] if driver['balance'] else 0.0,
                "online_status": "online",
                "last_seen":     datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        }), 200

    except Exception as e:
        logging.error(f"Login error: {e}")
        return jsonify({"success": False, "error": "Ошибка сервера"}), 500


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

        conn = get_db()
        c    = conn.cursor()
        c.execute("""
            INSERT INTO trips
            (car_number, price, city_distance, suburb_distance,
             waiting_seconds, total_seconds, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            car_number, price, city_distance, suburb_distance,
            waiting_seconds, total_seconds,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        conn.commit()
        conn.close()

        add_log("trip", 0, 0,
            f"Авто: {car_number} | "
            f"Цена: {price:,} сум | "
            f"Км: {city_distance:.1f}+{suburb_distance:.1f}"
        )
        return jsonify({"success": True}), 200

    except Exception as e:
        logging.error(f"Trip error: {e}")
        return jsonify({"success": False, "error": "Ошибка сервера"}), 500


# ✅ Получить историю поездок водителя
@flask_app.route('/api/driver/trips/<car_number>', methods=['GET'])
def api_get_driver_trips(car_number):
    try:
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
        return jsonify({
            "success":     True,
            "base_fare":   TaxiConfig.BASE_FARE,
            "city_rate":   TaxiConfig.CITY_RATE,
            "suburb_rate": TaxiConfig.SUBURB_RATE,
            "wait_rate":   TaxiConfig.WAIT_RATE
        }), 200
    except Exception as e:
        logging.error(f"Tariffs error: {e}")
        return jsonify({"success": False}), 500


# ==================== ЗАПУСК ====================
init_db()
app = flask_app

if __name__ == "__main__":
    flask_app.run(host='0.0.0.0', port=PORT, debug=False)
