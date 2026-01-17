import asyncio
import logging
import os
from datetime import datetime, time
import pytz
from bs4 import BeautifulSoup
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters, ConversationHandler

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфігурація 
TOKEN = os.getenv("BOT_TOKEN") 
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  
PARSE_URL = "http://mbk.mk.ua/?page_id=17254"
DEFAULT_NOTIFICATION_TIME = time(8, 0, 0)
TIMEZONE = pytz.timezone('Europe/Kiev')

# Перевірка налаштувань
if not TOKEN:
    raise ValueError("❌ BOT_TOKEN не встановлено в змінних оточення!")
if ADMIN_ID == 0:
    raise ValueError("❌ ADMIN_ID не встановлено в змінних оточення!")

# Доступні групи
GROUPS = ["Б-101", "Д-103", "Д-104", "БМ-106", "КН-107"]

# Зберігання даних користувачів
user_data = {}

# Стани для ConversationHandler
WAITING_FOR_REPORT = 1
WAITING_FOR_CUSTOM_TIME = 2


def update_groups_for_new_year():
    """Оновлює номери груп після 1 вересня (перехід на новий курс)"""
    global GROUPS
    now = datetime.now(TIMEZONE)
    
    if now.year >= 2026 and now.month >= 9:
        GROUPS = [group.replace("-1", "-2") for group in GROUPS]
        logger.info(f"Групи оновлено для нового навчального року: {GROUPS}")


async def parse_replacements(target_group):
    """Парсинг таблиці замін з сайту для конкретної групи"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(PARSE_URL, timeout=30) as response:
                if response.status != 200:
                    logger.error(f"Помилка запиту: статус {response.status}")
                    return None
                
                html = await response.text()
        
        soup = BeautifulSoup(html, 'html.parser')
        today = datetime.now(TIMEZONE)
    
        months_uk = {
            1: "січня", 2: "лютого", 3: "березня", 4: "квітня",
            5: "травня", 6: "червня", 7: "липня", 8: "серпня",
            9: "вересня", 10: "жовтня", 11: "листопада", 12: "грудня"
        }
        
        # Формат: "15 січня 2026"
        today_uk = f"{today.day} {months_uk[today.month]} {today.year}"
        
        logger.info(f"🔍 Шукаємо дату: {today_uk}")
        logger.info(f"🔍 Група: {target_group}")
        
        # Шукаємо елемент з датою
        date_element = soup.find(string=lambda text: text and today_uk in text)
        
        if not date_element:
            logger.warning(f"❌ Дату {today_uk} не знайдено на сторінці")
            # Використовуємо першу таблицю
            all_tables = soup.find_all('table')
            if not all_tables:
                return None
            target_table = all_tables[0]
            logger.info("📋 Використовуємо першу таблицю")
        else:
            logger.info(f"✅ Знайдено елемент з датою!")
            logger.info(f"📍 Текст елемента: {str(date_element)[:150]}")
            
            # Шукаємо таблицю після цього елемента
            target_table = date_element.find_next('table')
            
            if not target_table:
                logger.error("❌ Таблиця після дати не знайдена")
                return None
            
            logger.info("✅ Знайдено таблицю ПІСЛЯ дати!")
        
        # Парсимо таблицю
        replacements = []
        rows = target_table.find_all('tr')
        logger.info(f"\n📊 Рядків у таблиці: {len(rows)}")
        logger.info(f"🎯 Шукаємо групу: '{target_group}'")
        logger.info("\n=== ВСІ ГРУПИ В ТАБЛИЦІ ===")
        
        for row_idx, row in enumerate(rows):
            cells = row.find_all(['td', 'th'])
            
            if len(cells) >= 4:
                group_text = cells[0].get_text(strip=True)
                pair_num = cells[1].get_text(strip=True)
                
                # Логуємо всі рядки
                logger.info(f"Рядок {row_idx}: '{group_text}' | Пара: '{pair_num}'")
                
                # Пропускаємо заголовки
                if not group_text or "Групи" in group_text or group_text == "№":
                    logger.info(f"  ⏭️ Пропускаємо (заголовок)")
                    continue
                
                # перевіряємо групу
                if target_group == group_text:
                    logger.info(f"  ✅✅✅ ТОЧНЕ СПІВПАДІННЯ!")
                    
                    old_subject = cells[2].get_text(strip=True)
                    new_subject = cells[3].get_text(strip=True)
                    
                    if pair_num and pair_num not in ["№", "пар"]:
                        if "———" in old_subject:
                            old_subject = "—"
                        
                        replacements.append({
                            'group': group_text,
                            'pair': pair_num,
                            'old': old_subject if old_subject else "—",
                            'new': new_subject if new_subject else "—"
                        })
                        logger.info(f"  ✅ ДОДАНО заміну: пара {pair_num}")
                    else:
                        logger.info(f"  ⏭️ Пропускаємо (некоректна пара: '{pair_num}')")
                else:
                    logger.info(f"  ❌ Не співпадає (шукали '{target_group}', знайшли '{group_text}')")
        
        logger.info(f"\n{'='*50}")
        logger.info(f"🎯 ПІДСУМОК: {len(replacements)} замін для '{target_group}'")
        
        if len(replacements) == 0:
            logger.warning(f"⚠️ Жодної заміни не знайдено для групи '{target_group}'")
            logger.warning(f"⚠️ Можливо група записана інакше на сайті?")
        
        return replacements if replacements else None
        
    except Exception as e:
        logger.error(f"💥 ПОМИЛКА: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None


def format_message(replacements, group_name):
    """Форматування повідомлення про заміни"""
    if not replacements:
        return [f"📋 Змін для групи {group_name} не знайдено"]
    
    today = datetime.now(TIMEZONE).strftime("%d.%m.%Y")
    
    messages = []
    current_message = f"📢 <b>Заміни для групи {group_name}</b>\n"
    current_message += f"📅 Дата: {today}\n\n"
    
    for idx, repl in enumerate(replacements, 1):
        repl_text = f"<b>{idx}. Пара №{repl['pair']}</b>\n"
        repl_text += f"❌ Було: {repl['old'][:200]}{'...' if len(repl['old']) > 200 else ''}\n"
        repl_text += f"✅ Буде: {repl['new'][:200]}{'...' if len(repl['new']) > 200 else ''}\n\n"
        
        if len(current_message) + len(repl_text) > 4000:
            messages.append(current_message.strip())
            current_message = repl_text
        else:
            current_message += repl_text
    
    if current_message.strip():
        messages.append(current_message.strip())
    
    return messages


def get_group_selection_keyboard():
    """Створює клавіатуру з вибором груп"""
    keyboard = []
    row = []
    
    for idx, group in enumerate(GROUPS):
        row.append(InlineKeyboardButton(group, callback_data=f"select_{group}"))
        
        if len(row) == 2 or idx == len(GROUPS) - 1:
            keyboard.append(row)
            row = []
    
    return InlineKeyboardMarkup(keyboard)


def get_main_menu_keyboard():
    """Головне меню після підписки"""
    keyboard = [
        [InlineKeyboardButton("🔄 Змінити групу", callback_data="change_group")],
        [InlineKeyboardButton("🕐 Налаштувати час", callback_data="change_time")],
        [InlineKeyboardButton("⚙️ Налаштування", callback_data="settings")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_settings_keyboard():
    """Меню налаштувань"""
    keyboard = [
        [InlineKeyboardButton("🔄 Змінити групу", callback_data="change_group")],
        [InlineKeyboardButton("🕐 Налаштувати час", callback_data="change_time")],
        [InlineKeyboardButton("⚠️ Повідомити про помилку", callback_data="report_issue")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_time_selection_keyboard():
    """Клавіатура вибору часу сповіщень"""
    keyboard = [
        [InlineKeyboardButton("07:00", callback_data="time_07:00"),
         InlineKeyboardButton("08:00", callback_data="time_08:00")],
        [InlineKeyboardButton("09:00", callback_data="time_09:00"),
         InlineKeyboardButton("✏️ Свій варіант", callback_data="time_custom")],
        [InlineKeyboardButton("◀️ Назад", callback_data="settings")]
    ]
    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробник команди /start"""
    user_id = update.effective_user.id
    update_groups_for_new_year()
    
    if user_id in user_data:
        group = user_data[user_id].get("group", "не обрана")
        notify_time = user_data[user_id].get("time", DEFAULT_NOTIFICATION_TIME)
        
        await update.message.reply_text(
            f"👋 <b>З поверненням!</b>\n\n"
            f"📚 Ваша група: <b>{group}</b>\n"
            f"🕐 Час сповіщень: <b>{notify_time.strftime('%H:%M')}</b>\n\n"
            f"Використовуйте меню нижче:",
            parse_mode='HTML',
            reply_markup=get_main_menu_keyboard()
        )
    else:
        await update.message.reply_text(
            "👋 <b>Вітаю!</b>\n\n"
            "Я бот для відстеження замін у МБК.\n"
            "Оберіть вашу групу:",
            parse_mode='HTML',
            reply_markup=get_group_selection_keyboard()
        )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробник натискань на кнопки"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if query.data == "change_group":
        await query.edit_message_text(
            "🔄 <b>Оберіть нову групу:</b>",
            parse_mode='HTML',
            reply_markup=get_group_selection_keyboard()
        )
    
    elif query.data.startswith("select_"):
        selected_group = query.data.replace("select_", "")
        
        if user_id not in user_data:
            user_data[user_id] = {}
        
        user_data[user_id]["group"] = selected_group
        
        if "time" not in user_data[user_id]:
            user_data[user_id]["time"] = DEFAULT_NOTIFICATION_TIME
        
        logger.info(f"Користувач {user_id} підписався на групу {selected_group}")
        
        notify_time = user_data[user_id]["time"]
        
        await query.edit_message_text(
            f"✅ <b>Підписка оформлена!</b>\n\n"
            f"📚 Група: <b>{selected_group}</b>\n"
            f"🕐 Час сповіщень: <b>{notify_time.strftime('%H:%M')}</b>\n\n"
            f"📬 Повідомлення надходитимуть щодня.\n"
            f"🔍 Перевірити зараз: /check",
            parse_mode='HTML',
            reply_markup=get_main_menu_keyboard()
        )
    
    elif query.data == "change_time":
        await query.edit_message_text(
            "🕐 <b>Оберіть час щоденних сповіщень:</b>\n\n"
            "Сповіщення надходитимуть щодня о обраному часі (за київським часом).",
            parse_mode='HTML',
            reply_markup=get_time_selection_keyboard()
        )
    
    elif query.data.startswith("time_"):
        if query.data == "time_custom":
            await query.edit_message_text(
                "✏️ <b>Введіть свій час у форматі ГГ:ХХ</b>\n\n"
                "Наприклад: <code>07:30</code> або <code>10:15</code>\n\n"
                "Час вказується за київським часовим поясом.",
                parse_mode='HTML'
            )
            context.user_data['waiting_custom_time'] = True
        else:
            time_str = query.data.replace("time_", "")
            hour, minute = map(int, time_str.split(":"))
            new_time = time(hour, minute, 0)
            
            if user_id not in user_data:
                user_data[user_id] = {}
            
            user_data[user_id]["time"] = new_time
            
            group = user_data[user_id].get("group", "не обрана")
            
            await query.edit_message_text(
                f"✅ <b>Час оновлено!</b>\n\n"
                f"📚 Група: <b>{group}</b>\n"
                f"🕐 Новий час сповіщень: <b>{new_time.strftime('%H:%M')}</b>\n\n"
                f"Сповіщення надходитимуть щодня о {new_time.strftime('%H:%M')} за київським часом.",
                parse_mode='HTML',
                reply_markup=get_main_menu_keyboard()
            )
    
    elif query.data == "settings":
        group = user_data.get(user_id, {}).get("group", "не обрана")
        notify_time = user_data.get(user_id, {}).get("time", DEFAULT_NOTIFICATION_TIME)
        
        await query.edit_message_text(
            f"⚙️ <b>Налаштування</b>\n\n"
            f"📚 Група: <b>{group}</b>\n"
            f"🕐 Час сповіщень: <b>{notify_time.strftime('%H:%M')}</b>",
            parse_mode='HTML',
            reply_markup=get_settings_keyboard()
        )
    
    elif query.data == "back_to_menu":
        group = user_data.get(user_id, {}).get("group", "не обрана")
        notify_time = user_data.get(user_id, {}).get("time", DEFAULT_NOTIFICATION_TIME)
        
        await query.edit_message_text(
            f"📚 Ваша група: <b>{group}</b>\n"
            f"🕐 Час сповіщень: <b>{notify_time.strftime('%H:%M')}</b>\n\n"
            f"Використовуйте меню нижче:",
            parse_mode='HTML',
            reply_markup=get_main_menu_keyboard()
        )
    
    elif query.data == "report_issue":
        await query.edit_message_text(
            "⚠️ <b>Повідомлення про помилку</b>\n\n"
            "Опишіть проблему одним повідомленням.\n"
            "Наприклад: «У розкладі на середу не співпадає 3 пара»\n\n"
            "Ваше повідомлення буде надіслано адміністратору.",
            parse_mode='HTML'
        )
        context.user_data['waiting_report'] = True


async def handle_custom_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка введення користувацького часу"""
    if not context.user_data.get('waiting_custom_time'):
        return
    
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    try:
        hour, minute = map(int, text.split(":"))
        
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        
        new_time = time(hour, minute, 0)
        
        if user_id not in user_data:
            user_data[user_id] = {}
        
        user_data[user_id]["time"] = new_time
        context.user_data['waiting_custom_time'] = False
        
        group = user_data[user_id].get("group", "не обрана")
        
        await update.message.reply_text(
            f"✅ <b>Час оновлено!</b>\n\n"
            f"📚 Група: <b>{group}</b>\n"
            f"🕐 Новий час сповіщень: <b>{new_time.strftime('%H:%M')}</b>\n\n"
            f"Сповіщення надходитимуть щодня о {new_time.strftime('%H:%M')} за київським часом.",
            parse_mode='HTML',
            reply_markup=get_main_menu_keyboard()
        )
    except:
        await update.message.reply_text(
            "❌ Невірний формат!\n\n"
            "Введіть час у форматі <code>ГГ:ХХ</code>\n"
            "Наприклад: <code>07:30</code>",
            parse_mode='HTML'
        )


async def handle_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка повідомлення про помилку"""
    if not context.user_data.get('waiting_report'):
        # Якщо не в режимі очікування репорту, перевіряємо custom time
        if context.user_data.get('waiting_custom_time'):
            await handle_custom_time(update, context)
        return
    
    user_id = update.effective_user.id
    report_text = update.message.text
    group = user_data.get(user_id, {}).get("group", "не вказана")
    now = datetime.now(TIMEZONE).strftime("%d.%m.%Y %H:%M")
    
    admin_message = (
        f"⚠️ <b>Повідомлення про помилку</b>\n\n"
        f"📚 Група: <b>{group}</b>\n"
        f"👤 User ID: <code>{user_id}</code>\n"
        f"📅 Дата і час: {now}\n\n"
        f"💬 Текст:\n{report_text}"
    )
    
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=admin_message,
            parse_mode='HTML'
        )
        
        context.user_data['waiting_report'] = False
        
        await update.message.reply_text(
            "✅ <b>Дякуємо!</b>\n\n"
            "Ваше повідомлення надіслано адміністратору.\n"
            "Ми розглянемо його найближчим часом.",
            parse_mode='HTML',
            reply_markup=get_main_menu_keyboard()
        )
        
        logger.info(f"Отримано звіт від користувача {user_id}, надіслано до {ADMIN_ID}")
    except Exception as e:
        logger.error(f"Помилка відправки звіту до {ADMIN_ID}: {e}")
        await update.message.reply_text(
            f"❌ Виникла помилка при відправці повідомлення: {e}\n"
            f"Перевірте чи правильний ADMIN_ID: {ADMIN_ID}",
            reply_markup=get_main_menu_keyboard()
        )


async def check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробник команди /check"""
    user_id = update.effective_user.id
    
    if user_id not in user_data or "group" not in user_data[user_id]:
        await update.message.reply_text(
            "❌ Ви не підписані на жодну групу.\n"
            "Використайте /start щоб обрати групу.",
            reply_markup=get_group_selection_keyboard()
        )
        return
    
    user_group = user_data[user_id]["group"]
    await update.message.reply_text(f"🔍 Перевіряю заміни для групи {user_group}...")
    
    try:
        replacements = await parse_replacements(user_group)
        messages = format_message(replacements, user_group)
        
        for msg in messages:
            await update.message.reply_text(msg, parse_mode='HTML')
            await asyncio.sleep(0.5)
    except Exception as e:
        logger.error(f"Помилка при перевірці замін: {e}")
        await update.message.reply_text(
            "❌ Виникла помилка при перевірці замін. Спробуйте пізніше."
        )


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробник команди /settings"""
    user_id = update.effective_user.id
    
    if user_id not in user_data:
        await update.message.reply_text(
            "❌ Спочатку оберіть групу через /start"
        )
        return
    
    group = user_data[user_id].get("group", "не обрана")
    notify_time = user_data[user_id].get("time", DEFAULT_NOTIFICATION_TIME)
    
    await update.message.reply_text(
        f"⚙️ <b>Налаштування</b>\n\n"
        f"📚 Група: <b>{group}</b>\n"
        f"🕐 Час сповіщень: <b>{notify_time.strftime('%H:%M')}</b>",
        parse_mode='HTML',
        reply_markup=get_settings_keyboard()
    )


async def send_daily_notification(context: ContextTypes.DEFAULT_TYPE):
    """Щоденна розсилка сповіщень"""
    logger.info("Запуск щоденної розсилки")
    update_groups_for_new_year()
    
    try:
        current_time = datetime.now(TIMEZONE).time()
        current_hour_minute = time(current_time.hour, current_time.minute)
        
        for user_id, data in user_data.items():
            user_time = data.get("time", DEFAULT_NOTIFICATION_TIME)
            user_group = data.get("group")
            
            if not user_group:
                continue
            
            if user_time.hour == current_hour_minute.hour and user_time.minute == current_hour_minute.minute:
                logger.info(f"Відправка сповіщення користувачу {user_id} (група {user_group})")
                
                try:
                    replacements = await parse_replacements(user_group)
                    messages = format_message(replacements, user_group)
                    
                    for msg in messages:
                        await context.bot.send_message(
                            chat_id=user_id,
                            text=msg,
                            parse_mode='HTML'
                        )
                        await asyncio.sleep(0.3)
                    
                    logger.info(f"Сповіщення відправлено користувачу {user_id}")
                except Exception as e:
                    logger.error(f"Помилка відправки користувачу {user_id}: {e}")
                
                await asyncio.sleep(1)
    except Exception as e:
        logger.error(f"Помилка при розсилці: {e}")


async def post_init(application: Application):
    """Ініціалізація після запуску"""
    job_queue = application.job_queue
    
    job_queue.run_repeating(
        send_daily_notification,
        interval=60,
        first=10
    )
    
    logger.info("Налаштовано щоденну розсилку (перевірка кожну хвилину)")


def main():
    """Головна функція запуску бота"""
    logger.info("Запуск бота...")
    
    application = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("check", check))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # MessageHandler для текстових повідомлень (custom time і reports)
    async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if context.user_data.get('waiting_custom_time'):
            await handle_custom_time(update, context)
        elif context.user_data.get('waiting_report'):
            await handle_report(update, context)
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    
    logger.info("Бот запущено успішно!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':

    main()
