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
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

from flask import (Flask, render_template, request,
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
    c.execute("UPDATE drivers SET status = 'rejected' WHERE tg_id = ?", (tg_id,))
    conn.commit()
    conn.close()

def reject_driver_by_car(car_number):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE drivers SET status = 'rejected' WHERE car_number = ?", (car_number.upper(),))
    conn.commit()
    conn.close()

def block_driver(tg_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE drivers SET is_blocked = 1 WHERE tg_id = ?", (tg_id,))
    conn.commit()
    conn.close()

def unblock_driver(tg_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE drivers SET is_blocked = 0 WHERE tg_id = ?", (tg_id,))
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
        SET pin = ?, pin_created_at = ?, pin_expires_at = ?
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
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("SELECT COUNT(*) FROM drivers WHERE created_at LIKE ?", (f"{today}%",))
    stats['today'] = c.fetchone()[0]
    conn.close()
    return stats

def get_logs():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM logs ORDER BY created_at DESC LIMIT 50")
    logs = c.fetchall()
    conn.close()
    return logs

def save_broadcast(message, sent_count):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO broadcasts (message, sent_count) VALUES (?, ?)", (message, sent_count))
    conn.commit()
    conn.close()

# ==================== БОТ ====================
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

class DriverReg(StatesGroup):
    full_name  = State()
    phone      = State()
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
        keyboard=[[KeyboardButton(text="📱 Отправить номер", request_contact=True)]],
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

# ==================== КОМАНДЫ БОТА ====================

@dp.message(Command("start"))
async def start(message: types.Message):
    if message.from_user.id in ADMIN_IDS:
        await message.answer("👋 Добро пожаловать, Администратор!", reply_markup=admin_keyboard())
    else:
        await message.answer(
            "👋 Добро пожаловать!\n\n"
            "Для работы необходимо:\n"
            "1️⃣ Зарегистрироваться\n"
            "2️⃣ Дождаться одобрения\n"
            "3️⃣ Получить PIN-код",
            reply_markup=main_keyboard()
        )

# ==================== CALLBACK КНОПКИ ====================

@dp.callback_query(lambda c: c.data and c.data.startswith("approve_"))
async def callback_approve(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа!")
        return

    car_number = callback.data.replace("approve_", "", 1)
    pin = approve_driver_by_car(car_number)
    add_log("approve", callback.from_user.id, 0, f"Авто: {car_number} PIN: {pin}")

    await callback.message.edit_text(
        f"✅ Водитель одобрен!\n\n"
        f"🚗 Авто: {car_number}\n"
        f"🔑 PIN: <b>{pin}</b>\n\n"
        f"Водитель может войти в приложение",
        parse_mode="HTML"
    )
    await callback.answer("✅ Одобрено!")

@dp.callback_query(lambda c: c.data and c.data.startswith("reject_"))
async def callback_reject(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа!")
        return

    car_number = callback.data.replace("reject_", "", 1)
    reject_driver_by_car(car_number)
    add_log("reject", callback.from_user.id, 0, f"Авто: {car_number}")

    await callback.message.edit_text(
        f"❌ Водитель отклонён!\n\n"
        f"🚗 Авто: {car_number}"
    )
    await callback.answer("❌ Отклонено!")

# ==================== РЕГИСТРАЦИЯ ====================

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
    await message.answer("📱 Отправьте номер телефона:", reply_markup=phone_keyboard())
    await state.set_state(DriverReg.phone)

@dp.message(DriverReg.phone)
async def get_phone(message: types.Message, state: FSMContext):
    phone = message.contact.phone_number if message.contact else message.text
    await state.update_data(phone=phone)
    await message.answer("🚗 Введите номер автомобиля:", reply_markup=types.ReplyKeyboardRemove())
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
    await message.answer("✅ Заявка отправлена!\n⏳ Ожидайте одобрения", reply_markup=main_keyboard())

    # ✅ Inline кнопки для админа
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{message.text.upper()}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{message.text.upper()}")
        ]
    ])
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"🆕 Новая заявка!\n\n"
                f"👤 {data['full_name']}\n"
                f"📱 {data['phone']}\n"
                f"🚗 {message.text}\n"
                f"🔗 @{message.from_user.username}",
                reply_markup=keyboard
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
            f"🔑 PIN-код: <b>{driver['pin']}</b>\n"
            f"⏰ До: {driver['pin_expires_at']}",
            parse_mode="HTML"
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
        'pending':  '⏳ На рассмотрении',
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

# ==================== АДМИН ====================

@dp.message(lambda m: m.text == "📝 Заявки")
async def pending_list(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    drivers = get_pending_drivers()
    if not drivers:
        await message.answer("📭 Нет новых заявок")
        return
    for driver in drivers:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Одобрить",
                    callback_data=f"approve_{driver['car_number']}"
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"reject_{driver['car_number']}"
                )
            ]
        ])
        await message.answer(
            f"🆕 Заявка\n\n"
            f"👤 {driver['full_name']}\n"
            f"📱 {driver['phone']}\n"
            f"🚗 {driver['car_number']}\n"
            f"🔗 @{driver['username']}",
            reply_markup=keyboard
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
        status = {'pending': '⏳', 'approved': '✅', 'rejected': '❌'}.get(driver['status'], '❓')
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
                await bot.send_message(driver['tg_id'], f"📢 Сообщение:\n\n{message.text}")
                sent += 1
            except:
                failed += 1
    save_broadcast(message.text, sent)
    await message.answer(f"✅ Отправлено: {sent}\n❌ Ошибок: {failed}")

@dp.message(lambda m: m.text and m.text.startswith("/info_"))
async def driver_info(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    tg_id = int(message.text.split("_")[1])
    driver = get_driver(tg_id)
    if not driver:
        await message.answer("❌ Не найден")
        return
    status_text = {'pending': '⏳', 'approved': '✅', 'rejected': '❌'}
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
            f"🔄 PIN сброшен!\n\n🔑 Новый PIN: <b>{pin}</b>",
            parse_mode="HTML"
        )
    except:
        pass

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
    return render_template('requests.html', drivers=get_pending_drivers())

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
    return render_template('stats.html', stats=get_stats(), logs=get_logs())

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
                if driver['status'] == 'approved' and not driver['is_blocked']:
                    try:
                        await bot.send_message(driver['tg_id'], f"📢 Сообщение:\n\n{msg}")
                        sent += 1
                    except:
                        failed += 1
        asyncio.run(send_all())
        save_broadcast(msg, sent)
        return render_template('broadcast.html', success=True, sent=sent, failed=failed)
    return render_template('broadcast.html')

# ==================== API для APK ====================

@flask_app.route('/api/driver/register', methods=['POST'])
def api_register():
    try:
        data = request.get_json()
        name       = data.get('name', '').strip()
        car_number = data.get('car_number', '').strip().upper()
        phone      = data.get('phone', '').strip()

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

        # ✅ Уведомление с кнопками
        async def notify():
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Одобрить",
                        callback_data=f"approve_{car_number}"
                    ),
                    InlineKeyboardButton(
                        text="❌ Отклонить",
                        callback_data=f"reject_{car_number}"
                    )
                ]
            ])
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        admin_id,
                        f"🆕 Новая заявка из APK!\n\n"
                        f"👤 Имя: {name}\n"
                        f"🚗 Авто: {car_number}\n"
                        f"📱 Device: {phone}",
                        reply_markup=keyboard
                    )
                except Exception as e:
                    logging.error(f"Notify error: {e}")

        asyncio.run(notify())

        return jsonify({"success": True, "message": "Заявка отправлена! Ожидайте PIN"}), 200

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
            "driver": {
                "id":      driver['id'],
                "name":    driver['full_name'],
                "car":     driver['car_number'],
                "balance": 0.0
            }
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


# ✅ Инициализация БД (вызывается при запуске gunicorn)
init_db()

# ✅ Запуск бота в отдельном потоке
bot_thread = threading.Thread(target=run_bot)
bot_thread.daemon = True
bot_thread.start()

# ✅ Псевдоним для gunicorn
app = flask_app

if __name__ == "__main__":
    flask_app.run(host='0.0.0.0', port=PORT, debug=False)
