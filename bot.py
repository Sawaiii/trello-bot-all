import os
import tempfile
import requests
import json
from datetime import datetime, timedelta
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    CommandHandler,
    Updater,
    CallbackContext,
    ConversationHandler,
    MessageHandler,
    Filters,
    CallbackQueryHandler,
)

from config import telegram_api_token, trello_key, trello_token, board_id, delay

trello_base_url = "https://api.trello.com/1"
telegram_bot = Updater(telegram_api_token, use_context=True)
dispatcher = telegram_bot.dispatcher

ENTER_CARD_NAME, ENTER_CARD_DESC, CHECK_CARD_STATUS = range(3)

def create_inline_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("Отключить уведомления", callback_data="disable_notifications"),
            InlineKeyboardButton("Включить уведомления", callback_data="enable_notifications"),
        ],
        [
            InlineKeyboardButton("Создать карточку", callback_data="create_card"),
            InlineKeyboardButton("Начать мониторинг", callback_data="start_monitoring"),
        ],
        [
            InlineKeyboardButton("Статус чек-листа", callback_data="check_card_status"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)

def add_checklist_to_card(card_id):
    url = f"{trello_base_url}/checklists"
    checklist_name = "My Checklist"
    items = ["АРТ", "ПРО", "АРЕНДА"]

    # Create a new checklist
    payload = {
        "key": trello_key,
        "token": trello_token,
        "idCard": card_id,
        "name": checklist_name,
    }
    checklist_id = trello_request("POST", url, payload)["id"]

    # Add items to the checklist
    for item in items:
        url = f"{trello_base_url}/checklists/{checklist_id}/checkItems"
        payload = {
            "key": trello_key,
            "token": trello_token,
            "name": item,
        }
        trello_request("POST", url, payload)

def trello_request(method, url, payload=None):
    http_method = getattr(requests, method.lower())
    response = http_method(url, params={"key": trello_key, "token": trello_token}, json=payload)
    return json.loads(response.text)

def start(update, context):
    message = (
        "Привет! Я бот-уведомитель о событиях на доске Trello📊. Чтобы начать получать уведомления, "
        "отправьте в чат команду /start_monitoring."
    )
    keyboard = create_inline_keyboard()
    context.bot.send_message(chat_id=update.effective_chat.id, text=message, reply_markup=keyboard)
    if "notifications_enabled" not in context.user_data:
        context.user_data["notifications_enabled"] = True
        context.user_data["notifications_start_time"] = datetime.utcnow()

start_handler = CommandHandler("start", start)
dispatcher.add_handler(start_handler)

def create_card_input(update, context):
    query = update.callback_query
    query.answer()
    return create_card(update, context)

def get_trello_board_actions(board_id):
    actions_url = f"{trello_base_url}/boards/{board_id}/actions"
    return trello_request("GET", actions_url)

def has_action_changed(user_data, action_type, action_id, action_time):
    if action_type not in user_data:
        user_data[action_type] = {}
    if action_id not in user_data[action_type] or user_data[action_type][action_id] < action_time:
        user_data[action_type][action_id] = action_time
        return True
    return False

def push_to_chat(context, message, chat_id):
    context.bot.send_message(chat_id=chat_id, text=message)

def check_trello_updates(context: CallbackContext):
    user_data, chat_id, initial_scan = context.job.context
    if not user_data.get("notifications_enabled", True):
        return

    card_actions = get_trello_board_actions(board_id)
    if not card_actions:
        return

    last_notif_disable_time = user_data.get("last_notif_disable_time", datetime.utcnow() - timedelta(days=7))

    # Определить время последней отправки группы уведомлений
    last_notif_post_time = user_data.get("last_notif_post_time", datetime.utcnow() - timedelta(days=7))

    # Определить время начала отправки уведомлений
    notifications_start_time = user_data.get("notifications_start_time", datetime.utcnow())

    # Выполните проверку для обновления времени начала отправки уведомлений.
    if not user_data.get("notifications_enabled", True):
        user_data.setdefault("last_notif_disable_time", datetime.utcnow())
        user_data.setdefault("notifications_start_time", datetime.utcnow())
        return

    card_actions = get_trello_board_actions(board_id)
    if not card_actions:
        return

    if "cards" not in user_data:
        user_data["cards"] = {}

    cards = user_data["cards"]

    for action in card_actions:
        action_time = datetime.strptime(action["date"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=None)

        # Пропустить уведомление, если оно старше последней отправки группы уведомлений
        if action_time <= last_notif_post_time:
            continue

        action_id = action["id"]
        action_type = action["type"]

        if "card" in action["data"]:
            card = action["data"]["card"]
            card_id = card["id"]

            if card_id not in cards:
                cards[card_id] = {
                    "name": card["name"]
                }

            if action_type == "createCard" and has_action_changed(user_data, action_type, action_id, action_time):
                card_name = action["data"]["card"]["name"]
                message = f"💥 Новая карточка добавлена 💥: {card_name}"
                if not initial_scan:
                    push_to_chat(context, message, chat_id)

            elif action_type == "updateCard" and "listAfter" in action["data"] and has_action_changed(user_data,
                                                                                                      action_type,
                                                                                                      action_id,
                                                                                                      action_time):
                list_before = action["data"]["listBefore"]["name"]
                list_after = action["data"]["listAfter"]["name"]
                card_name = action["data"]["card"]["name"]
                message = f"🦽 Карточка перемещена 🦽: {card_name}\n" \
                          f"Список до: {list_before}\n" \
                          f"Список после: {list_after}"
                if not initial_scan:
                    push_to_chat(context, message, chat_id)

            elif action_type == "commentCard" and has_action_changed(user_data, action_type, action_id,
                                                                     action_time):
                card_name = action["data"]["card"]["name"]
                comment_text = action["data"]["text"]
                message = f"📢 Новый комментарий к карточке 📢: {card_name}: {comment_text}"
                if not initial_scan:
                    push_to_chat(context, message, chat_id)

            elif action_type == "updateCheckItemStateOnCard" and has_action_changed(user_data, action_type,
                                                                                    action_id, action_time):
                if action["data"]["checkItem"]["state"] == "complete":
                    card_name = action["data"]["card"]["name"]
                    check_item_name = action["data"]["checkItem"]["name"]
                    message = f"✅ Новый выполненный пункт чек-листа у карточки ✅: {card_name}: {check_item_name}"
                    if not initial_scan:
                        push_to_chat(context, message, chat_id)
def save_attached_file(update, context):
    file_id = update.message.document.file_id
    new_file = context.bot.get_file(file_id)
    file_name = update.message.document.file_name or f"doc_{update.message.document.file_unique_id}"
    fd, path = tempfile.mkstemp()
    with os.fdopen(fd, "wb") as tmp:
        new_file.download(out=tmp)
    return file_name, path

def upload_file_to_trello_card(card_id, file_path):
    url = f"{trello_base_url}/cards/{card_id}/attachments"
    with open(file_path, "rb") as f:
        response = requests.post(url, files={"file": f}, params={"key": trello_key, "token": trello_token})
    return response.status_code
def add_job(update, context):
    chat_id = update.effective_chat.id
    due = delay
    user_data = context.user_data
    user_chat_id = update.effective_chat.id

    context.job_queue.run_once(
        check_trello_updates,
        when=0,
        context=(user_data, user_chat_id, True),
        name="initial_scan",
    )

    context.job_queue.run_repeating(
        check_trello_updates,
        interval=10,
        context=(user_data, user_chat_id, False),
    )

    context.bot.send_message(
        chat_id,
        text="Все тесты пройдены👾. Мониторинг запущен🚀."
    )

add_job_handler = CommandHandler("start_monitoring", add_job)
dispatcher.add_handler(add_job_handler)

def create_trello_card(name, desc, list_id):
    url = f"{trello_base_url}/cards"
    payload = {
        "key": trello_key,
        "token": trello_token,
        "name": name,
        "desc": desc,
        "idList": list_id,
    }
    return trello_request("POST", url, payload)

def get_trello_board_list(list_name):
    url = f"{trello_base_url}/boards/{board_id}/lists"
    lists = trello_request("GET", url)

    for lst in lists:
        if lst["name"] == list_name:
            return lst["id"]

    return None

def create_card(update, context):
    message = "Пожалуйста, введите название новой карточки:"
    context.bot.send_message(chat_id=update.effective_chat.id, text=message)
    return ENTER_CARD_NAME

def card_name_callback(update, context):
    card_name = update.message.text
    context.user_data["card_name"] = card_name

    message = "Теперь введите описание карточки:"
    context.bot.send_message(chat_id=update.effective_chat.id, text=message)
    return ENTER_CARD_DESC

def card_desc_callback(update, context):
    card_description = update.message.text
    chat_id = update.effective_chat.id

    list_name = "Задачи от мурзилки"
    list_id = get_trello_board_list(list_name)

    card_name = context.user_data["card_name"]
    created_card = create_trello_card(card_name, card_description, list_id)

    # Add a checklist to the newly created card
    add_checklist_to_card(created_card["id"])

    message = f"Карточка успешно создана: {card_name}\n"

    # Handle attachment if it exists
    if update.message.reply_to_message and update.message.reply_to_message.document:
        file_name, file_path = save_attached_file(update, context)
        upload_response = upload_file_to_trello_card(created_card["id"], file_path)
        message += f"Файл {file_name} {'успешно прикреплен' if upload_response == 200 else 'не был прикреплен'}\n"

    context.bot.send_message(chat_id=update.effective_chat.id, text=message)
    del context.user_data["card_name"]
    return ConversationHandler.END

create_card_handler = ConversationHandler(
    entry_points=[
        CommandHandler("create", create_card),
        CallbackQueryHandler(create_card_input, pattern="create_card"),
    ],
    states={
        ENTER_CARD_NAME: [MessageHandler(Filters.text, card_name_callback)],
        ENTER_CARD_DESC: [MessageHandler(Filters.text, card_desc_callback)],
    },
    fallbacks=[],
)
dispatcher.add_handler(create_card_handler)

def enable_notifications(update, context):
    context.user_data["notifications_start_time"] = datetime.utcnow()
    context.user_data["notifications_enabled"] = True
    context.bot.send_message(chat_id=update.effective_chat.id, text="Уведомления включены")

def disable_notifications(update, context):
    context.user_data["notifications_enabled"] = False
    context.user_data["last_notif_disable_time"] = datetime.utcnow()

    context.user_data["last_notif_post_time"] = datetime.utcnow()

    context.bot.send_message(chat_id=update.effective_chat.id, text="Уведомления выключены")

def button_callback(update, context):
    query = update.callback_query
    data = query.data

    if data == "disable_notifications":
        disable_notifications(update, context)
    elif data == "enable_notifications":
        enable_notifications(update, context)
    elif data == "create_card":
        query.answer()
        create_card_input(update, context)
    elif data == "start_monitoring":
        add_job(update, context)
    elif data == "check_card_status":
        query.answer()
        context.bot.send_message(chat_id=update.effective_chat.id, text="Введите название карточки, чтобы проверить статус чек-листа:")
        return CHECK_CARD_STATUS
    else:
        query.answer(text="Неизвестная команда!")

enable_notifications_handler = CommandHandler("enable_notifications", enable_notifications)
disable_notifications_handler = CommandHandler("disable_notifications", disable_notifications)

dispatcher.add_handler(enable_notifications_handler)
dispatcher.add_handler(disable_notifications_handler)

button_callback_handler = CallbackQueryHandler(
    button_callback,
    pattern="(disable_notifications|enable_notifications|create_card|start_monitoring|check_card_status)",
)
dispatcher.add_handler(button_callback_handler)

status_callback_handler = CallbackQueryHandler(
    button_callback, pattern="check_card_status"
)
dispatcher.add_handler(status_callback_handler)

def find_card_by_name(card_name):
    url = f"{trello_base_url}/boards/{board_id}/cards"
    cards = trello_request("GET", url)
    for card in cards:
        if card["name"].lower() == card_name.lower():
            return card["id"]
    return None

def get_checklist_items(card_id):
    url = f"{trello_base_url}/cards/{card_id}/checklists"
    checklists = trello_request("GET", url)
    items = []
    for checklist in checklists:
        for item in checklist["checkItems"]:
            items.append({
                "name": item["name"],
                "state": item["state"],
            })
    return items

def check_card_status(update, context):
    message = "Введите название карточки, чтобы проверить статус чек-листа:"
    context.bot.send_message(chat_id=update.effective_chat.id, text=message)
    return CHECK_CARD_STATUS

def card_status_callback(update, context):
    chat_id = update.effective_chat.id
    card_name = update.message.text
    card_id = find_card_by_name(card_name)

    if card_id is None:
        return CHECK_CARD_STATUS

    items = get_checklist_items(card_id)
    completed_items = [item for item in items if item["state"] == "complete"]
    message = f"Состояние чек-листа для карточки '{card_name}':\n\n"
    message += f"{len(completed_items)} из {len(items)} выполнено.\n"
    for item in items:
        message += f"{item['name']}: {'✅' if item['state'] == 'complete' else '❌'}\n"

    context.bot.send_message(chat_id=update.effective_chat.id, text=message)
    return ConversationHandler.END

check_card_status_handler = MessageHandler(Filters.text, card_status_callback)
dispatcher.add_handler(check_card_status_handler, group=CHECK_CARD_STATUS)

telegram_bot.start_polling()
telegram_bot.idle()