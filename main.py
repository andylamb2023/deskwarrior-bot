import os
import json
import logging
import random
from datetime import datetime, date, timezone
from typing import Dict, Any

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    PreCheckoutQueryHandler,
    MessageHandler,
    Filters,
)

# ----------------- Config -----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")

DATA_FILE = "bot_data.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ----------------- Data helpers -----------------
def load_data() -> Dict[str, Any]:
    if not os.path.exists(DATA_FILE):
        return {"users": {}, "leaderboards": {}}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"users": {}, "leaderboards": {}}

def save_data(data: Dict[str, Any]) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def today_key() -> str:
    return date.today().isoformat()

def get_user(data: Dict[str, Any], user_id: int) -> Dict[str, Any]:
    u = data["users"].setdefault(str(user_id), {
        "today": {},
        "points_today": 0,
    })
    if u.get("_last_date") != today_key():
        u["today"] = {}
        u["points_today"] = 0
        u["_last_date"] = today_key()
    return u

def lb_add(data: Dict[str, Any], chat_id: int, user_id: int, pts: int):
    chat = data["leaderboards"].setdefault(str(chat_id), {})
    day = chat.setdefault(today_key(), {})
    day[str(user_id)] = day.get(str(user_id), 0) + pts

# ----------------- Cards -----------------
EXERCISES = [
    {"key": "pushups", "label": "Push-ups", "reps": [8, 15]},
    {"key": "squats", "label": "Bodyweight squats", "reps": [12, 20]},
    {"key": "plank", "label": "Plank (seconds)", "reps": [30, 60]},
    {"key": "stretch", "label": "Neck/Shoulder stretch (seconds)", "reps": [30, 45]},
    {"key": "walk", "label": "Brisk walk (minutes)", "reps": [5, 8]},
]

WELLNESS_CARDS = [
    {"key": "hydration", "text": "Hydration: Drink a glass of water."},
    {"key": "sitting", "text": "Sitting too long increases risk of back pain and poor circulation. Stand and stretch."},
    {"key": "posture", "text": "Posture check: ears over shoulders, shoulders down, breathe deep x5."},
    {"key": "eyes", "text": "20-20-20 rule: Every 20 minutes, look 20 feet away for 20 seconds."},
    {"key": "breaks", "text": "Micro-break: 60 seconds of movement resets focus."},
]

def pick_card() -> Dict[str, Any]:
    if random.random() < 0.25:
        info = random.choice(WELLNESS_CARDS)
        return {"type": "info", **info}
    ex = random.choice(EXERCISES)
    lo, hi = ex["reps"]
    amt = random.randint(lo, hi)
    return {"type": "exercise", "key": ex["key"], "label": ex["label"], "amount": amt, "points": amt}

# ----------------- Payments -----------------
async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    title = "Desk Warrior Premium"
    description = "Unlock premium: custom intervals (30/45/60), extra cards, streaks."
    payload = "deskwarrior-premium"
    currency = "XTR"
    prices = [LabeledPrice("Premium Upgrade", 50)]  # 50 Stars

    await context.bot.send_invoice(
        chat_id,
        title,
        description,
        payload,
        provider_token="",  # Empty for Stars
        currency=currency,
        prices=prices,
        start_parameter="buy",
    )

async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await query.answer(ok=True)

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    user = get_user(data, update.effective_user.id)
    user["premium"] = True
    save_data(data)
    await update.message.reply_text("🎉 Premium unlocked! Use /interval to set reminders.")


# ----------------- Handlers -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "Desk Warrior - your office workout mate.\n\n"
        "I send mini workouts and wellness tips to keep you moving.\n\n"
        "Commands:\n"
        "/flashcard - get a card now\n"
        "/summary - today's totals and points\n"
        "/leaderboard - top scores in this chat\n\n"
        "Disclaimer: Not medical advice."
    )
    await update.message.reply_text(msg)

async def flashcard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    user = get_user(data, update.effective_user.id)
    card = pick_card()

    if card["type"] == "info":
        await update.message.reply_text(card["text"])
    else:
        text = f"{card['label']} - {card['amount']}\nTap Done when finished."
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Done", callback_data=f"done:{card['key']}:{card['amount']}:{card['points']}")],
            [InlineKeyboardButton("New card", callback_data="newcard")]
        ])
        user["pending"] = {
            "key": card["key"],
            "amount": card["amount"],
            "points": card["points"],
            "issued_at": datetime.now(timezone.utc).timestamp(),
            "consumed": False,
        }
        save_data(data)
        await update.message.reply_text(text, reply_markup=kb)

async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    user = get_user(data, update.effective_user.id)
    totals = user.get("today", {})
    lines = ["Today's totals:"]
    for k, v in totals.items():
        lines.append(f"{k}: {v}")
    lines.append(f"Points: {user.get('points_today', 0)}")
    await update.message.reply_text("\n".join(lines))

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    chat = data["leaderboards"].get(str(update.effective_chat.id), {})
    today = chat.get(today_key(), {})
    if not today:
        await update.message.reply_text("No scores yet today.")
        return
    items = sorted(today.items(), key=lambda kv: kv[1], reverse=True)[:10]
    lines = ["Leaderboard:"]
    for rank, (uid, pts) in enumerate(items, start=1):
        lines.append(f"{rank}. User {uid}: {pts} pts")
    await update.message.reply_text("\n".join(lines))

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = load_data()
    user = get_user(data, query.from_user.id)

    if query.data == "newcard":
        card = pick_card()
        if card["type"] == "info":
            await query.edit_message_text(card["text"])
        else:
            text = f"{card['label']} - {card['amount']}\nTap Done when finished."
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("Done", callback_data=f"done:{card['key']}:{card['amount']}:{card['points']}")],
                [InlineKeyboardButton("New card", callback_data="newcard")]
            ])
            user["pending"] = {
                "key": card["key"],
                "amount": card["amount"],
                "points": card["points"],
                "issued_at": datetime.now(timezone.utc).timestamp(),
                "consumed": False,
            }
            save_data(data)
            await query.edit_message_text(text, reply_markup=kb)
        return

    if query.data.startswith("done:"):
        _, key, amount, pts = query.data.split(":")
        pts = int(pts)
        user["today"][key] = user["today"].get(key, 0) + int(amount)
        user["points_today"] += pts
        lb_add(data, query.message.chat_id, query.from_user.id, pts)
        save_data(data)
        await query.edit_message_text(f"Logged {amount} {key}. +{pts} pts!")

# ----------------- Main -----------------
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN not set")
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("flashcard", flashcard))
    app.add_handler(CommandHandler("summary", summary))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CallbackQueryHandler(button))
    app.run_polling()
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))


if __name__ == "__main__":
    main()
