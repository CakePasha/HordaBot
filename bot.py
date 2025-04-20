import logging
import sqlite3
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, Update
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
import asyncio
from datetime import datetime

from dotenv import load_dotenv
import os

load_dotenv()
API_TOKEN = os.getenv("API_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))


bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Логирование
logging.basicConfig(level=logging.INFO)

# Подключение к базе данных SQLite
conn = sqlite3.connect('users.db')
cursor = conn.cursor()

# Добавляем колонку discount, если её нет
try:
    cursor.execute("ALTER TABLE users ADD COLUMN discount REAL DEFAULT 0.0")
    conn.commit()
except sqlite3.OperationalError:
    # Колонка уже существует
    pass

# Таблица пользователей
cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        referrer_id INTEGER,
        referrals_count INTEGER DEFAULT 0,
        discount REAL DEFAULT 0.0
    )
""")
conn.commit()

# Словарь для отслеживания времени последнего использования команды
last_command_time = {}

# Функция для ограничения частоты команд
async def throttle_command(user_id: int, command: str, rate: int = 2):
    now = datetime.now()
    if user_id in last_command_time:
        last_time = last_command_time[user_id].get(command)
        if last_time and (now - last_time).total_seconds() < rate:
            return False
    last_command_time.setdefault(user_id, {})[command] = now
    return True

# Функция добавления нового пользователя в БД
def add_user(user_id, username, referrer_id=None):
    # Проверяем, существует ли пользователь
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    if cursor.fetchone():
        logging.info(f"Пользователь {user_id} уже существует в базе данных.")
        return

    # Проверяем, существует ли реферер
    if referrer_id:
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (referrer_id,))
        if not cursor.fetchone():
            logging.warning(f"Реферальный ID {referrer_id} не существует. Пользователь {user_id} добавлен без реферера.")
            referrer_id = None

    # Добавляем нового пользователя
    cursor.execute("INSERT INTO users (user_id, username, referrer_id) VALUES (?, ?, ?)", 
                   (user_id, username, referrer_id))
    conn.commit()
    logging.info(f"Добавлен новый пользователь: {user_id}, реферер: {referrer_id}")

    # Если есть реферер, обновляем его данные
    if referrer_id:
        logging.info(f"Обновляем данные реферера: {referrer_id}")
        update_referrals_count(referrer_id)
        update_discount_and_notify(referrer_id)

# Функция обновления количества рефералов
def update_referrals_count(user_id):
    cursor.execute("UPDATE users SET referrals_count = referrals_count + 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    logging.info(f"Количество рефералов обновлено для пользователя {user_id}")

# Функция обновления скидки и уведомления реферера
def update_discount_and_notify(user_id):
    cursor.execute("SELECT referrals_count FROM users WHERE user_id = ?", (user_id,))
    referrals_count = cursor.fetchone()[0]
    discount = min(referrals_count * 2, 50)  # 2% за каждого реферала, максимум 50%
    cursor.execute("UPDATE users SET discount = ? WHERE user_id = ?", (discount, user_id))
    conn.commit()
    logging.info(f"Скидка обновлена для пользователя {user_id}: {discount}%")

    # Уведомляем реферера
    asyncio.create_task(bot.send_message(
        user_id,
        f"🎉 *You have +1 new referral!*\n"
        f"*Your discount has been increased by 2%.*\n"
        f"*Current discount: {discount}%.*",
        parse_mode="Markdown"
    ))

# Главное меню (Reply-кнопки)
def main_menu():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👤 My Profile"), KeyboardButton(text="🛒 Catalog ")],
            [KeyboardButton(text="ℹ️ About Us"), KeyboardButton(text="🎁 Referral System")],
            [KeyboardButton(text="💬 Help & Support"), KeyboardButton(text="👀 Soon...")]
        ],
        resize_keyboard=True,  # Уменьшает размер кнопок для компактного отображения
    )
    return keyboard

# Проверка, является ли пользователь администратором
def is_admin(user_id):
    return user_id == ADMIN_ID

# Команда для просмотра всех пользователей
@dp.message(Command(commands=["users"]))
async def handle_users(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("🚫 You don't have permission to use this command.")
        return

    cursor.execute("SELECT user_id, username, referrals_count, discount FROM users")
    users = cursor.fetchall()

    if not users:
        await message.answer("No users found in the database.")
        return

    response = "👥 *List of Users:*\n\n"
    for user in users:
        user_id, username, referrals_count, discount = user
        response += (
            f"🆔 *User ID:* `{user_id}`\n"
            f"👤 *Username:* {username or 'N/A'}\n"
            f"👥 *Referrals:* {referrals_count}\n"
            f"💸 *Discount:* {discount}%\n\n"
        )

    await message.answer(response, parse_mode="Markdown")

# Команда для просмотра профиля конкретного пользователя
@dp.message(Command(commands=["user"]))
async def handle_user(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("🚫 You don't have permission to use this command.")
        return

    # Проверяем, указан ли user_id
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Usage: `/user <user_id>`", parse_mode="Markdown")
        return

    try:
        user_id = int(args[1])
    except ValueError:
        await message.answer("Invalid user ID. Please provide a valid number.")
        return

    # Получаем данные пользователя
    cursor.execute("SELECT user_id, username, referrals_count, discount FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()

    if not user:
        await message.answer(f"No user found with ID `{user_id}`.", parse_mode="Markdown")
        return

    user_id, username, referrals_count, discount = user
    response = (
        f"👤 *User Profile:*\n\n"
        f"🆔 *User ID:* `{user_id}`\n"
        f"👤 *Username:* {username or 'N/A'}\n"
        f"👥 *Referrals:* {referrals_count}\n"
        f"💸 *Discount:* {discount}%\n"
    )

    await message.answer(response, parse_mode="Markdown")

# Команда для просмотра профиля конкретного пользователя по username с отображением приглашенных
@dp.message(Command(commands=["userstat"]))
async def handle_userstat(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("🚫 You don't have permission to use this command.")
        return

    # Проверяем, указан ли username
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Usage: `/userstat @username`", parse_mode="Markdown")
        return

    username = args[1].lstrip("@")  # Убираем символ @, если он есть

    # Получаем данные пользователя
    cursor.execute("SELECT user_id, username, referrals_count, discount FROM users WHERE username = ?", (username,))
    user = cursor.fetchone()

    if not user:
        await message.answer(f"No user found with username `@{username}`.", parse_mode="Markdown")
        return

    user_id, username, referrals_count, discount = user

    # Получаем список приглашенных пользователей
    cursor.execute("SELECT username, user_id FROM users WHERE referrer_id = ?", (user_id,))
    invited_users = cursor.fetchall()

    # Формируем список приглашенных
    if invited_users:
        invited_list = "\n".join([f"👤 @{invited[0] or 'N/A'} (ID: `{invited[1]}`)" for invited in invited_users])
    else:
        invited_list = "No invited users."

    # Формируем ответ
    response = (
        f"👤 *User Profile:*\n\n"
        f"🆔 *User ID:* `{user_id}`\n"
        f"👤 *Username:* @{username}\n"
        f"👥 *Referrals:* {referrals_count}\n"
        f"💸 *Discount:* {discount}%\n\n"
        f"📋 *Invited Users:*\n{invited_list}"
    )

    await message.answer(response, parse_mode="Markdown")

# Команда для удаления пользователя
@dp.message(Command(commands=["delete_user"]))
async def delete_user(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("🚫 You don't have permission to use this command.")
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer("Usage: `/delete_user <user_id>`", parse_mode="Markdown")
        return

    try:
        user_id = int(args[1])
        cursor.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        conn.commit()
        await message.answer(f"User with ID `{user_id}` has been deleted.", parse_mode="Markdown")
    except ValueError:
        await message.answer("Invalid user ID. Please provide a valid number.")

# Команда для выдачи скидки пользователю
@dp.message(Command(commands=["give_discount"]))
async def handle_give_discount(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("🚫 You don't have permission to use this command.")
        return

    args = message.text.split()
    if len(args) < 3:
        await message.answer("Usage: `/give_discount @username <discount>`", parse_mode="Markdown")
        return

    try:
        username = args[1].lstrip("@")  # Убираем символ @, если он есть
        discount_to_add = float(args[2])

        # Проверяем, существует ли пользователь
        cursor.execute("SELECT user_id, discount FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        if not user:
            await message.answer(f"User with username `@{username}` not found.", parse_mode="Markdown")
            return

        user_id, current_discount = user
        new_discount = current_discount + discount_to_add

        # Обновляем скидку пользователя
        cursor.execute("UPDATE users SET discount = ? WHERE user_id = ?", (new_discount, user_id))
        conn.commit()

        # Уведомляем пользователя о новой скидке
        await bot.send_message(
            user_id,
            f"🎉* You have received a bonus discount: {discount_to_add:.2f}%*\n"
            f"*Ваша текущая скидка: {new_discount:.2f}%.*",
            parse_mode="Markdown"
        )

        # Уведомляем администратора об успешной операции
        await message.answer(
            f"Пользователю с username `@{username}` успешно начислена скидка: {discount_to_add:.2f}%.\n"
            f"Новая скидка пользователя: {new_discount:.2f}%.",
            parse_mode="Markdown"
        )

    except ValueError:
        await message.answer("Invalid input. Please provide a valid username and discount amount.")

# Команда для удаления скидки у пользователя
@dp.message(Command(commands=["remove_discount"]))
async def handle_remove_discount(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("🚫 You don't have permission to use this command.")
        return

    args = message.text.split()
    if len(args) < 3:
        await message.answer("Usage: `/remove_discount @username <discount>`", parse_mode="Markdown")
        return

    try:
        username = args[1].lstrip("@")  # Убираем символ @, если он есть
        discount_to_remove = float(args[2])

        # Проверяем, существует ли пользователь
        cursor.execute("SELECT user_id, discount FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        if not user:
            await message.answer(f"User with username `@{username}` not found.", parse_mode="Markdown")
            return

        user_id, current_discount = user
        new_discount = max(current_discount - discount_to_remove, 0)  # Скидка не может быть меньше 0

        # Обновляем скидку пользователя
        cursor.execute("UPDATE users SET discount = ? WHERE user_id = ?", (new_discount, user_id))
        conn.commit()

        # Уведомляем пользователя об изменении скидки
        await bot.send_message(
            user_id,
            f"❌ *Your discount has been decreased on: {discount_to_remove:.2f}%*\n"
            f"*Your current discount: {new_discount:.2f}% ⭐*",
        parse_mode="Markdown")

        # Уведомляем администратора об успешной операции
        await message.answer(
            f"Скидка пользователя с username `@{username}` уменьшена на {discount_to_remove:.2f}%.\n"
            f"Новая скидка пользователя: {new_discount:.2f}%.",
            parse_mode="Markdown"
        )

    except ValueError:
        await message.answer("Invalid input. Please provide a valid username and discount amount.")

# Команда для регистрации покупки по username
@dp.message(Command(commands=["register_purchase"]))
async def handle_register_purchase(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("🚫 You don't have permission to use this command.")
        return

    args = message.text.split()
    if len(args) < 3:
        await message.answer("Usage: `/register_purchase @username <amount>`", parse_mode="Markdown")
        return

    try:
        username = args[1].lstrip("@")  # Убираем символ @, если он есть
        amount = float(args[2])

        # Проверяем, существует ли пользователь
        cursor.execute("SELECT user_id, referrer_id FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        if not user:
            await message.answer(f"User with username `@{username}` not found.", parse_mode="Markdown")
            return

        user_id, referrer_id = user

        # Начисляем скидку рефереру, если он существует
        if referrer_id:
            discount = 10  # 10% за покупку
            cursor.execute("SELECT discount FROM users WHERE user_id = ?", (referrer_id,))
            referrer_discount = cursor.fetchone()[0]
            new_discount = referrer_discount + discount

            cursor.execute("UPDATE users SET discount = ? WHERE user_id = ?", (new_discount, referrer_id))
            conn.commit()

            # Уведомляем реферера
            await bot.send_message(
                referrer_id,
                f"*🎉 The user you invited made a purchase!*\n"
                f"*You've received a discount: {discount}%.*\n"
                f"*Current discount: {new_discount}%.*",
                parse_mode="Markdown"
            )

        # Уведомляем администратора об успешной регистрации
        await message.answer(
            f"Purchase by user `@{username}` for the amount `{amount}` has been successfully registered.",
            parse_mode="Markdown"
        )

    except ValueError:
        await message.answer("Invalid input. Please provide a valid username and amount.")

# Обработчик команды /start
@dp.message(Command(commands=["start"]))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username
    referrer_id = None

    # Ограничение частоты команды
    if not await throttle_command(user_id, "start", rate=2):
        await message.answer("⏳ Please wait before using this command again.")
        return

    # Если сообщение содержит /start и реферальный код
    if len(message.text.split()) > 1:
        referrer_id = int(message.text.split()[1])
        logging.info(f"Пользователь {user_id} пришел по реферальной ссылке от {referrer_id}")

    # Добавляем пользователя в базу данных
    add_user(user_id, username, referrer_id)

    # Приветственное сообщение с фотографией и текстом
    photo_url = "https://i.imgur.com/lnr4Z0M.jpeg" 
    user_name = message.from_user.first_name
    await bot.send_photo(
        chat_id=message.chat.id,
        photo=photo_url,
        caption=(
            f"Hello, *{user_name}*! \nWelcome to *Horda Shop*! 🎉\n\n"
            "*💫 Tap the menu below to snoop around.*\n"
            "*Deals don’t bite, but they do disappear🫥 — so don’t blink...*\n\n\n"
            "*🪴Our News Channel:* [@HORDAHORDA]"
        ),
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

# Обработчик кнопки "Your Profile"
@dp.message(F.text == "👤 My Profile")
async def handle_profile(message: Message):
    user_id = message.from_user.id
    cursor.execute("SELECT referrals_count, discount FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()

    if result:
        referrals_count, discount = result
        await message.answer(
            f"*👤 Your Profile*\n\n"
            f"*👥 Referrals: *{referrals_count}\n"
            f"*-You've referred {referrals_count} friends! Keep it up!*\n\n"
            f"*💸 Discount: *{discount:.2f}%\n"
            f"*- Active discount for you!*",
        parse_mode="Markdown")
    else:
        await message.answer("You are not registered in the system yet.")

# Обработчик кнопки "Assortiment"
@dp.message(F.text == "🛒 Catalog")
async def handle_assortiment(message: Message):                              
    await message.answer(
        "Choose a category:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🎧 Spotify Premium"), KeyboardButton(text="🔴 YouTube Premium")],
                [KeyboardButton(text="🟣 Twitch Subscription"), KeyboardButton(text="💎 Discord Nitro")],
                [KeyboardButton(text="⭐ Telegram Stars"), KeyboardButton(text="Turkish Bankcards 🇹🇷")],
                                            [KeyboardButton(text="Back")]
            ],
            resize_keyboard=True
        )
    )

# Обработчики для Spotify, YouTube Premium и Twitch Prime
@dp.message(F.text == "🎧 Spotify Premium")
async def handle_spotify(message: Message):
    await message.answer(
        "🎵 *Spotify Premium Individual*\n\n"
        "▫️* 1 month — $3.99*\n\n"
        "▫️* 3 months — $8.99*\n\n"
        "▫️ *6 months — $12.99*\n\n"
        "*▫️ 12 months — $22.99* \n\n"
        "*Payment methods:\n🪙Crypto\n💸PayPal*\n\n"
        "*To buy: @headphony*",
    parse_mode="Markdown")

@dp.message(F.text == "🔴 YouTube Premium")
async def handle_youtube(message: Message):
    await message.answer(
        "soon..."
    )

@dp.message(F.text == "🟣 Twitch Subscription")
async def handle_twitch(message: Message):
    await message.answer(
        "*🎮 Twitch Subscription*\n"
        "*LEVEL 1✅\n\n*"
        "*▫️ Level 1 — 1 Month — $3.99*\n\n"
        "*▫️ Level 1 — 3 Months — $8.99*\n\n"
        "*▫️ Level 1 — 6 Months — $17.99*\n\n"
        "*LEVEL 2✅\n\n*"
        "*▫️ Level 2 — 1 Month — $5.99*\n\n"
        "*LEVEL 3✅\n\n*"
        "*▫️ Level 3 — 1 Month — $14.99*\n\n"
        "🥰No account access needed — just *your* and the *streamer’s* *nicknames!*\n\n"
        "*Payment methods:\n- Crypto\n- PayPal*\n\n"
        "*To buy: @heaphony*",
        
        parse_mode="Markdown"
    )

# Обработчик кнопки "Turkish Bankcards"
@dp.message(F.text == "Turkish Bankcards 🇹🇷")
async def handle_turkish_bankcards(message: Message):
    await message.answer(
        "Choose a card type:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Popypara 🇹🇷")],
                [KeyboardButton(text="Back")]
            ],
            resize_keyboard=True
        )
    )

@dp.message(F.text == "💎 Discord Nitro")
async def handle_discord(message: Message):
    await message.answer(
        "💎 *Discord Nitro Full*\n\n"
        "*1 month — $6.49*\n\n"
        "*3 months — $13.99*\n\n"
        "*6 months — soon...*\n\n"
        "*🎁 You'll get Nitro as a gift — no need to log in anywhere, no data required!*\n\n"
        "*⚜️ You'll only have to activate it with VPN and that's it!*\n\n"
        "*Payment methods:\n- Crypto (TON, BTC, USDC, BNB)\n- PayPal*\n\n"
        "*To buy: @headphony*",
        parse_mode="Markdown"
    )

@dp.message(F.text == "⭐ Telegram Stars")
async def handle_telegram_stars(message: Message):
    await message.answer(
        "*⭐ Telegram Stars*\n\n"
        "*100⭐ — $1.79*\n\n"
        "*250⭐ — $4.59*\n\n"
        "*500⭐ — $8.99*\n\n"
        "*1000⭐ — $16.99*\n\n"
        "*📦 All stars are purchased officially and delivered via Telegram!*\n\n"
        "✅ No account info, no logins — just your *@username* to receive the gift.\n\n"
        "*Payment methods:\n- Crypto (TON, BTC, USDC, BNB)\n- PayPal*\n\n"
        "*To buy: @headphony*",
        parse_mode="Markdown"
    )

# Обработчик кнопки "Popypara"
@dp.message(F.text == "Popypara 🇹🇷")
async def handle_popypara(message: Message):
    await message.answer(
        "🇹🇷 *Popypara*\n\n"
        "*Features:\n\n*"
        "▫️ Monthly limit of *2750 TRY🇹🇷*\n\n"
        "▫️ Works with *all online services✅*\n\n"
        "▫️ *Quick & easy* top-up process✅\n\n"
        "▫️ *Stable & reliable* performance🤗\n\n"
        "▫️ Super *user-friendly* experience\n\n"
        "*Price:*\n\n"
        "*⚠️Currently anavailable! Check our news channel for the updates*\n\n"
        "Payment - Crypto, Paypal\n"
        "*Contact us - @headphony*",
        parse_mode="Markdown"
    )

# Обработчик кнопки "Назад"
@dp.message(F.text == "Back")
async def handle_back(message: Message):
    await message.answer("You are back to the main menu.", reply_markup=main_menu())

# Обработчик кнопки "Info about us"
@dp.message(F.text == "ℹ️ About Us")
async def handle_about(message: Message):
    await message.answer(
        "*Horda Shop. We don’t beg — we deliver.*\n\n"
        "*Fast deals, clean setup, zero bullshit.*\n\n"
        "You came for the *price* — you’ll stay for the service 👊\n\n"
        "*Cheap? Yeah 🤩*\n"
        "*Shady? Nah 😎*\n\n"
        "*We move different...*",
        parse_mode="Markdown"
    )

# Обработчик кнопки "Referral System"
@dp.message(F.text == "🎁 Referral System")
async def handle_referral(message: Message):
    user_id = message.from_user.id
    referral_link = f"https://t.me/hordashop_bot?start={user_id}"
    await message.answer(
        f"*🎉 Referral System*\n\n"
        f"*Invite* your *friends* and earn *rewards!*\n"
        f"For every user who joins with your link, you’ll receive:\n\n"
        f"• *🔁 2% discount automatically just for the referral*\n\n"
        f"• *💸 +10% if your referral makes a purchase*\n\n"
        f"*Your referral link: {referral_link}*",
    parse_mode="Markdown")

# Обработчик кнопки "Help"
@dp.message(F.text == "💬 Help & Support")
async def handle_help(message: Message):
    await message.answer(
        "*Got any questions?*\n\n"
        "Feel free to reach out to us anytime:\n"
        "*📩 @headphony*",
       parse_mode="Markdown" 
       )

# Глобальный обработчик ошибок
@dp.errors()
async def handle_errors(update: Update, exception: Exception):
    logging.error(f"An error occurred: {exception}\nUpdate: {update}")
    return True  # Возвращаем True, чтобы ошибка не прерывала работу бота

# Обработчик для необработанных сообщений
@dp.message()
async def handle_unhandled_messages(message: Message):
    await message.answer("There is no such command. Try again!")

# Запуск бота
async def main():
    await dp.start_polling(bot)

logging.basicConfig(level=logging.DEBUG)

if __name__ == '__main__':
    asyncio.run(main())