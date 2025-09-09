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

# ----------------- Shared helpers -----------------
async def send_flashcard(target, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    data = load_data()
    user = get_user(data, user_id)
    card = pick_card()

    if card["type"] == "info":
        await target.reply_text(card["text"])
    else:
        wait_time = card["amount"] if card["key"] in ["plank", "stretch", "walk"] else max(15, card["amount"] // 2)
        now = datetime.now(timezone.utc).timestamp()
        ready_at = now + wait_time

        text = f"{card['label']} - {card['amount']}\n⏳ {wait_time}s remaining..."
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏳ Waiting...", callback_data="tooearly")]])

        user["pending"] = {
            "key": card["key"],
            "amount": card["amount"],
            "points": card["points"],
            "ready_at": ready_at,
            "consumed": False,
        }
        save_data(data)

        sent = await target.reply_text(text, reply_markup=kb)

        # Stable countdown with one repeating job
        async def countdown_tick(ctx):
            remaining = int(user["pending"]["ready_at"] - datetime.now(timezone.utc).timestamp())
            if remaining > 0:
                try:
                    await ctx.bot.edit_message_text(
                        chat_id=sent.chat_id,
                        message_id=sent.message_id,
                        text=f"{card['label']} - {card['amount']}\n⏳ {remaining}s remaining...",
                        reply_markup=kb,
                    )
                except Exception:
                    pass
            else:
                try:
                    await ctx.bot.edit_message_text(
                        chat_id=sent.chat_id,
                        message_id=sent.message_id,
                        text=f"{card['label']} - {card['amount']}\n✅ Time’s up! Log your exercise.",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("✅ Done", callback_data=f"done:{card['key']}:{card['amount']}:{card['points']}")],
                            [InlineKeyboardButton("🔁 New card", callback_data="flashcard")]
                        ])
                    )
                except Exception:
                    pass
                ctx.job.schedule_removal()  # stop repeating job

        context.job_queue.run_repeating(countdown_tick, interval=1, first=1)

async def send_summary(target, user_id: int):
    data = load_data()
    user = get_user(data, user_id)
    totals = user.get("today", {})
    lines = ["📊 Today's totals:"]
    for k, v in totals.items():
        lines.append(f"{k}: {v}")
    lines.append(f"Points: {user.get('points_today', 0)}")
    await target.reply_text("\n".join(lines))

async def send_leaderboard(target, chat_id: int):
    data = load_data()
    chat = data["leaderboards"].get(str(chat_id), {})
    today = chat.get(today_key(), {})
    if not today:
        await target.reply_text("No scores yet today.")
        return
    items = sorted(today.items(), key=lambda kv: kv[1], reverse=True)[:10]
    lines = ["🏆 Leaderboard:"]
    for rank, (uid, pts) in enumerate(items, start=1):
        lines.append(f"{rank}. User {uid}: {pts} pts")
    await target.reply_text("\n".join(lines))

# ----------------- Command handlers -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "💪 Desk Warrior - your office workout mate.\n\n"
        "All features are free to use.\n"
        "If you like this bot, consider tipping ⭐\n\n"
        "Disclaimer: Not medical advice."
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏋️ Flashcard", callback_data="flashcard"),
            InlineKeyboardButton("📊 Summary", callback_data="summary"),
        ],
        [
            InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard"),
            InlineKeyboardButton("☕ Tip the Creator (100⭐)", callback_data="tip"),
        ]
    ])
    await update.message.reply_text(msg, reply_markup=kb)

async def flashcard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_flashcard(update.message, context, update.effective_user.id)

async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_summary(update.message, update.effective_user.id)

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_leaderboard(update.message, update.effective_chat.id)

# ----------------- Button handler -----------------
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
            await query.answer("⏳ Too early!", show_alert=True)
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
        await send_flashcard(query.message, context, query.from_user.id)
    elif query.data == "summary":
        await send_summary(query.message, query.from_user.id)
    elif query.data == "leaderboard":
        await send_leaderboard(query.message, query.message.chat_id)
    elif query.data == "tip":
        await tip(update, context)

# ----------------- Payments -----------------
async def tip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    title = "Support Desk Warrior"
    description = "If this bot helps you, consider leaving a 100⭐ tip!"
    payload = "deskwarrior-tip"
    currency = "XTR"
    prices = [LabeledPrice("Tip", 100)]  # 100 Stars

    await context.bot.send_invoice(
        chat_id,
        title,
        description,
        payload,
        provider_token="",  # Empty for Stars
        currency=currency,
        prices=prices,
        start_parameter="tip",
    )

async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await query.answer(ok=True)

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🙏 Thank you for your tip! You’re keeping Desk Warrior alive.")

# ----------------- Main -----------------
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN not set")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("flashcard", flashcard))
    app.add_handler(CommandHandler("summary", summary))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("tip", tip))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    async def set_commands(application):
        commands = [
            BotCommand("flashcard", "🏋️ Workout Card"),
            BotCommand("summary", "📊 Today’s Totals"),
            BotCommand("leaderboard", "🏆 Leaderboard"),
            BotCommand("tip", "☕ Tip the Creator (100⭐)"),
        ]
        await application.bot.set_my_commands(commands)

    app.post_init = set_commands
    app.run_polling()

if __name__ == "__main__":
    main()
