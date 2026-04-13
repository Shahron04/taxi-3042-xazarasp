import asyncio
import logging
import random
import sqlite3
import threading
from datetime import datetime, timedelta
from functools import wraps

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

from flask import (Flask, render_template_string, request,
                   redirect, url_for, session, jsonify)

import os

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = "8671480651:AAHxDVRUfULTSZRPMMvJ7NO5TfbSS1GqHiQ".strip()
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing or empty")

ADMIN_IDS = [int(x.strip()) for x in os.environ.get("ADMIN_IDS", "123456789").split(",") if x.strip()]
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin").strip()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123").strip()
SECRET_KEY = os.environ.get("SECRET_KEY", "taxi2024secret").strip()
PIN_EXPIRE_DAYS = 30
PORT = int(os.environ.get("PORT", 5000))

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
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
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
    conn.commit()
    conn.close()
    print("✅ База данных готова")

def generate_pin():
    return str(random.randint(1000, 9999))

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
        INSERT OR IGNORE INTO drivers 
        (tg_id, username, full_name, phone, car_number, status)
        VALUES (?, ?, ?, ?, ?, 'pending')
    """, (tg_id, username, full_name, phone, car_number))
    conn.commit()
    conn.close()

def get_driver(tg_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM drivers WHERE tg_id = ?", (tg_id,))
    driver = c.fetchone()
    conn.close()
    return driver

def get_driver_by_car(car_number):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM drivers 
        WHERE car_number = ?
    """, (car_number.upper(),))
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
    c.execute("""
        SELECT * FROM drivers 
        WHERE status = 'pending' AND is_blocked = 0
    """)
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

def approve_driver(tg_id):
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
        WHERE tg_id = ?
    """, (pin, now.strftime("%Y-%m-%d %H:%M:%S"),
          expires.strftime("%Y-%m-%d %H:%M:%S"), tg_id))
    conn.commit()
    conn.close()
    return pin

def reject_driver(tg_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE drivers SET status = 'rejected'
        WHERE tg_id = ?
    """, (tg_id,))
    conn.commit()
    conn.close()

def reject_driver_by_car(car_number):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE drivers SET status = 'rejected'
        WHERE car_number = ?
    """, (car_number.upper(),))
    conn.commit()
    conn.close()

def block_driver(tg_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE drivers SET is_blocked = 1
        WHERE tg_id = ?
    """, (tg_id,))
    conn.commit()
    conn.close()

def unblock_driver(tg_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE drivers SET is_blocked = 0
        WHERE tg_id = ?
    """, (tg_id,))
    conn.commit()
    conn.close()

def reset_pin(tg_id):
    pin = generate_pin()
    now = datetime.now()
    expires = now + timedelta(days=PIN_EXPIRE_DAYS)
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE drivers
        SET pin = ?,
            pin_created_at = ?,
            pin_expires_at = ?
        WHERE tg_id = ?
    """, (pin, now.strftime("%Y-%m-%d %H:%M:%S"),
          expires.strftime("%Y-%m-%d %H:%M:%S"), tg_id))
    conn.commit()
    conn.close()
    return pin

def search_drivers(query):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM drivers
        WHERE full_name LIKE ?
        OR car_number LIKE ?
        OR phone LIKE ?
        OR username LIKE ?
    """, (f"%{query}%", f"%{query}%",
          f"%{query}%", f"%{query}%"))
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
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("""
        SELECT COUNT(*) FROM drivers
        WHERE created_at LIKE ?
    """, (f"{today}%",))
    stats['today'] = c.fetchone()[0]
    conn.close()
    return stats

def get_logs():
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM logs
        ORDER BY created_at DESC LIMIT 50
    """)
    logs = c.fetchall()
    conn.close()
    return logs

def save_broadcast(message, sent_count):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO broadcasts (message, sent_count)
        VALUES (?, ?)
    """, (message, sent_count))
    conn.commit()
    conn.close()

# ==================== БОТ ====================
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

class DriverReg(StatesGroup):
    full_name = State()
    phone = State()
    car_number = State()

class BroadcastState(StatesGroup):
    message = State()

def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Регистрация")],
            [KeyboardButton(text="🔑 Мой PIN")],
            [KeyboardButton(text="📊 Мой статус")],
        ],
        resize_keyboard=True
    )

def phone_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(
                text="📱 Отправить номер",
                request_contact=True
            )]
        ],
        resize_keyboard=True
    )

def admin_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Заявки")],
            [KeyboardButton(text="👥 Все водители")],
            [KeyboardButton(text="📊 Статистика")],
            [KeyboardButton(text="📢 Рассылка")]
        ],
        resize_keyboard=True
    )

@dp.message(Command("start"))
async def start(message: types.Message):
    if message.from_user.id in ADMIN_IDS:
        await message.answer(
            "👋 Добро пожаловать, Администратор!",
            reply_markup=admin_keyboard()
        )
    else:
        await message.answer(
            "👋 Добро пожаловать!\n\n"
            "Для работы необходимо:\n"
            "1️⃣ Зарегистрироваться\n"
            "2️⃣ Дождаться одобрения\n"
            "3️⃣ Получить PIN-код",
            reply_markup=main_keyboard()
        )

@dp.message(lambda m: m.text == "📝 Регистрация")
async def registration(message: types.Message, state: FSMContext):
    if message.from_user.id in ADMIN_IDS:
        return
    driver = get_driver(message.from_user.id)
    if driver:
        if driver['is_blocked']:
            await message.answer("🚫 Аккаунт заблокирован!")
            return
        if driver['status'] == 'pending':
            await message.answer("⏳ Заявка на рассмотрении!")
            return
        elif driver['status'] == 'approved':
            await message.answer("✅ Вы уже зарегистрированы!")
            return
        elif driver['status'] == 'rejected':
            await message.answer("❌ Заявка отклонена!")
            return
    await message.answer("📝 Введите полное имя:")
    await state.set_state(DriverReg.full_name)

@dp.message(DriverReg.full_name)
async def get_full_name(message: types.Message, state: FSMContext):
    await state.update_data(full_name=message.text)
    await message.answer(
        "📱 Отправьте номер телефона:",
        reply_markup=phone_keyboard()
    )
    await state.set_state(DriverReg.phone)

@dp.message(DriverReg.phone)
async def get_phone(message: types.Message, state: FSMContext):
    if message.contact:
        phone = message.contact.phone_number
    else:
        phone = message.text
    await state.update_data(phone=phone)
    await message.answer(
        "🚗 Введите номер автомобиля:",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(DriverReg.car_number)

@dp.message(DriverReg.car_number)
async def get_car_number(message: types.Message, state: FSMContext):
    data = await state.get_data()
    add_driver(
        tg_id=message.from_user.id,
        username=message.from_user.username or "нет",
        full_name=data['full_name'],
        phone=data['phone'],
        car_number=message.text
    )
    await state.clear()
    await message.answer(
        "✅ Заявка отправлена!\n"
        "⏳ Ожидайте одобрения",
        reply_markup=main_keyboard()
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"🆕 Новая заявка!\n\n"
                f"👤 {data['full_name']}\n"
                f"📱 {data['phone']}\n"
                f"🚗 {message.text}\n"
                f"🔗 @{message.from_user.username}\n\n"
                f"✅ /approve_{message.from_user.id}\n"
                f"❌ /reject_{message.from_user.id}"
            )
        except:
            pass

@dp.message(lambda m: m.text == "🔑 Мой PIN")
async def my_pin(message: types.Message):
    driver = get_driver(message.from_user.id)
    if not driver:
        await message.answer("❌ Вы не зарегистрированы!")
        return
    if driver['is_blocked']:
        await message.answer("🚫 Аккаунт заблокирован!")
        return
    if driver['status'] == 'approved':
        await message.answer(
            f"🔑 PIN-код: *{driver['pin']}*\n"
            f"⏰ До: {driver['pin_expires_at']}",
            parse_mode="Markdown"
        )
    elif driver['status'] == 'pending':
        await message.answer("⏳ Заявка на рассмотрении")
    else:
        await message.answer("❌ Заявка отклонена")

@dp.message(lambda m: m.text == "📊 Мой статус")
async def my_status(message: types.Message):
    driver = get_driver(message.from_user.id)
    if not driver:
        await message.answer("❌ Вы не зарегистрированы!")
        return
    status_text = {
        'pending': '⏳ На рассмотрении',
        'approved': '✅ Одобрен',
        'rejected': '❌ Отклонен'
    }
    blocked = "🚫" if driver['is_blocked'] else ""
    await message.answer(
        f"📊 Статус: {status_text[driver['status']]} {blocked}\n\n"
        f"👤 {driver['full_name']}\n"
        f"📱 {driver['phone']}\n"
        f"🚗 {driver['car_number']}\n"
        f"📅 {driver['created_at']}"
    )

@dp.message(lambda m: m.text == "📝 Заявки")
async def pending_list(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    drivers = get_pending_drivers()
    if not drivers:
        await message.answer("📭 Нет новых заявок")
        return
    for driver in drivers:
        await message.answer(
            f"🆕 Заявка\n\n"
            f"👤 {driver['full_name']}\n"
            f"📱 {driver['phone']}\n"
            f"🚗 {driver['car_number']}\n"
            f"🔗 @{driver['username']}\n\n"
            f"✅ /approve_{driver['tg_id']}\n"
            f"❌ /reject_{driver['tg_id']}"
        )

@dp.message(lambda m: m.text == "👥 Все водители")
async def all_drivers(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    drivers = get_all_drivers()
    if not drivers:
        await message.answer("📭 Нет водителей")
        return
    text = "👥 Водители:\n\n"
    for driver in drivers:
        status = {
            'pending': '⏳',
            'approved': '✅',
            'rejected': '❌'
        }.get(driver['status'], '❓')
        blocked = "🚫" if driver['is_blocked'] else ""
        text += (f"{status}{blocked} {driver['full_name']} "
                 f"| {driver['car_number']}\n"
                 f"    /info_{driver['tg_id']}\n\n")
    await message.answer(text)

@dp.message(lambda m: m.text == "📊 Статистика")
async def statistics(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    stats = get_stats()
    await message.answer(
        f"📊 Статистика:\n\n"
        f"👥 Всего: {stats['total']}\n"
        f"✅ Одобрено: {stats['approved']}\n"
        f"⏳ Ожидают: {stats['pending']}\n"
        f"❌ Отклонено: {stats['rejected']}\n"
        f"🚫 Заблокировано: {stats['blocked']}\n"
        f"📅 Сегодня: {stats['today']}"
    )

@dp.message(lambda m: m.text == "📢 Рассылка")
async def broadcast_start(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer("📢 Введите сообщение:")
    await state.set_state(BroadcastState.message)

@dp.message(BroadcastState.message)
async def broadcast_send(message: types.Message, state: FSMContext):
    await state.clear()
    drivers = get_all_drivers()
    sent = 0
    failed = 0
    for driver in drivers:
        if driver['status'] == 'approved' and not driver['is_blocked']:
            try:
                await bot.send_message(
                    driver['tg_id'],
                    f"📢 Сообщение:\n\n{message.text}"
                )
                sent += 1
            except:
                failed += 1
    save_broadcast(message.text, sent)
    await message.answer(
        f"✅ Отправлено: {sent}\n❌ Ошибок: {failed}"
    )

@dp.message(Command("pending"))
async def pending_cmd(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await pending_list(message)

@dp.message(lambda m: m.text and m.text.startswith("/approve_car_"))
async def approve_by_car(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    car_number = message.text.split("_car_")[1]
    pin = approve_driver_by_car(car_number)
    add_log("approve", message.from_user.id, 0, f"Авто: {car_number} PIN: {pin}")
    await message.answer(
        f"✅ Водитель одобрен!\n"
        f"🚗 Авто: {car_number}\n"
        f"🔑 PIN: {pin}\n\n"
        f"Водитель получит PIN при следующем входе в APK"
    )

@dp.message(lambda m: m.text and m.text.startswith("/reject_car_"))
async def reject_by_car(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    car_number = message.text.split("_car_")[1]
    reject_driver_by_car(car_number)
    add_log("reject", message.from_user.id, 0, f"Авто: {car_number}")
    await message.answer(f"❌ Водитель отклонён!\n🚗 Авто: {car_number}")

@dp.message(lambda m: m.text and m.text.startswith("/approve_"))
async def approve(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    tg_id = int(message.text.split("_")[1])
    pin = approve_driver(tg_id)
    add_log("approve", message.from_user.id, tg_id, f"PIN: {pin}")
    await message.answer(f"✅ Одобрен! PIN: {pin}")
    try:
        await bot.send_message(
            tg_id,
            f"✅ Заявка одобрена!\n\n🔑 PIN: *{pin}*",
            parse_mode="Markdown"
        )
    except:
        pass

@dp.message(lambda m: m.text and m.text.startswith("/reject_"))
async def reject(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    tg_id = int(message.text.split("_")[1])
    reject_driver(tg_id)
    add_log("reject", message.from_user.id, tg_id, "Отклонен")
    await message.answer("❌ Отклонен!")
    try:
        await bot.send_message(tg_id, "❌ Заявка отклонена!")
    except:
        pass

@dp.message(lambda m: m.text and m.text.startswith("/info_"))
async def driver_info(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    tg_id = int(message.text.split("_")[1])
    driver = get_driver(tg_id)
    if not driver:
        await message.answer("❌ Не найден")
        return
    status_text = {
        'pending': '⏳',
        'approved': '✅',
        'rejected': '❌'
    }
    await message.answer(
        f"👤 {driver['full_name']}\n"
        f"📱 {driver['phone']}\n"
        f"🚗 {driver['car_number']}\n"
        f"Статус: {status_text[driver['status']]}\n"
        f"PIN: {driver['pin'] or 'нет'}\n"
        f"До: {driver['pin_expires_at'] or 'нет'}\n"
        f"Блок: {'Да 🚫' if driver['is_blocked'] else 'Нет'}\n\n"
        f"🔄 /resetpin_{tg_id}\n"
        f"🚫 /block_{tg_id}\n"
        f"✅ /unblock_{tg_id}"
    )

@dp.message(lambda m: m.text and m.text.startswith("/block_"))
async def block(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    tg_id = int(message.text.split("_")[1])
    block_driver(tg_id)
    add_log("block", message.from_user.id, tg_id, "Заблокирован")
    await message.answer("🚫 Заблокирован!")
    try:
        await bot.send_message(tg_id, "🚫 Аккаунт заблокирован!")
    except:
        pass

@dp.message(lambda m: m.text and m.text.startswith("/unblock_"))
async def unblock(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    tg_id = int(message.text.split("_")[1])
    unblock_driver(tg_id)
    add_log("unblock", message.from_user.id, tg_id, "Разблокирован")
    await message.answer("✅ Разблокирован!")
    try:
        await bot.send_message(tg_id, "✅ Аккаунт разблокирован!")
    except:
        pass

@dp.message(lambda m: m.text and m.text.startswith("/resetpin_"))
async def reset_pin_cmd(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    tg_id = int(message.text.split("_")[1])
    pin = reset_pin(tg_id)
    add_log("reset_pin", message.from_user.id, tg_id, f"PIN: {pin}")
    await message.answer(f"🔄 Новый PIN: {pin}")
    try:
        await bot.send_message(
            tg_id,
            f"🔄 PIN сброшен!\n\n🔑 Новый PIN: *{pin}*",
            parse_mode="Markdown"
        )
    except:
        pass

# ==================== FLASK ====================
flask_app = Flask(__name__)
flask_app.secret_key = SECRET_KEY

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'admin' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ---------- HTML ШАБЛОНЫ (встроенные) ----------
LOGIN_HTML = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Вход в админку</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
    <div class="container mt-5">
        <div class="row justify-content-center">
            <div class="col-md-4">
                <div class="card shadow">
                    <div class="card-header bg-primary text-white">
                        <h4 class="mb-0">Вход в панель управления</h4>
                    </div>
                    <div class="card-body">
                        {% if error %}
                            <div class="alert alert-danger">{{ error }}</div>
                        {% endif %}
                        <form method="post">
                            <div class="mb-3">
                                <label class="form-label">Логин</label>
                                <input type="text" name="username" class="form-control" required>
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Пароль</label>
                                <input type="password" name="password" class="form-control" required>
                            </div>
                            <button type="submit" class="btn btn-primary w-100">Войти</button>
                        </form>
                    </div>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
'''

DASHBOARD_HTML = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Дашборд | Админка такси</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
</head>
<body>
    <nav class="navbar navbar-dark bg-dark">
        <div class="container-fluid">
            <span class="navbar-brand">🚖 Админ-панель такси</span>
            <a href="/logout" class="btn btn-outline-light">Выйти</a>
        </div>
    </nav>
    <div class="container mt-4">
        <h2>Главная</h2>
        <div class="row mt-4">
            <div class="col-md-3 mb-3">
                <div class="card text-white bg-primary">
                    <div class="card-body">
                        <h5 class="card-title">Всего водителей</h5>
                        <h2>{{ stats.total }}</h2>
                    </div>
                </div>
            </div>
            <div class="col-md-3 mb-3">
                <div class="card text-white bg-success">
                    <div class="card-body">
                        <h5 class="card-title">Одобрено</h5>
                        <h2>{{ stats.approved }}</h2>
                    </div>
                </div>
            </div>
            <div class="col-md-3 mb-3">
                <div class="card text-white bg-warning">
                    <div class="card-body">
                        <h5 class="card-title">Ожидают</h5>
                        <h2>{{ stats.pending }}</h2>
                    </div>
                </div>
            </div>
            <div class="col-md-3 mb-3">
                <div class="card text-white bg-danger">
                    <div class="card-body">
                        <h5 class="card-title">Заблокировано</h5>
                        <h2>{{ stats.blocked }}</h2>
                    </div>
                </div>
            </div>
        </div>
        <div class="row mt-2">
            <div class="col-md-4">
                <div class="card">
                    <div class="card-body">
                        <h5><i class="fas fa-list"></i> Заявки</h5>
                        <p>Новых: {{ stats.pending }}</p>
                        <a href="/requests" class="btn btn-sm btn-primary">Просмотреть</a>
                    </div>
                </div>
            </div>
            <div class="col-md-4">
                <div class="card">
                    <div class="card-body">
                        <h5><i class="fas fa-users"></i> Водители</h5>
                        <p>Всего: {{ stats.total }}</p>
                        <a href="/drivers" class="btn btn-sm btn-primary">Список</a>
                    </div>
                </div>
            </div>
            <div class="col-md-4">
                <div class="card">
                    <div class="card-body">
                        <h5><i class="fas fa-chart-line"></i> Статистика</h5>
                        <p>Подробные отчёты</p>
                        <a href="/stats" class="btn btn-sm btn-primary">Перейти</a>
                    </div>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
'''

DRIVERS_HTML = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Водители</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body>
    <nav class="navbar navbar-dark bg-dark">
        <div class="container-fluid">
            <a href="/" class="navbar-brand">🚖 Назад</a>
            <a href="/logout" class="btn btn-outline-light">Выйти</a>
        </div>
    </nav>
    <div class="container mt-4">
        <h2>Список водителей</h2>
        <form method="get" class="mb-3">
            <div class="input-group">
                <input type="text" name="search" class="form-control" placeholder="Поиск по имени, машине, телефону..." value="{{ search }}">
                <button class="btn btn-primary" type="submit">Найти</button>
            </div>
        </form>
        <table class="table table-bordered">
            <thead>
                <tr><th>ID</th><th>Имя</th><th>Машина</th><th>Телефон</th><th>Статус</th><th>PIN</th><th>Действия</th></tr>
            </thead>
            <tbody>
                {% for d in drivers %}
                <tr>
                    <td>{{ d.id }}</td><td>{{ d.full_name }}</td><td>{{ d.car_number }}</td><td>{{ d.phone }}</td>
                    <td>{% if d.status == 'approved' %}✅ Одобрен{% elif d.status == 'pending' %}⏳ Ожидает{% else %}❌ Отклонён{% endif %} {% if d.is_blocked %}🚫{% endif %}</td>
                    <td>{{ d.pin or '-' }}</td>
                    <td>
                        <a href="/block/{{ d.tg_id }}" class="btn btn-sm btn-danger" onclick="return confirm('Блокировать?')">Блок</a>
                        <a href="/unblock/{{ d.tg_id }}" class="btn btn-sm btn-success" onclick="return confirm('Разблокировать?')">Разблок</a>
                        <a href="/reset_pin/{{ d.tg_id }}" class="btn btn-sm btn-warning">Сброс PIN</a>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</body>
</html>
'''

REQUESTS_HTML = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Заявки</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body>
    <nav class="navbar navbar-dark bg-dark">
        <div class="container-fluid">
            <a href="/" class="navbar-brand">🚖 Назад</a>
            <a href="/logout" class="btn btn-outline-light">Выйти</a>
        </div>
    </nav>
    <div class="container mt-4">
        <h2>Новые заявки</h2>
        {% if drivers %}
            <table class="table">
                <thead><tr><th>Имя</th><th>Телефон</th><th>Авто</th><th>Действия</th></tr></thead>
                <tbody>
                {% for d in drivers %}
                <tr><td>{{ d.full_name }}</td><td>{{ d.phone }}</td><td>{{ d.car_number }}</td>
                    <td><a href="/approve/{{ d.tg_id }}" class="btn btn-sm btn-success">Одобрить</a>
                        <a href="/reject/{{ d.tg_id }}" class="btn btn-sm btn-danger">Отклонить</a></td>
                </tr>
                {% endfor %}
                </tbody>
            </table>
        {% else %}
            <div class="alert alert-info">Нет новых заявок</div>
        {% endif %}
    </div>
</body>
</html>
'''

STATS_HTML = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Статистика</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body>
    <nav class="navbar navbar-dark bg-dark">
        <div class="container-fluid">
            <a href="/" class="navbar-brand">🚖 Назад</a>
            <a href="/logout" class="btn btn-outline-light">Выйти</a>
        </div>
    </nav>
    <div class="container mt-4">
        <h2>Статистика</h2>
        <ul class="list-group mb-4">
            <li class="list-group-item">Всего: {{ stats.total }}</li>
            <li class="list-group-item">Одобрено: {{ stats.approved }}</li>
            <li class="list-group-item">Ожидают: {{ stats.pending }}</li>
            <li class="list-group-item">Отклонено: {{ stats.rejected }}</li>
            <li class="list-group-item">Заблокировано: {{ stats.blocked }}</li>
            <li class="list-group-item">Сегодня: {{ stats.today }}</li>
        </ul>
        <h3>Последние логи</h3>
        <table class="table table-sm">
            <thead><tr><th>Время</th><th>Действие</th><th>Детали</th></tr></thead>
            <tbody>
                {% for log in logs %}
                <tr><td>{{ log.created_at }}</td><td>{{ log.action }}</td><td>{{ log.details }}</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</body>
</html>
'''

BROADCAST_HTML = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Рассылка</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body>
    <nav class="navbar navbar-dark bg-dark">
        <div class="container-fluid">
            <a href="/" class="navbar-brand">🚖 Назад</a>
            <a href="/logout" class="btn btn-outline-light">Выйти</a>
        </div>
    </nav>
    <div class="container mt-4">
        <h2>Рассылка сообщений водителям</h2>
        {% if success %}
            <div class="alert alert-success">✅ Отправлено: {{ sent }}, ошибок: {{ failed }}</div>
        {% endif %}
        <form method="post">
            <div class="mb-3">
                <label class="form-label">Текст сообщения</label>
                <textarea name="message" rows="5" class="form-control" required></textarea>
            </div>
            <button type="submit" class="btn btn-primary">Отправить</button>
        </form>
    </div>
</body>
</html>
'''

@flask_app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if (request.form.get('username') == ADMIN_USERNAME and
                request.form.get('password') == ADMIN_PASSWORD):
            session['admin'] = True
            return redirect(url_for('dashboard'))
        return render_template_string(LOGIN_HTML, error="Неверный логин или пароль")
    return render_template_string(LOGIN_HTML)

@flask_app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@flask_app.route('/')
@admin_required
def dashboard():
    return render_template_string(DASHBOARD_HTML, stats=get_stats())

@flask_app.route('/drivers')
@admin_required
def drivers():
    query = request.args.get('search', '')
    drivers_list = search_drivers(query) if query else get_all_drivers()
    return render_template_string(DRIVERS_HTML, drivers=drivers_list, search=query)

@flask_app.route('/requests')
@admin_required
def requests_page():
    return render_template_string(REQUESTS_HTML, drivers=get_pending_drivers())

@flask_app.route('/approve/<int:tg_id>')
@admin_required
def web_approve(tg_id):
    pin = approve_driver(tg_id)
    add_log("approve", 0, tg_id, f"PIN: {pin}")
    return redirect(url_for('requests_page'))

@flask_app.route('/reject/<int:tg_id>')
@admin_required
def web_reject(tg_id):
    reject_driver(tg_id)
    add_log("reject", 0, tg_id, "Отклонен")
    return redirect(url_for('requests_page'))

@flask_app.route('/block/<int:tg_id>')
@admin_required
def web_block(tg_id):
    block_driver(tg_id)
    add_log("block", 0, tg_id, "Заблокирован")
    return redirect(url_for('drivers'))

@flask_app.route('/unblock/<int:tg_id>')
@admin_required
def web_unblock(tg_id):
    unblock_driver(tg_id)
    add_log("unblock", 0, tg_id, "Разблокирован")
    return redirect(url_for('drivers'))

@flask_app.route('/reset_pin/<int:tg_id>')
@admin_required
def web_reset_pin(tg_id):
    pin = reset_pin(tg_id)
    add_log("reset_pin", 0, tg_id, f"PIN: {pin}")
    return redirect(url_for('drivers'))

@flask_app.route('/stats')
@admin_required
def stats():
    return render_template_string(STATS_HTML, stats=get_stats(), logs=get_logs())

@flask_app.route('/broadcast', methods=['GET', 'POST'])
@admin_required
def broadcast():
    if request.method == 'POST':
        msg = request.form.get('message')
        sent = 0
        failed = 0
        async def send_all():
            nonlocal sent, failed
            for driver in get_all_drivers():
                if (driver['status'] == 'approved'
                        and not driver['is_blocked']):
                    try:
                        await bot.send_message(
                            driver['tg_id'],
                            f"📢 Сообщение:\n\n{msg}"
                        )
                        sent += 1
                    except:
                        failed += 1
        asyncio.run(send_all())
        save_broadcast(msg, sent)
        return render_template_string(BROADCAST_HTML, success=True, sent=sent, failed=failed)
    return render_template_string(BROADCAST_HTML)

# ==================== API для APK ====================
@flask_app.route('/api/driver/register', methods=['POST'])
def api_register():
    try:
        data = request.get_json()
        name = data.get('name', '').strip()
        car_number = data.get('car_number', '').strip().upper()
        phone = data.get('phone', '').strip()

        if not name or not car_number:
            return jsonify({"success": False, "error": "Заполните все поля"}), 400

        driver = get_driver_by_car(car_number)
        if driver:
            if driver['status'] == 'approved':
                return jsonify({"success": False, "error": "Вы уже зарегистрированы"}), 200
            elif driver['status'] == 'pending':
                return jsonify({"success": False, "error": "Заявка уже отправлена"}), 200
            elif driver['status'] == 'rejected':
                return jsonify({"success": False, "error": "Ваша заявка отклонена"}), 200

        add_driver(tg_id=0, username=name, full_name=name, phone=phone, car_number=car_number)

        async def notify():
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        admin_id,
                        f"🆕 Новая заявка из APK!\n\n👤 Имя: {name}\n🚗 Авто: {car_number}\n📱 Device: {phone}\n\n✅ /approve_car_{car_number}\n❌ /reject_car_{car_number}"
                    )
                except:
                    pass
        asyncio.run(notify())
        return jsonify({"success": True, "message": "Заявка отправлена! Ожидайте PIN"}), 200
    except Exception as e:
        logging.error(f"Register error: {e}")
        return jsonify({"success": False, "error": "Ошибка сервера"}), 500

@flask_app.route('/api/driver/login', methods=['POST'])
def api_login():
    try:
        data = request.get_json()
        car_number = data.get('car_number', '').strip().upper()
        pin = data.get('pin', '').strip()
        if not car_number or not pin:
            return jsonify({"success": False, "error": "Заполните все поля"}), 400
        driver = get_driver_by_car(car_number)
        if not driver:
            return jsonify({"success": False, "error": "Водитель не найден"}), 404
        if driver['is_blocked']:
            return jsonify({"success": False, "error": "Аккаунт заблокирован"}), 403
        if driver['status'] != 'approved':
            return jsonify({"success": False, "error": "Заявка ещё не одобрена"}), 403
        if driver['pin'] != pin:
            return jsonify({"success": False, "error": "Неверный PIN код"}), 401
        return jsonify({
            "success": True,
            "driver": {"id": driver['id'], "name": driver['full_name'], "car": driver['car_number'], "balance": 0.0}
        }), 200
    except Exception as e:
        logging.error(f"Login error: {e}")
        return jsonify({"success": False, "error": "Ошибка сервера"}), 500

# ==================== ЗАПУСК ====================
def run_bot():
    async def start_bot():
        logging.basicConfig(level=logging.INFO)
        await dp.start_polling(bot)
    asyncio.run(start_bot())

app = flask_app
# ГАРАНТИРОВАННАЯ ИНИЦИАЛИЗАЦИЯ БД
init_db()

if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.daemon = True
    bot_thread.start()
    flask_app.run(host='0.0.0.0', port=PORT, debug=False)
