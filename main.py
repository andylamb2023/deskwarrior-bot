# Telegram shop bot (EU XLSX + stock + silent markup & shipping)
# - Region selection (US/UK/EU) + catalogue (EU loads from local XLSX/CSV or CSV URL)
# - Shows stock (✅ In Stock / ❌ OOS); blocks OOS adds
# - Cart -> checkout -> collects sender wallet + shipping
# - Total the user sees: FINAL = round(sum(items) * 1.30 + 30, 2)
# - Save orders in SQLite (status=Pending)
# - Post order card to ADMIN_GROUP_ID with inline status buttons
# - Admin: tap buttons or /setstatus; reply tickets with /reply
# - /reload_eu to refresh EU list from file/URL
# - Pagination for large catalogues

import os
import re
import csv
import sqlite3
from io import StringIO
from contextlib import closing
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor

# Optional deps (used only if present)
try:
    import requests
except Exception:
    requests = None

try:
    import openpyxl  # for .xlsx reading
except Exception:
    openpyxl = None

# ----------- Config / ENV -----------
API_TOKEN = os.getenv("API_TOKEN")
ADMIN_GROUP_ID = int(os.getenv("ADMIN_GROUP_ID", "0"))   # e.g. -1001234567890
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS", "YOUR_USDT_WALLET")
EU_PRICELIST_CSV_URL = os.getenv("EU_PRICELIST_CSV_URL")  # optional fallback
EU_PRICELIST_PATH = os.getenv("EU_PRICELIST_PATH")        # e.g. "eu_pricelist.xlsx" in repo

if not API_TOKEN:
    raise ValueError("API_TOKEN not set")
if not ADMIN_GROUP_ID:
    raise ValueError("ADMIN_GROUP_ID not set (Telegram group chat id)")

bot = Bot(token=API_TOKEN, parse_mode="Markdown")
dp = Dispatcher(bot)

DB_PATH = "orders.db"
PAGE_SIZE = 10

# Silent pricing knobs
MARKUP_RATE = 0.30  # 30%
SHIPPING_FEE = 30.0 # flat

ORDER_STATUSES = {"pending", "paid", "shipped", "delivered", "canceled"}

# ---------------- DB ----------------
def init_db():
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        c = conn.cursor()
        c.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            region TEXT NOT NULL,
            products TEXT NOT NULL,
            total REAL NOT NULL,                -- final charged total
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

def insert_order(user_id, region, products_csv, final_total, user_wallet, shipping):
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO orders (user_id, region, products, total, user_wallet, shipping)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, region, products_csv, final_total, user_wallet, shipping))
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

def get_order(order_id):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        c = conn.cursor()
        c.execute("""
            SELECT id, user_id, region, products, total, user_wallet, shipping, status, created_at
            FROM orders WHERE id=?
        """, (order_id,))
        return c.fetchone()

def insert_message(user_id, text):
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        c = conn.cursor()
        c.execute("INSERT INTO messages (user_id, text) VALUES (?, ?)", (user_id, text))
        return c.lastrowid

def get_message(msg_id):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        c = conn.cursor()
        c.execute("SELECT id, user_id, text, status, created_at FROM messages WHERE id=?", (msg_id,))
        return c.fetchone()

def mark_message_replied(msg_id):
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        c = conn.cursor()
        c.execute("UPDATE messages SET status='closed', replied_at=datetime('now') WHERE id=?", (msg_id,))
        return c.rowcount

# ------------- Catalogue ------------
# US/UK simple examples with stock defaulted In Stock
catalogues = {
    "US": {1: {"name": "US Product 1", "price": 10.0, "stock": "In Stock"},
           2: {"name": "US Product 2", "price": 20.0, "stock": "In Stock"},
           3: {"name": "US Product 3", "price": 30.0, "stock": "In Stock"}},
    "UK": {1: {"name": "UK Product 1", "price": 12.0, "stock": "In Stock"},
           2: {"name": "UK Product 2", "price": 18.0, "stock": "In Stock"},
           3: {"name": "UK Product 3", "price": 28.0, "stock": "In Stock"}},
    "EU": {}  # loaded at boot /reload_eu
}

def parse_price(val: str) -> float:
    v = (val or "").strip().replace(",", ".")
    v = re.sub(r"[^0-9.]", "", v)
    try:
        return round(float(v), 2) if v else 0.0
    except Exception:
        return 0.0

def availability_label(s: str) -> str:
    s = (s or "").strip().lower()
    if "out" in s:
        return "Out Of Stock"
    if "in" in s or "stock" in s:
        return "In Stock"
    return s.title() if s else "In Stock"

def load_eu_from_csv_text(text: str) -> int:
    reader = csv.DictReader(StringIO(text))
    items = {}
    next_id = 1
    for row in reader:
        name = (row.get("name") or row.get("Name") or "").strip()
        if not name:
            continue
        if "id" in row and str(row["id"]).strip().isdigit():
            pid = int(str(row["id"]).strip())
        else:
            pid = next_id; next_id += 1
        price = parse_price(row.get("price") or row.get("Price") or "0")
        stock = availability_label(row.get("stock") or row.get("Stock") or "")
        items[pid] = {"name": name, "price": price, "stock": stock}
    catalogues["EU"] = dict(sorted(items.items(), key=lambda kv: kv[0]))
    return len(items)

def load_eu_from_local(path: str) -> int:
    if not os.path.exists(path):
        return 0
    ext = os.path.splitext(path)[1].lower()

    if ext == ".csv":
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return load_eu_from_csv_text(f.read())

    elif ext in (".xlsx", ".xls"):
        if openpyxl is None:
            return 0

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return 0

        headers = [str(h or "").strip().lower() for h in rows[0]]

        # --- choose name column ---
        try:
            name_idx = headers.index("name")
        except ValueError:
            name_idx = 0  # your sheet puts name in column A, row2+

        # --- choose price column (header or heuristic) ---
        price_idx = None
        for key in ("price", "cost", "amount"):
            if key in headers:
                price_idx = headers.index(key); break
        if price_idx is None:
            max_hits = -1; best_col = None
            col_count = max(len(r) for r in rows)
            sample = rows[1: min(len(rows), 30)]
            for j in range(col_count):
                hits = 0
                for r in sample:
                    s = str((r[j] if j < len(r) else "") or "")
                    if any(ch.isdigit() for ch in s):
                        try:
                            _ = parse_price(s)
                            if s.strip(): hits += 1
                        except Exception:
                            pass
                if hits > max_hits:
                    max_hits = hits; best_col = j
            price_idx = best_col if best_col is not None else 1  # column B in your sheet

        # --- choose stock column (header or heuristic looking for "stock") ---
        stock_idx = None
        if "stock" in headers:
            stock_idx = headers.index("stock")
        else:
            col_count = max(len(r) for r in rows)
            sample = rows[1: min(len(rows), 50)]
            best_hits = -1; best_col = None
            for j in range(col_count):
                hits = 0
                for r in sample:
                    val = str((r[j] if j < len(r) else "") or "").lower()
                    if "stock" in val:
                        hits += 1
                if hits > best_hits:
                    best_hits = hits; best_col = j
            stock_idx = best_col  # may be None if truly absent

        id_idx = headers.index("id") if "id" in headers else None

        # --- build items ---
        items, next_id = {}, 1
        for r in rows[1:]:
            if not r:
                continue
            # name
            name = ""
            if name_idx is not None and name_idx < len(r) and r[name_idx] is not None:
                name = str(r[name_idx]).strip()
            if not name:
                continue

            # id
            pid = None
            if id_idx is not None and id_idx < len(r) and r[id_idx] is not None:
                try:
                    pid = int(str(r[id_idx]).split(".")[0])
                except Exception:
                    pid = None
            if pid is None:
                pid = next_id; next_id += 1

            # price
            price_val = ""
            if price_idx is not None and price_idx < len(r) and r[price_idx] is not None:
                price_val = str(r[price_idx])
            price = parse_price(price_val)

            # stock
            stock_val = ""
            if stock_idx is not None and stock_idx < len(r) and r[stock_idx] is not None:
                stock_val = str(r[stock_idx])
            stock = availability_label(stock_val)

            items[pid] = {"name": name, "price": price, "stock": stock}

        catalogues["EU"] = dict(sorted(items.items(), key=lambda kv: kv[0]))
        return len(items)

    else:
        return 0

def load_eu_catalogue() -> int:
    # Try local file first (XLSX/CSV), then CSV URL
    if EU_PRICELIST_PATH:
        count = load_eu_from_local(EU_PRICELIST_PATH)
        if count:
            return count
    if EU_PRICELIST_CSV_URL and requests:
        try:
            resp = requests.get(EU_PRICELIST_CSV_URL, timeout=15)
            resp.raise_for_status()
            return load_eu_from_csv_text(resp.text)
        except Exception:
            return 0
    return 0

# ---------- Session State -----------
user_region = {}
carts = {}
step = {}     # awaiting_wallet | awaiting_shipping | contact_msg
temp = {}     # per-user transient dict

# -------------- UI ------------------
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
        types.InlineKeyboardButton("🇺🇸 U.S", callback_data="region_US_1"),
        types.InlineKeyboardButton("🇬🇧 U.K", callback_data="region_UK_1"),
        types.InlineKeyboardButton("🇪🇺 EU", callback_data="region_EU_1"),
    )
    return kb

def products_kb(region, page: int):
    items = catalogues.get(region, {})
    kb = types.InlineKeyboardMarkup(row_width=1)
    if not items:
        kb.add(types.InlineKeyboardButton("⬅️ Back", callback_data="menu_new"))
        return kb

    pids = sorted(items.keys())
    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    page_slice = pids[start:end]

    for pid in page_slice:
        info = items[pid]
        in_stock = "out" not in info.get("stock", "").lower()
        stock_tag = "✅ In Stock" if in_stock else "❌ OOS"
        label = f"{pid}. {info['name']} ({stock_tag}) — ${info['price']}"
        kb.add(types.InlineKeyboardButton(label, callback_data=f"add_{pid}"))

    nav = []
    if start > 0:
        nav.append(types.InlineKeyboardButton("« Prev", callback_data=f"page_{region}_{page-1}"))
    if end < len(pids):
        nav.append(types.InlineKeyboardButton("Next »", callback_data=f"page_{region}_{page+1}"))
    if nav:
        kb.row(*nav)

    kb.add(types.InlineKeyboardButton("✅ Checkout", callback_data="checkout"))
    kb.add(types.InlineKeyboardButton("⬅️ Back", callback_data="menu_new"))
    return kb

def admin_status_kb(order_id):
    row1 = [
        types.InlineKeyboardButton("✅ Mark Paid", callback_data=f"st:{order_id}:paid"),
        types.InlineKeyboardButton("📦 Shipped", callback_data=f"st:{order_id}:shipped"),
    ]
    row2 = [
        types.InlineKeyboardButton("🚚 Delivered", callback_data=f"st:{order_id}:delivered"),
        types.InlineKeyboardButton("⏳ Pending", callback_data=f"st:{order_id}:pending"),
    ]
    row3 = [types.InlineKeyboardButton("🛑 Cancel", callback_data=f"st:{order_id}:canceled")]
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.row(*row1); kb.row(*row2); kb.row(*row3)
    return kb

def order_card_text(row):
    oid, user_id, region, products, total, user_wallet, shipping, status, created_at = row
    return (
        f"🧾 *Order* [#{oid}]\n"
        f"User: `{user_id}`\n"
        f"Region: {region}\n"
        f"Items: {products}\n"
        f"Total: ${total} USDT\n"
        f"Sender wallet: `{user_wallet}`\n"
        f"Shipping:\n{shipping}\n\n"
        f"Current status: *{status}*"
    )

# ---------- Customer handlers -------
@dp.message_handler(commands=["start"])
async def cmd_start(msg: types.Message):
    await msg.answer("Welcome. What would you like to do?", reply_markup=main_menu_kb())

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
        await call.message.edit_text("No orders found.", reply_markup=main_menu_kb()); return
    lines = [f"• **#{oid}** | {status.title()} | ${total} | {region} | {created}"
             for (oid, status, total, region, created) in rows]
    await call.message.edit_text("Recent orders:\n" + "\n".join(lines), reply_markup=main_menu_kb())

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
    # region_<REG>_<PAGE>
    _, region, page = call.data.split("_")
    uid = call.from_user.id
    user_region[uid] = region
    await call.message.edit_text(
        f"Selected region: *{region}*\nPick your products:",
        reply_markup=products_kb(region, int(page))
    )

@dp.callback_query_handler(lambda c: c.data.startswith("page_"))
async def paginate(call: types.CallbackQuery):
    _, region, page = call.data.split("_")
    await call.message.edit_text(
        f"Selected region: *{region}*\nPick your products:",
        reply_markup=products_kb(region, int(page))
    )

@dp.callback_query_handler(lambda c: c.data.startswith("add_"))
async def add_item(call: types.CallbackQuery):
    uid = call.from_user.id
    region = user_region.get(uid)
    if not region:
        await call.answer("Choose region first", show_alert=True); return
    pid = int(call.data.split("_")[1])

    # Validate product exists & stock
    item = catalogues.get(region, {}).get(pid)
    if not item:
        await call.answer("Item not found on this page.", show_alert=True); return
    in_stock = "out" not in item.get("stock", "").lower()
    if not in_stock:
        await call.answer("That item is currently out of stock.", show_alert=True); return

    carts.setdefault(uid, []).append(pid)
    # show *base* subtotal while browsing; final total is shown at checkout
    base_subtotal = sum(catalogues[region][i]["price"] for i in carts[uid])
    await call.answer(f"Added. Subtotal: ${round(base_subtotal,2)}")

@dp.callback_query_handler(lambda c: c.data == "checkout")
async def checkout(call: types.CallbackQuery):
    uid = call.from_user.id
    region = user_region.get(uid)
    cart = carts.get(uid, [])
    if not region or not cart:
        await call.answer("No items selected.", show_alert=True); return

    base_sum = sum(catalogues[region][i]["price"] for i in cart)
    final_total = round(base_sum * (1.0 + MARKUP_RATE) + SHIPPING_FEE, 2)
    items = ", ".join(str(i) for i in cart)

    temp[uid] = {
        "region": region,
        "items": items,
        "base_sum": round(base_sum, 2),
        "final_total": final_total
    }
    step[uid] = "awaiting_wallet"

    await call.message.edit_text(
        f"Your order ({region}): {items}\n"
        f"Total: *${final_total} USDT*\n\n"
        f"Send payment to:\n`{WALLET_ADDRESS}`\n\n"
        "Reply with the *crypto address you will send from*."
    )

@dp.message_handler(lambda m: True, content_types=types.ContentTypes.TEXT)
async def collect_steps(msg: types.Message):
    uid = msg.from_user.id
    # Contact Team
    if step.get(uid) == "contact_msg":
        text = msg.text.strip()
        if len(text) < 2:
            await msg.answer("Please write a bit more."); return
        msg_id = insert_message(uid, text)
        await bot.send_message(ADMIN_GROUP_ID, f"📩 *New Message* [MSG-{msg_id}]\nFrom user: `{uid}`\n\n\"{text}\"")
        step.pop(uid, None)
        await msg.answer("✅ Message sent. The team will reply here via the bot.", reply_markup=main_menu_kb())
        return

    # Order steps
    if step.get(uid) == "awaiting_wallet":
        temp.setdefault(uid, {})["user_wallet"] = msg.text.strip()
        step[uid] = "awaiting_shipping"
        await msg.answer("✅ Wallet noted.\nNow send your *shipping address* (one message).")
        return

    if step.get(uid) == "awaiting_shipping":
        shipping = msg.text.strip()
        data = temp.get(uid, {})
        region = data.get("region"); items = data.get("items")
        base_sum = data.get("base_sum"); final_total = data.get("final_total")
        user_wallet = data.get("user_wallet")
        if not (region and items and base_sum is not None and final_total is not None and user_wallet):
            await msg.answer("Session error. Please /start again.")
            step.pop(uid, None); temp.pop(uid, None); return

        order_id = insert_order(uid, region, items, final_total, user_wallet, shipping)

        await msg.answer(
            f"✅ Order *#{order_id}* received.\nStatus: *Pending*\n\n"
            "We’ll notify you when status changes.",
            reply_markup=main_menu_kb()
        )

        row = get_order(order_id)
        await bot.send_message(ADMIN_GROUP_ID, order_card_text(row), reply_markup=admin_status_kb(order_id))

        carts[uid] = []
        step.pop(uid, None); temp.pop(uid, None)
        return

# ------------- Admin area -----------
def is_admin_group(message: types.Message) -> bool:
    return message.chat.type in ("group", "supergroup") and message.chat.id == ADMIN_GROUP_ID

@dp.callback_query_handler(lambda c: c.data.startswith("st:"))
async def cb_set_status(call: types.CallbackQuery):
    if call.message.chat.id != ADMIN_GROUP_ID:
        await call.answer("Not allowed here.", show_alert=True); return

    _, oid_str, status = call.data.split(":")
    try:
        oid = int(oid_str)
    except ValueError:
        await call.answer("Bad order id.", show_alert=True); return
    status = status.lower()
    if status not in ORDER_STATUSES:
        await call.answer("Bad status.", show_alert=True); return

    changed = update_order_status(oid, status)
    if not changed:
        await call.answer("Order not found.", show_alert=True); return

    row = get_order(oid)
    try:
        await call.message.edit_text(order_card_text(row), reply_markup=admin_status_kb(oid))
    except Exception:
        await bot.send_message(ADMIN_GROUP_ID, order_card_text(row), reply_markup=admin_status_kb(oid))

    _, user_id, *_ = row
    try:
        await bot.send_message(user_id, f"📦 Update for order *#{oid}*: *{status.title()}*")
    except Exception:
        pass

    await call.answer(f"Updated to {status.title()}")

@dp.message_handler(commands=["setstatus"])
async def setstatus_cmd(msg: types.Message):
    if not is_admin_group(msg):
        return
    args = msg.get_args().strip()
    order_id = None; new_status = None

    ORDER_ID_RE = re.compile(r"#(\d+)")
    if args:
        parts = args.split(maxsplit=1)
        if len(parts) == 2 and parts[0].isdigit():
            order_id = int(parts[0]); new_status = parts[1].lower()
        elif len(parts) == 1 and msg.reply_to_message:
            src = (msg.reply_to_message.text or "") + (msg.reply_to_message.caption or "")
            m = ORDER_ID_RE.search(src)
            if m: order_id = int(m.group(1)); new_status = parts[0].lower()

    if not order_id or not new_status:
        await msg.reply(
            "Usage:\n`/setstatus <order_id> <pending|paid|shipped|delivered|canceled>`\n"
            "or reply to the order post with: `/setstatus <status>`",
            parse_mode="Markdown"
        ); return

    if new_status not in ORDER_STATUSES:
        await msg.reply(f"Invalid status. Use: {', '.join(sorted(ORDER_STATUSES))}"); return

    changed = update_order_status(order_id, new_status)
    if not changed:
        await msg.reply(f"Order #{order_id} not found."); return

    row = get_order(order_id)
    try:
        await msg.reply(f"✅ Order #{order_id} → *{new_status.title()}*")
        if msg.reply_to_message:
            try:
                await msg.reply_to_message.edit_text(order_card_text(row), reply_markup=admin_status_kb(order_id))
            except Exception:
                pass
    except Exception:
        pass

    _, user_id, *_ = row
    try:
        await bot.send_message(user_id, f"📦 Update for order *#{order_id}*: *{new_status.title()}*")
    except Exception:
        await msg.reply("FYI: Could not notify user (not reachable).")

@dp.message_handler(commands=["reply"])
async def reply_cmd(msg: types.Message):
    if not is_admin_group(msg):
        return
    args = msg.get_args()
    if not args:
        await msg.reply("Usage: `/reply <msg_id> <your message>`", parse_mode="Markdown"); return
    parts = args.split(maxsplit=1)
    if len(parts) < 2:
        await msg.reply("Include both message id and text."); return
    try:
        msg_id = int(parts[0])
    except ValueError:
        await msg.reply("Message id must be a number."); return
    reply_text = parts[1]

    rec = get_message(msg_id)
    if not rec:
        await msg.reply(f"Message MSG-{msg_id} not found."); return
    _, user_id, _, _, _ = rec

    try:
        await bot.send_message(user_id, f"📩 *Reply from Team:*\n{reply_text}")
        mark_message_replied(msg_id)
        await msg.reply(f"✅ Sent reply to user `{user_id}` for MSG-{msg_id}")
    except Exception as e:
        await msg.reply(f"Could not send reply to user `{user_id}`. Error: {e}")

@dp.message_handler(commands=["reload_eu"])
async def reload_eu_cmd(msg: types.Message):
    if not is_admin_group(msg):
        return
    count = load_eu_catalogue()
    if count:
        await msg.reply(f"EU pricelist reloaded. Items: {count}")
    else:
        await msg.reply("Failed to load EU pricelist. Check EU_PRICELIST_PATH or EU_PRICELIST_CSV_URL.")

@dp.message_handler(commands=["helpadmin"])
async def helpadmin_cmd(msg: types.Message):
    if not is_admin_group(msg):
        return
    await msg.reply(
        "Admin controls:\n"
        "• Inline buttons on order cards (Paid/Shipped/Delivered/Pending/Cancel)\n"
        "• `/setstatus <order_id> <pending|paid|shipped|delivered|canceled>`\n"
        "• Reply to order card: `/setstatus <status>`\n"
        "• `/reply <msg_id> <text>` to answer Contact Team tickets\n"
        "• `/reload_eu` to refresh EU catalogue from file/URL\n",
        parse_mode="Markdown"
    )

@dp.message_handler(commands=["chatid"])
async def chatid(msg: types.Message):
    await msg.reply(f"Chat ID: {msg.chat.id}")

# --------------- Run ----------------
if __name__ == "__main__":
    init_db()
    load_eu_catalogue()  # preload EU list on boot
    executor.start_polling(dp, skip_updates=True)
