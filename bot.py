#!/usr/bin/env python3
"""
Telegram бот ИИ ПЕПЕЛ
✅ С принудительной установкой модели
✅ Диагностика переменных
"""

import logging
import asyncio
import httpx
import json
import os
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode
from telegram.error import RetryAfter

# Загружаем .env (для локального запуска)
load_dotenv()

# ===== ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ADMIN_IDS = os.getenv("ADMIN_IDS", "")

# ПРИНУДИТЕЛЬНАЯ УСТАНОВКА МОДЕЛИ (если переменная не загрузилась)
MODEL = os.getenv("MODEL", "gemini-3.1-flash-lite-preview")
if not MODEL or MODEL == "":
    MODEL = "gemini-3.1-flash-lite-preview"  # Принудительно

# ДИАГНОСТИКА
print("=" * 50)
print("🔍 ДИАГНОСТИКА ПЕРЕМЕННЫХ:")
print(f"BOT_TOKEN: {'✅' if BOT_TOKEN else '❌'} (длина: {len(BOT_TOKEN) if BOT_TOKEN else 0})")
print(f"GEMINI_API_KEY: {'✅' if GEMINI_API_KEY else '❌'} (длина: {len(GEMINI_API_KEY) if GEMINI_API_KEY else 0})")
print(f"MODEL: {MODEL}")
print(f"ADMIN_IDS: {ADMIN_IDS}")
print("=" * 50)

# Проверка
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не задан!")
if not GEMINI_API_KEY:
    raise ValueError("❌ GEMINI_API_KEY не задан!")
if not ADMIN_IDS:
    raise ValueError("❌ ADMIN_IDS не задан!")

# Настройки
TIMEOUT = int(os.getenv("TIMEOUT", "60"))
MAX_HISTORY = int(os.getenv("MAX_HISTORY", "2"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
FAKE_MESSAGE_DELAY = float(os.getenv("FAKE_MESSAGE_DELAY", "2.0"))

admins_list = [int(x.strip()) for x in ADMIN_IDS.split(",") if x.strip().isdigit()]

# URL для Gemini API
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

DATA_FILE = "bot_data.json"

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)-8s | %(message)s')
logger = logging.getLogger(__name__)

# ===== ХРАНЕНИЕ ДАННЫХ =====
user_histories = {}
user_modes = {}
bot_enabled = True
admins = admins_list.copy()
banned_users = []
banned_chats = []

def load_data():
    global admins, banned_users, banned_chats, bot_enabled
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                admins = data.get('admins', admins_list.copy())
                banned_users = data.get('banned_users', [])
                banned_chats = data.get('banned_chats', [])
                bot_enabled = data.get('bot_enabled', True)
        except:
            pass

def save_data():
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump({
            'admins': admins,
            'banned_users': banned_users,
            'banned_chats': banned_chats,
            'bot_enabled': bot_enabled
        }, f, ensure_ascii=False, indent=2)

def is_admin(user_id: int) -> bool:
    return user_id in admins

def is_banned(user_id: int, chat_id: int) -> bool:
    return user_id in banned_users or chat_id in banned_chats

def starts_with_pepel(text: str) -> tuple:
    if not text:
        return False, None
    text_lower = text.lower().strip()
    if text_lower.startswith("пепел"):
        cleaned = text[5:].strip() if len(text) > 5 else ""
        if cleaned and cleaned[0] in [',', ' ', '.', '!', '?', ':']:
            cleaned = cleaned[1:].strip()
        return True, cleaned
    return False, None

# ===== КНОПКИ =====
async def mode_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("🤬 ОСКОРБИТЕЛЬНЫЙ", callback_data="mode_rude")],
        [InlineKeyboardButton("😊 ОБЫЧНЫЙ", callback_data="mode_normal")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await query.edit_message_text(
            "🎭 *Выбери режим общения:*\n\n"
            "🤬 *Оскорбительный* - отвечает с легкими оскорблениями\n"
            "😊 *Обычный* - вежливый ассистент",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )
    except RetryAfter as e:
        await asyncio.sleep(e.retry_after)

async def mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    mode = query.data.split("_")[1]
    user_modes[user_id] = mode
    
    mode_text = "ОСКОРБИТЕЛЬНЫЙ" if mode == "rude" else "ОБЫЧНЫЙ"
    emoji = "🤬" if mode == "rude" else "😊"
    
    try:
        await query.edit_message_text(
            f"{emoji} *Режим изменен на {mode_text}*",
            parse_mode=ParseMode.MARKDOWN
        )
    except RetryAfter as e:
        await asyncio.sleep(e.retry_after)
    
    await asyncio.sleep(2)
    try:
        await query.delete_message()
    except:
        pass

# ===== АДМИН КОМАНДЫ (сокращенно) =====
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Нет доступа")
        return
    
    status = "✅ ВКЛЮЧЕН" if bot_enabled else "❌ ВЫКЛЮЧЕН"
    keyboard = [
        [InlineKeyboardButton(f"{status}", callback_data="admin_toggle")],
        [InlineKeyboardButton("👥 Админы", callback_data="admin_list")],
        [InlineKeyboardButton("🔨 Бан юзеров", callback_data="admin_banned_users")],
        [InlineKeyboardButton("🚫 Бан групп", callback_data="admin_banned_chats")],
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🔧 *Админ-панель*", parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if not is_admin(user_id):
        await query.edit_message_text("❌ Доступ запрещен")
        return
    
    action = query.data.split("_")[1]
    
    if action == "toggle":
        global bot_enabled
        bot_enabled = not bot_enabled
        save_data()
        status = "ВКЛЮЧЕН" if bot_enabled else "ВЫКЛЮЧЕН"
        await query.edit_message_text(f"✅ Бот {status}")
        await asyncio.sleep(2)
        await query.delete_message()
    
    elif action == "list":
        admin_list = "\n".join([f"• `{aid}`" for aid in admins])
        await query.edit_message_text(f"👥 *Админы:*\n{admin_list}", parse_mode=ParseMode.MARKDOWN)
        await asyncio.sleep(5)
        await query.delete_message()
    
    elif action == "banned_users":
        if banned_users:
            banned_list = "\n".join([f"• `{uid}`" for uid in banned_users])
            await query.edit_message_text(f"🔨 *Забаненные:*\n{banned_list}", parse_mode=ParseMode.MARKDOWN)
        else:
            await query.edit_message_text("🔨 Нет забаненных")
        await asyncio.sleep(5)
        await query.delete_message()
    
    elif action == "banned_chats":
        if banned_chats:
            banned_list = "\n".join([f"• `{cid}`" for cid in banned_chats])
            await query.edit_message_text(f"🚫 *Забаненные группы:*\n{banned_list}", parse_mode=ParseMode.MARKDOWN)
        else:
            await query.edit_message_text("🚫 Нет забаненных групп")
        await asyncio.sleep(5)
        await query.delete_message()
    
    elif action == "stats":
        stats_text = (
            f"📊 *Статистика:*\n"
            f"• Диалогов: {len(user_histories)}\n"
            f"• Админов: {len(admins)}\n"
            f"• В бане: {len(banned_users)}\n"
            f"• Бот: {'Вкл' if bot_enabled else 'Выкл'}"
        )
        await query.edit_message_text(stats_text, parse_mode=ParseMode.MARKDOWN)
        await asyncio.sleep(5)
        await query.delete_message()

async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    try:
        new_admin = int(context.args[0])
        if new_admin not in admins:
            admins.append(new_admin)
            save_data()
            await update.message.reply_text(f"✅ Админ {new_admin} добавлен")
    except:
        await update.message.reply_text("❌ /addadmin ID")

async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    try:
        old_admin = int(context.args[0])
        if old_admin in admins and old_admin != admins[0]:
            admins.remove(old_admin)
            save_data()
            await update.message.reply_text(f"✅ Админ {old_admin} удален")
    except:
        await update.message.reply_text("❌ /removeadmin ID")

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    try:
        ban_id = int(context.args[0])
        if ban_id not in banned_users:
            banned_users.append(ban_id)
            save_data()
            await update.message.reply_text(f"🔨 Пользователь {ban_id} забанен")
    except:
        await update.message.reply_text("❌ /ban ID")

async def ban_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    try:
        ban_id = int(context.args[0])
        if ban_id not in banned_chats:
            banned_chats.append(ban_id)
            save_data()
            await update.message.reply_text(f"🚫 Группа {ban_id} забанена")
    except:
        await update.message.reply_text("❌ /banchat ID")

async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    try:
        unban_id = int(context.args[0])
        if unban_id in banned_users:
            banned_users.remove(unban_id)
            save_data()
            await update.message.reply_text(f"🔓 Пользователь {unban_id} разбанен")
        elif unban_id in banned_chats:
            banned_chats.remove(unban_id)
            save_data()
            await update.message.reply_text(f"🔓 Группа {unban_id} разбанена")
    except:
        await update.message.reply_text("❌ /unban ID")

# ===== ОСНОВНЫЕ ФУНКЦИИ =====
def get_user_history(user_id: int) -> list:
    if user_id not in user_histories:
        user_histories[user_id] = []
    return user_histories[user_id]

def add_to_history(user_id: int, role: str, content: str):
    history = get_user_history(user_id)
    history.append({"role": role, "parts": [{"text": content}]})
    if len(history) > MAX_HISTORY:
        user_histories[user_id] = history[-MAX_HISTORY:]

def clear_user_history(user_id: int):
    user_histories[user_id] = []

async def safe_send_message(update, text):
    try:
        return await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except RetryAfter as e:
        logger.warning(f"Flood control, ждем {e.retry_after} сек")
        await asyncio.sleep(e.retry_after)
        return await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")
        return None

async def ask_gemini(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str, user_id: int, chat_id: int, attempt=0):
    if is_banned(user_id, chat_id):
        return
    
    add_to_history(user_id, "user", prompt)
    history = get_user_history(user_id)
    mode = user_modes.get(user_id, 'rude')
    
    if mode == 'rude':
        system_prompt = "Ты грубый ИИ ИИ Пепел. Отвечай на вопрос и добавь оскорбление в конце. Коротко."
    else:
        system_prompt = "Ты вежливый ассистент ИИ Пепел. Отвечай коротко и дружелюбно."
    
    contents = [
        {"role": "user", "parts": [{"text": system_prompt}]},
        {"role": "model", "parts": [{"text": "Понял."}]}
    ]
    
    for msg in history:
        contents.append(msg)
    
    contents.append({"role": "user", "parts": [{"text": prompt}]})
    
    payload = {
        "contents": contents,
        "generationConfig": {
            "temperature": 0.8 if mode == 'rude' else 0.7,
            "maxOutputTokens": 200,
        }
    }
    
    start_time = datetime.now()
    await asyncio.sleep(FAKE_MESSAGE_DELAY)
    
    fake_message = await safe_send_message(update, "⏳ генерирую...")
    
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                f"{GEMINI_API_URL}?key={GEMINI_API_KEY}",
                headers={"Content-Type": "application/json"},
                json=payload
            )
            
            elapsed = (datetime.now() - start_time).total_seconds()
            
            try:
                await fake_message.delete()
            except:
                pass
            
            if response.status_code != 200:
                error_text = response.text[:200]
                logger.error(f"Ошибка {response.status_code}: {error_text}")
                await safe_send_message(update, f"❌ Ошибка {response.status_code}")
                return
            
            result = response.json()
            
            if "candidates" in result and result["candidates"]:
                candidate = result["candidates"][0]
                if "content" in candidate and "parts" in candidate["content"]:
                    full_response = "".join(part.get("text", "") for part in candidate["content"]["parts"])
                    if full_response:
                        await safe_send_message(update, full_response)
                        add_to_history(user_id, "model", full_response)
                        logger.info(f"✅ {elapsed:.1f} сек")
                        return
            
            await safe_send_message(update, "❌ Пустой ответ")
    
    except httpx.TimeoutException:
        logger.warning(f"Таймаут")
        try:
            await fake_message.delete()
        except:
            pass
        await safe_send_message(update, "❌ Сервер не отвечает")
                
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        try:
            await fake_message.delete()
        except:
            pass
        await safe_send_message(update, "❌ Ошибка")

# ===== КОМАНДЫ БОТА =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🎭 Выбрать режим", callback_data="mode_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "👋 *ИИ ПЕПЕЛ*\n\n"
        "📱 *В личке:* просто пиши\n"
        "👥 *В группе:* 'пепел' в начале\n\n"
        "📝 /clear - очистить историю\n"
        "🔧 /admin - админ-панель",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup
    )

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    clear_user_history(user_id)
    await update.message.reply_text("🧹 *История очищена*", parse_mode=ParseMode.MARKDOWN)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    message_text = update.message.text
    reply_to_message = update.message.reply_to_message
    
    if not message_text or message_text.startswith('/'):
        return
    
    if is_banned(user_id, chat_id) or (not bot_enabled and not is_admin(user_id)):
        return
    
    chat_type = update.effective_chat.type
    should_answer = False
    cleaned_text = message_text
    
    if chat_type == "private":
        should_answer = True
    else:
        starts_with, cleaned = starts_with_pepel(message_text)
        if starts_with:
            should_answer = True
            cleaned_text = cleaned if cleaned else "скажи что-нибудь"
        elif reply_to_message and reply_to_message.from_user and reply_to_message.from_user.id == context.bot.id:
            should_answer = True
            cleaned_text = message_text
    
    if not should_answer:
        return
    
    await update.message.chat.send_action(action="typing")
    await ask_gemini(update, context, cleaned_text, user_id, chat_id)

def main():
    load_data()
    
    print("=" * 50)
    print("🤬 ИИ ПЕПЕЛ БОТ")
    print(f"✅ Модель: {MODEL}")
    print(f"✅ Админы: {admins}")
    print("=" * 50)
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("addadmin", add_admin))
    app.add_handler(CommandHandler("removeadmin", remove_admin))
    app.add_handler(CommandHandler("ban", ban_user))
    app.add_handler(CommandHandler("banchat", ban_chat))
    app.add_handler(CommandHandler("unban", unban))
    
    app.add_handler(CallbackQueryHandler(mode_menu_callback, pattern="mode_menu"))
    app.add_handler(CallbackQueryHandler(mode_callback, pattern="mode_"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="admin_"))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("✅ Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 Бот остановлен")
