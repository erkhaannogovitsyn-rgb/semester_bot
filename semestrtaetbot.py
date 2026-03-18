import os
import telebot
import datetime
import time
import sqlite3
import threading
import schedule
import random
from openai import OpenAI

# ======================== ЧАСОВОЙ ПОЯС =========================
os.environ['TZ'] = 'Asia/Yakutsk'
try:
    time.tzset()
except AttributeError:
    pass

# ======================== НАСТРОЙКИ =========================
TOKEN = "8436819790:AAEJhDLebvj2KzBi-7bp0AZCHLIQIQ4xiiI"  # токен Telegram-бота
bot = telebot.TeleBot(TOKEN)

# Хардкод-ключ
OPENAI_API_KEY = "sk-paU7MeggMzxGB8WMEpPwEN8sRuAgoNQW"
gpt_client = OpenAI(api_key=OPENAI_API_KEY)


def ask_gpt(prompt: str) -> str:
    """
    Простой вызов GPT: один промпт -> один короткий ответ.
    """
    if not OPENAI_API_KEY or "YOUR_OPENAI_API_KEY_HERE" in OPENAI_API_KEY:
        return "⚠️ GPT недоступен: не настроен API-ключ. Попроси разработчика вставить свой OpenAI-ключ в код."

    try:
        resp = gpt_client.chat.completions.create(
            model="gpt-4o-mini",  # лёгкая и недорогая модель
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты добрый и краткий мотивационный коуч для студента. "
                        "Отвечай по-русски, коротко, 1–3 предложения."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=200,
            temperature=0.8,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"⚠️ Ошибка при обращении к GPT: {e}"


# ======================== МЕНЮ КОМАНД ========================
bot.set_my_commands([
    telebot.types.BotCommand("start", "Запустить бота"),
    telebot.types.BotCommand("help", "Показать все команды"),
    telebot.types.BotCommand("time", "Время до конца семестра"),
    telebot.types.BotCommand("motivate", "Случайная мотивация"),
    telebot.types.BotCommand("motivate_ai", "AI-мотивация"),
    telebot.types.BotCommand("today", "План на сегодня"),
    telebot.types.BotCommand("coach", "AI-план по дедлайнам"),
    telebot.types.BotCommand("settings", "Настройки уведомлений"),
    telebot.types.BotCommand("deadline", "Управление дедлайнами"),
])

# ======================== БАЗА ДАННЫХ ========================
conn = sqlite3.connect('users.db', check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER PRIMARY KEY,
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
)""")

# Таблица для дедлайнов
cursor.execute("""CREATE TABLE IF NOT EXISTS user_deadlines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    title TEXT,
    deadline_date TEXT,
    deadline_time TEXT,
    last_notified_date TEXT DEFAULT NULL
)""")
conn.commit()

# ======================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ========================
def get_user_settings(user_id):
    cursor.execute('SELECT * FROM user_settings WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    if row:
        return row
    else:
        cursor.execute('INSERT INTO user_settings (user_id) VALUES (?)', (user_id,))
        conn.commit()
        cursor.execute('SELECT * FROM user_settings WHERE user_id = ?', (user_id,))
        return cursor.fetchone()


def update_user_time(user_id, notif_type, new_time):
    type_map = {
        'утро': 'morning_time', 'morning': 'morning_time',
        'обед': 'lunch_time', 'lunch': 'lunch_time',
        'ужин': 'dinner_time', 'dinner': 'dinner_time',
        'проверка': 'check_time', 'check': 'check_time',
        'сон': 'sleep_time', 'sleep': 'sleep_time'
    }
    if notif_type in type_map:
        col = type_map[notif_type]
        cursor.execute(f'UPDATE user_settings SET {col} = ? WHERE user_id = ?', (new_time, user_id))
        conn.commit()
        return True
    return False


def toggle_notification(user_id, notif_type):
    type_map = {
        'утро': 'morning_enabled', 'morning': 'morning_enabled',
        'обед': 'lunch_enabled', 'lunch': 'lunch_enabled',
        'ужин': 'dinner_enabled', 'dinner': 'dinner_enabled',
        'проверка': 'check_enabled', 'check': 'check_enabled',
        'сон': 'sleep_enabled', 'sleep': 'sleep_enabled'
    }
    if notif_type in type_map:
        col = type_map[notif_type]
        cursor.execute(f'SELECT {col} FROM user_settings WHERE user_id = ?', (user_id,))
        current = cursor.fetchone()[0]
        new_val = 0 if current == 1 else 1
        cursor.execute(f'UPDATE user_settings SET {col} = ? WHERE user_id = ?', (new_val, user_id))
        conn.commit()
        return new_val
    return None


def add_deadline(user_id, title, date_str, time_str):
    cursor.execute('''
    INSERT INTO user_deadlines (user_id, title, deadline_date, deadline_time, last_notified_date)
    VALUES (?, ?, ?, ?, NULL)
    ''', (user_id, title, date_str, time_str))
    conn.commit()


def get_user_deadlines(user_id):
    today = datetime.date.today().isoformat()
    cursor.execute('''
    SELECT id, title, deadline_date, deadline_time
    FROM user_deadlines
    WHERE user_id = ? AND deadline_date >= ?
    ORDER BY deadline_date, deadline_time
    ''', (user_id, today))
    return cursor.fetchall()


def delete_deadline(deadline_id, user_id):
    cursor.execute('DELETE FROM user_deadlines WHERE id = ? AND user_id = ?', (deadline_id, user_id))
    conn.commit()


def get_deadline_info():
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

# ======================== КОМАНДЫ ========================
@bot.message_handler(commands=['start', 'help'])
def start(message):
    user_id = message.from_user.id
    get_user_settings(user_id)
    days, h, m, s, sem = get_deadline_info()
    text = (f"🎓 Привет! Я помогу тебе не провалить сессию и соблюдать режим!\n\n"
            f"Сейчас идёт {sem}. До конца осталось:\n"
            f"📅 {days} дней\n"
            f"⏰ {h} часов {m} минут {s} секунд\n\n"
            f"Команды (можно выбрать в меню):\n"
            f"/time — точное время до конца семестра\n"
            f"/motivate — случайная мотивация\n"
            f"/motivate_ai — AI-мотивация\n"
            f"/today — план на сегодня\n"
            f"/coach — AI-план по дедлайнам\n"
            f"/settings — настройки уведомлений\n"
            f"/deadline — управление личными дедлайнами\n"
            f"/help — эта справка")
    bot.reply_to(message, text)


@bot.message_handler(commands=['time'])
def cmd_time(message):
    days, h, m, s, sem = get_deadline_info()
    resp = (f"📆 До КОНЦА {sem.upper()} осталось:\n\n"
            f"🎯 {days} дней\n⏰ {h} часов {m} минут {s} секунд\n\n")
    if days < 7:
        resp += "⚠️ Меньше недели! СРОЧНО СДАВАЙ ДОЛГИ!"
    elif days < 30:
        resp += "😬 Месяц пролетит быстро — поторопись!"
    else:
        resp += "💪 Время есть, но оно летит быстрее, чем кажется!"
    bot.reply_to(message, resp)


@bot.message_handler(commands=['motivate'])
def cmd_motivate(message):
    phrases = [
        "Преподаватель уже заждался твою лабораторную! ⏰",
        "Хвосты сами себя не сдадут! Бегом закрывать! 🏃",
        "Осталось совсем чуть-чуть, соберись! 💪",
        "Представь, как круто отдыхать без долгов...",
        "Сессия ближе, чем кажется!",
        "Каждая сданная работа приближает каникулы! ☀️",
        "Не уходи в семестр с хвостами!",
        "Время тикает, а ты ещё не сдал? 🤔",
        "Закрой долги сейчас — отдыхай спокойно потом!",
        "Препод поверит в тебя, если принесешь лабу!"
    ]
    days, _, _, _, _ = get_deadline_info()
    bot.reply_to(message, f"{random.choice(phrases)}\n\n(До конца семестра {days} дней)")


@bot.message_handler(commands=['motivate_ai'])
def cmd_motivate_ai(message):
    days, _, _, _, sem = get_deadline_info()
    user_text = (
        "Мне сложно собраться с учебой. "
        f"До конца {sem} осталось {days} дней. "
        "Дай мне короткую мотивацию, чтобы я занялся учебой прямо сейчас."
    )
    answer = ask_gpt(user_text)
    bot.reply_to(message, answer)


@bot.message_handler(commands=['today'])
def cmd_today(message):
    user_id = message.from_user.id
    deadlines = get_user_deadlines(user_id)

    if not deadlines:
        bot.reply_to(message, "📭 У тебя нет активных дедлайнов. Добавь их через /deadline add.")
        return

    today = datetime.date.today()
    text = "📅 План на сегодня по дедлайнам:\n\n"
    count = 0

    for did, title, ddate, dtime in deadlines:
        deadline_date = datetime.date.fromisoformat(ddate)
        days_left = (deadline_date - today).days

        if days_left < 0:
            continue

        text += (
            f"• {title} — до {deadline_date.strftime('%d.%m.%Y')} "
            f"(осталось {days_left} дн.), напоминание в {dtime}\n"
        )
        count += 1
        if count >= 5:
            break

    text += "\nВыбери 1–2 задачи и сделай их сегодня 💪"
    bot.reply_to(message, text)


@bot.message_handler(commands=['coach'])
def cmd_coach(message):
    user_id = message.from_user.id
    deadlines = get_user_deadlines(user_id)

    if not deadlines:
        bot.reply_to(message, "📭 У тебя пока нет дедлайнов. Добавь их через /deadline add.")
        return

    lines = []
    for did, title, ddate, dtime in deadlines:
        lines.append(f"{title} — до {ddate} в {dtime}")
    deadlines_text = "\n".join(lines)

    prompt = (
        "Я студент, который хочет нормально сдать сессию.\n"
        "Вот мои ближайшие дедлайны:\n"
        f"{deadlines_text}\n\n"
        "Составь, пожалуйста, короткий план на СЕГОДНЯ из 3–5 конкретных шагов "
        "и добавь одну мотивирующую фразу в конце."
    )

    answer = ask_gpt(prompt)
    bot.reply_to(message, answer)


@bot.message_handler(commands=['settings'])
def cmd_settings(message):
    user_id = message.from_user.id
    s = get_user_settings(user_id)
    text = ("⚙️ **Твои настройки уведомлений:**\n\n"
            f"🌅 Утро: {s[1]} {'✅' if s[6] else '❌'}\n"
            f"🍽 Обед: {s[2]} {'✅' if s[7] else '❌'}\n"
            f"🌙 Ужин: {s[3]} {'✅' if s[8] else '❌'}\n"
            f"✅ Проверка: {s[4]} {'✅' if s[9] else '❌'}\n"
            f"😴 Сон: {s[5]} {'✅' if s[10] else '❌'}\n\n"
            "**Команды:**\n"
            "/settime [тип] [ЧЧ:ММ] — например: /settime утро 08:00\n"
            " Типы: утро, обед, ужин, проверка, сон\n"
            "/toggle [тип] — включить/выключить\n"
            "/deadline — управление личными дедлайнами")
    bot.send_message(user_id, text, parse_mode="Markdown")


@bot.message_handler(commands=['settime'])
def cmd_settime(message):
    try:
        parts = message.text.split()
        if len(parts) != 3:
            bot.reply_to(message, "❌ Формат: /settime [тип] [ЧЧ:ММ]")
            return
        notif_type = parts[1].lower()
        new_time = parts[2]
        datetime.datetime.strptime(new_time, "%H:%M")
        user_id = message.from_user.id
        if update_user_time(user_id, notif_type, new_time):
            bot.reply_to(message, f"✅ Время для '{notif_type}' изменено на {new_time}")
        else:
            bot.reply_to(message, "❌ Неверный тип. Доступны: утро, обед, ужин, проверка, сон")
    except ValueError:
        bot.reply_to(message, "❌ Неверный формат времени. Используй ЧЧ:ММ")
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка: {e}")


@bot.message_handler(commands=['toggle'])
def cmd_toggle(message):
    try:
        parts = message.text.split()
        if len(parts) != 2:
            bot.reply_to(message, "❌ Формат: /toggle [тип]")
            return
        notif_type = parts[1].lower()
        user_id = message.from_user.id
        new_val = toggle_notification(user_id, notif_type)
        if new_val is not None:
            state = "включено" if new_val == 1 else "выключено"
            bot.reply_to(message, f"✅ Уведомление '{notif_type}' теперь {state}")
        else:
            bot.reply_to(message, "❌ Неверный тип. Доступны: утро, обед, ужин, проверка, сон")
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка: {e}")


@bot.message_handler(commands=['deadline'])
def cmd_deadline(message):
    parts = message.text.split()
    if len(parts) == 1:
        help_text = ("📌 **Управление личными дедлайнами**\n\n"
                     "✅ **Как добавить:**\n"
                     "`/deadline add ТЕМА ДД.ММ.ГГГГ ЧЧ:ММ`\n"
                     "🔹 Пример: `/deadline add Курсовая работа 25.05.2026 07:00`\n\n"
                     "📅 **Посмотреть все:**\n"
                     "`/deadline list`\n\n"
                     "❌ **Как удалить:**\n"
                     "`/deadline delete ID` (ID из списка)\n"
                     "🔹 Пример: `/deadline delete 1`")
        bot.reply_to(message, help_text, parse_mode="Markdown")
        return

    sub = parts[1].lower()
    uid = message.from_user.id

    if sub == "add":
        if len(parts) < 4:
            bot.reply_to(message, "❌ Не хватает данных. Формат: /deadline add ТЕМА ДД.ММ.ГГГГ ЧЧ:ММ")
            return
        try:
            title_parts = []
            for i in range(2, len(parts) - 2):
                title_parts.append(parts[i])

            date_str = parts[-2]
            time_str = parts[-1]
            title = " ".join(title_parts) if title_parts else "Без темы"

            day, month, year = map(int, date_str.split('.'))
            deadline_date = datetime.date(year, month, day)
            datetime.datetime.strptime(time_str, "%H:%M")

            if deadline_date < datetime.date.today():
                bot.reply_to(message, "❌ Нельзя создать дедлайн на прошедшую дату")
                return

            add_deadline(uid, title, deadline_date.isoformat(), time_str)
            bot.reply_to(message, f"✅ Дедлайн \"{title}\" до {date_str} сохранён. Буду напоминать каждый день в {time_str}")
        except Exception as e:
            bot.reply_to(message, f"❌ Ошибка: {e}. Формат: /deadline add ТЕМА ДД.ММ.ГГГГ ЧЧ:ММ")

    elif sub == "list":
        deadlines = get_user_deadlines(uid)
        if not deadlines:
            bot.reply_to(message, "📭 У вас нет активных дедлайнов.")
            return

        today = datetime.date.today()
        text = "📋 **Ваши дедлайны (ежедневные напоминания):**\n\n"

        for d in deadlines:
            did, title, ddate, dtime = d
            deadline_date = datetime.date.fromisoformat(ddate)
            days_left = (deadline_date - today).days
            text += (f"🆔 {did} — {title}\n"
                     f" до {deadline_date.strftime('%d.%m.%Y')} (осталось {days_left} дн.)\n"
                     f" 📌 напоминание в {dtime}\n\n")

        text += "Чтобы удалить, отправьте: /deadline delete ID"
        bot.send_message(uid, text, parse_mode="Markdown")

    elif sub == "delete":
        if len(parts) != 3:
            bot.reply_to(message, "❌ Формат: /deadline delete ID")
            return
        try:
            did = int(parts[2])
            cursor.execute('SELECT user_id FROM user_deadlines WHERE id = ?', (did,))
            row = cursor.fetchone()
            if not row or row[0] != uid:
                bot.reply_to(message, "❌ Дедлайн не найден или это не ваш")
                return
            delete_deadline(did, uid)
            bot.reply_to(message, f"✅ Дедлайн {did} удалён")
        except ValueError:
            bot.reply_to(message, "❌ ID должен быть числом")
    else:
        bot.reply_to(message, "❌ Неизвестная команда. Используй add, list, delete")


# ======================== РАССЫЛКА ========================
def send_notification(user_id, notif_type):
    settings = get_user_settings(user_id)
    enabled = {
        'morning': settings[6],
        'lunch': settings[7],
        'dinner': settings[8],
        'check': settings[9],
        'sleep': settings[10]
    }.get(notif_type, 0)
    if not enabled:
        return
    days, h, m, s, sem = get_deadline_info()

    if notif_type == 'morning':
        msg = (f"🌅 ДОБРОЕ УТРО!\n\n"
               f"До конца {sem} осталось {days} дней.\n"
               f"Соберись, сегодня отличный день, чтобы закрыть пару долгов! 💪")
    elif notif_type == 'lunch':
        msg = (f"🍽 ВРЕМЯ ОБЕДАТЬ!\n\n"
               f"Отложи учебу на 30 минут, поешь и отдохни.\n"
               f"До конца семестра {days} дней — силы тебе ещё пригодятся!")
    elif notif_type == 'dinner':
        msg = (f"🌙 ПОРА УЖИНАТЬ!\n\n"
               f"Вечер наступает, пора подкрепиться.\n"
               f"До конца семестра осталось {days} дней.\n"
               f"Вечером можно ещё успеть доделать лабораторные!")
    elif notif_type == 'check':
        msg = (f"🌆 ВЕЧЕРНЯЯ ПРОВЕРКА\n\n"
               f"Привет! Сейчас 19:00 — время подвести итоги дня.\n\n"
               f"📌 **Ты выполнил сегодня задания?**\n"
               f"• Если **да** — отлично! Ты молодец, теперь можно отдохнуть 😊\n"
               f"• Если **нет** — не переживай, у тебя ещё есть вечер. Наберись сил и начни.\n"
               f"• Если **совсем нет сил** — отложи на завтра. Главное — не выгорать!\n\n"
               f"До конца семестра осталось {days} дней. Ты успеваешь!")
    elif notif_type == 'sleep':
        msg = (f"😴 Эй, привет! Вижу, {settings[5]}!\n\n"
               f"Пора спать! Если ты сейчас делаешь уроки — отложи на завтра.\n"
               f"Сон важнее учебы. Выспишься — завтра всё сделаешь быстрее!\n"
               f"До конца семестра осталось {days} дней, но здоровье важнее. Спокойной ночи! 🌙")
    else:
        return
    try:
        bot.send_message(user_id, msg)
        print(f"Уведомление {notif_type} отправлено {user_id} в {datetime.datetime.now().strftime('%H:%M')}")
    except Exception as e:
        print(f"Ошибка отправки {user_id}: {e}")


def check_deadlines():
    today = datetime.date.today()
    today_str = today.isoformat()
    now_time = datetime.datetime.now().strftime("%H:%M")

    cursor.execute('''
    SELECT id, user_id, title, deadline_date, deadline_time, last_notified_date 
    FROM user_deadlines 
    WHERE deadline_date >= ?
    ''', (today_str,))

    for d in cursor.fetchall():
        did, uid, title, ddate, dtime, last_notified = d
        deadline_date = datetime.date.fromisoformat(ddate)

        if today <= deadline_date:
            if dtime == now_time and last_notified != today_str:
                days_left = (deadline_date - today).days
                msg = (f"⏰ НАПОМИНАНИЕ О ДЕДЛАЙНЕ\n"
                       f"📌 Тема: {title}\n"
                       f"📅 Дедлайн: {deadline_date.strftime('%d.%m.%Y')}\n"
                       f"🔗 Осталось дней: {days_left}")

                try:
                    bot.send_message(uid, msg)
                    cursor.execute('''
                    UPDATE user_deadlines 
                    SET last_notified_date = ? 
                    WHERE id = ?
                    ''', (today_str, did))
                    conn.commit()
                    print(f"Дедлайн {did} напомнил пользователю {uid} в {now_time}")
                except Exception as e:
                    print(f"Ошибка дедлайна {did}: {e}")


def check_and_send():
    now = datetime.datetime.now().strftime("%H:%M")
    cursor.execute('SELECT user_id FROM user_settings')
    for (uid,) in cursor.fetchall():
        s = get_user_settings(uid)
        times = {
            'morning': s[1], 'lunch': s[2], 'dinner': s[3],
            'check': s[4], 'sleep': s[5]
        }
        for ttype, tm in times.items():
            if tm == now:
                send_notification(uid, ttype)
    check_deadlines()


def run_schedule():
    print("⏰ Планировщик запущен. Ждем времени отправки...")
    while True:
        check_and_send()
        time.sleep(60)


# ======================== ЗАПУСК ========================
if __name__ == "__main__":
    print("🤖 ФИНАЛЬНАЯ ВЕРСИЯ БОТА ЗАПУЩЕНА!")

    t = threading.Thread(target=run_schedule, daemon=True)
    t.start()
    bot.infinity_polling()
