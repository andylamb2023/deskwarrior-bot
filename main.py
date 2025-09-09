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
        "interval": FREE_INTERVAL_MIN,
        "today": {},
        "points_today": 0,
        "pending": None,
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
    {"key": "pushups", "label": "Push-ups", "reps": [8, 15], "sec_per_rep": 1.2, "points_per_rep": 1},
    {"key": "squats", "label": "Bodyweight squats", "reps": [12, 20], "sec_per_rep": 1.1, "points_per_rep": 1},
    {"key": "plank", "label": "Plank (seconds)", "reps": [30, 60], "sec_per_rep": 1.0, "points_per_sec": 0.1},
    {"key": "stretch", "label": "Neck/Shoulder stretch (seconds)", "reps": [30, 45], "sec_per_rep": 1.0, "points_per_sec": 0.05},
    {"key": "walk", "label": "Brisk walk (minutes)", "reps": [5, 8], "sec_per_min": 60, "points_per_min": 5},
]

WELLNESS_CARDS = [
    {"key": "hydration", "text": "Hydration: Drink a glass of water."},
    {"key": "sitting", "text": "Sitting too long increases risk of back pain and poor circulation. Stand and stretch."},
    {"key": "posture", "text": "Posture check: ears over shoulders, shoulders down, breathe deep x5."},
    {"key": "eyes", "text": "20-20-20 rule: Every 20 minutes, look 20 feet away for 20 seconds."},
    {"key": "breaks", "text": "Micro-break: 60 seconds of movement resets focus."},
]
