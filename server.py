import asyncio
import logging
import threading
import random
from datetime import datetime, timedelta
from functools import wraps

import sqlite3
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

from flask import Flask, render_template, request, redirect, url_for, session
from flask import jsonify

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = "8671480651:AAHxDVRUfULTSZRPMMvJ7NO5TfbSS1GqHiQ"
ADMIN_IDS = [1053431273]
ADMIN_USERNAME = "shahron04"
ADMIN_PASSWORD = "ABD03040909"
SECRET_KEY = "taxi2024secret"
PIN_EXPIRE_DAYS = 30

# ==================== БАЗА ДАННЫХ ====================
def get_db():
    conn = sqlite3.connect("taxi.db")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS drivers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER UNIQUE,
            username TEXT,
            full_name TEXT,
            phone TEXT,
            car_number TEXT,
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
        INSERT OR REPLACE INTO drivers 
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
        WHERE status = 'pending' 
        AND is_blocked = 0
    """)
    drivers = c.fetchall()
    conn.close()
    return drivers

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

# ==================== FLASK ====================
app = Flask(__name__)
app.secret_key = SECRET_KEY

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'admin' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['admin'] = True
            return redirect(url_for('dashboard'))
        return render_template('login.html', error="Неверный логин или пароль")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
@admin_required
def dashboard():
    stats = get_stats()
    return render_template('dashboard.html', stats=stats)

@app.route('/drivers')
@admin_required
def drivers():
    query = request.args.get('search', '')
    if query:
        drivers_list = search_drivers(query)
    else:
        drivers_list = get_all_drivers()
    return render_template('drivers.html', 
                         drivers=drivers_list, 
                         search=query)

@app.route('/requests')
@admin_required
def requests_page():
    drivers_list = get_pending_drivers()
    return render_template('requests.html', drivers=drivers_list)

@app.route('/approve/<int:tg_id>')
@admin_required
def web_approve(tg_id):
    pin = approve_driver(tg_id)
    add_log("approve", 0, tg_id, f"PIN: {pin}")
    asyncio.run(send_message(
        tg_id,
        f"✅ Ваша заявка одобрена!\n\n"
        f"🔑 Ваш PIN-код: *{pin}*\n\n"
        f"Используйте его для входа в приложение",
        parse_mode="Markdown"
    ))
    return redirect(url_for('requests_page'))

@app.route('/reject/<int:tg_id>')
@admin_required
def web_reject(tg_id):
    reject_driver(tg_id)
    add_log("reject", 0, tg_id, "Отклонен")
    asyncio.run(send_message(tg_id, "❌ Ваша заявка отклонена!"))
    return redirect(url_for('requests_page'))

@app.route('/block/<int:tg_id>')
@admin_required
def web_block(tg_id):
    block_driver(tg_id)
    add_log("block", 0, tg_id, "Заблокирован")
    asyncio.run(send_message(tg_id, "🚫 Ваш аккаунт заблокирован!"))
    return redirect(url_for('drivers'))

@app.route('/unblock/<int:tg_id>')
@admin_required
def web_unblock(tg_id):
    unblock_driver(tg_id)
    add_log("unblock", 0, tg_id, "Разблокирован")
    asyncio.run(send_message(tg_id, "✅ Ваш аккаунт разблокирован!"))
    return redirect(url_for('drivers'))

@app.route('/reset_pin/<int:tg_id>')
@admin_required
def web_reset_pin(tg_id):
    pin = reset_pin(tg_id)
    add_log("reset_pin", 0, tg_id, f"Новый PIN: {pin}")
    asyncio.run(send_message(
        tg_id,
        f"🔄 Ваш PIN сброшен!\n\n🔑 Новый PIN: *{pin}*",
        parse_mode="Markdown"
    ))
    return redirect(url_for('drivers'))

@app.route('/stats')
@admin_required
def stats():
    stats_data = get_stats()
    logs = get_logs()
    return render_template('stats.html', 
                         stats=stats_data, 
                         logs=logs)

@app.route('/broadcast', methods=['GET', 'POST'])
@admin_required
def broadcast():
    if request.method == 'POST':
        message_text = request.form.get('message')
        drivers_list = get_all_drivers()
        sent = 0
        failed = 0

        async def send_all():
            nonlocal sent, failed
            temp_bot = Bot(token=BOT_TOKEN)
            for driver in drivers_list:
                if (driver['status'] == 'approved' 
                        and not driver['is_blocked']):
                    try:
                        await temp_bot.send_message(
                            driver['tg_id'],
                            f"📢 Сообщение:\n\n{message_text}"
                        )
                        sent += 1
                    except:
                        failed += 1
            await temp_bot.session.close()

        asyncio.run(send_all())
        save_broadcast(message_text, sent)
        return render_template('broadcast.html',
                             success=True,
                             sent=sent,
                             failed=failed)
    return render_template('broadcast.html')

# ==================== ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ====================
async def send_message(tg_id, text, parse_mode=None):
    temp_bot = Bot(token=BOT_TOKEN)
    try:
        await temp_bot.send_message(tg_id, text, 
                                   parse_mode=parse_mode)
    except Exception as e:
        logging.error(f"Ошибка отправки: {e}")
    finally:
        await temp_bot.session.close()

# ==================== ТЕЛЕГРАМ БОТ ====================
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
            [KeyboardButton(text="🔄 Обновить данные")]
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
            "👋 Добро пожаловать в сервис такси!\n\n"
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
            await message.answer("🚫 Ваш аккаунт заблокирован!")
            return
        if driver['status'] == 'pending':
            await message.answer("⏳ Заявка уже на рассмотрении!")
            return
        elif driver['status'] == 'approved':
            await message.answer("✅ Вы уже зарегистрированы!")
            return
        elif driver['status'] == 'rejected':
            await message.answer("❌ Ваша заявка отклонена!")
            return
    await message.answer("📝 Введите ваше полное имя:")
    await state.set_state(DriverReg.full_name)

@dp.message(DriverReg.full_name)
async def get_full_name(message: types.Message, state: FSMContext):
    await state.update_data(full_name=message.text)
    await message.answer(
        "📱 Отправьте ваш номер телефона:",
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
                f"👤 Имя: {data['full_name']}\n"
                f"📱 Телефон: {data['phone']}\n"
                f"🚗 Авто: {message.text}\n"
                f"🔗 TG: @{message.from_user.username}\n\n"
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
        await message.answer("🚫 Ваш аккаунт заблокирован!")
        return
    if driver['status'] == 'approved':
        await message.answer(
            f"🔑 Ваш PIN-код: *{driver['pin']}*\n"
            f"⏰ Действует до: {driver['pin_expires_at']}",
            parse_mode="Markdown"
        )
    elif driver['status'] == 'pending':
        await message.answer("⏳ Заявка на рассмотрении")
    elif driver['status'] == 'rejected':
        await message.answer("❌ Ваша заявка отклонена")

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
    blocked = "🚫 Заблокирован" if driver['is_blocked'] else ""
    await message.answer(
        f"📊 Статус: {status_text[driver['status']]} {blocked}\n\n"
        f"👤 Имя: {driver['full_name']}\n"
        f"📱 Телефон: {driver['phone']}\n"
        f"🚗 Авто: {driver['car_number']}\n"
        f"📅 Дата: {driver['created_at']}"
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
            f"🆕 Новая заявка\n\n"
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
    text = "👥 Все водители:\n\n"
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
    await message.answer("📢 Введите сообщение для рассылки:")
    await state.set_state(BroadcastState.message)

@dp.message(BroadcastState.message)
async def broadcast_send(message: types.Message, state: FSMContext):
    await state.clear()
    drivers = get_all_drivers()
    sent = 0
    failed = 0
    for driver in drivers:
        if (driver['status'] == 'approved' 
                and not driver['is_blocked']):
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
        f"📢 Рассылка завершена!\n"
        f"✅ Отправлено: {sent}\n"
        f"❌ Ошибок: {failed}"
    )

@dp.message(Command("pending"))
async def pending_cmd(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await pending_list(message)

@dp.message(lambda m: m.text and m.text.startswith("/approve_"))
async def approve(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    tg_id = int(message.text.split("_")[1])
    pin = approve_driver(tg_id)
    add_log("approve", message.from_user.id, tg_id, f"PIN: {pin}")
    await message.answer(f"✅ Водитель одобрен!\n🔑 PIN: {pin}")
    try:
        await bot.send_message(
            tg_id,
            f"✅ Заявка одобрена!\n\n"
            f"🔑 PIN-код: *{pin}*\n\n"
            f"Используйте для входа в приложение",
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
    await message.answer("❌ Водитель отклонен!")
    try:
        await bot.send_message(tg_id, "❌ Ваша заявка отклонена!")
    except:
        pass

@dp.message(lambda m: m.text and m.text.startswith("/info_"))
async def driver_info(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    tg_id = int(message.text.split("_")[1])
    driver = get_driver(tg_id)
    if not driver:
        await message.answer("❌ Водитель не найден")
        return
    status_text = {
        'pending': '⏳ На рассмотрении',
        'approved': '✅ Одобрен',
        'rejected': '❌ Отклонен'
    }
    await message.answer(
        f"👤 Информация:\n\n"
        f"Имя: {driver['full_name']}\n"
        f"Телефон: {driver['phone']}\n"
        f"Авто: {driver['car_number']}\n"
        f"TG: @{driver['username']}\n"
        f"Статус: {status_text[driver['status']]}\n"
        f"PIN: {driver['pin'] or 'нет'}\n"
        f"PIN до: {driver['pin_expires_at'] or 'нет'}\n"
        f"Заблокирован: {'Да 🚫' if driver['is_blocked'] else 'Нет'}\n\n"
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
    await message.answer("🚫 Водитель заблокирован!")
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
    await message.answer("✅ Водитель разблокирован!")
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
    await message.answer(f"🔄 PIN сброшен!\n🔑 Новый PIN: {pin}")
    try:
        await bot.send_message(
            tg_id,
            f"🔄 PIN сброшен!\n\n🔑 Новый PIN: *{pin}*",
            parse_mode="Markdown"
        )
    except:
        pass

# ==================== ЗАПУСК ====================

def run_flask():
    app.run(host='0.0.0.0', port=5000, debug=False)

async def run_bot():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

async def main():
    init_db()
    
    # Flask в отдельном потоке
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    # Бот в основном потоке
    await run_bot()

if __name__ == "__main__":
    asyncio.run(main())
