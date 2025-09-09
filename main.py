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
    BotCommand,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    PreCheckoutQueryHandler,
    MessageHandler,
    filters,
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
        "premium": False,
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

# ----------------- Handlers -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "💪 Desk Warrior - your office workout mate.\n\n"
        "I send mini workouts and wellness tips to keep you moving.\n\n"
        "Disclaimer: Not medical advice."
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏋️ Flashcard", callback_data="flashcard"),
            InlineKeyboardButton("📊 Summary", callback_data="summary"),
        ],
        [
            InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard"),
            InlineKeyboardButton("💎 Buy Premium (100⭐)", callback_data="buy"),
        ]
    ])
    await update.message.reply_text(msg, reply_markup=kb)

async def flashcard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    user = get_user(data, update.effective_user.id)
    card = pick_card()

    if card["type"] == "info":
        await update.message.reply_text(card["text"])
    else:
        wait_time = card["amount"] if card["key"] in ["plank", "stretch", "walk"] else max(15, card["amount"] // 2)
        now = datetime.now(timezone.utc).timestamp()
        ready_at = now + wait_time

        text = f"{card['label']} - {card['amount']}\n⏳ Please wait {wait_time}s..."
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏳ Waiting...", callback_data="tooearly")]
        ])

        user["pending"] = {
            "key": card["key"],
            "amount": card["amount"],
            "points": card["points"],
            "ready_at": ready_at,
            "consumed": False,
        }
        save_data(data)

        sent = await update.message.reply_text(text, reply_markup=kb)

        # Auto-swap ⏳ → ✅ Done
        async def unlock_done(ctx: ContextTypes.DEFAULT_TYPE):
            await ctx.bot.edit_message_reply_markup(
                chat_id=sent.chat_id,
                message_id=sent.message_id,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Done", callback_data=f"done:{card['key']}:{card['amount']}:{card['points']}")],
                    [InlineKeyboardButton("🔁 New card", callback_data="flashcard")]
                ])
            )

        context.job_queue.run_once(unlock_done, wait_time)

async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    user = get_user(data, update.effective_user.id)
    totals = user.get("today", {})
    lines = ["📊 Today's totals:"]
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
    lines = ["🏆 Leaderboard:"]
    for rank, (uid, pts) in enumerate(items, start=1):
        lines.append(f"{rank}. User {uid}: {pts} pts")
    await update.message.reply_text("\n".join(lines))

# ----------------- Button Handler -----------------
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = load_data()
    user = get_user(data, query.from_user.id)

    if query.data == "tooearly":
        await query.answer("⏳ Still counting down!", show_alert=True)
        return

    if query.data.startswith("done:"):
        now = datetime.now(timezone.utc).timestamp()
        pending = user.get("pending")
        if not pending or pending.get("consumed"):
            await query.edit_message_text("No active exercise.")
            return
        if now < pending.get("ready_at", 0):
            await query.answer("⏳ Too early! Wait for the bot to unlock Done.", show_alert=True)
            return

        _, key, amount, pts = query.data.split(":")
        pts = int(pts)
        user["today"][key] = user["today"].get(key, 0) + int(amount)
        user["points_today"] += pts
        pending["consumed"] = True
        lb_add(data, query.message.chat_id, query.from_user.id, pts)
        save_data(data)
        await query.edit_message_text(f"✅ Logged {amount} {key}. +{pts} pts!")

    if query.data == "flashcard":
        await flashcard(update, context)
    elif query.data == "summary":
        await summary(update, context)
    elif query.data == "leaderboard":
        await leaderboard(update, context)
    elif query.data == "buy":
        await buy(update, context)

# ----------------- Payments -----------------
async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    title = "Desk Warrior Premium"
    description = "Unlock premium: custom intervals (30/45/60), extra cards, streaks."
    payload = "deskwarrior-premium"
    currency = "XTR"
    prices = [LabeledPrice("Premium Upgrade", 100)]  # 100 Stars

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

# ----------------- Main -----------------
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN not set")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("flashcard", flashcard))
    app.add_handler(CommandHandler("summary", summary))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    async def set_commands(application):
        commands = [
            BotCommand("flashcard", "🏋️ Workout Card"),
            BotCommand("summary", "📊 Today’s Totals"),
            BotCommand("leaderboard", "🏆 Leaderboard"),
            BotCommand("buy", "💎 Premium Upgrade (100⭐)"),
        ]
        await application.bot.set_my_commands(commands)

    app.post_init = set_commands
    app.run_polling()

if __name__ == "__main__":
    main()
