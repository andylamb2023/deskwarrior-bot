from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor

API_TOKEN = "YOUR_TELEGRAM_BOT_API_KEY"
ADMIN_HANDLE = "@YourTelegramHandle"   # who gets pinged
WALLET_ADDRESS = "YOUR_USDT_WALLET_ADDRESS"

bot = Bot(token=API_TOKEN, parse_mode="Markdown")
dp = Dispatcher(bot)

# Mock product list
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
states = {}  # track what we’re asking for (wallet or shipping)
orders = {}  # hold temporary order info per user


@dp.message_handler(commands=["start"])
async def start(msg: types.Message):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("View Products", callback_data="view_products"))
    await msg.answer("Welcome! Please choose:", reply_markup=kb)


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


@dp.callback_query_handler(lambda c: c.data.startswith("add_"))
async def add_product(call: types.CallbackQuery):
    uid = call.from_user.id
    pid = int(call.data.split("_")[1])
    carts.setdefault(uid, []).append(pid)

    subtotal = sum(products[i]["price"] for i in carts[uid])
    await call.answer(f"Added {products[pid]['name']}! Subtotal: ${subtotal}")


@dp.callback_query_handler(lambda c: c.data == "checkout")
async def checkout(call: types.CallbackQuery):
    uid
