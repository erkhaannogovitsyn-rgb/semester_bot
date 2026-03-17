import telebot
import datetime
import time
import sqlite3
import threading
import random
from openai import OpenAI
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import os
from dotenv import load_dotenv

# ======================== ЗАГРУЗКА ПЕРЕМЕННЫХ =========================
load_dotenv()

# ======================== ЧАСОВОЙ ПОЯС =========================
try:
    time.tzset()
except AttributeError:
    pass

# ======================== НАСТРОЙКИ =========================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

gpt_client = OpenAI(
    api_key=OPENAI_API_KEY,
    base_url="https://api.proxyapi.ru/openai/v1"
)
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

DB_PATH = "users.db"
user_states = {}


# ======================== GPT =========================
def ask_gpt(prompt: str, system: str = None) -> str:
    if not OPENAI_API_KEY or "ВСТАВЬ" in OPENAI_API_KEY:
        return "⚠️ GPT недоступен: не настроен API-ключ."
    if system is None:
        system = "Ты умный помощник для студента. Отвечай по-русски, кратко и по делу."
    try:
        resp = gpt_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            max_tokens=350,
            temperature=0.8,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"⚠️ Ошибка GPT: {e}"


# ======================== БАЗА ДАННЫХ ========================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            first_name TEXT DEFAULT 'Студент',
            morning_time TEXT DEFAULT '07:00',
            lunch_time TEXT DEFAULT '12:00',
            dinner_time TEXT DEFAULT '18:00',
            check_time TEXT DEFAULT '19:00',
            sleep_time TEXT DEFAULT '22:00',
            morning_enabled INTEGER DEFAULT 1,
            lunch_enabled INTEGER DEFAULT 1,
            dinner_enabled INTEGER DEFAULT 1,
            check_enabled INTEGER DEFAULT 1,
            sleep_enabled INTEGER DEFAULT 1
        )
    """)
    try:
        c.execute("ALTER TABLE user_settings ADD COLUMN first_name TEXT DEFAULT 'Студент'")
    except:
        pass
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_deadlines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            title TEXT,
            deadline_date TEXT,
            deadline_time TEXT,
            last_notified_date TEXT DEFAULT NULL
        )
    """)
    conn.commit()
    conn.close()


def get_user_settings(user_id, first_name=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM user_settings WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    if not row:
        name = first_name or "Студент"
        c.execute("INSERT INTO user_settings (user_id, first_name) VALUES (?, ?)", (user_id, name))
        conn.commit()
        c.execute("SELECT * FROM user_settings WHERE user_id = ?", (user_id,))
        row = c.fetchone()
    elif first_name and row[1] != first_name:
        c.execute("UPDATE user_settings SET first_name = ? WHERE user_id = ?", (first_name, user_id))
        conn.commit()
        c.execute("SELECT * FROM user_settings WHERE user_id = ?", (user_id,))
        row = c.fetchone()
    conn.close()
    return row


def toggle_notification(user_id, notif_type):
    type_map = {
        "morning": "morning_enabled", "lunch": "lunch_enabled",
        "dinner": "dinner_enabled", "check": "check_enabled", "sleep": "sleep_enabled",
    }
    if notif_type not in type_map:
        return None
    col = type_map[notif_type]
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(f"SELECT {col} FROM user_settings WHERE user_id = ?", (user_id,))
    current = c.fetchone()[0]
    new_val = 0 if current == 1 else 1
    c.execute(f"UPDATE user_settings SET {col} = ? WHERE user_id = ?", (new_val, user_id))
    conn.commit()
    conn.close()
    return new_val


def update_user_time(user_id, notif_type, new_time):
    type_map = {
        "утро": "morning_time", "обед": "lunch_time", "ужин": "dinner_time",
        "проверка": "check_time", "сон": "sleep_time",
    }
    if notif_type not in type_map:
        return False
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(f"UPDATE user_settings SET {type_map[notif_type]} = ? WHERE user_id = ?", (new_time, user_id))
    conn.commit()
    conn.close()
    return True


def add_deadline(user_id, title, date_str, time_str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO user_deadlines (user_id, title, deadline_date, deadline_time) VALUES (?, ?, ?, ?)",
        (user_id, title, date_str, time_str)
    )
    conn.commit()
    conn.close()


def get_user_deadlines(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    today = datetime.date.today().isoformat()
    c.execute("""
        SELECT id, title, deadline_date, deadline_time FROM user_deadlines
        WHERE user_id = ? AND deadline_date >= ?
        ORDER BY deadline_date, deadline_time
    """, (user_id, today))
    rows = c.fetchall()
    conn.close()
    return rows


def delete_deadline(deadline_id, user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM user_deadlines WHERE id = ? AND user_id = ?", (deadline_id, user_id))
    conn.commit()
    conn.close()


# ======================== СЕМЕСТР ========================
def get_semester_info():
    today = datetime.datetime.now()
    spring_end = datetime.datetime(today.year, 5, 31)
    winter_end = datetime.datetime(today.year, 12, 31)
    if today > spring_end:
        deadline = winter_end
        name = "осеннего семестра"
        if today > winter_end:
            deadline = datetime.datetime(today.year + 1, 5, 31)
            name = "весеннего семестра"
    else:
        deadline = spring_end
        name = "весеннего семестра"
    delta = deadline - today
    return delta.days, delta.seconds // 3600, (delta.seconds % 3600) // 60, delta.seconds % 60, name


# ======================== КЛАВИАТУРЫ ========================
def main_menu_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🎯 AI Мотивация", callback_data="motivate_ai"),
        InlineKeyboardButton("📋 AI План на сегодня", callback_data="coach"),
    )
    kb.add(
        InlineKeyboardButton("🤖 Задать вопрос AI", callback_data="ask_ai"),
        InlineKeyboardButton("⏰ Время до сессии", callback_data="time"),
    )
    kb.add(
        InlineKeyboardButton("📌 Мои дедлайны", callback_data="deadlines"),
        InlineKeyboardButton("⚙️ Настройки", callback_data="settings"),
    )
    return kb


def back_kb():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu"))
    return kb


def deadlines_kb(user_id):
    kb = InlineKeyboardMarkup(row_width=1)
    deadlines = get_user_deadlines(user_id)
    for did, title, ddate, dtime in deadlines:
        days_left = (datetime.date.fromisoformat(ddate) - datetime.date.today()).days
        if days_left == 0:
            label = f"🔥 {title} — СЕГОДНЯ!"
        elif days_left <= 3:
            label = f"⚠️ {title} — {days_left} дн."
        else:
            label = f"📅 {title} — {days_left} дн."
        kb.add(InlineKeyboardButton(f"❌ {label}", callback_data=f"del_dl_{did}"))
    kb.add(InlineKeyboardButton("➕ Добавить дедлайн", callback_data="add_deadline"))
    kb.add(InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu"))
    return kb


def settings_kb(user_id):
    s = get_user_settings(user_id)
    def ic(v): return "✅" if v else "❌"
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton(f"🌅 Утро {s[2]} {ic(s[7])}", callback_data="toggle_morning"),
        InlineKeyboardButton(f"🍽 Обед {s[3]} {ic(s[8])}", callback_data="toggle_lunch"),
    )
    kb.add(
        InlineKeyboardButton(f"🌙 Ужин {s[4]} {ic(s[9])}", callback_data="toggle_dinner"),
        InlineKeyboardButton(f"✅ Проверка {s[5]} {ic(s[10])}", callback_data="toggle_check"),
    )
    kb.add(InlineKeyboardButton(f"😴 Сон {s[6]} {ic(s[11])}", callback_data="toggle_sleep"))
    kb.add(InlineKeyboardButton("✏️ Как изменить время?", callback_data="change_time_help"))
    kb.add(InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu"))
    return kb


# ======================== КОМАНДЫ ========================
@bot.message_handler(commands=["start", "help"])
def cmd_start(message):
    uid = message.from_user.id
    name = message.from_user.first_name
    get_user_settings(uid, name)
    days, _, _, _, sem = get_semester_info()
    text = (
        f"🎓 Привет, {name}!\n\n"
        f"Я помогу не завалить сессию и держать режим.\n"
        f"До конца {sem} — *{days} дней*\n\n"
        f"Выбери что хочешь 👇"
    )
    bot.send_message(uid, text, reply_markup=main_menu_kb(), parse_mode="Markdown")
    user_states.pop(uid, None)


@bot.message_handler(commands=["settime"])
def cmd_settime(message):
    try:
        parts = message.text.split()
        if len(parts) != 3:
            bot.reply_to(message, "❌ Формат: /settime [тип] [ЧЧ:ММ]\nТипы: утро, обед, ужин, проверка, сон")
            return
        notif_type = parts[1].lower()
        new_time = parts[2]
        datetime.datetime.strptime(new_time, "%H:%M")
        if update_user_time(message.from_user.id, notif_type, new_time):
            bot.reply_to(message, f"✅ Время для '{notif_type}' изменено на {new_time}")
        else:
            bot.reply_to(message, "❌ Тип не найден. Доступны: утро, обед, ужин, проверка, сон")
    except ValueError:
        bot.reply_to(message, "❌ Неверный формат. Используй ЧЧ:ММ")


# ======================== CALLBACK ========================
@bot.callback_query_handler(func=lambda c: True)
def handle_callback(call):
    uid = call.from_user.id
    name = call.from_user.first_name
    get_user_settings(uid, name)
    data = call.data

    if data == "main_menu":
        user_states.pop(uid, None)
        days, _, _, _, sem = get_semester_info()
        bot.edit_message_text(
            f"🏠 Главное меню\n\nДо конца {sem}: *{days} дней*\n\nВыбери что хочешь 👇",
            call.message.chat.id, call.message.message_id,
            reply_markup=main_menu_kb(), parse_mode="Markdown"
        )

    elif data == "time":
        days, h, m, s, sem = get_semester_info()
        if days < 7:
            status = "🔥 Меньше недели! СРОЧНО СДАВАЙ ДОЛГИ!"
        elif days < 30:
            status = "😬 Месяц пролетит быстро — поторопись!"
        else:
            status = "💪 Время есть, но летит быстрее чем кажется!"
        bot.edit_message_text(
            f"⏰ До конца *{sem.upper()}*:\n\n🎯 {days} дней\n⏰ {h} ч {m} мин {s} сек\n\n{status}",
            call.message.chat.id, call.message.message_id,
            reply_markup=back_kb(), parse_mode="Markdown"
        )

    elif data == "motivate_ai":
        bot.answer_callback_query(call.id, "⏳ Генерирую мотивацию...")
        days, _, _, _, sem = get_semester_info()
        deadlines = get_user_deadlines(uid)
        dl_info = ""
        if deadlines:
            closest = deadlines[0]
            days_left = (datetime.date.fromisoformat(closest[2]) - datetime.date.today()).days
            dl_info = f" Ближайший дедлайн: {closest[1]} через {days_left} дней."
        prompt = (
            f"Меня зовут {name}. Мне сложно собраться с учёбой. "
            f"До конца {sem} осталось {days} дней.{dl_info} "
            "Дай короткую живую мотивацию прямо сейчас. 2-3 предложения, обращайся по имени."
        )
        answer = ask_gpt(prompt, "Ты вдохновляющий мотивационный коуч для студента. Говори по-русски, живо и с энергией.")
        bot.edit_message_text(
            f"🎯 {answer}",
            call.message.chat.id, call.message.message_id,
            reply_markup=back_kb()
        )

    elif data == "coach":
        bot.answer_callback_query(call.id, "⏳ Составляю план...")
        deadlines = get_user_deadlines(uid)
        if not deadlines:
            bot.edit_message_text(
                "📭 Нет активных дедлайнов.\nДобавь через раздел 📌 Мои дедлайны — тогда план будет точнее!",
                call.message.chat.id, call.message.message_id, reply_markup=back_kb()
            )
            return
        lines = []
        for _, title, ddate, dtime in deadlines:
            days_left = (datetime.date.fromisoformat(ddate) - datetime.date.today()).days
            lines.append(f"• {title} — через {days_left} дней ({ddate})")
        prompt = (
            f"Меня зовут {name}, я студент.\nМои ближайшие дедлайны:\n" + "\n".join(lines) +
            "\n\nСоставь конкретный план на СЕГОДНЯ: 3-5 шагов что именно нужно сделать. "
            "В конце добавь одну мотивирующую фразу. Обращайся по имени."
        )
        answer = ask_gpt(prompt, "Ты опытный академический коуч. Говори конкретно, по-русски, без воды.")
        bot.edit_message_text(
            f"📋 {answer}",
            call.message.chat.id, call.message.message_id,
            reply_markup=back_kb()
        )

    elif data == "ask_ai":
        user_states[uid] = {"state": "ask_question"}
        bot.edit_message_text(
            "🤖 *Задай любой вопрос*\n\n"
            "Можно спрашивать про учёбу, предметы, как объяснить тему, что сдать первым — что угодно.\n\n"
            "✍️ Пиши свой вопрос:",
            call.message.chat.id, call.message.message_id,
            reply_markup=back_kb(), parse_mode="Markdown"
        )

    elif data == "deadlines":
        deadlines = get_user_deadlines(uid)
        today = datetime.date.today()
        if not deadlines:
            text = "📭 *Нет активных дедлайнов*\n\nНажми ➕ чтобы добавить первый."
        else:
            text = "📌 *Твои дедлайны:*\n\n"
            for _, title, ddate, dtime in deadlines:
                days_left = (datetime.date.fromisoformat(ddate) - today).days
                if days_left == 0:
                    emoji, urgency = "🔥", "СЕГОДНЯ!"
                elif days_left == 1:
                    emoji, urgency = "⚠️", "завтра!"
                elif days_left <= 3:
                    emoji, urgency = "😬", f"{days_left} дня"
                else:
                    emoji, urgency = "📅", f"{days_left} дней"
                dl_date_fmt = datetime.date.fromisoformat(ddate).strftime('%d.%m.%Y')
                text += f"{emoji} *{title}*\n└ до {dl_date_fmt} · осталось {urgency} · 🔔 {dtime}\n\n"
            text += "Нажми на дедлайн чтобы удалить ❌"
        bot.edit_message_text(
            text, call.message.chat.id, call.message.message_id,
            reply_markup=deadlines_kb(uid), parse_mode="Markdown"
        )

    elif data.startswith("del_dl_"):
        did = int(data.replace("del_dl_", ""))
        delete_deadline(did, uid)
        bot.answer_callback_query(call.id, "✅ Удалено")
        deadlines = get_user_deadlines(uid)
        today = datetime.date.today()
        if not deadlines:
            text = "📭 *Нет активных дедлайнов*\n\nНажми ➕ чтобы добавить первый."
        else:
            text = "📌 *Твои дедлайны:*\n\n"
            for _, title, ddate, dtime in deadlines:
                days_left = (datetime.date.fromisoformat(ddate) - today).days
                if days_left == 0:
                    emoji, urgency = "🔥", "СЕГОДНЯ!"
                elif days_left == 1:
                    emoji, urgency = "⚠️", "завтра!"
                elif days_left <= 3:
                    emoji, urgency = "😬", f"{days_left} дня"
                else:
                    emoji, urgency = "📅", f"{days_left} дней"
                dl_date_fmt = datetime.date.fromisoformat(ddate).strftime('%d.%m.%Y')
                text += f"{emoji} *{title}*\n└ до {dl_date_fmt} · осталось {urgency} · 🔔 {dtime}\n\n"
            text += "Нажми на дедлайн чтобы удалить ❌"
        bot.edit_message_text(
            text, call.message.chat.id, call.message.message_id,
            reply_markup=deadlines_kb(uid), parse_mode="Markdown"
        )

    elif data == "add_deadline":
        user_states[uid] = {"state": "deadline_title", "data": {}}
        bot.edit_message_text(
            "📝 *Добавление дедлайна* — шаг 1/3\n\nНапиши название задания или предмета:",
            call.message.chat.id, call.message.message_id, parse_mode="Markdown"
        )

    elif data == "settings":
        bot.edit_message_text(
            "⚙️ *Настройки уведомлений*\n\nНажми на кнопку чтобы включить/выключить.\n"
            "Для изменения времени напиши: /settime утро 08:30",
            call.message.chat.id, call.message.message_id,
            reply_markup=settings_kb(uid), parse_mode="Markdown"
        )

    elif data.startswith("toggle_"):
        notif = data.replace("toggle_", "")
        toggle_notification(uid, notif)
        bot.edit_message_text(
            "⚙️ *Настройки уведомлений*\n\nНажми на кнопку чтобы включить/выключить.\n"
            "Для изменения времени напиши: /settime утро 08:30",
            call.message.chat.id, call.message.message_id,
            reply_markup=settings_kb(uid), parse_mode="Markdown"
        )

    elif data == "change_time_help":
        bot.answer_callback_query(
            call.id,
            "Напиши команду:\n/settime утро 08:00\nТипы: утро, обед, ужин, проверка, сон",
            show_alert=True
        )

    try:
        bot.answer_callback_query(call.id)
    except:
        pass


# ======================== ДИАЛОГ ========================
@bot.message_handler(func=lambda m: m.from_user.id in user_states)
def handle_dialog(message):
    uid = message.from_user.id
    state_data = user_states.get(uid, {})
    state = state_data.get("state")
    text = message.text.strip()

    if text.startswith("/"):
        user_states.pop(uid, None)
        return

    if state == "ask_question":
        bot.send_message(uid, "⏳ Думаю над ответом...")
        answer = ask_gpt(
            text,
            "Ты умный помощник-репетитор для студента. "
            "Если вопрос учебный — объясни понятно и кратко. "
            "Если личный — дай дельный совет. Отвечай по-русски, без воды."
        )
        del user_states[uid]
        bot.send_message(uid, f"🤖 {answer}", reply_markup=back_kb())

    elif state == "deadline_title":
        user_states[uid]["data"]["title"] = text
        user_states[uid]["state"] = "deadline_date"
        bot.send_message(uid, "📅 Шаг 2/3: Когда крайний срок?\nФормат: ДД.ММ.ГГГГ\nПример: 25.05.2026")

    elif state == "deadline_date":
        try:
            day, month, year = map(int, text.split("."))
            dl_date = datetime.date(year, month, day)
            if dl_date < datetime.date.today():
                bot.send_message(uid, "❌ Эта дата уже прошла. Введи будущую дату:")
                return
            user_states[uid]["data"]["date"] = dl_date.isoformat()
            user_states[uid]["state"] = "deadline_time"
            bot.send_message(uid, "⏰ Шаг 3/3: В какое время напоминать каждый день?\nФормат: ЧЧ:ММ\nПример: 09:00")
        except:
            bot.send_message(uid, "❌ Неверный формат. Введи дату как ДД.ММ.ГГГГ\nПример: 25.05.2026")

    elif state == "deadline_time":
        try:
            datetime.datetime.strptime(text, "%H:%M")
            data = user_states[uid]["data"]
            add_deadline(uid, data["title"], data["date"], text)
            del user_states[uid]
            dl_date = datetime.date.fromisoformat(data["date"])
            days_left = (dl_date - datetime.date.today()).days
            bot.send_message(
                uid,
                f"✅ *Дедлайн добавлен!*\n\n"
                f"📌 {data['title']}\n"
                f"📅 до {dl_date.strftime('%d.%m.%Y')} — осталось {days_left} дней\n"
                f"🔔 Буду напоминать каждый день в {text}",
                reply_markup=back_kb(), parse_mode="Markdown"
            )
        except:
            bot.send_message(uid, "❌ Неверный формат. Введи время как ЧЧ:ММ\nПример: 09:00")


# ======================== УВЕДОМЛЕНИЯ ========================
MORNING = [
    "🌅 Доброе утро, {name}!\n\nДо конца {sem} — {days} дней.\nСегодня отличный день закрыть пару долгов! 💪",
    "☀️ {name}, подъём! Новый день — новые возможности.\nДо сессии {days} дней. Начни с одного задания прямо утром!",
    "🌤 Доброе утро, {name}! {days} дней до конца {sem}.\nКаждое утро — шанс стать ближе к каникулам 🏖",
]
LUNCH = [
    "🍽 {name}, обед! Отложи учёбу на 30 минут, поешь нормально.\nДо конца семестра {days} дней — силы ещё пригодятся!",
    "🥗 Эй, {name}! Пора пообедать. Мозг работает лучше сытым 😄\nОсталось {days} дней — держишься!",
]
DINNER = [
    "🌙 {name}, вечер! Время поужинать.\nДо конца семестра {days} дней. Вечером ещё можно сделать одно задание!",
    "🍜 Ужин, {name}! Подкрепись и отдохни немного.\nОсталось {days} дней — ты справляешься 💪",
]
SLEEP = [
    "😴 {name}, уже {sleep_time}! Пора спать.\nЕсли делаешь задания — отложи до завтра. Выспишься — сделаешь всё быстрее! 🌙",
    "🌙 {name}, время ложиться! Мозгу нужен отдых.\n{days} дней ещё есть — успеешь. Спокойной ночи! 💤",
]


def send_notification(user_id, notif_type):
    s = get_user_settings(user_id)
    name = s[1] or "Студент"
    enabled_map = {
        "morning": s[7], "lunch": s[8],
        "dinner": s[9], "check": s[10], "sleep": s[11]
    }
    if not enabled_map.get(notif_type, 0):
        return

    days, _, _, _, sem = get_semester_info()

    urgent_block = ""
    if notif_type == "morning":
        deadlines = get_user_deadlines(user_id)
        urgent = [
            (t, (datetime.date.fromisoformat(d) - datetime.date.today()).days)
            for _, t, d, _ in deadlines
            if (datetime.date.fromisoformat(d) - datetime.date.today()).days <= 3
        ]
        if urgent:
            urgent_block = "\n\n⚠️ Срочные дедлайны:\n" + "\n".join(
                [f"• {t} — {'СЕГОДНЯ!' if d == 0 else f'{d} дн.'}" for t, d in urgent]
            )

    if notif_type == "morning":
        msg = random.choice(MORNING).format(name=name, sem=sem, days=days) + urgent_block
    elif notif_type == "lunch":
        msg = random.choice(LUNCH).format(name=name, days=days)
    elif notif_type == "dinner":
        msg = random.choice(DINNER).format(name=name, days=days)
    elif notif_type == "check":
        msg = (
            f"🌆 Вечерняя проверка, {name}!\n\n"
            f"📌 Ты выполнил сегодня задания?\n"
            f"• Если да — отлично, отдыхай 😊\n"
            f"• Если нет — у тебя ещё есть вечер, начни прямо сейчас!\n"
            f"• Если нет сил — отложи на завтра, главное не выгорать!\n\n"
            f"До конца семестра {days} дней. Ты успеваешь!"
        )
    elif notif_type == "sleep":
        msg = random.choice(SLEEP).format(name=name, sleep_time=s[6], days=days)
    else:
        return

    try:
        bot.send_message(user_id, msg, reply_markup=back_kb())
        print(f"✅ {notif_type} → {user_id} ({name}) в {datetime.datetime.now().strftime('%H:%M')}")
    except Exception as e:
        print(f"❌ Ошибка {user_id}: {e}")


def check_deadlines():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    today = datetime.date.today()
    today_str = today.isoformat()
    now_time = datetime.datetime.now().strftime("%H:%M")
    c.execute("""
        SELECT id, user_id, title, deadline_date, deadline_time, last_notified_date
        FROM user_deadlines WHERE deadline_date >= ?
    """, (today_str,))
    for did, uid, title, ddate, dtime, last_notified in c.fetchall():
        dl_date = datetime.date.fromisoformat(ddate)
        if today <= dl_date and dtime == now_time and last_notified != today_str:
            days_left = (dl_date - today).days
            if days_left == 0:
                emoji, urgency = "🔥", "СЕГОДНЯ последний день!"
            elif days_left == 1:
                emoji, urgency = "⚠️", "Завтра сдавать!"
            elif days_left <= 3:
                emoji, urgency = "😬", f"Осталось {days_left} дня!"
            else:
                emoji, urgency = "📌", f"Осталось {days_left} дней"
            msg = f"{emoji} *Напоминание о дедлайне*\n\n📌 {title}\n📅 {dl_date.strftime('%d.%m.%Y')}\n⏰ {urgency}"
            try:
                bot.send_message(uid, msg, reply_markup=back_kb(), parse_mode="Markdown")
                c.execute("UPDATE user_deadlines SET last_notified_date = ? WHERE id = ?", (today_str, did))
                conn.commit()
            except Exception as e:
                print(f"❌ Дедлайн {did}: {e}")
    conn.close()


def check_and_send():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now = datetime.datetime.now().strftime("%H:%M")
    c.execute("SELECT user_id FROM user_settings")
    users = c.fetchall()
    conn.close()
    for (uid,) in users:
        s = get_user_settings(uid)
        times = {"morning": s[2], "lunch": s[3], "dinner": s[4], "check": s[5], "sleep": s[6]}
        for ttype, tm in times.items():
            if tm == now:
                send_notification(uid, ttype)
    check_deadlines()


def run_schedule():
    print("⏰ Планировщик запущен")
    while True:
        check_and_send()
        time.sleep(60)


# ======================== ЗАПУСК ========================
if __name__ == "__main__":
    init_db()
    print("🤖 Бот запущен!")
    t = threading.Thread(target=run_schedule, daemon=True)
    t.start()
    bot.infinity_polling()
