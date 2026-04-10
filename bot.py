#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import io
import os

# ПРИНУДИТЕЛЬНАЯ УСТАНОВКА UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
os.environ['PYTHONIOENCODING'] = 'utf-8'
os.environ['LC_ALL'] = 'C.UTF-8'
os.environ['LANG'] = 'C.UTF-8'

import logging
import asyncio
import httpx
import json
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode
from telegram.error import RetryAfter

load_dotenv()

# ===== ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
ADMIN_IDS = os.getenv("ADMIN_IDS", "")

# Модель
MODEL = "google/gemini-2.0-flash-lite-001"
API_URL = "https://openrouter.ai/api/v1/chat/completions"

TIMEOUT = int(os.getenv("TIMEOUT", "60"))
MAX_HISTORY = int(os.getenv("MAX_HISTORY", "2"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
FAKE_MESSAGE_DELAY = float(os.getenv("FAKE_MESSAGE_DELAY", "2.0"))

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not set!")
if not OPENROUTER_API_KEY:
    raise ValueError("OPENROUTER_API_KEY not set!")
if not ADMIN_IDS:
    raise ValueError("ADMIN_IDS not set!")

admins_list = [int(x.strip()) for x in ADMIN_IDS.split(",") if x.strip().isdigit()]

DATA_FILE = "bot_data.json"

# Логи ТОЛЬКО на английском (чтобы не было ошибок)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
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

def safe_text(text: str) -> str:
    if not text:
        return ""
    try:
        return text.encode('utf-8', errors='ignore').decode('utf-8')
    except:
        return str(text)

# ===== КНОПКИ (РУССКИЙ) =====
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
            "🤬 *Оскорбительный* - отвечает с лёгкими оскорблениями\n"
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
            f"{emoji} *Режим изменён на {mode_text}*",
            parse_mode=ParseMode.MARKDOWN
        )
    except RetryAfter as e:
        await asyncio.sleep(e.retry_after)
    
    await asyncio.sleep(2)
    try:
        await query.delete_message()
    except:
        pass

# ===== АДМИН КОМАНДЫ (РУССКИЙ) =====
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Нет доступа")
        return
    
    status = "✅ ВКЛЮЧЕН" if bot_enabled else "❌ ВЫКЛЮЧЕН"
    keyboard = [
        [InlineKeyboardButton(f"Статус: {status}", callback_data="admin_toggle")],
        [InlineKeyboardButton("👥 Список админов", callback_data="admin_list")],
        [InlineKeyboardButton("🔨 Забаненные пользователи", callback_data="admin_banned_users")],
        [InlineKeyboardButton("🚫 Забаненные группы", callback_data="admin_banned_chats")],
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("💾 Сохранить данные", callback_data="admin_save")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🔧 *Админ-панель*", parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if not is_admin(user_id):
        await query.edit_message_text("❌ Доступ запрещён")
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
            await query.edit_message_text(f"🔨 *Забаненные пользователи:*\n{banned_list}", parse_mode=ParseMode.MARKDOWN)
        else:
            await query.edit_message_text("🔨 Нет забаненных пользователей")
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
            f"📊 *Статистика:*\n\n"
            f"• Диалогов: {len(user_histories)}\n"
            f"• Админов: {len(admins)}\n"
            f"• В бане: {len(banned_users)}\n"
            f"• Бот: {'Вкл' if bot_enabled else 'Выкл'}"
        )
        await query.edit_message_text(stats_text, parse_mode=ParseMode.MARKDOWN)
        await asyncio.sleep(5)
        await query.delete_message()
    
    elif action == "save":
        save_data()
        await query.edit_message_text("💾 Данные сохранены")
        await asyncio.sleep(2)
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
        await update.message.reply_text("❌ Используй: /addadmin ID")

async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    try:
        old_admin = int(context.args[0])
        if old_admin in admins and old_admin != admins[0]:
            admins.remove(old_admin)
            save_data()
            await update.message.reply_text(f"✅ Админ {old_admin} удалён")
    except:
        await update.message.reply_text("❌ Используй: /removeadmin ID")

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
        await update.message.reply_text("❌ Используй: /ban ID")

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
        await update.message.reply_text("❌ Используй: /banchat ID")

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
        await update.message.reply_text("❌ Используй: /unban ID")

# ===== ОСНОВНЫЕ ФУНКЦИИ =====
def get_user_history(user_id: int) -> list:
    if user_id not in user_histories:
        user_histories[user_id] = []
    return user_histories[user_id]

def add_to_history(user_id: int, role: str, content: str):
    history = get_user_history(user_id)
    content = safe_text(content)
    history.append({"role": role, "content": content})
    if len(history) > MAX_HISTORY:
        user_histories[user_id] = history[-MAX_HISTORY:]

def clear_user_history(user_id: int):
    user_histories[user_id] = []

async def safe_send_message(update, text, parse_mode=None):
    try:
        text = safe_text(text)
        return await update.message.reply_text(text, parse_mode=parse_mode)
    except RetryAfter as e:
        logger.warning(f"Flood control, wait {e.retry_after} sec")
        await asyncio.sleep(e.retry_after)
        return await update.message.reply_text(text, parse_mode=parse_mode)
    except Exception as e:
        logger.error(f"Send error: {e}")
        return None

async def ask_gemini(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str, user_id: int, chat_id: int, attempt=0):
    if is_banned(user_id, chat_id):
        return
    
    prompt = safe_text(prompt)
    add_to_history(user_id, "user", prompt)
    history = get_user_history(user_id)
    mode = user_modes.get(user_id, 'rude')
    
    if mode == 'rude':
        system_prompt = """Ты грубый ИИ по имени "ИИ Пепел". ПРАВИЛА:
1. ВСЕГДА отвечай на вопрос по существу
2. Добавь небольшое оскорбление в конце
3. Используй иногда: сын шлюхи, блек рашер, фрик, тупой, даун, еблан
4. Отвечай коротко (максимум 2 предложения)"""
    else:
        system_prompt = """Ты вежливый ассистент по имени "ИИ Пепел". Отвечай на вопрос по существу, коротко и дружелюбно."""
    
    messages = [{"role": "system", "content": system_prompt}]
    
    for msg in history:
        messages.append({"role": msg["role"], "content": safe_text(msg["content"])})
    
    messages.append({"role": "user", "content": prompt})
    
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.8 if mode == 'rude' else 0.7,
        "max_tokens": 200,
    }
    
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://t.me/pepe_bot",
        "X-Title": "II Pepel Bot"
    }
    
    start_time = datetime.now()
    await asyncio.sleep(FAKE_MESSAGE_DELAY)
    
    fake_message = await safe_send_message(update, "⏳ генерирую...")
    
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(API_URL, headers=headers, json=payload)
            
            elapsed = (datetime.now() - start_time).total_seconds()
            
            try:
                await fake_message.delete()
            except:
                pass
            
            if response.status_code != 200:
                error_text = response.text[:200]
                logger.error(f"API error {response.status_code}: {error_text}")
                await safe_send_message(update, f"❌ Ошибка API: {response.status_code}")
                return
            
            result = response.json()
            
            if "choices" in result and result["choices"]:
                full_response = result["choices"][0]["message"]["content"]
                if full_response:
                    full_response = safe_text(full_response)
                    await safe_send_message(update, full_response, parse_mode=ParseMode.MARKDOWN)
                    add_to_history(user_id, "assistant", full_response)
                    logger.info(f"Done in {elapsed:.1f}s")
                    return
            
            await safe_send_message(update, "❌ Пустой ответ")
    
    except httpx.TimeoutException:
        logger.warning(f"Timeout, attempt {attempt + 1}/{MAX_RETRIES}")
        try:
            await fake_message.delete()
        except:
            pass
        
        if attempt < MAX_RETRIES - 1:
            msg = await safe_send_message(update, f"⏳ Таймаут, повтор... ({attempt + 1}/{MAX_RETRIES})")
            await asyncio.sleep(5)
            if msg:
                try:
                    await msg.delete()
                except:
                    pass
            return await ask_gemini(update, context, prompt, user_id, chat_id, attempt + 1)
        else:
            await safe_send_message(update, "❌ Сервер не отвечает. Попробуй позже")
                
    except Exception as e:
        logger.error(f"Error: {e}")
        try:
            await fake_message.delete()
        except:
            pass
        await safe_send_message(update, f"❌ Ошибка: {str(e)[:100]}")

# ===== КОМАНДЫ (РУССКИЙ) =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🎭 Выбрать режим", callback_data="mode_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "👋 *Привет! Это бот ИИ ПЕПЕЛ*\n\n"
        "🎯 *Как меня вызвать:*\n\n"
        "📱 *В личке:* просто пиши любое сообщение\n\n"
        "👥 *В группе:*\n"
        "• Напиши `пепел` в начале сообщения\n"
        "• ИЛИ ответь на моё сообщение\n\n"
        "📝 *Команды:*\n"
        "• `/clear` - очистить историю\n"
        "• `/admin` - админ-панель\n\n"
        "👇 Нажми на кнопку ниже, чтобы выбрать стиль общения",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup
    )

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    clear_user_history(user_id)
    await update.message.reply_text("🧹 *История диалога очищена!*", parse_mode=ParseMode.MARKDOWN)

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
    print("🤬 ИИ ПЕПЕЛ БОТ (через OpenRouter)")
    print(f"Модель: {MODEL}")
    print(f"Админы: {admins}")
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
    
    print("Бот успешно запущен!")
    app.run_polling()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nБот остановлен")
    except Exception as e:
        print(f"\nКритическая ошибка: {e}")
