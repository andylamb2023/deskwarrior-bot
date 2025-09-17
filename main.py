import os
import sqlite3
from contextlib import closing
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor

# =========================
# Config from ENV
# =========================
API_TOKEN = os.getenv("API_TOKEN")
ADMIN_GROUP_ID = int(os.getenv("ADMIN_GROUP_ID", "0"))   # e.g. -1001234567890
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS", "YOUR_USDT_WALLET")

if not API_TOKEN:
    raise ValueError("API_TOKEN not set")
if not ADMIN_GROUP_ID:
    raise ValueError("ADMIN_GROUP_ID not set (Telegram group chat id)")

bot = Bot(token=API_TOKEN, parse_mode="Markdown")
dp = Dispatcher(bot)

DB_PATH = "orders.db"

# =========================
# DB helpers (SQLite)
# =========================
def init_db():
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        c = conn.cursor()
        c.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            region TEXT NOT NULL,
            products TEXT NOT NULL,   -- comma-separated product ids
            total REAL NOT NULL,
            user_wallet TEXT,
            shipping TEXT,
            status TEXT NOT NULL DEFAULT 'Pending',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            replied_at TEXT
        )""")

def insert_order(user_id, region, products_csv, total, user_wallet, shipping):
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO orders (user_id, region, products, total, user_wallet, shipping)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, region, products_csv, total, user_wallet, shipping))
        return c.lastrowid

def update_order_status(order_id, new_status):
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        c = conn.cursor()
        c.execute("UPDATE orders SET status=? WHERE id=?", (new_status, order_id))
        return c.rowcount

def get_user_orders(user_id, limit=5):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        c = conn.cursor()
        c.execute("""
            SELECT id, status, total, region, created_at
            FROM orders
            WHERE user_id=?
            ORDER BY id DESC
            LIMIT ?
        """, (user_id, limit))
        return c.fetchall()

def insert_message(user_id, text):
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO messages (user_id, text)
            VALUES (?, ?)
        """, (user_id, text))
        return c.lastrowid

def get_message(msg_id):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        c = conn.cursor()
        c.execute("SELECT id, user_id, text, status, created_at FROM messages WHERE id=?", (msg_id,))
        return c.fetchone()

def mark_message_replied(msg_id):
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        c = conn.cursor()
        c.execute("""
            UPDATE messages
               SET status='closed', replied_at=datetime('now')
             WHERE id=?
        """, (msg_id,))
        return c.rowcount

# =========================
# Catalogue (per region)
# =========================
catalogues = {
    "US": {
        1: {"name": "US Product 1", "price": 10},
        2: {"name": "US Product 2", "price": 20},
        3: {"name": "US Product 3", "price": 30},
    },
    "UK": {
        1: {"name": "UK Product 1", "price": 12},
        2: {"name": "UK Product 2", "price": 18},
        3: {"name": "UK Product 3", "price": 28},
    },
    "EU": {
        1: {"name": "EU Product 1", "price": 15},
        2: {"name": "EU Product 2", "price": 25},
        3: {"name": "EU Product 3", "price": 35},
    }
}

ORDER_STATUSES = {"pending", "paid", "shipped", "delivered", "canceled"}

# =========================
# In-memory session state
# =========================
user_region = {}   # uid -> "US"/"UK"/"EU"
carts = {}         # uid -> [product_ids]
step = {}          # uid -> "awaiting_wallet" | "awaiting_shipping" | "contact_msg"
temp = {}          # uid -> dict for transient data (subtotal, etc.)

# =========================
# UI helpers
# =========================
def main_menu_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("🛒 Place New Order", callback_data="menu_new"),
        types.InlineKeyboardButton("📦 Check Order Status", callback_data="menu_status"),
        types.InlineKeyboardButton("📩 Contact Team", callback_data="menu_contact"),
    )
    return kb

def region_kb():
    kb = types.InlineKeyboardMarkup(row_width=3)
    kb.add(
        types.InlineKeyboardButton("🇺🇸 U.S", callback_data="region_US"),
        types.InlineKeyboardButton("🇬🇧 U.K", callback_data="region_UK"),
        types.InlineKeyboardButton("🇪🇺 EU", callback_data="region_EU"),
    )
    return kb

def products_kb(region):
    kb = types.InlineKeyboardMarkup(row_width=2)
    for pid, info in catalogues[region].items():
        kb.insert(
            types.InlineKeyboardButton(
                f"{pid}. {info['name']} (${info['price']})",
                callback_data=f"add_{pid}"
            )
        )
    kb.add(types.InlineKeyboardButton("✅ Checkout", callback_data="checkout"))
    kb.add(types.InlineKeyboardButton("⬅️ Back", callback_data="menu_new"))
    return kb

# =========================
# Customer-facing handlers
# =========================
@dp.message_handler(commands=["start"])
async def cmd_start(msg: types.Message):
    await msg.answer(
        "Welcome. What would you like to do?",
        reply_markup=main_menu_kb()
    )

@dp.callback_query_handler(lambda c: c.data == "menu_new")
async def menu_new(call: types.CallbackQuery):
    uid = call.from_user.id
    carts[uid] = []
    temp.pop(uid, None)
    await call.message.edit_text("Choose your region:", reply_markup=region_kb())

@dp.callback_query_handler(lambda c: c.data == "menu_status")
async def menu_status(call: types.CallbackQuery):
    uid = call.from_user.id
    rows = get_user_orders(uid, limit=5)
    if not rows:
        await call.message.edit_text("No orders found.", reply_markup=main_menu_kb())
        return
    lines = []
    for oid, status, total, region, created in rows:
        lines.append(f"• **#{oid}** | {status.title()} | ${total} | {region} | {created}")
    await call.message.edit_text(
        "Recent orders:\n" + "\n".join(lines),
        reply_markup=main_menu_kb()
    )

@dp.callback_query_handler(lambda c: c.data == "menu_contact")
async def menu_contact(call: types.CallbackQuery):
    uid = call.from_user.id
    step[uid] = "contact_msg"
    await call.message.edit_text(
        "Type the message you want to send to the team.\n\n"
        "_They will reply via the bot; you cannot DM them directly._"
    )

@dp.callback_query_handler(lambda c: c.data.startswith("region_"))
async def choose_region(call: types.CallbackQuery):
    uid = call.from_user.id
    region = call.data.split("_")[1]
    user_region[uid] = region
    await call.message.edit_text(
        f"Selected region: *{region}*\nPick your products:",
        reply_markup=products_kb(region)
    )

@dp.callback_query_handler(lambda c: c.data.startswith("add_"))
async def add_item(call: types.CallbackQuery):
    uid = call.from_user.id
    region = user_region.get(uid)
    if not region:
        await call.answer("Choose region first", show_alert=True)
        return
    pid = int(call.data.split("_")[1])
    carts.setdefault(uid, []).append(pid)
    subtotal = sum(catalogues[region][i]["price"] for i in carts[uid])
    await call.answer(f"Added. Subtotal: ${subtotal}")

@dp.callback_query_handler(lambda c: c.data == "checkout")
async def checkout(call: types.CallbackQuery):
    uid = call.from_user.id
    region = user_region.get(uid)
    cart = carts.get(uid, [])
    if not region or not cart:
        await call.answer("No items selected.", show_alert=True)
        return

    subtotal = sum(catalogues[region][i]["price"] for i in cart)
    items = ", ".join(str(i) for i in cart)
    temp[uid] = {"region": region, "items": items, "subtotal": subtotal}
    step[uid] = "awaiting_wallet"

    await call.message.edit_text(
        f"Your order ({region}): {items}\n"
        f"Total: *${subtotal} USDT*\n\n"
        f"Send payment to:\n`{WALLET_ADDRESS}`\n\n"
        "Reply with the *crypto address you will send from*."
    )

@dp.message_handler(lambda m: True, content_types=types.ContentTypes.TEXT)
async def collect_steps(msg: types.Message):
    uid = msg.from_user.id
    # Contact-to-team step
    if step.get(uid) == "contact_msg":
        text = msg.text.strip()
        if len(text) < 2:
            await msg.answer("Please write a bit more.")
            return
        msg_id = insert_message(uid, text)
        await bot.send_message(
            ADMIN_GROUP_ID,
            f"📩 *New Message* [MSG-{msg_id}]\n"
            f"From user: `{uid}`\n\n"
            f"\"{text}\""
        )
        step.pop(uid, None)
        await msg.answer("✅ Message sent. The team will reply here via the bot.", reply_markup=main_menu_kb())
        return

    # Order steps
    if step.get(uid) == "awaiting_wallet":
        # store sender wallet, ask shipping
        wallet = msg.text.strip()
        if " " in wallet and not wallet.startswith("@"):
            # very loose guard, but keep it permissive
            wallet = wallet.split()[0]
        temp.setdefault(uid, {})["user_wallet"] = wallet
        step[uid] = "awaiting_shipping"
        await msg.answer("✅ Wallet noted.\nNow send your *shipping address* (one message).")
        return

    if step.get(uid) == "awaiting_shipping":
        shipping = msg.text.strip()
        data = temp.get(uid, {})
        region = data.get("region")
        items = data.get("items")
        subtotal = data.get("subtotal")
        user_wallet = data.get("user_wallet")

        if not (region and items and subtotal is not None and user_wallet):
            await msg.answer("Session error. Please /start again.")
            step.pop(uid, None)
            temp.pop(uid, None)
            return

        # Save order
        order_id = insert_order(
            user_id=uid,
            region=region,
            products_csv=items,
            total=subtotal,
            user_wallet=user_wallet,
            shipping=shipping
        )

        # Notify user
        await msg.answer(
            f"✅ Order *#{order_id}* received.\nStatus: *Pending*\n\n"
            "We’ll notify you when status changes.",
            reply_markup=main_menu_kb()
        )

        # Notify admin group
        await bot.send_message(
            ADMIN_GROUP_ID,
            f"🚨 *New Order* [#{order_id}]\n"
            f"User: `{uid}`\n"
            f"Region: {region}\n"
            f"Items: {items}\n"
            f"Total: ${subtotal} USDT\n"
            f"Sender wallet: `{user_wallet}`\n"
            f"Shipping:\n{shipping}\n\n"
            f"_Set status with:_ `/setstatus {order_id} <pending|paid|shipped|delivered|canceled>`",
        )

        # cleanup session
        carts[uid] = []
        step.pop(uid, None)
        temp.pop(uid, None)
        return

# =========================
# Admin-group commands
# =========================
def is_admin_group(message: types.Message) -> bool:
    return message.chat.type in ("group", "supergroup") and message.chat.id == ADMIN_GROUP_ID

@dp.message_handler(commands=["setstatus"])
async def setstatus_cmd(msg: types.Message):
    if not is_admin_group(msg):
        return
    parts = msg.get_args().split()
    if len(parts) != 2:
        await msg.reply("Usage: `/setstatus <order_id> <pending|paid|shipped|delivered|canceled>`", parse_mode="Markdown")
        return
    try:
        order_id = int(parts[0])
    except ValueError:
        await msg.reply("Order id must be a number.")
        return
    new_status = parts[1].lower()
    if new_status not in ORDER_STATUSES:
        await msg.reply(f"Invalid status. Choose one of: {', '.join(sorted(ORDER_STATUSES))}")
        return

    changed = update_order_status(order_id, new_status)
    if not changed:
        await msg.reply(f"Order #{order_id} not found.")
        return

    await msg.reply(f"✅ Order #{order_id} updated → *{new_status.title()}*", parse_mode="Markdown")

    # Try notifying the user about the update
    # fetch user_id
    with closing(sqlite3.connect(DB_PATH)) as conn:
        c = conn.cursor()
        c.execute("SELECT user_id FROM orders WHERE id=?", (order_id,))
        row = c.fetchone()
    if row:
        user_id = row[0]
        try:
            await bot.send_message(user_id, f"📦 Update for order *#{order_id}*: *{new_status.title()}*", parse_mode="Markdown")
        except Exception:
            # user might have blocked the bot or never started it
            await msg.reply("FYI: Could not notify user (not reachable).")

@dp.message_handler(commands=["reply"])
async def reply_cmd(msg: types.Message):
    if not is_admin_group(msg):
        return
    # Syntax: /reply <msg_id> <text...>
    args = msg.get_args()
    if not args:
        await msg.reply("Usage: `/reply <msg_id> <your message>`", parse_mode="Markdown")
        return
    parts = args.split(maxsplit=1)
    if len(parts) < 2:
        await msg.reply("Include both message id and text.")
        return
    try:
        msg_id = int(parts[0])
    except ValueError:
        await msg.reply("Message id must be a number.")
        return
    reply_text = parts[1]

    rec = get_message(msg_id)
    if not rec:
        await msg.reply(f"Message MSG-{msg_id} not found.")
        return
    _, user_id, orig_text, status, created_at = rec

    # Send reply to user
    try:
        await bot.send_message(user_id, f"📩 *Reply from Team:*\n{reply_text}", parse_mode="Markdown")
        mark_message_replied(msg_id)
        await msg.reply(f"✅ Sent reply to user `{user_id}` for MSG-{msg_id}", parse_mode="Markdown")
    except Exception as e:
        await msg.reply(f"Could not send reply to user `{user_id}`. Error: {e}")

@dp.message_handler(commands=["helpadmin"])
async def helpadmin_cmd(msg: types.Message):
    if not is_admin_group(msg):
        return
    await msg.reply(
        "Admin commands:\n"
        "• `/setstatus <order_id> <pending|paid|shipped|delivered|canceled>`\n"
        "• `/reply <msg_id> <text>`\n",
        parse_mode="Markdown"
    )

# =========================
# Bootstrap
# =========================
if __name__ == "__main__":
    init_db()
    executor.start_polling(dp, skip_updates=True)
