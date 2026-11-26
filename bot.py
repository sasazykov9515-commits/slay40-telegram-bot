import asyncio
import json
import os
import sqlite3
import urllib.request
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
DB_PATH = os.getenv("DB_PATH", "slay40.sqlite3")
SHEETS_WEB_APP_URL = os.getenv("SHEETS_WEB_APP_URL", "").strip()
SHEETS_SECRET = os.getenv("SHEETS_SECRET", "").strip()

NOMINATIONS = [
    "Король школы",
    "Королева школы",
    "Стиль",
    "Тихоня",
    "Самый красивый ученик",
    "Самая красивая ученица",
    "Юмор",
    "Задрот",
    "Спортсмен",
    "Харизма",
]

class Registration(StatesGroup):
    full_name = State()
    school_class = State()

class Suggestion(StatesGroup):
    candidate_name = State()

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def send_vote_to_sheet(payload):
    if not SHEETS_WEB_APP_URL or not SHEETS_SECRET:
        return
    payload["secret"] = SHEETS_SECRET
    request = urllib.request.Request(
        SHEETS_WEB_APP_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
            if not result.get("ok"):
                print("Google Sheets rejected vote:", result.get("error", "unknown error"))
    except Exception as exc:
        print("Google Sheets sync failed:", exc)


def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY, full_name TEXT NOT NULL,
            school_class TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT, nomination INTEGER NOT NULL,
            name TEXT NOT NULL, proposer_id INTEGER NOT NULL,
            approved INTEGER NOT NULL DEFAULT 0,
            UNIQUE(nomination, name)
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS votes (
            voter_id INTEGER NOT NULL, nomination INTEGER NOT NULL,
            candidate_id INTEGER NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(voter_id, nomination)
        )""")

def is_registered(user_id):
    with db() as con:
        return con.execute("SELECT 1 FROM users WHERE telegram_id=?", (user_id,)).fetchone() is not None

def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏆 Номинации и голосование", callback_data="menu:nominations")],
        [InlineKeyboardButton(text="➕ Предложить кандидата", callback_data="menu:suggest")],
        [InlineKeyboardButton(text="📊 Результаты", callback_data="menu:results")],
    ])

def nominations_keyboard(prefix):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=name, callback_data=f"{prefix}:{i}")]
        for i, name in enumerate(NOMINATIONS)
    ])

async def show_nomination(message_or_callback, index):
    with db() as con:
        rows = con.execute(
            "SELECT id, name FROM candidates WHERE nomination=? AND approved=1 ORDER BY name",
            (index,)
        ).fetchall()
    if not rows:
        text = f"В номинации «{NOMINATIONS[index]}» пока нет одобренных кандидатов."
        if isinstance(message_or_callback, CallbackQuery):
            await message_or_callback.message.answer(text)
        else:
            await message_or_callback.answer(text)
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=row["name"], callback_data=f"vote:{index}:{row['id']}")]
        for row in rows
    ])
    text = f"Выбери кандидата в номинации «{NOMINATIONS[index]}». Повторный выбор изменит твой голос."
    if isinstance(message_or_callback, CallbackQuery):
        await message_or_callback.message.answer(text, reply_markup=keyboard)
    else:
        await message_or_callback.answer(text, reply_markup=keyboard)

async def start_registration(message: Message, state: FSMContext):
    await state.set_state(Registration.full_name)
    await message.answer("Привет! Для регистрации напиши свои фамилию и имя.")

async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN в переменных окружения.")
    init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties())
    # This bot uses long polling; remove any webhook left by an earlier setup.
    await bot.delete_webhook(drop_pending_updates=True)
    dp = Dispatcher()

    @dp.message(Command("start"))
    async def cmd_start(message: Message, state: FSMContext):
        if is_registered(message.from_user.id):
            await message.answer("Ты уже зарегистрирован. Выбирай действие:", reply_markup=main_menu())
        else:
            await start_registration(message, state)

    @dp.message(Registration.full_name)
    async def reg_name(message: Message, state: FSMContext):
        name = (message.text or "").strip()
        if len(name) < 3 or len(name) > 80:
            await message.answer("Напиши имя и фамилию (от 3 до 80 символов).")
            return
        await state.update_data(full_name=name)
        await state.set_state(Registration.school_class)
        await message.answer("В каком ты классе? Например: 9А")

    @dp.message(Registration.school_class)
    async def reg_class(message: Message, state: FSMContext):
        school_class = (message.text or "").strip()
        if not school_class or len(school_class) > 20:
            await message.answer("Укажи класс, например 9А.")
            return
        data = await state.get_data()
        with db() as con:
            con.execute(
                "INSERT OR REPLACE INTO users(telegram_id, full_name, school_class) VALUES(?,?,?)",
                (message.from_user.id, data["full_name"], school_class)
            )
        await state.clear()
        await message.answer("Регистрация завершена! 🎉", reply_markup=main_menu())

    @dp.message(Command("nominations"))
    async def cmd_nominations(message: Message):
        if not is_registered(message.from_user.id):
            await message.answer("Сначала зарегистрируйся командой /start.")
            return
        await message.answer("Выбери номинацию:", reply_markup=nominations_keyboard("nom"))

    @dp.callback_query(F.data == "menu:nominations")
    async def menu_nominations(call: CallbackQuery):
        if not is_registered(call.from_user.id):
            await call.answer("Сначала зарегистрируйся через /start.", show_alert=True)
            return
        await call.message.answer("Выбери номинацию:", reply_markup=nominations_keyboard("nom"))
        await call.answer()

    @dp.callback_query(F.data.startswith("nom:"))
    async def nomination_selected(call: CallbackQuery):
        if not is_registered(call.from_user.id):
            await call.answer("Сначала зарегистрируйся через /start.", show_alert=True)
            return
        index = int(call.data.split(":")[1])
        if 0 <= index < len(NOMINATIONS):
            await show_nomination(call, index)
        await call.answer()

    @dp.callback_query(F.data.startswith("vote:"))
    async def cast_vote(call: CallbackQuery):
        if not is_registered(call.from_user.id):
            await call.answer("Сначала зарегистрируйся через /start.", show_alert=True)
            return
        _, nom_text, candidate_text = call.data.split(":")
        nom, candidate_id = int(nom_text), int(candidate_text)
        with db() as con:
            candidate = con.execute(
                "SELECT id, name FROM candidates WHERE id=? AND nomination=? AND approved=1",
                (candidate_id, nom)
            ).fetchone()
            if not candidate:
                await call.answer("Этот кандидат больше недоступен.", show_alert=True)
                return
            con.execute(
                """INSERT INTO votes(voter_id, nomination, candidate_id) VALUES(?,?,?)
                ON CONFLICT(voter_id, nomination) DO UPDATE SET
                candidate_id=excluded.candidate_id, updated_at=CURRENT_TIMESTAMP""",
                (call.from_user.id, nom, candidate_id)
            )
            voter = con.execute(
                "SELECT full_name, school_class FROM users WHERE telegram_id=?",
                (call.from_user.id,)
            ).fetchone()
        if voter and SHEETS_WEB_APP_URL and SHEETS_SECRET:
            await asyncio.to_thread(
                send_vote_to_sheet,
                {
                    "telegram_id": call.from_user.id,
                    "name": voter["full_name"],
                    "class_name": voter["school_class"],
                    "nomination": NOMINATIONS[nom],
                    "candidate": candidate["name"],
                },
            )
        await call.answer("Голос учтён! Его можно изменить позже.", show_alert=True)

    @dp.callback_query(F.data == "menu:suggest")
    async def menu_suggest(call: CallbackQuery):
        if not is_registered(call.from_user.id):
            await call.answer("Сначала зарегистрируйся через /start.", show_alert=True)
            return
        await call.message.answer("В какой номинации предложить кандидата:",
                                  reply_markup=nominations_keyboard("suggest"))
        await call.answer()

    @dp.callback_query(F.data.startswith("suggest:"))
    async def suggest_nomination(call: CallbackQuery, state: FSMContext):
        if not is_registered(call.from_user.id):
            await call.answer("Сначала зарегистрируйся через /start.", show_alert=True)
            return
        index = int(call.data.split(":")[1])
        if not 0 <= index < len(NOMINATIONS):
            await call.answer()
            return
        await state.update_data(suggest_nomination=index)
        await state.set_state(Suggestion.candidate_name)
        await call.message.answer(f"Напиши имя и фамилию кандидата для номинации «{NOMINATIONS[index]}».")
        await call.answer()

    @dp.message(Suggestion.candidate_name)
    async def save_suggestion(message: Message, state: FSMContext):
        name = (message.text or "").strip()
        if len(name) < 3 or len(name) > 80:
            await message.answer("Напиши имя и фамилию (от 3 до 80 символов).")
            return
        data = await state.get_data()
        try:
            with db() as con:
                con.execute(
                    "INSERT INTO candidates(nomination, name, proposer_id) VALUES(?,?,?)",
                    (data["suggest_nomination"], name, message.from_user.id)
                )
            await message.answer("Кандидат отправлен администраторам на подтверждение. Спасибо!")
        except sqlite3.IntegrityError:
            await message.answer("Такой кандидат уже предложен в этой номинации.")
        await state.clear()

    @dp.message(Command("pending"))
    async def cmd_pending(message: Message):
        if message.from_user.id not in ADMIN_IDS:
            await message.answer("Эта команда доступна только администраторам.")
            return
        with db() as con:
            rows = con.execute(
                "SELECT id, nomination, name FROM candidates WHERE approved=0 ORDER BY id"
            ).fetchall()
        if not rows:
            await message.answer("Новых кандидатов на подтверждение нет.")
            return
        for row in rows:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve:{row['id']}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{row['id']}")
            ]])
            await message.answer(
                f"Кандидат: {row['name']}\nНоминация: {NOMINATIONS[row['nomination']]}",
                reply_markup=keyboard
            )

    @dp.callback_query(F.data.startswith("approve:"))
    async def approve_candidate(call: CallbackQuery):
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("Только администратор может это сделать.", show_alert=True)
            return
        candidate_id = int(call.data.split(":")[1])
        with db() as con:
            cur = con.execute("UPDATE candidates SET approved=1 WHERE id=? AND approved=0", (candidate_id,))
        await call.message.edit_reply_markup(reply_markup=None)
        await call.answer("Кандидат одобрен." if cur.rowcount else "Заявка уже обработана.")

    @dp.callback_query(F.data.startswith("reject:"))
    async def reject_candidate(call: CallbackQuery):
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("Только администратор может это сделать.", show_alert=True)
            return
        candidate_id = int(call.data.split(":")[1])
        with db() as con:
            cur = con.execute("DELETE FROM candidates WHERE id=? AND approved=0", (candidate_id,))
        await call.message.edit_reply_markup(reply_markup=None)
        await call.answer("Кандидат отклонён." if cur.rowcount else "Заявка уже обработана.")

    @dp.message(Command("results"))
    @dp.callback_query(F.data == "menu:results")
    async def results(event):
        message = event.message if isinstance(event, CallbackQuery) else event
        with db() as con:
            for i, title in enumerate(NOMINATIONS):
                rows = con.execute(
                    """SELECT c.name
                    FROM candidates c
                    WHERE c.nomination=? AND c.approved=1
                    ORDER BY c.name LIMIT 10""", (i,)
                ).fetchall()
                if rows:
                    lines = [r["name"] for r in rows]
                    await message.answer(f"🏆 {title}\n" + "\n".join(lines))
                else:
                    await message.answer(f"🏆 {title}\nПока нет одобренных кандидатов.")
        if isinstance(event, CallbackQuery):
            await event.answer()

    @dp.message(Command("help"))
    async def cmd_help(message: Message):
        await message.answer(
            "Команды:\n/start — регистрация и меню\n/nominations — голосование\n"
            "/results — результаты\n/pending — заявки на кандидатов (только админ)"
        )

    print("Bot is polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
