import os
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor

# === Load config from Railway ENV Vars ===
API_TOKEN = os.getenv("API_TOKEN")
ADMIN_HANDLE = os.getenv("ADMIN_HANDLE", "@YourHandleHere")   # default fallback
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS", "YOUR_WALLET_HERE")

if not API_TOKEN:
    raise ValueError("API_TOKEN not set in Railway environment variables!")

bot = Bot(token=API_TOKEN, parse_mode="Markdown")
dp = Dispatcher(bot)

# === Product catalogue ===
products = {
    1: {"name": "Product 1", "price": 10},
    2: {"name": "Product 2", "price": 15},
    3: {"name": "Product 3", "price": 20},
    4: {"name": "Product 4", "price": 25},
    5: {"name": "Product 5", "price": 30},
    6: {"name": "Product 6", "price": 12},
    7: {"name": "Product 7", "price": 50},
    8: {"name": "Product 8", "price": 8},
    9: {"name": "Product 9", "price": 60},
    10: {"name": "Product 10", "price": 100}
}

carts = {}   # user carts
states = {}  # track user steps (wallet/shipping)
orders = {}  # store active order data


# === Start ===
@dp.message_handler(commands=["start"])
async def start(msg: types.Message):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("View Products", callback_data="view_products"))
    await msg.answer("Welcome! Please choose:", reply_markup=kb)


# === Show Products ===
@dp.callback_query_handler(lambda c: c.data == "view_products")
async def show_products(call: types.CallbackQuery):
    kb = types.InlineKeyboardMarkup(row_width=2)
    for pid, info in products.items():
        kb.insert(
            types.InlineKeyboardButton(
                f"{pid}. {info['name']} (${info['price']})",
                callback_data=f"add_{pid}"
            )
        )
    kb.add(types.InlineKeyboardButton("Checkout", callback_data="checkout"))
    await call.message.edit_text("Select products:", reply_markup=kb)


# === Add product to cart ===
@dp.callback_query_handler(lambda c: c.data.startswith("add_"))
async def add_product(call: types.CallbackQuery):
    uid = call.from_user.id
    pid = int(call.data.split("_")[1])
    carts.setdefault(uid, []).append(pid)

    subtotal = sum(products[i]["price"] for i in carts[uid])
    await call.answer(f"Added {products[pid]['name']}! Subtotal: ${subtotal}")


# === Checkout ===
@dp.callback_query_handler(lambda c: c.data == "checkout")
async def checkout(call: types.CallbackQuery):
    uid = call.from_user.id
    cart = carts.get(uid, [])
    if not cart:
        await call.answer("Your cart is empty!", show_alert=True)
        return

    subtotal = sum(products[i]["price"] for i in cart)
    items = ", ".join(str(i) for i in cart)

    orders[uid] = {"cart": cart, "subtotal": subtotal}

    await call.message.answer(
        f"Your order: {items}\n"
        f"Total: *${subtotal} USDT*\n\n"
        f"Please send payment to:\n`{WALLET_ADDRESS}`\n\n"
        "👉 Now, reply with the *crypto address you’ll send from* so we can verify."
    )
    states[uid] = "awaiting_wallet"


# === Handle messages (wallet + shipping) ===
@dp.message_handler()
async def handle_messages(msg: types.Message):
    uid = msg.from_user.id
    if uid not in states:
        return

    if states[uid] == "awaiting_wallet":
        orders[uid]["user_wallet"] = msg.text.strip()
        await msg.answer("✅ Got your wallet address.\nNow please send me your *shipping address*.")
        states[uid] = "awaiting_shipping"

    elif states[uid] == "awaiting_shipping":
        orders[uid]["shipping"] = msg.text.strip()
        cart = orders[uid]["cart"]
        subtotal = orders[uid]["subtotal"]
        items = ", ".join(str(i) for i in cart)
        user_wallet = orders[uid].get("user_wallet", "N/A")

        await msg.answer("✅ Order received! We’ll process once payment is confirmed.")

        # Notify admin
        await bot.send_message(
            ADMIN_HANDLE,
            f"🚨 New Order from user `{uid}`\n\n"
            f"Products: {items}\nTotal: ${subtotal} USDT\n\n"
            f"User wallet: `{user_wallet}`\n\n"
            f"Shipping address:\n{orders[uid]['shipping']}"
        )

        # cleanup
        carts[uid] = []
        states.pop(uid)
        orders.pop(uid)


# === Run bot ===
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
