import os
import json
import logging
import random
import string
import asyncio
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

def make_arcade_tag(name: str) -> str:
    """Make a 3-char arcade style tag from a name or random letters."""
    if name:
        tag = ''.join(ch for ch in name.upper() if ch.isalpha())
        if len(tag) >= 3:
            return tag[:3]
        return tag.ljust(3, 'X')
    return ''.join(random.choice(string.ascii_uppercase) for _ in range(3))

def get_user(data: Dict[str, Any], user_id: int, tg_user=None) -> Dict[str, Any]:
    u = data["users"].setdefault(str(user_id), {
        "today": {},
        "points_today": 0,
        "_last_date": today_key(),
        "tag": None,
    })
    if u.get("_last_date") != today_key():
        u["today"] = {}
        u["points_today"] = 0
        u["_last_date"] = today_key()
    if not u.get("tag") and tg_user:
        display = tg_user.first_name or tg_user.username or ""
        u["tag"] = make_arcade_tag(display)
    return u

# Forever-accumulating leaderboard
def lb_add(data: Dict[str, Any], chat_id: int, user_id: int, pts: int):
    chat = data["leaderboards"].setdefault(str(chat_id), {})
    chat[str(user_id)] = chat.get(str(user_id), 0) + pts

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

# ----------------- Flashcard helper -----------------
async def send_flashcard(target, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    data = load_data()
    user = get_user(data, user_id, target.from_user)
    card = pick_card()

    if card["type"] == "info":
        await target.reply_text(card["text"])
        return

    wait_time = card["amount"] if card["key"] in ["plank", "stretch", "walk"] else max(15, card["amount"] // 2)
    now = datetime.now(timezone.utc).timestamp()
    ready_at = now + wait_time

    user["pending"] = {
        "key": card["key"],
        "amount": card["amount"],
        "points": card["points"],
        "ready_at": ready_at,
        "consumed": False,
    }
    save_data(data)

    wait_kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏳ Waiting...", callback_data="tooearly")]])
    sent = await target.reply_text(f"{card['label']} - {card['amount']}\n⏳ {wait_time}s remaining...", reply_markup=wait_kb)

    for r in range(wait_time, 0, -1):
        await asyncio.sleep(1)
        remaining = r - 1
        try:
            if remaining > 0:
                await context.bot.edit_message_text(
                    chat_id=sent.chat_id,
                    message_id=sent.message_id,
                    text=f"{card['label']} - {card['amount']}\n⏳ {remaining}s remaining...",
                    reply_markup=wait_kb,
                )
        except Exception:
            pass

    try:
        await context.bot.edit_message_text(
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

# ----------------- Summary & Leaderboard -----------------
async def send_summary(target, user_id: int):
    data = load_data()
    user = get_user(data, user_id, target.from_user)
    totals = user.get("today", {})
    lines = ["📊 Today's totals:"]
    for k, v in totals.items():
        lines.append(f"{k}: {v}")
    lines.append(f"Points: {user.get('points_today', 0)}")
    await target.reply_text("\n".join(lines))

async def send_leaderboard(target, chat_id: int):
    data = load_data()
    lb = data["leaderboards"].get(str(chat_id), {})
    if not lb:
        await target.reply_text("No scores yet.")
        return
    items = sorted(lb.items(), key=lambda kv: kv[1], reverse=True)[:10]
    lines = ["🏆 All-time leaderboard:"]
    for rank, (uid, pts) in enumerate(items, start=1):
        tag = data["users"].get(uid, {}).get("tag", "???")
        lines.append(f"{rank}. {tag} — {pts} pts")
    await target.reply_text("\n".join(lines))

# ----------------- Commands -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    get_user(data, update.effective_user.id, update.effective_user)
    save_data(data)

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

async def tag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    user = get_user(data, update.effective_user.id, update.effective_user)

    if not context.args:
        await update.message.reply_text("Usage: /tag ABC (choose 3 letters for your initials)")
        return

    chosen = ''.join(ch for ch in context.args[0].upper() if ch.isalpha())[:3]
    if len(chosen) < 3:
        chosen = chosen.ljust(3, 'X')

    user["tag"] = chosen
    save_data(data)
    await update.message.reply_text(f"Your arcade tag has been set to: {chosen}")

async def flashcard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_flashcard(update.message, context, update.effective_user.id)

async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_summary(update.message, update.effective_user.id)

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_leaderboard(update.message, update.effective_chat.id)

# ----------------- Button Handler -----------------
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = load_data()
    user = get_user(data, query.from_user.id, query.from_user)

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

# ----------------- Payments (Tips) -----------------
async def tip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    title = "Support Desk Warrior"
    description = "If this bot helps you, consider leaving a 100⭐ tip!"
    payload = "deskwarrior-tip"
    currency = "XTR"
    prices = [LabeledPrice("Tip", 100)]

    await context.bot.send_invoice(
        chat_id,
        title,
        description,
        payload,
        provider_token="",  # Stars
        currency=currency,
        prices=prices,
        start_parameter="tip",
    )

async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await query.answer(ok=True)

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🙏 Thank you for your tip! You’re keeping Desk Warrior alive.")

# ----------------- Error Handler -----------------
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception in handler", exc_info=context.error)

# ----------------- Main -----------------
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN not set")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("tag", tag))
    app.add_handler(CommandHandler("flashcard", flashcard))
    app.add_handler(CommandHandler("summary", summary))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("tip", tip))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_error_handler(on_error)

    async def set_commands(application):
        commands = [
            BotCommand("flashcard", "🏋️ Workout Card"),
            BotCommand("summary", "📊 Today’s Totals"),
            BotCommand("leaderboard", "🏆 Leaderboard"),
            BotCommand("tag", "🎮 Set your arcade initials"),
            BotCommand("tip", "☕ Tip the Creator (100⭐)"),
        ]
        await application.bot.set_my_commands(commands)

    app.post_init = set_commands
    app.run_polling()

if __name__ == "__main__":
    main()
