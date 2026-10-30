import asyncio
import hashlib
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

import aiomysql
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
                           BufferedInputFile)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# Ищем файл с настройками рядом со скриптом: сначала BOT_TOKEN.env, затем .env
BASE_DIR = Path(__file__).resolve().parent
for env_name in ("BOT_TOKEN.env", ".env"):
    env_path = BASE_DIR / env_name
    if env_path.exists():
        load_dotenv(env_path, encoding="utf-8-sig")
        break
else:
    raise RuntimeError(f"Файл с настройками не найден. Положите BOT_TOKEN.env или .env в папку: {BASE_DIR}")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_NAME = os.getenv("DB_NAME", "school_shop")
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
SUPERADMIN_ID = int(os.getenv("SUPERADMIN_ID", "0"))

if not BOT_TOKEN:
    raise RuntimeError(f"В файле {env_path.name} нет строки BOT_TOKEN=... (формат: BOT_TOKEN=токен)")
if SUPERADMIN_ID <= 0:
    raise RuntimeError(f"В файле {env_path.name} нет строки SUPERADMIN_ID=... (ваш числовой Telegram ID)")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
pool = None


@dp.update.outer_middleware()
async def log_updates(handler, event, data):
    """Пишет в консоль, КТО прислал обновление (id пользователя, а не бота)."""
    u = data.get("event_from_user")
    what = ""
    if event.message and event.message.text:
        text = event.message.text
        what = text[:30] if text.startswith("/") else f"<текст, {len(text)} симв.>"
    elif event.callback_query:
        what = f"кнопка {event.callback_query.data}"
    logging.info("ОТ ПОЛЬЗОВАТЕЛЯ id=%s @%s: %s", u.id if u else None, u.username if u else None, what)
    return await handler(event, data)


ADMINS = ("admin", "superadmin")
CATEGORIES = ("beverages", "sweets", "food", "other")

TEXT = {
    "ru": {
        "choose_lang": "Выберите язык / בחר שפה",
        "welcome": "Добро пожаловать в школьный магазин.",
        "registered": "Регистрация завершена.",
        "not_registered": "Вы не зарегистрированы. Попросите регистрационный код у суперадминистратора.",
        "blocked": "Доступ заблокирован. Обратитесь к суперадминистратору.",
        "bad_code": "Код недействителен, уже использован или не подходит.",
        "menu": "Главное меню",
        "sale": "🛒 Новая продажа", "stock": "📦 Остатки", "my_sales": "🧾 Мои продажи",
        "products": "🛍 Товары и цены", "receive": "📥 Приём товара", "finance": "💰 Касса и Bybit",
        "reports": "📊 Отчёты и рейтинг", "checks": "🧮 Проверка кассы", "cancel": "↩️ Запросы отмены",
        "users": "👥 Пользователи и коды", "support": "🆘 Поддержка", "language": "🌐 Язык",
        "back": "⬅️ Назад", "cancelled": "Действие отменено.", "no_products": "Активных товаров пока нет.",
        "choose_category": "Выберите категорию:", "choose_product": "Выберите товар:",
        "qty": "Количество:", "cart_empty": "Корзина пуста.", "cart": "Ваш заказ:",
        "confirm": "✅ Подтвердить заказ", "edit": "✏️ Продолжить покупки", "clear": "❌ Очистить заказ",
        "payment": "Выберите способ оплаты:", "cash": "💵 Наличные", "bybit": "🪙 Bybit",
        "done": "Продажа №{id} завершена. Сумма: {sum}", "no_stock": "Недостаточно товара на складе.",
        "enter_code": "Отправьте регистрационный код одним сообщением.",
        "code_created": "Одноразовый код для роли {role}: `{code}`",
        "choose_role": "Выберите роль для нового кода:",
        "enter_name_ru": "Введите название товара на русском:",
        "enter_name_he": "Введите название товара на иврите:",
        "enter_category": "Введите категорию (beverages / sweets / food / other):",
        "enter_sell": "Введите цену продажи в шекелях, например 5.50:",
        "enter_buy": "Введите закупочную цену в шекелях, например 3.00:",
        "enter_qty": "Введите целое количество:",
        "enter_amount": "Введите сумму в шекелях:",
        "enter_note": "Введите комментарий или причину:",
        "saved": "Сохранено.", "bad_number": "Неверное число. Попробуйте ещё раз.",
        "support_text": "Напишите сообщение для суперадминистратора:",
        "support_sent": "Сообщение отправлено суперадминистратору.",
        "not_allowed": "Недостаточно прав.", "balance": "Баланс {account}: {balance}",
        "expected": "Ожидаемый баланс: {expected}\nВведите фактическую сумму в шекелях:",
        "check_saved": "Проверка сохранена. Разница: {diff}",
        "my_sales_text": "Ваши последние продажи:\n",
        "no_sales": "Продаж пока нет.", "report": "Отчёт за период:\nПродажи: {n}\nВыручка: {revenue}\nПрибыль: {profit}",
        "cancel_sale": "Выберите продажу для запроса отмены:", "reason_cancel": "Укажите причину отмены продажи №{id}:",
        "cancel_requested": "Запрос на отмену отправлен.",
        "pending": "Ожидающие запросы отмены:", "approve": "✅ Одобрить", "reject": "❌ Отклонить",
        "reply_to": "Введите Telegram ID получателя ответа:", "reply_text": "Введите текст ответа:",
        "reply_sent": "Ответ отправлен.", "user_list": "Пользователи:\n",
        "product_saved": "Товар создан с ID {id}.", "select_existing": "Выберите товар для изменения/поставки:",
        "edit_price": "Введите новую цену продажи в шекелях:",
        "stock_saved": "Остаток обновлён.", "finance_action": "Выберите действие:",
        "deposit": "➕ Внести деньги", "withdraw": "➖ Снять деньги",
        "choose_account": "Выберите счёт:", "cash_account": "Наличные", "bybit_account": "Bybit",
        "rank": "Рейтинг продавцов (по числу продаж):\n",
        "help": "Используйте кнопки меню. Для возврата нажмите «Назад».",
    },
    "he": {
        "choose_lang": "בחר שפה / Выберите язык",
        "welcome": "ברוכים הבאים לחנות בית הספר.",
        "registered": "ההרשמה הושלמה.",
        "not_registered": "אינך רשום. בקש קוד הרשמה ממנהל-העל.",
        "blocked": "הגישה חסומה. פנה למנהל-העל.",
        "bad_code": "הקוד שגוי, נוצל או אינו מתאים.",
        "menu": "תפריט ראשי",
        "sale": "🛒 מכירה חדשה", "stock": "📦 מלאי", "my_sales": "🧾 המכירות שלי",
        "products": "🛍 מוצרים ומחירים", "receive": "📥 קבלת סחורה", "finance": "💰 מזומן ו-Bybit",
        "reports": "📊 דוחות ודירוג", "checks": "🧮 בדיקת קופה", "cancel": "↩️ בקשות ביטול",
        "users": "👥 משתמשים וקודים", "support": "🆘 תמיכה", "language": "🌐 שפה",
        "back": "⬅️ חזרה", "cancelled": "הפעולה בוטלה.", "no_products": "אין מוצרים פעילים כרגע.",
        "choose_category": "בחר קטגוריה:", "choose_product": "בחר מוצר:",
        "qty": "כמות:", "cart_empty": "העגלה ריקה.", "cart": "ההזמנה שלך:",
        "confirm": "✅ אישור הזמנה", "edit": "✏️ המשך קניות", "clear": "❌ ריקון העגלה",
        "payment": "בחר אמצעי תשלום:", "cash": "💵 מזומן", "bybit": "🪙 Bybit",
        "done": "מכירה №{id} הושלמה. סכום: {sum}", "no_stock": "אין מספיק מלאי.",
        "enter_code": "שלח את קוד ההרשמה בהודעה אחת.",
        "code_created": "קוד חד-פעמי לתפקיד {role}: `{code}`",
        "choose_role": "בחר תפקיד לקוד החדש:",
        "enter_name_ru": "הזן שם מוצר ברוסית:",
        "enter_name_he": "הזן שם מוצר בעברית:",
        "enter_category": "הזן קטגוריה (beverages / sweets / food / other):",
        "enter_sell": "הזן מחיר מכירה בשקלים, למשל 5.50:",
        "enter_buy": "הזן מחיר קנייה בשקלים, למשל 3.00:",
        "enter_qty": "הזן כמות שלמה:",
        "enter_amount": "הזן סכום בשקלים:",
        "enter_note": "הזן הערה או סיבה:",
        "saved": "נשמר.", "bad_number": "מספר לא תקין. נסה שוב.",
        "support_text": "כתוב הודעה למנהל-העל:",
        "support_sent": "ההודעה נשלחה למנהל-העל.",
        "not_allowed": "אין הרשאה.", "balance": "יתרה {account}: {balance}",
        "expected": "יתרה צפויה: {expected}\nהזן את הסכום בפועל בשקלים:",
        "check_saved": "הבדיקה נשמרה. הפרש: {diff}",
        "my_sales_text": "המכירות האחרונות שלך:\n",
        "no_sales": "אין מכירות עדיין.", "report": "דוח לתקופה:\nמכירות: {n}\nהכנסה: {revenue}\nרווח: {profit}",
        "cancel_sale": "בחר מכירה לבקשת ביטול:", "reason_cancel": "ציין סיבה לביטול מכירה №{id}:",
        "cancel_requested": "בקשת הביטול נשלחה.",
        "pending": "בקשות ביטול ממתינות:", "approve": "✅ אישור", "reject": "❌ דחייה",
        "reply_to": "הזן Telegram ID של הנמען:", "reply_text": "הזן את תשובתך:",
        "reply_sent": "התשובה נשלחה.", "user_list": "משתמשים:\n",
        "product_saved": "המוצר נוצר עם ID {id}.", "select_existing": "בחר מוצר לשינוי/קבלת מלאי:",
        "edit_price": "הזן מחיר מכירה חדש בשקלים:",
        "stock_saved": "המלאי עודכן.", "finance_action": "בחר פעולה:",
        "deposit": "➕ הפקדה", "withdraw": "➖ משיכה",
        "choose_account": "בחר חשבון:", "cash_account": "מזומן", "bybit_account": "Bybit",
        "rank": "דירוג מוכרים (לפי מספר מכירות):\n",
        "help": "השתמש בכפתורי התפריט. לחזרה לחץ «חזרה».",
    }
}

ROLE_NAMES = {
    "ru": {"superadmin":"суперадминистратор","admin":"администратор","guarantor":"гарант","seller":"продавец"},
    "he": {"superadmin":"מנהל-על","admin":"מנהל","guarantor":"אחראי בדיקה","seller":"מוכר"}
}
CATEGORY_NAMES = {
    "ru": {"beverages":"Напитки","sweets":"Сладости","food":"Еда","other":"Другое"},
    "he": {"beverages":"משקאות","sweets":"ממתקים","food":"אוכל","other":"אחר"}
}


class Form(StatesGroup):
    invite_code = State()
    support_message = State()
    new_name_ru = State()
    new_name_he = State()
    new_category = State()
    new_sell = State()
    new_buy = State()
    receive_qty = State()
    amount = State()
    finance_note = State()
    check_actual = State()
    cancel_reason = State()
    reply_to = State()
    reply_text = State()
    edit_sell = State()
    report_range = State()
    edit_price = State()


class Abort(Exception):
    """Прерывает транзакцию (откат) по бизнес-условию."""


class OutOfStock(Exception):
    pass


# ---------------------------------------------------------------- helpers

def money(agorot, lang="ru"):
    return f"₪{int(agorot) / 100:.2f}"


def to_agorot(value):
    try:
        d = Decimal(value.replace(",", ".").strip())
        if d < 0:
            raise ValueError
        return int(d * 100)
    except (InvalidOperation, ValueError, AttributeError, OverflowError):
        raise ValueError("bad amount")


async def fetchone(sql, args=()):
    async with pool.acquire() as conn, conn.cursor(aiomysql.DictCursor) as cur:
        await cur.execute(sql, args)
        return await cur.fetchone()


async def fetchall(sql, args=()):
    async with pool.acquire() as conn, conn.cursor(aiomysql.DictCursor) as cur:
        await cur.execute(sql, args)
        return await cur.fetchall()


async def execute(sql, args=()):
    # pool работает в autocommit-режиме: одиночные запросы коммитятся сами
    async with pool.acquire() as conn, conn.cursor() as cur:
        await cur.execute(sql, args)
        return cur.lastrowid


@asynccontextmanager
async def tx(dict_cursor=False):
    """Транзакция: commit при успехе, rollback при любом исключении."""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor if dict_cursor else aiomysql.Cursor) as cur:
            await conn.begin()
            try:
                yield cur
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise


async def audit(uid, action, details=""):
    await execute("INSERT INTO audit_log(actor_telegram_id,action,details) VALUES(%s,%s,%s)",
                  (uid, action, details[:4000]))


async def get_user(uid):
    return await fetchone("SELECT * FROM users WHERE telegram_id=%s", (uid,))


def lang_for(u):
    return u["language"] if u else "ru"


async def lang_of(uid):
    return lang_for(await get_user(uid))


def t(lang, key):
    return TEXT[lang].get(key, TEXT["ru"].get(key, key))


async def tr(uid, key):
    return t(await lang_of(uid), key)


def has_role(u, roles):
    return bool(u and not u["is_blocked"] and u["role"] in roles)


async def allowed(uid, roles):
    return has_role(await get_user(uid), roles)


async def guard(call, roles):
    """Возвращает пользователя, если роль подходит; иначе отвечает 'нет прав' и возвращает None."""
    u = await get_user(call.from_user.id)
    if has_role(u, roles):
        return u
    await call.answer(t(lang_for(u), "not_allowed"), show_alert=True)
    return None


def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
        for row in rows
    ])


def back_kb(lang, target="menu:main"):
    return kb([[(t(lang, "back"), target)]])


def menu_kb(u):
    lang = lang_for(u)
    role = u["role"] if u else "seller"
    x = TEXT[lang]
    rows = [[(x["sale"], "sale:start"), (x["stock"], "stock:view")]]
    if role in ADMINS:
        rows += [[(x["products"], "products:menu"), (x["receive"], "stock:receive")],
                 [(x["finance"], "finance:menu"), (x["reports"], "reports:menu")],
                 [(x["checks"], "checks:menu"), (x["cancel"], "cancel:list")]]
    elif role == "guarantor":
        rows.append([(x["checks"], "checks:menu")])
    if role == "superadmin":
        rows.append([(x["users"], "users:menu")])
    rows += [[(x["my_sales"], "sales:mine"), (x["support"], "support:start")],
             [(x["language"], "language:choose")]]
    return kb(rows)


async def main_menu(uid):
    return menu_kb(await get_user(uid))


async def say(target, uid, key, **kw):
    """Отправить локализованный текст (target — Message)."""
    await target.answer(await tr(uid, key), **kw)


async def answer_menu(message, uid, key, **fmt):
    """Ответить локализованным текстом + главное меню (один запрос к БД)."""
    u = await get_user(uid)
    text = t(lang_for(u), key)
    await message.answer(text.format(**fmt) if fmt else text, reply_markup=menu_kb(u))


async def read_amount(message):
    """Сумма в агорот из текста сообщения; при ошибке отвечает и возвращает None."""
    try:
        return to_agorot(message.text)
    except ValueError:
        await say(message, message.from_user.id, "bad_number")
        return None


BALANCE_SQL = (
    "SELECT COALESCE(SUM(CASE WHEN operation_type IN ('deposit','sale') THEN amount_agorot "
    "WHEN operation_type IN ('withdrawal','sale_cancellation') THEN -amount_agorot "
    "ELSE amount_agorot END),0) AS bal FROM cash_operations WHERE account=%s"
)


async def account_balance(account):
    row = await fetchone(BALANCE_SQL, (account,))
    return int(row["bal"] or 0)


# ---------------------------------------------------------------- start / language / registration

@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    uid = message.from_user.id
    user = await get_user(uid)
    if uid == SUPERADMIN_ID:
        if not user:
            await execute(
                "INSERT INTO users(telegram_id,username,full_name,role,language) VALUES(%s,%s,%s,'superadmin','ru')",
                (uid, message.from_user.username, message.from_user.full_name))
            user = await get_user(uid)
        elif user["role"] != "superadmin":
            await execute("UPDATE users SET role='superadmin',is_blocked=0 WHERE telegram_id=%s", (uid,))
            user = {**user, "role": "superadmin", "is_blocked": 0}
    if user and user["is_blocked"]:
        await message.answer(t(user["language"], "blocked"))
        return
    if not user:
        await state.clear()
        await ask_language(message)
        return
    await message.answer(t(user["language"], "welcome"), reply_markup=menu_kb(user))


LANG_PROMPT = "Выберите язык интерфейса / בחר שפת ממשק"


async def ask_language(message):
    """Первый шаг для нового пользователя: выбор языка интерфейса."""
    await message.answer(LANG_PROMPT, reply_markup=kb([
        [("🇷🇺 Русский", "initlang:ru"), ("🇮🇱 עברית", "initlang:he")]]))


@dp.callback_query(F.data.startswith("initlang:"))
async def init_language(call: CallbackQuery, state: FSMContext):
    if await get_user(call.from_user.id):  # уже зарегистрирован — язык меняется кнопкой «Язык»
        await call.answer()
        return
    lang = call.data.split(":")[1]
    if lang not in ("ru", "he"):
        await call.answer()
        return
    await state.update_data(lang=lang)
    await state.set_state(Form.invite_code)
    await call.message.edit_text(TEXT[lang]["enter_code"])
    await call.answer()


@dp.message(Command("cancel"))
async def cancel_state(message: Message, state: FSMContext):
    await state.clear()
    await answer_menu(message, message.from_user.id, "cancelled")


@dp.callback_query(F.data == "language:choose")
async def choose_language(call: CallbackQuery):
    await call.message.edit_text("Выберите язык / בחר שפה", reply_markup=kb([
        [("🇷🇺 Русский", "language:ru"), ("🇮🇱 עברית", "language:he")]]))
    await call.answer()


@dp.callback_query(F.data.startswith("language:"))
async def set_language(call: CallbackQuery):
    lang = call.data.split(":")[1]
    if lang not in ("ru", "he"):
        await call.answer()
        return
    await execute("UPDATE users SET language=%s WHERE telegram_id=%s", (lang, call.from_user.id))
    await call.message.edit_text(TEXT[lang]["menu"], reply_markup=await main_menu(call.from_user.id))
    await call.answer()


@dp.message(Form.invite_code)
async def register(message: Message, state: FSMContext):
    lang = (await state.get_data()).get("lang", "ru")
    code_hash = hashlib.sha256(message.text.strip().encode()).hexdigest()
    logging.info("register: user %s sent a code (len=%s)", message.from_user.id, len(message.text.strip()))
    invite = await fetchone("SELECT * FROM auth_invites WHERE code_hash=%s AND used_by IS NULL", (code_hash,))
    if not invite:
        await message.answer(TEXT[lang]["bad_code"])
        return
    uid = message.from_user.id
    try:
        async with tx() as cur:
            await cur.execute("SELECT id FROM auth_invites WHERE id=%s AND used_by IS NULL FOR UPDATE",
                              (invite["id"],))
            if not await cur.fetchone():
                raise Abort
            await cur.execute(
                "INSERT INTO users(telegram_id,username,full_name,role,language) VALUES(%s,%s,%s,%s,%s)",
                (uid, message.from_user.username, message.from_user.full_name, invite["role"], lang))
            await cur.execute("UPDATE auth_invites SET used_by=%s,used_at=NOW() WHERE id=%s", (uid, invite["id"]))
    except (Abort, aiomysql.IntegrityError):
        await message.answer(TEXT[lang]["bad_code"])
        return
    await state.clear()
    await audit(uid, "register", invite["role"])
    await answer_menu(message, uid, "registered")


# ---------------------------------------------------------------- sale

# Sale cart lives in FSM state; database is changed only at final payment.
async def show_categories(call, state, reset):
    """Список категорий. reset=True начинает новую продажу (пустая корзина),
    reset=False возвращает к выбору товара, не трогая корзину."""
    u = await get_user(call.from_user.id)
    lang = lang_for(u)
    if not u or u["is_blocked"]:
        await call.answer(t(lang, "blocked"), show_alert=True)
        return
    if reset:
        await state.update_data(cart={})
    categories = await fetchall("SELECT DISTINCT category FROM products WHERE is_active=1 ORDER BY category")
    if not categories:
        await call.message.edit_text(t(lang, "no_products"))
    else:
        rows = [[(CATEGORY_NAMES[lang].get(c["category"], c["category"]), f"sale:cat:{c['category']}")]
                for c in categories]
        rows.append([(t(lang, "back"), "menu:main")])
        await call.message.edit_text(t(lang, "choose_category"), reply_markup=kb(rows))
    await call.answer()


@dp.callback_query(F.data == "sale:start")
async def sale_start(call: CallbackQuery, state: FSMContext):
    await show_categories(call, state, reset=True)


@dp.callback_query(F.data == "sale:more")
async def sale_more(call: CallbackQuery, state: FSMContext):
    await show_categories(call, state, reset=False)


QUICK_QTY = (1, 2, 3, 5, 10)


async def safe_edit(call, text, markup=None):
    """edit_text, который не падает, если текст и кнопки не изменились."""
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


async def show_products(call, state, cat):
    """Список товаров категории. Товары из корзины помечены ✅ ×N, можно сразу выбирать следующие."""
    lang = await lang_of(call.from_user.id)
    cart = (await state.get_data()).get("cart", {})
    products = await fetchall(
        "SELECT id,name_ru,name_he,sell_price_agorot,stock FROM products WHERE is_active=1 AND category=%s ORDER BY name_ru",
        (cat,))
    name_key = "name_ru" if lang == "ru" else "name_he"
    left = "ост." if lang == "ru" else "מלאי"
    rows = []
    for p in products:
        qty = int(cart.get(str(p["id"]), 0))
        mark = f"✅ ×{qty} " if qty > 0 else ""
        rows.append([(f'{mark}{p[name_key]} — {money(p["sell_price_agorot"])} ({left} {p["stock"]})'[:60],
                      f"sale:product:{p['id']}")])
    units = sum(int(q) for q in cart.values() if int(q) > 0)
    if units:
        rows.append([(f"🛒 Корзина ({units})" if lang == "ru" else f"🛒 עגלה ({units})", "sale:cart")])
    rows.append([(t(lang, "back"), "sale:more")])
    await safe_edit(call, t(lang, "choose_product"), kb(rows))


@dp.callback_query(F.data.startswith("sale:cat:"))
async def sale_category(call: CallbackQuery, state: FSMContext):
    cat = call.data.split(":", 2)[2]
    await state.update_data(last_cat=cat)
    await show_products(call, state, cat)
    await call.answer()


@dp.callback_query(F.data == "sale:done")
async def sale_done(call: CallbackQuery, state: FSMContext):
    cat = (await state.get_data()).get("last_cat")
    if not cat:
        await show_categories(call, state, reset=False)
        return
    await show_products(call, state, cat)
    await call.answer()


async def show_picker(call, state, p, lang):
    """Экран товара: количество выбирается сразу (кнопки 1/2/3/5/10 и ➖/➕)."""
    ru = lang == "ru"
    cart = (await state.get_data()).get("cart", {})
    qty = int(cart.get(str(p["id"]), 0))
    price, stock = int(p["sell_price_agorot"]), int(p["stock"])
    text = "\n".join([
        p["name_ru" if ru else "name_he"],
        f'{"Цена" if ru else "מחיר"}: {money(price)} · {"ост." if ru else "מלאי"}: {stock}',
        "",
        f'{"В корзине" if ru else "בעגלה"}: {qty} × {money(price)} = {money(qty * price)}',
        "",
        "Выберите количество:" if ru else "בחר כמות:",
    ])
    pid = p["id"]
    rows = [[("➖", f"sale:adj:{pid}:-1"), ("➕", f"sale:adj:{pid}:1")]]
    quick = [(f"✓{n}" if n == qty else str(n), f"sale:set:{pid}:{n}") for n in QUICK_QTY if n <= stock]
    if quick:
        rows.append(quick)
    rows.append([("⬅️ К товарам" if ru else "⬅️ למוצרים", "sale:done"),
                 ("🛒 Корзина" if ru else "🛒 עגלה", "sale:cart")])
    await safe_edit(call, text, kb(rows))


@dp.callback_query(F.data.startswith("sale:product:"))
async def sale_product(call: CallbackQuery, state: FSMContext):
    pid = int(call.data.rsplit(":", 1)[1])
    lang = await lang_of(call.from_user.id)
    p = await fetchone("SELECT * FROM products WHERE id=%s AND is_active=1", (pid,))
    if not p:
        await call.answer("Product not found", show_alert=True)
        return
    stock = int(p["stock"])
    if stock <= 0:
        await call.answer(t(lang, "no_stock"), show_alert=True)
        return
    cart = (await state.get_data()).get("cart", {})
    key = str(pid)
    cart[key] = min(max(int(cart.get(key, 0)), 1), stock)  # первый тап = 1 шт., дальше можно изменить
    await state.update_data(cart=cart)
    await show_picker(call, state, p, lang)
    await call.answer()


async def apply_qty(call, state, pid, compute):
    """Меняет количество товара в корзине (не больше остатка) и обновляет экран товара."""
    lang = await lang_of(call.from_user.id)
    p = await fetchone("SELECT * FROM products WHERE id=%s AND is_active=1", (pid,))
    if not p:
        await call.answer("Product not found", show_alert=True)
        return
    stock = int(p["stock"])
    cart = (await state.get_data()).get("cart", {})
    key = str(pid)
    want = compute(int(cart.get(key, 0)))
    qty = max(0, min(want, stock))
    if qty:
        cart[key] = qty
    else:
        cart.pop(key, None)
    await state.update_data(cart=cart)
    await show_picker(call, state, p, lang)
    if want > stock:
        await call.answer(f"Максимум на складе: {stock}" if lang == "ru" else f"מקסימום במלאי: {stock}")
    else:
        await call.answer()


@dp.callback_query(F.data.startswith("sale:set:"))
async def sale_set_qty(call: CallbackQuery, state: FSMContext):
    _, _, pid, n = call.data.split(":")
    await apply_qty(call, state, int(pid), lambda cur: int(n))


@dp.callback_query(F.data.startswith("sale:adj:"))
async def sale_adjust_qty(call: CallbackQuery, state: FSMContext):
    _, _, pid, delta = call.data.split(":")
    await apply_qty(call, state, int(pid), lambda cur: cur + int(delta))


@dp.callback_query(F.data == "sale:cart")
async def sale_cart(call: CallbackQuery, state: FSMContext):
    await show_cart(call, state)
    await call.answer()


async def show_cart(call, state):
    cart = (await state.get_data()).get("cart", {})
    lang = await lang_of(call.from_user.id)
    # один запрос вместо запроса на каждую позицию корзины
    ids = [int(pid) for pid, qty in cart.items() if qty > 0]
    products = {}
    if ids:
        found = await fetchall(f"SELECT * FROM products WHERE id IN ({','.join(['%s'] * len(ids))})", tuple(ids))
        products = {p["id"]: p for p in found}
    lines = []
    total = 0
    for pid, qty in cart.items():
        p = products.get(int(pid))
        if not p or qty <= 0:
            continue
        line = int(p["sell_price_agorot"]) * qty
        total += line
        lines.append(f'{p["name_ru" if lang == "ru" else "name_he"]} × {qty} = {money(line)}')
    if not lines:
        await call.message.edit_text(t(lang, "cart_empty"), reply_markup=back_kb(lang, "sale:more"))
        return
    await call.message.edit_text(
        t(lang, "cart") + "\n" + "\n".join(lines) + f"\n\n{'Итого' if lang == 'ru' else 'סה״כ'}: {money(total)}",
        reply_markup=kb([
            [("➕ Ещё товар" if lang == "ru" else "➕ מוצר נוסף", "sale:more"),
             ("➖ Уменьшить" if lang == "ru" else "➖ הפחת", "sale:decrease")],
            [(t(lang, "confirm"), "sale:confirm"), (t(lang, "clear"), "sale:clear")],
            [(t(lang, "back"), "menu:main")]
        ]))


@dp.callback_query(F.data == "sale:decrease")
async def sale_decrease(call: CallbackQuery, state: FSMContext):
    cart = (await state.get_data()).get("cart", {})
    if cart:
        pid = next(reversed(cart))
        cart[pid] -= 1
        if cart[pid] <= 0:
            del cart[pid]
        await state.update_data(cart=cart)
    await show_cart(call, state)
    await call.answer()


@dp.callback_query(F.data == "sale:clear")
async def sale_clear(call: CallbackQuery, state: FSMContext):
    await state.update_data(cart={})
    await show_cart(call, state)
    await call.answer()


@dp.callback_query(F.data == "sale:confirm")
async def sale_confirm(call: CallbackQuery, state: FSMContext):
    cart = (await state.get_data()).get("cart", {})
    lang = await lang_of(call.from_user.id)
    if not cart:
        await call.answer(t(lang, "cart_empty"), show_alert=True)
        return
    await call.message.edit_text(t(lang, "payment"), reply_markup=kb([
        [(t(lang, "cash"), "sale:pay:cash"), (t(lang, "bybit"), "sale:pay:bybit")],
        [(t(lang, "back"), "sale:more")]
    ]))
    await call.answer()


@dp.callback_query(F.data.startswith("sale:pay:"))
async def sale_payment(call: CallbackQuery, state: FSMContext):
    uid = call.from_user.id
    method = call.data.rsplit(":", 1)[1]
    cart = (await state.get_data()).get("cart", {})
    lang = await lang_of(uid)
    if not cart:
        await call.answer(t(lang, "cart_empty"), show_alert=True)
        return
    try:
        async with tx(True) as cur:
            total = cost = 0
            items = []
            for pid_s, qty in cart.items():
                await cur.execute("SELECT * FROM products WHERE id=%s AND is_active=1 FOR UPDATE", (int(pid_s),))
                p = await cur.fetchone()
                if not p or p["stock"] < qty:
                    raise OutOfStock
                total += p["sell_price_agorot"] * qty
                cost += p["buy_price_agorot"] * qty
                items.append((p, qty))
            await cur.execute(
                "INSERT INTO sales(seller_telegram_id,total_agorot,total_cost_agorot,payment_method) VALUES(%s,%s,%s,%s)",
                (uid, total, cost, method))
            sale_id = cur.lastrowid
            for p, qty in items:
                await cur.execute("UPDATE products SET stock=stock-%s WHERE id=%s", (qty, p["id"]))
                await cur.execute(
                    "INSERT INTO sale_items(sale_id,product_id,product_name_ru,product_name_he,quantity,unit_sell_agorot,unit_buy_agorot) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                    (sale_id, p["id"], p["name_ru"], p["name_he"], qty, p["sell_price_agorot"], p["buy_price_agorot"]))
                await cur.execute(
                    "INSERT INTO stock_operations(product_id,operation_type,quantity_change,note,author_telegram_id) VALUES(%s,'sale',%s,%s,%s)",
                    (p["id"], -qty, f"sale #{sale_id}", uid))
            await cur.execute(
                "INSERT INTO cash_operations(account,operation_type,amount_agorot,note,related_sale_id,author_telegram_id) VALUES(%s,'sale',%s,%s,%s,%s)",
                (method, total, f"sale #{sale_id}", sale_id, uid))
    except OutOfStock:
        await call.answer(t(lang, "no_stock"), show_alert=True)
        return
    except Exception:
        await call.answer("Database error. Contact admin.", show_alert=True)
        raise
    await state.update_data(cart={})
    await call.message.edit_text(t(lang, "done").format(id=sale_id, sum=money(total)))
    await audit(uid, "sale_completed", f"sale={sale_id}; total={total}; payment={method}")
    await call.answer()


@dp.callback_query(F.data == "stock:view")
async def stock_view(call: CallbackQuery):
    lang = await lang_of(call.from_user.id)
    rows = await fetchall(
        "SELECT name_ru,name_he,stock,sell_price_agorot FROM products WHERE is_active=1 ORDER BY category,name_ru")
    name_key = "name_ru" if lang == "ru" else "name_he"
    unit = "шт." if lang == "ru" else "יח׳"
    if rows:
        total_value = sum(int(p["stock"]) * int(p["sell_price_agorot"]) for p in rows)
        header = ("💰 Товара на продажу на сумму: " if lang == "ru" else "💰 מוצרים למכירה בסכום של: ") + money(total_value)
        lines = [f'{p[name_key]}: {p["stock"]} {unit} — {money(p["sell_price_agorot"])}' for p in rows]
        text = header + "\n\n" + "\n".join(lines)
        if len(text) > 4000:
            text = text[:3990] + "\n…"
    else:
        text = t(lang, "no_products")
    await call.message.edit_text(text, reply_markup=kb([
        [("📄 Скачать PDF" if lang == "ru" else "📄 הורד PDF", "stock:pdf")],
        [(t(lang, "back"), "menu:main")]]))
    await call.answer()


# ---------------------------------------------------------------- PDF остатков

_PDF_FONTS = None


def _register_pdf_fonts():
    """Ищет шрифт с кириллицей и ивритом (Arial на Windows) и регистрирует его в reportlab."""
    global _PDF_FONTS
    if _PDF_FONTS:
        return _PDF_FONTS
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    win = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    candidates = [
        (BASE_DIR / "font.ttf", BASE_DIR / "font-bold.ttf"),  # свой шрифт рядом со скриптом
        (win / "arial.ttf", win / "arialbd.ttf"),
        (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
         Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
        (Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
         Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf")),
        (Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
         Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf")),
    ]
    for regular, bold in candidates:
        if regular.exists():
            pdfmetrics.registerFont(TTFont("BotFont", str(regular)))
            pdfmetrics.registerFont(TTFont("BotFont-Bold", str(bold if bold.exists() else regular)))
            _PDF_FONTS = ("BotFont", "BotFont-Bold")
            return _PDF_FONTS
    raise RuntimeError("Не найден шрифт с кириллицей и ивритом. Положите arial.ttf как font.ttf рядом со скриптом.")


def _visual_rtl(text):
    """Иврит пишется справа налево, а reportlab рисует слева направо: переупорядочиваем символы."""
    try:
        from bidi.algorithm import get_display
        return get_display(text)
    except ImportError:
        # запасной вариант без python-bidi: меняем порядок символов, но числа и латиницу оставляем целыми
        import re
        tokens = re.findall(r"[0-9A-Za-z][0-9A-Za-z.,:%/\-]*|.", text, re.S)
        return "".join(reversed(tokens))


def build_stock_pdf(rows, lang, show_buy=False):
    """PDF: наименование, количество, цена продажи (и цена закупки для администраторов). Возвращает bytes."""
    import io
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    font, font_bold = _register_pdf_fonts()
    ru = lang == "ru"
    fix = (lambda x: x) if ru else _visual_rtl
    name_key = "name_ru" if ru else "name_he"
    align = TA_LEFT if ru else TA_RIGHT
    size = 10

    title = "Остатки товара" if ru else "מלאי מוצרים"
    headers = ["№", "Наименование", "Кол-во", "Цена продажи"] if ru else ["#", "שם מוצר", "כמות", "מחיר מכירה"]
    if show_buy:
        headers.append("Цена закупки" if ru else "מחיר קנייה")
    total_label = "Итого" if ru else "סה״כ"

    widths = [12 * mm, 66 * mm, 22 * mm, 40 * mm, 40 * mm] if show_buy else [12 * mm, 90 * mm, 28 * mm, 50 * mm]

    def fit(text, width):
        """Обрезает слишком длинное название, чтобы оно не вылезало за ячейку."""
        limit = width - 8
        if stringWidth(text, font, size) <= limit:
            return text
        while text and stringWidth(text + "…", font, size) > limit:
            text = text[:-1]
        return text + "…"

    data = [[fix(h) for h in headers]]
    total_qty = sell_value = buy_value = 0
    for i, p in enumerate(rows, 1):
        qty = int(p["stock"])
        sell, buy = int(p["sell_price_agorot"]), int(p["buy_price_agorot"])
        total_qty += qty
        sell_value += qty * sell
        buy_value += qty * buy
        line = [str(i), fix(fit(str(p[name_key]), widths[1])), str(qty), money(sell)]
        if show_buy:
            line.append(money(buy))
        data.append(line)
    data.append(["", fix(total_label), str(total_qty)] + [""] * (len(headers) - 3))

    name_col = 1
    if not ru:  # для иврита колонки идут справа налево
        data = [list(reversed(r)) for r in data]
        widths = list(reversed(widths))
        name_col = len(headers) - 2
    last = len(data) - 1

    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font),
        ("FONTNAME", (0, 0), (-1, 0), font_bold),
        ("FONTNAME", (0, last), (-1, last), font_bold),
        ("FONTSIZE", (0, 0), (-1, -1), size),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2f4858")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, last - 1), [colors.white, colors.HexColor("#eef2f5")]),
        ("BACKGROUND", (0, last), (-1, last), colors.HexColor("#d9e2e8")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b8c4cc")),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ALIGN", (name_col, 0), (name_col, -1), "LEFT" if ru else "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))

    title_style = ParagraphStyle("t", fontName=font_bold, fontSize=18, leading=22, alignment=align)
    date_style = ParagraphStyle("d", fontName=font, fontSize=10, leading=14, alignment=align,
                                textColor=colors.HexColor("#555555"))
    sum_style = ParagraphStyle("s", fontName=font_bold, fontSize=11, leading=16, alignment=align)

    summary = [fix(("Стоимость остатков по цене продажи: " if ru else "שווי המלאי במחיר מכירה: ") + money(sell_value))]
    if show_buy:
        summary.append(fix(("Стоимость остатков по цене закупки: " if ru else "שווי המלאי במחיר קנייה: ") + money(buy_value)))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=15 * mm, bottomMargin=15 * mm, title=title)
    doc.build([
        Paragraph(fix(title), title_style),
        Paragraph(datetime.now().strftime("%d.%m.%Y %H:%M"), date_style),
        Spacer(1, 8 * mm),
        table,
        Spacer(1, 6 * mm),
    ] + [Paragraph(line, sum_style) for line in summary])
    return buf.getvalue()


@dp.callback_query(F.data == "stock:pdf")
async def stock_pdf(call: CallbackQuery):
    u = await get_user(call.from_user.id)
    lang = lang_for(u)
    if not u or u["is_blocked"]:
        await call.answer(t(lang, "blocked"), show_alert=True)
        return
    rows = await fetchall(
        "SELECT name_ru,name_he,stock,sell_price_agorot,buy_price_agorot FROM products "
        "WHERE is_active=1 ORDER BY category,name_ru")
    if not rows:
        await call.answer(t(lang, "no_products"), show_alert=True)
        return
    await call.answer("⏳")
    try:
        # цену закупки видят только администраторы
        data = await asyncio.to_thread(build_stock_pdf, rows, lang, has_role(u, ADMINS))
    except Exception:
        logging.exception("PDF generation failed")
        await call.message.answer(
            "Не удалось создать PDF. Проверьте: pip install reportlab python-bidi" if lang == "ru"
            else "יצירת ה-PDF נכשלה. בדוק: pip install reportlab python-bidi")
        return
    await call.message.answer_document(
        BufferedInputFile(data, filename=f"stock_{datetime.now():%Y-%m-%d}.pdf"),
        caption="📄 Остатки товара" if lang == "ru" else "📄 מלאי מוצרים")


@dp.callback_query(F.data == "sales:mine")
async def sales_mine(call: CallbackQuery):
    uid = call.from_user.id
    lang = await lang_of(uid)
    ru = lang == "ru"
    name_key = "name_ru" if ru else "name_he"
    sales, totals, by_product = await asyncio.gather(
        fetchall("SELECT id,total_agorot,payment_method,confirmed_at,status FROM sales "
                 "WHERE seller_telegram_id=%s ORDER BY id DESC LIMIT 15", (uid,)),
        fetchone("SELECT COUNT(*) n,COALESCE(SUM(total_agorot),0) total FROM sales "
                 "WHERE seller_telegram_id=%s AND status='completed'", (uid,)),
        fetchall("SELECT i.product_name_ru name_ru,i.product_name_he name_he,SUM(i.quantity) qty,"
                 "SUM(i.quantity*i.unit_sell_agorot) revenue FROM sale_items i "
                 "JOIN sales s ON s.id=i.sale_id WHERE s.seller_telegram_id=%s AND s.status='completed' "
                 "GROUP BY i.product_name_ru,i.product_name_he ORDER BY revenue DESC LIMIT 10", (uid,)))
    if not sales:
        await call.message.edit_text(t(lang, "no_sales"), reply_markup=back_kb(lang))
        await call.answer()
        return
    items = await fetchall(
        f"SELECT sale_id,product_name_ru name_ru,product_name_he name_he,quantity FROM sale_items "
        f"WHERE sale_id IN ({','.join(['%s'] * len(sales))}) ORDER BY id", tuple(s_["id"] for s_ in sales))
    items_by_sale = {}
    for it in items:
        items_by_sale.setdefault(it["sale_id"], []).append(f'{it[name_key]} ×{it["quantity"]}')

    lines = [("💰 Всего продано за всё время: " if ru else "💰 סה״כ נמכר בכל הזמן: ")
             + f'{money(totals["total"])} ({totals["n"]} ' + ("продаж)" if ru else "מכירות)")]
    if by_product:
        lines.append("\n" + ("📦 По товарам (за всё время):" if ru else "📦 לפי מוצרים (כל הזמן):"))
        lines += [f'• {p[name_key]} — {int(p["qty"])} ' + ("шт." if ru else "יח׳") + f' — {money(p["revenue"])}'
                  for p in by_product]
    lines.append("\n" + ("🧾 Последние продажи:" if ru else "🧾 מכירות אחרונות:"))
    for r in sales:
        lines.append(f'#{r["id"]} — {money(r["total_agorot"])} — {r["payment_method"]} — {r["status"]} — {r["confirmed_at"]}')
        lines.append("   " + ", ".join(items_by_sale.get(r["id"], [])))
    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3990] + "\n…"
    await call.message.edit_text(text, reply_markup=back_kb(lang))
    await call.answer()


# ---------------------------------------------------------------- products

# Product creation wizard
@dp.callback_query(F.data == "products:menu")
async def products_menu(call: CallbackQuery):
    u = await guard(call, ADMINS)
    if not u:
        return
    lang = u["language"]
    await call.message.edit_text("🛍", reply_markup=kb([
        [("➕ Добавить товар / הוסף מוצר", "products:add")],
        [("💲 Изменить цену / שינוי מחיר", "products:edit_price")],
        [("🗑 Удалить товар / מחק מוצר", "products:delete")],
        [(t(lang, "back"), "menu:main")]
    ]))
    await call.answer()


# Изменение цены товара: выбрать товар → какую цену менять → ввести новое значение.
# Старые продажи не меняются: в них цена сохранена на момент продажи.
@dp.callback_query(F.data == "products:edit_price")
async def price_list(call: CallbackQuery, state: FSMContext):
    u = await guard(call, ADMINS)
    if not u:
        return
    await state.clear()
    lang = u["language"]
    name_key = "name_ru" if lang == "ru" else "name_he"
    products = await fetchall(
        "SELECT id,name_ru,name_he,sell_price_agorot FROM products WHERE is_active=1 ORDER BY name_ru")
    rows = [[(f'{p[name_key]} — {money(p["sell_price_agorot"])}'[:60], f"products:ep:{p['id']}")] for p in products]
    rows.append([(t(lang, "back"), "products:menu")])
    text = ("Выберите товар для изменения цены:" if lang == "ru" else "בחר מוצר לשינוי מחיר:") if products \
        else t(lang, "no_products")
    await call.message.edit_text(text, reply_markup=kb(rows))
    await call.answer()


@dp.callback_query(F.data.startswith("products:ep:"))
async def price_choose_field(call: CallbackQuery):
    u = await guard(call, ADMINS)
    if not u:
        return
    lang = u["language"]
    ru = lang == "ru"
    pid = int(call.data.rsplit(":", 1)[1])
    p = await fetchone("SELECT * FROM products WHERE id=%s AND is_active=1", (pid,))
    if not p:
        await call.answer("Товар не найден / מוצר לא נמצא", show_alert=True)
        return
    text = "\n".join([
        p["name_ru" if ru else "name_he"],
        f'{"Цена продажи" if ru else "מחיר מכירה"}: {money(p["sell_price_agorot"])}',
        f'{"Цена закупки" if ru else "מחיר קנייה"}: {money(p["buy_price_agorot"])}',
        "",
        "Какую цену изменить?" if ru else "איזה מחיר לשנות?",
    ])
    await call.message.edit_text(text, reply_markup=kb([
        [("💲 Цена продажи" if ru else "💲 מחיר מכירה", f"products:epf:{pid}:sell"),
         ("📥 Цена закупки" if ru else "📥 מחיר קנייה", f"products:epf:{pid}:buy")],
        [(t(lang, "back"), "products:edit_price")]
    ]))
    await call.answer()


@dp.callback_query(F.data.startswith("products:epf:"))
async def price_ask_value(call: CallbackQuery, state: FSMContext):
    u = await guard(call, ADMINS)
    if not u:
        return
    ru = u["language"] == "ru"
    _, _, pid, which = call.data.split(":")
    if which not in ("sell", "buy"):
        await call.answer()
        return
    p = await fetchone("SELECT * FROM products WHERE id=%s AND is_active=1", (int(pid),))
    if not p:
        await call.answer("Товар не найден / מוצר לא נמצא", show_alert=True)
        return
    field = "sell_price_agorot" if which == "sell" else "buy_price_agorot"
    await state.update_data(price_pid=int(pid), price_field=field)
    await state.set_state(Form.edit_price)
    label = ("цену продажи" if which == "sell" else "цену закупки") if ru \
        else ("מחיר מכירה" if which == "sell" else "מחיר קנייה")
    await call.message.answer(
        (f'{p["name_ru"]}: сейчас {money(p[field])}\nВведите новую {label} в шекелях (например 8.50). Отмена: /cancel'
         if ru else
         f'{p["name_he"]}: כרגע {money(p[field])}\nהזן {label} חדש בשקלים (למשל 8.50). ביטול: /cancel'))
    await call.answer()


@dp.message(Form.edit_price)
async def price_save(message: Message, state: FSMContext):
    uid = message.from_user.id
    u = await get_user(uid)
    if not has_role(u, ADMINS):
        await state.clear()
        await message.answer(t(lang_for(u), "not_allowed"))
        return
    ru = lang_for(u) == "ru"
    value = await read_amount(message)
    if value is None:
        return
    if value <= 0:
        await say(message, uid, "bad_number")
        return
    data = await state.get_data()
    pid, field = data.get("price_pid"), data.get("price_field")
    if not pid or field not in ("sell_price_agorot", "buy_price_agorot"):
        await state.clear()
        await message.answer("Начните заново: Товары → Изменить цену" if ru else "התחל מחדש: מוצרים ← שינוי מחיר")
        return
    old = await fetchone(f"SELECT name_ru,name_he,{field} AS price FROM products WHERE id=%s", (pid,))
    if not old:
        await state.clear()
        await message.answer("Товар не найден / מוצר לא נמצא")
        return
    await execute(f"UPDATE products SET {field}=%s WHERE id=%s", (value, pid))
    await audit(uid, "price_changed", f"product={pid};{field};{old['price']}->{value}")
    await state.clear()
    await message.answer(
        (f'✅ Цена изменена: {old["name_ru"]} — {money(old["price"])} → {money(value)}' if ru
         else f'✅ המחיר עודכן: {old["name_he"]} — {money(old["price"])} ← {money(value)}'),
        reply_markup=menu_kb(u))


# Удаление товара: мягкое (is_active=0), чтобы старые продажи и отчёты остались целыми.
@dp.callback_query(F.data == "products:delete")
async def product_delete_list(call: CallbackQuery):
    u = await guard(call, ADMINS)
    if not u:
        return
    lang = u["language"]
    products = await fetchall("SELECT id,name_ru,name_he,stock FROM products WHERE is_active=1 ORDER BY name_ru")
    rows = [[(f'🗑 {p["name_ru"]} / {p["name_he"]} ({p["stock"]})'[:60], f"products:del:{p['id']}")]
            for p in products]
    rows.append([(t(lang, "back"), "products:menu")])
    text = ("Выберите товар для удаления:" if lang == "ru" else "בחר מוצר למחיקה:") if products else t(lang, "no_products")
    await call.message.edit_text(text, reply_markup=kb(rows))
    await call.answer()


@dp.callback_query(F.data.startswith("products:del:"))
async def product_delete_confirm(call: CallbackQuery):
    u = await guard(call, ADMINS)
    if not u:
        return
    lang = u["language"]
    pid = int(call.data.rsplit(":", 1)[1])
    p = await fetchone("SELECT id,name_ru,name_he,stock FROM products WHERE id=%s AND is_active=1", (pid,))
    if not p:
        await call.answer("Товар не найден / מוצר לא נמצא", show_alert=True)
        return
    ask = "Удалить товар?" if lang == "ru" else "למחוק את המוצר?"
    left = "Остаток" if lang == "ru" else "מלאי"
    await call.message.edit_text(
        f'{ask}\n{p["name_ru"]} / {p["name_he"]}\n{left}: {p["stock"]}',
        reply_markup=kb([[("✅ Да, удалить" if lang == "ru" else "✅ כן, מחק", f"products:delok:{pid}"),
                          ("❌ Отмена" if lang == "ru" else "❌ ביטול", "products:delete")]]))
    await call.answer()


@dp.callback_query(F.data.startswith("products:delok:"))
async def product_delete_do(call: CallbackQuery):
    u = await guard(call, ADMINS)
    if not u:
        return
    lang = u["language"]
    pid = int(call.data.rsplit(":", 1)[1])
    async with tx() as cur:
        await cur.execute("UPDATE products SET is_active=0 WHERE id=%s AND is_active=1", (pid,))
        deleted = cur.rowcount
    if not deleted:
        await call.answer("Товар не найден / מוצר לא נמצא", show_alert=True)
        return
    await audit(call.from_user.id, "product_deleted", f"product={pid}")
    await call.message.edit_text("Товар удалён ✅" if lang == "ru" else "המוצר נמחק ✅",
                                 reply_markup=back_kb(lang, "products:delete"))
    await call.answer()


@dp.callback_query(F.data == "products:add")
async def product_add(call: CallbackQuery, state: FSMContext):
    u = await guard(call, ADMINS)
    if not u:
        return
    await state.set_state(Form.new_name_ru)
    await call.message.answer(t(u["language"], "enter_name_ru"))
    await call.answer()


@dp.message(Form.new_name_ru)
async def product_name_ru(message: Message, state: FSMContext):
    await state.update_data(name_ru=message.text.strip())
    await state.set_state(Form.new_name_he)
    await say(message, message.from_user.id, "enter_name_he")


@dp.message(Form.new_name_he)
async def product_name_he(message: Message, state: FSMContext):
    await state.update_data(name_he=message.text.strip())
    await state.set_state(Form.new_category)
    await say(message, message.from_user.id, "enter_category")


@dp.message(Form.new_category)
async def product_category(message: Message, state: FSMContext):
    cat = message.text.strip().lower()
    if cat not in CATEGORIES:
        await message.answer("Use beverages / sweets / food / other")
        return
    await state.update_data(category=cat)
    await state.set_state(Form.new_sell)
    await say(message, message.from_user.id, "enter_sell")


@dp.message(Form.new_sell)
async def product_sell(message: Message, state: FSMContext):
    val = await read_amount(message)
    if val is None:
        return
    await state.update_data(sell=val)
    await state.set_state(Form.new_buy)
    await say(message, message.from_user.id, "enter_buy")


@dp.message(Form.new_buy)
async def product_buy(message: Message, state: FSMContext):
    val = await read_amount(message)
    if val is None:
        return
    uid = message.from_user.id
    data = await state.get_data()
    pid = await execute(
        "INSERT INTO products(name_ru,name_he,category,sell_price_agorot,buy_price_agorot) VALUES(%s,%s,%s,%s,%s)",
        (data["name_ru"], data["name_he"], data["category"], data["sell"], val))
    await state.clear()
    await audit(uid, "product_created", f"product={pid}")
    await answer_menu(message, uid, "product_saved", id=pid)


@dp.callback_query(F.data == "stock:receive")
async def receive_start(call: CallbackQuery, state: FSMContext):
    u = await guard(call, ADMINS)
    if not u:
        return
    lang = u["language"]
    products = await fetchall("SELECT id,name_ru,name_he,stock FROM products WHERE is_active=1 ORDER BY name_ru")
    rows = [[(f'{p["name_ru"]} / {p["name_he"]} ({p["stock"]})'[:60], f"receive:product:{p['id']}")]
            for p in products]
    rows.append([(t(lang, "back"), "menu:main")])
    await call.message.edit_text(t(lang, "select_existing"), reply_markup=kb(rows))
    await call.answer()


@dp.callback_query(F.data.startswith("receive:product:"))
async def receive_product(call: CallbackQuery, state: FSMContext):
    await state.update_data(receive_pid=int(call.data.rsplit(":", 1)[1]))
    await state.set_state(Form.receive_qty)
    await call.message.answer(await tr(call.from_user.id, "enter_qty"))
    await call.answer()


@dp.message(Form.receive_qty)
async def receive_qty(message: Message, state: FSMContext):
    try:
        qty = int(message.text.strip())
    except (ValueError, AttributeError):
        qty = 0
    if qty <= 0:
        await say(message, message.from_user.id, "bad_number")
        return
    pid = (await state.get_data())["receive_pid"]
    uid = message.from_user.id
    async with tx() as cur:
        await cur.execute("UPDATE products SET stock=stock+%s WHERE id=%s", (qty, pid))
        await cur.execute(
            "INSERT INTO stock_operations(product_id,operation_type,quantity_change,note,author_telegram_id) VALUES(%s,'purchase',%s,'stock receipt',%s)",
            (pid, qty, uid))
    await state.clear()
    await audit(uid, "stock_received", f"product={pid};qty={qty}")
    await answer_menu(message, uid, "stock_saved")


# ---------------------------------------------------------------- finance

# Finance ledger: balance is derived from operations, never edited by a cash check.
@dp.callback_query(F.data == "finance:menu")
async def finance_menu(call: CallbackQuery):
    u = await guard(call, ADMINS)
    if not u:
        return
    lang = u["language"]
    await call.message.edit_text(t(lang, "finance_action"), reply_markup=kb([
        [(t(lang, "deposit"), "finance:deposit"), (t(lang, "withdraw"), "finance:withdraw")],
        [("📈 Баланс / יתרה", "finance:balance")],
        [(t(lang, "back"), "menu:main")]
    ]))
    await call.answer()


@dp.callback_query(F.data == "finance:balance")
async def finance_balance(call: CallbackQuery):
    lang = await lang_of(call.from_user.id)
    cash, bybit = await asyncio.gather(account_balance("cash"), account_balance("bybit"))
    text = (f'{t(lang, "balance").format(account="cash", balance=money(cash))}\n'
            f'{t(lang, "balance").format(account="Bybit", balance=money(bybit))}')
    await call.message.edit_text(text, reply_markup=back_kb(lang, "finance:menu"))
    await call.answer()


@dp.callback_query(F.data.in_({"finance:deposit", "finance:withdraw"}))
async def finance_select_account(call: CallbackQuery, state: FSMContext):
    u = await guard(call, ADMINS)
    if not u:
        return
    lang = u["language"]
    await state.update_data(finance_type="deposit" if call.data.endswith("deposit") else "withdrawal")
    await call.message.edit_text(t(lang, "choose_account"), reply_markup=kb([
        [(t(lang, "cash_account"), "finance:account:cash"), (t(lang, "bybit_account"), "finance:account:bybit")],
        [(t(lang, "back"), "finance:menu")]
    ]))
    await call.answer()


@dp.callback_query(F.data.startswith("finance:account:"))
async def finance_account(call: CallbackQuery, state: FSMContext):
    await state.update_data(account=call.data.rsplit(":", 1)[1])
    await state.set_state(Form.amount)
    await call.message.answer(await tr(call.from_user.id, "enter_amount"))
    await call.answer()


@dp.message(Form.amount)
async def finance_amount(message: Message, state: FSMContext):
    uid = message.from_user.id
    amount = await read_amount(message)
    if amount is None:
        return
    if amount <= 0:
        await say(message, uid, "bad_number")
        return
    data = await state.get_data()
    if not data.get("finance_type") or data.get("account") not in ("cash", "bybit"):
        await state.clear()
        await say(message, uid, "cancelled")
        return
    await state.update_data(amount=amount)
    await state.set_state(Form.finance_note)
    await say(message, uid, "enter_note")


@dp.message(Form.finance_note)
async def finance_note(message: Message, state: FSMContext):
    uid = message.from_user.id
    data = await state.get_data()
    amount, op, account = data["amount"], data["finance_type"], data["account"]
    if op == "withdrawal" and await account_balance(account) < amount:
        await message.answer("Insufficient balance / יתרה לא מספיקה")
        await state.clear()
        return
    await execute(
        "INSERT INTO cash_operations(account,operation_type,amount_agorot,note,author_telegram_id) VALUES(%s,%s,%s,%s,%s)",
        (account, op, amount, message.text.strip()[:500], uid))
    await audit(uid, "cash_operation", f"{account};{op};{amount}")
    await state.clear()
    await answer_menu(message, uid, "saved")


# ---------------------------------------------------------------- cash checks

# Guarantor: expected is calculated from ledger; mismatch is logged but never auto-adjusted.
@dp.callback_query(F.data == "checks:menu")
async def checks_menu(call: CallbackQuery):
    u = await guard(call, ("guarantor", "admin", "superadmin"))
    if not u:
        return
    lang = u["language"]
    await call.message.edit_text(t(lang, "choose_account"), reply_markup=kb([
        [(t(lang, "cash_account"), "checks:cash"), (t(lang, "bybit_account"), "checks:bybit")],
        [(t(lang, "back"), "menu:main")]
    ]))
    await call.answer()


@dp.callback_query(F.data.in_({"checks:cash", "checks:bybit"}))
async def check_start(call: CallbackQuery, state: FSMContext):
    account = call.data.split(":")[1]
    expected = await account_balance(account)
    await state.update_data(check_account=account, check_expected=expected)
    await state.set_state(Form.check_actual)
    lang = await lang_of(call.from_user.id)
    await call.message.answer(t(lang, "expected").format(expected=money(expected)))
    await call.answer()


@dp.message(Form.check_actual)
async def check_actual(message: Message, state: FSMContext):
    uid = message.from_user.id
    actual = await read_amount(message)
    if actual is None:
        return
    data = await state.get_data()
    expected = data["check_expected"]
    diff = actual - expected
    await execute(
        "INSERT INTO cash_checks(account,expected_agorot,actual_agorot,difference_agorot,guarantor_telegram_id) VALUES(%s,%s,%s,%s,%s)",
        (data["check_account"], expected, actual, diff, uid))
    await audit(uid, "cash_check", f'{data["check_account"]};expected={expected};actual={actual};diff={diff}')
    await state.clear()
    await answer_menu(message, uid, "check_saved", diff=money(diff))


# ---------------------------------------------------------------- cancellations

# Cancellation requests. Original sale stays in DB; approved cancellation restores stock and reverses account ledger.
@dp.callback_query(F.data == "cancel:list")
async def cancel_list(call: CallbackQuery):
    uid = call.from_user.id
    u = await guard(call, ("admin", "superadmin", "seller"))
    if not u:
        return
    lang = u["language"]
    if u["role"] in ADMINS:
        rows = await fetchall(
            "SELECT c.id,c.sale_id,c.requested_by,c.reason,s.total_agorot FROM operation_cancellations c JOIN sales s ON s.id=c.sale_id WHERE c.status='pending' ORDER BY c.id")
        buttons = [[(f'#{r["sale_id"]} {money(r["total_agorot"])} — {r["reason"][:20]}', f"cancel:review:{r['id']}")]
                   for r in rows]
        text = t(lang, "pending") if rows else ("Нет ожидающих запросов." if lang == "ru" else "אין בקשות ממתינות.")
        buttons.append([(t(lang, "back"), "menu:main")])
        await call.message.edit_text(text, reply_markup=kb(buttons))
    else:
        rows = await fetchall(
            "SELECT id,total_agorot,confirmed_at FROM sales WHERE seller_telegram_id=%s AND status='completed' ORDER BY id DESC LIMIT 15",
            (uid,))
        buttons = [[(f'#{r["id"]} — {money(r["total_agorot"])} — {r["confirmed_at"]}', f'cancel:request:{r["id"]}')]
                   for r in rows]
        buttons.append([(t(lang, "back"), "menu:main")])
        await call.message.edit_text(t(lang, "cancel_sale"), reply_markup=kb(buttons))
    await call.answer()


@dp.callback_query(F.data.startswith("cancel:request:"))
async def cancel_request(call: CallbackQuery, state: FSMContext):
    sid = int(call.data.rsplit(":", 1)[1])
    uid = call.from_user.id
    sale = await fetchone("SELECT id FROM sales WHERE id=%s AND seller_telegram_id=%s AND status='completed'",
                          (sid, uid))
    if not sale:
        await call.answer("Sale not found / מכירה לא נמצאה", show_alert=True)
        return
    await state.update_data(cancel_sale_id=sid)
    await state.set_state(Form.cancel_reason)
    await call.message.answer(t(await lang_of(uid), "reason_cancel").format(id=sid))
    await call.answer()


@dp.message(Form.cancel_reason)
async def cancel_reason(message: Message, state: FSMContext):
    uid = message.from_user.id
    sid = (await state.get_data())["cancel_sale_id"]
    reason = message.text[:2000]
    async with tx() as cur:
        await cur.execute(
            "UPDATE sales SET status='cancellation_pending',cancellation_reason=%s,cancellation_requested_by=%s WHERE id=%s AND status='completed'",
            (reason, uid, sid))
        if cur.rowcount:
            await cur.execute("INSERT INTO operation_cancellations(sale_id,requested_by,reason) VALUES(%s,%s,%s)",
                              (sid, uid, reason))
    await state.clear()
    await answer_menu(message, uid, "cancel_requested")


@dp.callback_query(F.data.startswith("cancel:review:"))
async def cancel_review(call: CallbackQuery):
    u = await guard(call, ADMINS)
    if not u:
        return
    lang = u["language"]
    cid = int(call.data.rsplit(":", 1)[1])
    c = await fetchone(
        "SELECT c.*,s.total_agorot FROM operation_cancellations c JOIN sales s ON s.id=c.sale_id WHERE c.id=%s AND c.status='pending'",
        (cid,))
    if not c:
        await call.answer("Already processed", show_alert=True)
        return
    await call.message.edit_text(
        f'Sale #{c["sale_id"]}\n{money(c["total_agorot"])}\nReason: {c["reason"]}',
        reply_markup=kb([[(t(lang, "approve"), f"cancel:approve:{cid}"),
                          (t(lang, "reject"), f"cancel:reject:{cid}")]]))
    await call.answer()


@dp.callback_query(F.data.startswith("cancel:approve:"))
async def cancel_approve(call: CallbackQuery):
    await resolve_cancel(call, True)


@dp.callback_query(F.data.startswith("cancel:reject:"))
async def cancel_reject(call: CallbackQuery):
    await resolve_cancel(call, False)


async def resolve_cancel(call, approve):
    uid = call.from_user.id
    if not await guard(call, ADMINS):
        return
    cid = int(call.data.rsplit(":", 1)[1])
    try:
        async with tx(True) as cur:
            await cur.execute(
                "SELECT c.*,s.payment_method,s.total_agorot,s.id AS sid FROM operation_cancellations c JOIN sales s ON s.id=c.sale_id WHERE c.id=%s AND c.status='pending' FOR UPDATE",
                (cid,))
            c = await cur.fetchone()
            if not c:
                raise Abort
            if approve:
                await cur.execute("SELECT product_id,quantity FROM sale_items WHERE sale_id=%s", (c["sid"],))
                for item in await cur.fetchall():
                    await cur.execute("UPDATE products SET stock=stock+%s WHERE id=%s",
                                      (item["quantity"], item["product_id"]))
                    await cur.execute(
                        "INSERT INTO stock_operations(product_id,operation_type,quantity_change,note,author_telegram_id) VALUES(%s,'cancellation',%s,%s,%s)",
                        (item["product_id"], item["quantity"], f"cancel sale #{c['sid']}", uid))
                await cur.execute(
                    "INSERT INTO cash_operations(account,operation_type,amount_agorot,note,related_sale_id,author_telegram_id) VALUES(%s,'sale_cancellation',%s,%s,%s,%s)",
                    (c["payment_method"], c["total_agorot"], f"cancel sale #{c['sid']}", c["sid"], uid))
                await cur.execute(
                    "UPDATE sales SET status='cancelled',cancelled_at=NOW(),cancellation_reviewed_by=%s WHERE id=%s",
                    (uid, c["sid"]))
                status = "approved"
            else:
                await cur.execute("UPDATE sales SET status='completed',cancellation_reviewed_by=%s WHERE id=%s",
                                  (uid, c["sid"]))
                status = "rejected"
            await cur.execute(
                "UPDATE operation_cancellations SET status=%s,reviewed_by=%s,reviewed_at=NOW() WHERE id=%s",
                (status, uid, cid))
    except Abort:
        await call.answer("Already processed", show_alert=True)
        return
    await audit(uid, "cancellation_" + status, f"cancellation={cid}")
    await call.message.edit_text("Processed / בוצע")
    await call.answer()


# ---------------------------------------------------------------- reports

# Basic reporting: all-time ranking, plus current month summary.
@dp.callback_query(F.data == "reports:menu")
async def reports_menu(call: CallbackQuery):
    u = await guard(call, ADMINS)
    if not u:
        return
    await call.message.edit_text("Выберите отчёт / בחר דוח", reply_markup=kb([
        [("📊 Финансовый отчёт за период / דוח כספי לתקופה", "reports:period")],
        [("🏆 Рейтинг продавцов / דירוג מוכרים", "reports:rank")],
        [(t(u["language"], "back"), "menu:main")]
    ]))
    await call.answer()


@dp.callback_query(F.data.in_({"reports:today", "reports:month"}))
async def report_period(call: CallbackQuery):
    u = await guard(call, ADMINS)
    if not u:
        return
    lang = u["language"]
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if call.data.endswith("month"):
        start = start.replace(day=1)
    row = await fetchone(
        "SELECT COUNT(*) n,COALESCE(SUM(total_agorot),0) revenue,COALESCE(SUM(total_agorot-total_cost_agorot),0) profit FROM sales WHERE status='completed' AND confirmed_at >= %s",
        (start,))
    text = t(lang, "report").format(n=row["n"], revenue=money(row["revenue"]), profit=money(row["profit"]))
    await call.message.edit_text(text, reply_markup=back_kb(lang, "reports:menu"))
    await call.answer()


async def seller_ranking(start):
    sql = ("SELECT s.seller_telegram_id sid,u.full_name name,COUNT(*) n,SUM(s.total_agorot) revenue,"
           "SUM(s.total_agorot-s.total_cost_agorot) profit FROM sales s "
           "LEFT JOIN users u ON u.telegram_id=s.seller_telegram_id WHERE s.status='completed'")
    args = []
    if start is not None:
        sql += " AND s.confirmed_at >= %s"
        args.append(start)
    sql += " GROUP BY s.seller_telegram_id,u.full_name ORDER BY n DESC,revenue DESC LIMIT 15"
    return await fetchall(sql, tuple(args))


def format_ranking(lang, rows):
    ru = lang == "ru"
    if not rows:
        return t(lang, "no_sales")
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    return "\n".join(
        f'{medals.get(i, str(i) + ".")} {r["name"] or "ID " + str(r["sid"])} — {r["n"]} '
        f'{"продаж" if ru else "מכירות"}, {money(r["revenue"])}, {"прибыль" if ru else "רווח"} {money(r["profit"])}'
        for i, r in enumerate(rows, 1))


@dp.callback_query(F.data == "reports:rank")
async def report_rank(call: CallbackQuery):
    u = await guard(call, ADMINS)
    if not u:
        return
    lang = u["language"]
    ru = lang == "ru"
    month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_rows, all_rows = await asyncio.gather(seller_ranking(month_start), seller_ranking(None))
    text = "\n".join([
        "🏆 Рейтинг продавцов" if ru else "🏆 דירוג מוכרים",
        "",
        "📅 Этот месяц:" if ru else "📅 החודש:",
        format_ranking(lang, month_rows),
        "",
        "🏅 За всё время:" if ru else "🏅 כל הזמן:",
        format_ranking(lang, all_rows),
    ])
    await call.message.edit_text(text[:4000], reply_markup=back_kb(lang, "reports:menu"))
    await call.answer()


# ---------------------------------------------------------------- финансы за период

PERIODS = [("today", "Сегодня", "היום"), ("yesterday", "Вчера", "אתמול"),
           ("week", "7 дней", "7 ימים"), ("month", "Этот месяц", "החודש"),
           ("prevmonth", "Прошлый месяц", "חודש שעבר"), ("all", "За всё время", "כל הזמן")]


def period_bounds(key):
    """(начало, конец-не-включая) для готового периода; None означает «без границы»."""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    day = timedelta(days=1)
    first = today.replace(day=1)
    next_first = (first + timedelta(days=32)).replace(day=1)
    prev_first = (first - day).replace(day=1)
    return {
        "today": (today, today + day),
        "yesterday": (today - day, today),
        "week": (today - 6 * day, today + day),
        "month": (first, next_first),
        "prevmonth": (prev_first, first),
        "all": (None, None),
    }[key]


def parse_period(text):
    """Одна дата = один день, две даты = период (включительно). Форматы: 01.10.2026 или 2026-10-01."""
    found = []
    for m in re.finditer(r"(\d{1,2})[./](\d{1,2})[./](\d{4})|(\d{4})-(\d{2})-(\d{2})", text or ""):
        try:
            if m.group(1):
                found.append(datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))))
            else:
                found.append(datetime(int(m.group(4)), int(m.group(5)), int(m.group(6))))
        except ValueError:
            return None
    if not found or len(found) > 2:
        return None
    return min(found), max(found) + timedelta(days=1)


def period_label(start, end, lang):
    if start is None and end is None:
        return "за всё время" if lang == "ru" else "כל הזמן"
    first = start.strftime("%d.%m.%Y")
    last = (end - timedelta(days=1)).strftime("%d.%m.%Y")
    return first if first == last else f"{first} – {last}"


async def finance_summary(start, end):
    sql = ("SELECT COUNT(*) n,COALESCE(SUM(total_agorot),0) revenue,"
           "COALESCE(SUM(total_cost_agorot),0) cost FROM sales WHERE status='completed'")
    args = []
    if start is not None:
        sql += " AND confirmed_at >= %s"
        args.append(start)
    if end is not None:
        sql += " AND confirmed_at < %s"
        args.append(end)
    return await fetchone(sql, tuple(args))


def format_finance(lang, start, end, row):
    ru = lang == "ru"
    revenue, cost = int(row["revenue"]), int(row["cost"])
    profit = revenue - cost
    margin = f" ({profit / revenue * 100:.1f}%)" if revenue > 0 else ""
    return "\n".join([
        ("📊 Период: " if ru else "📊 תקופה: ") + period_label(start, end, lang),
        ("🧾 Продаж: " if ru else "🧾 מכירות: ") + str(row["n"]),
        "",
        ("💰 Выручка: " if ru else "💰 הכנסות: ") + money(revenue),
        ("📦 Расходы (себестоимость проданного): " if ru else "📦 הוצאות (עלות המוצרים שנמכרו): ") + money(cost),
        ("📈 Прибыль: " if ru else "📈 רווח: ") + money(profit) + margin,
    ])


def finance_kb(lang):
    ru = lang == "ru"
    rows, row = [], []
    for key, name_ru, name_he in PERIODS:
        row.append((name_ru if ru else name_he, f"reports:p:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([("🗓 Свой период" if ru else "🗓 תקופה מותאמת", "reports:custom")])
    rows.append([(t(lang, "back"), "reports:menu")])
    return kb(rows)


_TS_CACHE = {}


async def ts_column(table):
    """Имя колонки с датой в таблице (created_at и т.п.); None, если её нет."""
    if table not in _TS_CACHE:
        rows = await fetchall("SELECT COLUMN_NAME AS col FROM information_schema.columns "
                              "WHERE table_schema=DATABASE() AND table_name=%s", (table,))
        names = {r["col"].lower(): r["col"] for r in rows}
        _TS_CACHE[table] = next((names[c] for c in ("created_at", "operation_date", "created", "timestamp",
                                                    "date", "created_on", "operation_at") if c in names), None)
    return _TS_CACHE[table]


def _as_dt(v):
    return v if isinstance(v, datetime) else datetime.combine(v, datetime.min.time())


async def top_products(start, end, limit=5):
    """Самые выгодные товары: чем быстрее продаётся товар и чем больше с него прибыль, тем выше.
    Время продажи = от закупки (последняя закупка до первой продажи периода) до последней продажи,
    не меньше 1 суток. Оценка = прибыль ÷ время продажи, то есть прибыль × скорость (1 / дни)."""
    sql = ("SELECT i.product_id,MAX(i.product_name_ru) name_ru,MAX(i.product_name_he) name_he,"
           "SUM(i.quantity*(i.unit_sell_agorot-i.unit_buy_agorot)) profit,"
           "MIN(s.confirmed_at) first_sale,MAX(s.confirmed_at) last_sale "
           "FROM sale_items i JOIN sales s ON s.id=i.sale_id WHERE s.status='completed'")
    args = []
    if start is not None:
        sql += " AND s.confirmed_at >= %s"
        args.append(start)
    if end is not None:
        sql += " AND s.confirmed_at < %s"
        args.append(end)
    items = [x for x in await fetchall(sql + " GROUP BY i.product_id", tuple(args))
             if x["profit"] is not None and x["profit"] > 0]
    if not items:
        return [], True
    col = await ts_column("stock_operations")
    purchases = {}
    if col:
        ids = [x["product_id"] for x in items]
        rows = await fetchall(
            f"SELECT product_id,`{col}` AS d FROM stock_operations WHERE operation_type='purchase' "
            f"AND product_id IN ({','.join(['%s'] * len(ids))})", tuple(ids))
        for r in rows:
            if r["d"]:
                purchases.setdefault(r["product_id"], []).append(_as_dt(r["d"]))
    result = []
    for x in items:
        first, last = _as_dt(x["first_sale"]), _as_dt(x["last_sale"])
        before = [d for d in purchases.get(x["product_id"], []) if d <= first]
        bought = max(before) if before else first
        days = max(1.0, (last - bought).total_seconds() / 86400)
        result.append({**x, "days": days, "score": int(x["profit"]) / days})
    result.sort(key=lambda r: r["score"], reverse=True)
    return result[:limit], bool(col)


def format_top_products(lang, top, has_dates):
    ru = lang == "ru"
    head = ["", "🏆 Самые выгодные товары" if ru else "🏆 המוצרים הרווחיים ביותר",
            "(чем быстрее продаётся и больше прибыль, тем выше)" if ru
            else "(ככל שנמכר מהר יותר ורווחי יותר — מקום גבוה יותר)"]
    if not top:
        return "\n".join(head + ["—"])
    lines = []
    for i, r in enumerate(top, 1):
        days = f'{r["days"]:.1f}'.rstrip("0").rstrip(".")
        name = r["name_ru" if ru else "name_he"]
        if ru:
            lines.append(f'{i}. {name} — прибыль {money(r["profit"])} за {days} дн. = {money(round(r["score"]))}/день')
        else:
            lines.append(f'{i}. {name} — רווח {money(r["profit"])} ב-{days} {'יום' if days == '1' else 'ימים'} = {money(round(r["score"]))} ליום')
    if not has_dates:
        lines.append("ℹ️ В таблице закупок нет колонки с датой — время считается от первой продажи." if ru
                     else "ℹ️ אין עמודת תאריך בטבלת הרכישות — הזמן נספר מהמכירה הראשונה.")
    return "\n".join(head + lines)


async def finance_report_text(lang, start, end):
    row = await finance_summary(start, end)
    top, has_dates = await top_products(start, end)
    return format_finance(lang, start, end, row) + "\n" + format_top_products(lang, top, has_dates)


@dp.callback_query(F.data == "reports:period")
async def finance_period_menu(call: CallbackQuery, state: FSMContext):
    u = await guard(call, ADMINS)
    if not u:
        return
    await state.clear()
    lang = u["language"]
    await call.message.edit_text(
        "Выручка, расходы и прибыль.\nВыберите период:" if lang == "ru"
        else "הכנסות, הוצאות ורווח.\nבחר תקופה:", reply_markup=finance_kb(lang))
    await call.answer()


@dp.callback_query(F.data.startswith("reports:p:"))
async def finance_period_result(call: CallbackQuery):
    u = await guard(call, ADMINS)
    if not u:
        return
    key = call.data.rsplit(":", 1)[1]
    if key not in dict((k, 1) for k, _, _ in PERIODS):
        await call.answer()
        return
    start, end = period_bounds(key)
    text = await finance_report_text(u["language"], start, end)
    await safe_edit(call, text, finance_kb(u["language"]))
    await call.answer()


@dp.callback_query(F.data == "reports:custom")
async def finance_custom_start(call: CallbackQuery, state: FSMContext):
    u = await guard(call, ADMINS)
    if not u:
        return
    await state.set_state(Form.report_range)
    await call.message.answer(
        "Введите период: две даты через дефис, например\n01.10.2026 - 27.10.2026\n"
        "Одна дата покажет один день. Отмена: /cancel" if u["language"] == "ru"
        else "הזן תקופה: שני תאריכים עם מקף, למשל\n01.10.2026 - 27.10.2026\n"
             "תאריך אחד יציג יום אחד. ביטול: /cancel")
    await call.answer()


@dp.message(Form.report_range)
async def finance_custom_result(message: Message, state: FSMContext):
    uid = message.from_user.id
    u = await get_user(uid)
    lang = lang_for(u)
    if not has_role(u, ADMINS):
        await state.clear()
        await message.answer(t(lang, "not_allowed"))
        return
    bounds = parse_period(message.text)
    if not bounds:
        await message.answer("Не понял даты. Пример: 01.10.2026 - 27.10.2026" if lang == "ru"
                             else "לא הבנתי את התאריכים. דוגמה: 01.10.2026 - 27.10.2026")
        return
    start, end = bounds
    text = await finance_report_text(lang, start, end)
    await state.clear()
    await message.answer(text, reply_markup=finance_kb(lang))


# ---------------------------------------------------------------- users (superadmin)

# Superadmin: create one-time registration codes and list users.
@dp.callback_query(F.data == "users:menu")
async def users_menu(call: CallbackQuery):
    u = await guard(call, ("superadmin",))
    if not u:
        return
    await call.message.edit_text("Управление / ניהול", reply_markup=kb([
        [("🔑 Создать код / צור קוד", "users:create_code")],
        [("👥 Список пользователей / משתמשים", "users:list")],
        [("🚫 Заблокировать ID / חסימת ID", "users:block")],
        [(t(u["language"], "back"), "menu:main")]
    ]))
    await call.answer()


@dp.callback_query(F.data == "users:create_code")
async def create_code_start(call: CallbackQuery):
    u = await guard(call, ("superadmin",))
    if not u:
        return
    await call.message.edit_text(t(u["language"], "choose_role"), reply_markup=kb([
        [("Продавец / מוכר", "invite:seller"), ("Гарант / אחראי", "invite:guarantor")],
        [("Администратор / מנהל", "invite:admin")]
    ]))
    await call.answer()


@dp.callback_query(F.data.startswith("invite:"))
async def create_code(call: CallbackQuery):
    uid = call.from_user.id
    role = call.data.split(":")[1]
    u = await guard(call, ("superadmin",))
    if not u:
        return
    lang = u["language"]
    if role not in ("admin", "seller", "guarantor"):
        await call.answer(t(lang, "not_allowed"), show_alert=True)
        return
    code = secrets.token_urlsafe(8)
    await execute("INSERT INTO auth_invites(code_hash,role,created_by) VALUES(%s,%s,%s)",
                  (hashlib.sha256(code.encode()).hexdigest(), role, uid))
    await call.message.edit_text(t(lang, "code_created").format(role=ROLE_NAMES[lang][role], code=code),
                                 parse_mode="Markdown")
    await audit(uid, "invite_created", role)
    await call.answer()


@dp.callback_query(F.data == "users:list")
async def users_list(call: CallbackQuery):
    u = await guard(call, ("superadmin",))
    if not u:
        return
    lang = u["language"]
    rows = await fetchall("SELECT telegram_id,full_name,role,is_blocked FROM users ORDER BY created_at DESC LIMIT 50")
    text = t(lang, "user_list") + "\n".join(
        f'{r["full_name"]} — {r["telegram_id"]} — {r["role"]} — blocked={r["is_blocked"]}' for r in rows)
    await call.message.edit_text(text, reply_markup=back_kb(lang, "users:menu"))
    await call.answer()


@dp.callback_query(F.data == "users:block")
async def block_start(call: CallbackQuery, state: FSMContext):
    if not await guard(call, ("superadmin",)):
        return
    await state.update_data(blocking=True)
    await state.set_state(Form.reply_to)
    await call.message.answer("Введите Telegram ID пользователя для блокировки:")
    await call.answer()


# ---------------------------------------------------------------- support / misc

# Support messages: sender's Telegram ID and name are stored. Admin replies by ID.
@dp.callback_query(F.data == "support:start")
async def support_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(Form.support_message)
    await call.message.answer(await tr(call.from_user.id, "support_text"))
    await call.answer()


@dp.message(Form.support_message)
async def support_send(message: Message, state: FSMContext):
    uid = message.from_user.id
    user = await get_user(uid)
    await execute(
        "INSERT INTO support_messages(sender_telegram_id,recipient_telegram_id,message_text,direction) VALUES(%s,%s,%s,'to_admin')",
        (uid, SUPERADMIN_ID, message.text[:4000]))
    await bot.send_message(
        SUPERADMIN_ID,
        f"Поддержка от {user['full_name']} (ID {uid}, роль {user['role']}):\n{message.text[:3500]}")
    await state.clear()
    await message.answer(t(user["language"], "support_sent"), reply_markup=menu_kb(user))


@dp.callback_query(F.data == "menu:main")
async def menu_main(call: CallbackQuery, state: FSMContext):
    await state.clear()
    u = await get_user(call.from_user.id)
    await call.message.edit_text(t(lang_for(u), "menu"), reply_markup=menu_kb(u))
    await call.answer()


@dp.message(Command("help"))
async def help_command(message: Message):
    await answer_menu(message, message.from_user.id, "help")


# Запасной обработчик (должен идти последним): если человек не нажал /start
# и просто что-то прислал, начинаем с выбора языка.
@dp.message(F.text, ~F.text.startswith("/"))
async def fallback_text(message: Message, state: FSMContext):
    user = await get_user(message.from_user.id)
    if user is None:
        await ask_language(message)
    elif not user["is_blocked"]:
        await message.answer(t(user["language"], "menu"), reply_markup=menu_kb(user))


async def on_startup():
    global pool
    pool = await aiomysql.create_pool(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD,
        db=DB_NAME, minsize=1, maxsize=10, autocommit=True,
        charset="utf8mb4"
    )
    # Superadmin is fixed by configuration, not by a registration code.
    if not await get_user(SUPERADMIN_ID):
        await execute("INSERT INTO users(telegram_id,full_name,role,language) VALUES(%s,%s,'superadmin','ru')",
                      (SUPERADMIN_ID, "Superadmin"))
    else:
        await execute("UPDATE users SET role='superadmin',is_blocked=0 WHERE telegram_id=%s", (SUPERADMIN_ID,))


async def main():
    await on_startup()
    try:
        await dp.start_polling(bot)
    finally:
        pool.close()
        await pool.wait_closed()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
