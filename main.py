import os
import logging
import asyncio
from datetime import time
from flask import Flask, request
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, JobQueue
import openai
import threading

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# --- OpenAI ---
openai.api_key = os.getenv("OPENAI_API_KEY")

# --- Хранение пользовательских настроек ---
user_settings = {}

# --- Flask для keep-alive ---
app = Flask("")

@app.route("/", methods=["GET"])
def home():
    return "Bot is running!"

@app.route(f"/{os.getenv('BOT_TOKEN')}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot=application.bot)
    asyncio.run(application.update_queue.put(update))
    return "ok"

# --- Telegram Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот с рецептами на ужин.\n"
        "Используй /settime <часы> <минуты> <повторы> чтобы настроить время и количество повторов.\n"
        "Используй /recipe чтобы получить рецепт прямо сейчас.\n"
        "Можно указать предпочтения и ингредиенты: /recipe вегетарианский картофель морковь"
    )

async def generate_recipe(preferences="", ingredients=""):
    prompt = "Предложи один легкий рецепт ужина с пошаговым описанием."
    if preferences:
        prompt += f" Предпочтения: {preferences}."
    if ingredients:
        prompt += f" Используй эти ингредиенты, если возможно: {ingredients}."

    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "Ты помогаешь пользователю придумывать простые, лёгкие рецепты на ужин."},
            {"role": "user", "content": prompt}
        ],
        max_tokens=250
    )
    return response.choices[0].message.content.strip()

async def recipe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    repeats = user_settings.get(user_id, {}).get("repeats", 1)

    preferences = ""
    ingredients = ""
    if context.args:
        preferences = context.args[0]
        if len(context.args) > 1:
            ingredients = ", ".join(context.args[1:])

    recipe_text = await generate_recipe(preferences, ingredients)
    user_settings[user_id] = {"last_recipe": recipe_text, "repeats_left": repeats, "repeats": repeats}

    keyboard = [
        [InlineKeyboardButton("Случайный другой рецепт", callback_data="random_recipe")],
        [InlineKeyboardButton("Принять рецепт", callback_data="accept_recipe")]
    ]
    await update.message.reply_text(recipe_text, reply_markup=InlineKeyboardMarkup(keyboard))

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "random_recipe":
        recipe_text = await generate_recipe()
        user_settings[user_id]["last_recipe"] = recipe_text
        await query.edit_message_text(recipe_text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Случайный другой рецепт", callback_data="random_recipe")],
            [InlineKeyboardButton("Принять рецепт", callback_data="accept_recipe")]
        ]))
    elif query.data == "accept_recipe":
        await query.edit_message_text(f"Отлично! Приятного ужина с рецептом:\n{user_settings[user_id]['last_recipe']}")
        user_settings[user_id]["repeats_left"] = 0

async def settime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        hour = int(context.args[0])
        minute = int(context.args[1])
        repeats = int(context.args[2])
    except (IndexError, ValueError):
        await update.message.reply_text("Использование: /settime <часы> <минуты> <повторы> (например /settime 18 30 3)")
        return

    user_id = update.effective_user.id
    user_settings[user_id] = {"repeats": repeats, "repeats_left": repeats}

    job_queue: JobQueue = context.job_queue
    job_queue.run_daily(send_recipe, time(hour, minute), context=user_id)

    await update.message.reply_text(f"Время для отправки рецепта установлено на {hour:02d}:{minute:02d}, повторов: {repeats}")

async def send_recipe(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    user_id = job.context
    repeats_left = user_settings.get(user_id, {}).get("repeats_left", 0)
    if repeats_left <= 0:
        return

    recipe_text = await generate_recipe()
    user_settings[user_id]["last_recipe"] = recipe_text

    keyboard = [
        [InlineKeyboardButton("Случайный другой рецепт", callback_data="random_recipe")],
        [InlineKeyboardButton("Принять рецепт", callback_data="accept_recipe")]
    ]

    await context.bot.send_message(chat_id=user_id, text=recipe_text, reply_markup=InlineKeyboardMarkup(keyboard))
    user_settings[user_id]["repeats_left"] -= 1

# --- Main ---
if __name__ == "__main__":
    application = ApplicationBuilder().token(os.getenv("BOT_TOKEN")).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("recipe", recipe))
    application.add_handler(CommandHandler("settime", settime))
    application.add_handler(CallbackQueryHandler(button))

    # --- Flask keep-alive ---
    port = int(os.environ.get("PORT", 5000))
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=port)).start()

    # --- Установка webhook в Telegram ---
    TELEGRAM_WEBHOOK_URL = f"https://rdecloud-bot.onrender.com/{os.getenv('BOT_TOKEN')}"
    requests.get(f"https://api.telegram.org/bot{os.getenv('BOT_TOKEN')}/setWebhook?url={TELEGRAM_WEBHOOK_URL}")
