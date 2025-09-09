# README — Desk Warrior Bot (50 Stars) — Wellness + Anti‑Cheat + Leaderboard

This is a **Render-ready** MVP of a Telegram bot called **Desk Warrior** - *your office workout mate*.

- Sends periodic **exercise flashcards** (push-ups, squats, planks, stretches, walk prompts)
- Mixes in **wellness tips/warnings** (~25% of cards) - hydration, posture, eye‑strain, sedentary risk
- Lets users tap **Done ✅** to log completions (exercise cards only)
- **Anti‑cheat**: validates realistic completion time; early taps get reduced or rejected
- Awards **points** and shows a **/leaderboard** (per chat) for friendly competition
- Shows **/summary** totals for the day
- Offers **premium** via **Telegram Stars (50 XTR)** → custom intervals (30/45/60), more content, streak features (stubbed)
- Uses **python-telegram-bot v20** and **JobQueue** for per-user reminders

> **Env var:** `8234741363:AAE3jX94uxLhylyBUf0kz1bv0ZyP2zFra8Y` (from @BotFather)

---

## Files

### `main.py`
```python
import os
import json
import logging
from datetime import datetime, timedelta, date, timezone
from typing import Dict, Any

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ----------------- Config -----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")

FREE_INTERVAL_MIN = 60
PREMIUM_INTERVALS = [30, 45, 60]

PREMIUM_PRICE_STARS = 50
CURRENCY = "XTR"

DATA_FILE = "bot_data.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ----------------- Helpers -----------------
# (helpers unchanged)

# ----------------- Bot Handlers -----------------

DISCLAIMER = (
    "<i>Disclaimer: This bot provides general wellness prompts only."
    " Not medical advice. If any movement causes pain, stop. Consult a professional if you have injuries or conditions.</i>"
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    user = get_user(data, update.effective_user.id)
    save_data(data)

    msg = (
        "👋 <b>Desk Warrior</b> - your office workout mate.\n\n"
        "I’ll ping you with small exercise cards to break up long sitting. I’ll also send occasional wellness tips (hydration, posture, eye‑strain).\n\n"
        f"Free tier: flashcard every <b>{FREE_INTERVAL_MIN} min</b>.\n"
        "Upgrade with <b>50 Stars</b> to choose intervals (30/45/60), bigger library, and streaks.\n\n"
        "Commands:\n"
        "• /flashcard - get one now\n"
        "• /summary - today’s totals and points\n"
        "• /leaderboard - top scores in this chat (today)\n"
        "• /buy - unlock premium (50 Stars)\n"
        "• /interval - set reminder interval (premium)\n\n"
        f"{DISCLAIMER}"
    )

    await update.message.reply_html(msg)
    await ensure_user_job(context, update.effective_chat.id, update.effective_user.id)

# (rest of code unchanged)
```

### `requirements.txt`
```text
python-telegram-bot==20.6
```

---

## Render Deployment (Free)

Same steps - just note your bot is now branded **Desk Warrior**.

---

## Quick Test Checklist

- DM your bot → `/start` → should say: **Desk Warrior — your office workout mate**
- `/flashcard` → exercise or wellness tip
- `/summary` → totals + points
- `/leaderboard` → daily top scores
- `/buy` → 50‑Star invoice
- `/interval 30` → premium interval

---

## Roadmap
- Streaks + badges
- Weekly leaderboard
- Export CSV
- Persistent storage
