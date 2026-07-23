import os
import re
import sqlite3
import json
import asyncio
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters, ConversationHandler, ApplicationHandlerStop
)
import telegram.error

# ================================================================
#   ⚙️  CONFIG 
# ================================================================
BOT_TOKEN          = os.environ.get("BOT_TOKEN") or "8697617688:AAGBxf2qZlXN9xjO8Xk-9f6HMURK_JNfAew"
ADMIN_ID           = 7163496323
PAYMENT_CHANNEL_ID = "@nscoinpaymentchannel"
RECEIVE_USERNAME   = "sakib173087"
SUPPORT_USERNAME   = "BDincometvadmin_sakib"

DEFAULT_PRICE_TIERS = [(500000, 7.90), (300000, 7.75), (10000, 7.70)]
DEFAULT_MAINTENANCE_MSG_BN = "🔧 <b>বট বর্তমানে মেইনটেন্যান্সে আছে।</b>\n⏳ কিছুক্ষণ পর আবার চেষ্টা করুন।"
DEFAULT_MAINTENANCE_MSG_EN = "🔧 <b>Bot is currently under maintenance.</b>\n⏳ Please try again."
DB_PATH = "orders.db"

# ================================================================
#   RENDER / HOSTING: HTTP সার্ভার ফিক্স
# ================================================================
class _HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - Bot is running")
    def log_message(self, format, *args):
        pass

def _run_health_server():
    try:
        port = int(os.environ.get("PORT", 10000))
        server = HTTPServer(("0.0.0.0", port), _HealthCheckHandler)
        logging.info(f"Health server running on port {port}")
        server.serve_forever()
    except Exception as e:
        logging.error(f"Health server failed to start: {e}")

def start_health_server():
    threading.Thread(target=_run_health_server, daemon=True).start()

async def send_db_backup(bot):
    try:
        if os.path.exists(DB_PATH):
            with open(DB_PATH, "rb") as f:
                await bot.send_document(
                    chat_id=ADMIN_ID, document=f,
                    filename=f"orders_backup_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.db",
                    caption="💾 Database Backup"
                )
    except Exception as e:
        logging.error(f"Backup failed: {e}")

async def periodic_backup_loop(app):
    while True:
        await asyncio.sleep(6 * 60 * 60)
        await send_db_backup(app.bot)

async def post_init(app):
    asyncio.create_task(periodic_backup_loop(app))

logging.basicConfig(level=logging.INFO)
LINE = "━━━━━━━━━━━━━━━━━━━━━━"

# ================================================================
#      READY-MADE PREMIUM TEMPLATES
# ================================================================
DEFAULT_ANNOUNCEMENT_BN = "🎉 <b>বিশেষ ঘোষণা!</b>\n" + LINE + "\n✅ আজকের রেট আপডেট হয়েছে\n📌 এখনই কয়েন সেল করে সুবিধা নিন!"
DEFAULT_RULES_BN = "📜 <b>NS Coin Sell — নিয়মাবলী</b>\n" + LINE + "\n1️⃣ সঠিক নাম্বার দিন\n2️⃣ পেমেন্ট প্রুফ আপলোড করুন\n3️⃣ নিয়ম মেনে লেনদেন করুন।"
DEFAULT_RULES_EN = "📜 <b>NS Coin Sell — Rules</b>\n" + LINE + "\n1️⃣ Provide correct number\n2️⃣ Upload payment proof\n3️⃣ Follow rules."

# ================================================================
#                    🌐 LANGUAGE STRINGS
# ================================================================
LANG = {
    "bn": {
        "pick_lang"           : "🌐 <b>ভাষা বেছে নিন</b>\n\nPlease choose your language:",
        "lang_set"            : "✅ বাংলা ভাষা সেট করা হয়েছে।",
        "join_required"       : "📢 <b>চ্যানেলে যোগ দেওয়া বাধ্যতামূলক!</b>\n" + LINE + "\n🔰 বট ব্যবহার করতে হলে আমাদের অফিশিয়াল চ্যানেলে যোগ দিন।",
        "join_btn"            : "📢 চ্যানেলে যোগ দিন",
        "join_check_btn"      : "✅ I've Joined – Check",
        "join_ok"             : "🎉 চমৎকার! এখন বট ব্যবহার শুরু করুন।",
        "join_fail"           : "❌ আপনি এখনো সব চ্যানেলে যোগ দেননি।\nচ্যানেলে যোগ দিন, তারপর আবার চেক করুন।",
        "join_check_error"    : "⚠️ <b>চ্যানেল যাচাই করতে সমস্যা হচ্ছে।</b>\n\nবট হয়তো চ্যানেলে নেই বা অ্যাডমিন নয়।",
        "btn_order"           : "💎 কয়েন বিক্রি করুন",
        "btn_price"           : "📈 প্রাইস লিস্ট",
        "btn_wallet"          : "👛 আমার Wallet",
        "btn_history"         : "📊 অর্ডার হিস্ট্রি",
        "btn_support"         : "🎧 সাপোর্ট",
        "btn_lang"            : "🌐 Language",
        "btn_referral"        : "🔗 রেফারেল",
        "btn_balance"         : "💰 আমার Balance",
        "btn_rules"           : "📜 নিয়মাবলী",
        "btn_leaderboard"     : "🏆 টপ সেলার",
        "btn_cancel_order"    : "❌ অর্ডার বাতিল করুন",
        "btn_continue_order"  : "▶️ চালু রাখুন",
        "btn_confirm_cancel"  : "⚠️ হ্যাঁ, বাতিল করুন",
        "confirm_cancel_ask"  : "আপনি কি নিশ্চিতভাবে অর্ডারটি বাতিল করতে চান?",
        "cancel_done"         : "❌ অর্ডার বাতিল করা হয়েছে।",
        "continue_done"       : "👍 সেলিং প্রসেস চালু রয়েছে। অনুগ্রহ করে কত কয়েন বিক্রি করবেন তা সংখ্যায় লিখুন:",
        "timeout_msg"         : "⏱️ ৫ মিনিট কোনো রেসপন্স না পাওয়ায় অর্ডার বাতিল করা হয়েছে।",
        "no_methods"          : "⚠️ <b>বর্তমানে কোনো পেমেন্ট মেথড চালু নেই।</b>",
        "number_invalid"      : "⚠️ সঠিক মোবাইল নম্বর দিন (01 দিয়ে শুরু, ১১ ডিজিট)।",
        "welcome"             : "👋 <b>স্বাগতম NS Coin Sell বটে!</b>\n" + LINE + "\n💹 <b>বর্তমান রেট</b>\n\n{{price_list}}\n" + LINE,
        "price_header"        : "📈 <b>বর্তমান প্রাইস লিস্ট</b>\n" + LINE + "\n{{price_list}}\n" + LINE,
        "wallet_empty"        : "👛 <b>আপনার Wallet খালি</b>",
        "wallet_header"       : "👛 <b>আপনার সংরক্ষিত Wallet</b>\n" + LINE + "\n{{wallets}}",
        "history_empty"       : "📜 আপনার কোনো অর্ডার হিস্ট্রি নেই।",
        "history_header"      : "📊 <b>আপনার সাম্প্রতিক অর্ডার</b>\n" + LINE + "\n{{orders}}",
        "support_msg"         : "🎧 <b>কাস্টমার সাপোর্ট</b>\n" + LINE + "\nআমাদের টিম সাহায্য করতে সর্বদা প্রস্তুত। 💬",
        "support_btn"         : "💬 সাপোর্টে মেসেজ করুন",
        "order_qty_ask"       : "✍️ আপনি কত কয়েন বিক্রি করতে চান?\n📌 শুধু সংখ্যা লিখুন  ➤  <code>50000</code>",
        "order_qty_invalid"   : "⚠️ শুধু সংখ্যা লিখুন।",
        "order_qty_low"       : "❌ সর্বনিম্ন <b>{{min_qty}}</b> কয়েন থেকে অর্ডার নেওয়া হয়।",
        "order_qty_max"       : "❌ একবারে সর্বোচ্চ <b>{{max_qty}}</b> কয়েন সেল করা যাবে।",
        "order_summary"       : "🧾 <b>অর্ডার সামারি</b>\n" + LINE + "\n💰 পরিমাণ : <b>{{qty}}</b> কয়েন\n💵 পাবেন : <b>{{taka}}৳</b>\n" + LINE + "\n👇 পেমেন্ট মেথড বেছে নিন:",
        "method_selected"     : "{{icon}} মেথড: <b>{{method}}</b>\n\nসংরক্ষিত তথ্য ব্যবহার করবেন নাকি নতুন দেবেন?",
        "method_new_ask"      : "📱 আপনার <b>{{method}}</b> নম্বরটি দিন:",
        "ask_bep20"           : "✍️ আপনার <b>BEP-20 Wallet Address</b> দিন:",
        "btn_use_saved"       : "✅ সংরক্ষিত ({{masked}})",
        "btn_new_number"      : "✏️ নতুন নম্বর দিন",
        "wallet_gone"         : "⚠️ সংরক্ষিত নম্বর খুঁজে পাওয়া যায়নি।",
        "bep20_invalid"       : "⚠️ অবৈধ অ্যাড্রেস!",
        "proof_ask"           : "📤 <b>কয়েন পাঠানোর নির্দেশনা</b>\nএখন আপনার <b>{{qty}}</b> কয়েন এই ইউজারনেমে পাঠান:\n👉 <code>{{username}}</code>\n📸 পাঠানো হলে স্ক্রিনশট দিন।",
        "proof_not_photo"     : "⚠️ দয়া করে স্ক্রিনশট (ছবি) পাঠান।",
        "order_submitted"     : "🎊 <b>অর্ডার সফলভাবে জমা হয়েছে! #{{order_id}}</b>",
        "cancelled"           : "❌ অর্ডার বাতিল করা হয়েছে।",
        "session_expired"     : "⚠️ সেশন মেয়াদ শেষ।",
        "admin_new_order"     : "🔔 <b>নতুন অর্ডার এসেছে!</b>\n👤 <b>{{name}}</b>\n💰 <b>{{qty}}</b> কয়েন\n💵 <b>{{taka}}৳</b>\n{{icon}} {{method}}: <code>{{masked_num}}</code>\n🆔 #{{order_id}}",
        "paid_user"           : "🎉 আপনার অর্ডার <b>#{{order_id}}</b> এর পেমেন্ট পাঠানো হয়েছে।",
        "paid_channel"        : "✅ পেমেন্ট সম্পন্ন: <b>{{taka}}৳</b> ({{method}}) - <code>{{masked_num}}</code>",
        "reject_user"         : "😔 আপনার অর্ডার <b>#{{order_id}}</b> বাতিল করা হয়েছে।",
        "reject_channel"      : "❌ অর্ডার বাতিল: <b>{{taka}}৳</b> ({{method}}) - <code>{{masked_num}}</code>",
        "admin_paid_confirm"  : "✅ অর্ডার #{{order_id}} Paid.",
        "admin_reject_confirm": "❌ অর্ডার #{{order_id}} Rejected.",
        "referral_msg"        : "🔗 আপনার লিংক: <code>{{link}}</code>",
        "balance_msg"         : "💰 Balance: <b>{{balance}}৳</b>",
        "withdraw_method_ask" : "কোন মাধ্যমে টাকা নিতে চান?",
        "withdraw_number_ask" : "নম্বর দিন:",
        "withdraw_confirm"    : "✅ নিশ্চিত করুন?",
        "withdraw_submitted"  : "🎊 Withdraw Request জমা হয়েছে!",
        "withdraw_paid_user"  : "🎉 Withdrawal সম্পন্ন!",
        "withdraw_rejected_user": "😔 Withdrawal বাতিল হয়েছে।"
    },
    "en": {
        "pick_lang"           : "🌐 <b>Choose your language:</b>",
        "lang_set"            : "✅ English language set.",
        "join_required"       : "📢 <b>Channel membership required!</b>",
        "join_btn"            : "📢 Join Channel",
        "join_check_btn"      : "✅ I've Joined – Check",
        "join_ok"             : "🎉 Great! You can use the bot now.",
        "join_fail"           : "❌ You haven't joined all channels.",
        "join_check_error"    : "⚠️ <b>Channel verification issue.</b>",
        "btn_order"           : "💎 Sell Coins",
        "btn_price"           : "📈 Price List",
        "btn_wallet"          : "👛 My Wallet",
        "btn_history"         : "📊 Order History",
        "btn_support"         : "🎧 Support",
        "btn_lang"            : "🌐 Language",
        "btn_referral"        : "🔗 Referral",
        "btn_balance"         : "💰 My Balance",
        "btn_rules"           : "📜 Rules",
        "btn_leaderboard"     : "🏆 Top Sellers",
        "btn_cancel_order"    : "❌ Cancel Order",
        "btn_continue_order"  : "▶️ Continue",
        "btn_confirm_cancel"  : "⚠️ Yes, Cancel",
        "confirm_cancel_ask"  : "Are you sure?",
        "cancel_done"         : "❌ Canceled.",
        "continue_done"       : "👍 Continue typing amount:",
        "timeout_msg"         : "⏱️ Order canceled due to timeout.",
        "no_methods"          : "⚠️ No methods available.",
        "number_invalid"      : "⚠️ Invalid number.",
        "welcome"             : "👋 <b>Welcome!</b>",
        "price_header"        : "📈 <b>Price List</b>\n{{price_list}}",
        "wallet_empty"        : "👛 Wallet empty",
        "wallet_header"       : "👛 <b>Saved Wallet</b>\n{{wallets}}",
        "history_empty"       : "📜 No history.",
        "history_header"      : "📊 <b>Recent Orders</b>\n{{orders}}",
        "support_msg"         : "🎧 Support ready.",
        "support_btn"         : "💬 Message Support",
        "order_qty_ask"       : "✍️ How many coins?",
        "order_qty_invalid"   : "⚠️ Numbers only.",
        "order_qty_low"       : "❌ Minimum {{min_qty}}.",
        "order_qty_max"       : "❌ Maximum {{max_qty}}.",
        "order_summary"       : "🧾 <b>Summary</b>\nCoins: {{qty}}\nTaka: {{taka}}",
        "method_selected"     : "Use saved or new?",
        "method_new_ask"      : "Enter number:",
        "ask_bep20"           : "Enter BEP-20:",
        "btn_use_saved"       : "✅ Saved ({{masked}})",
        "btn_new_number"      : "✏️ New",
        "wallet_gone"         : "⚠️ Not found.",
        "bep20_invalid"       : "⚠️ Invalid address.",
        "proof_ask"           : "Send to: <code>{{username}}</code>\nThen upload screenshot.",
        "proof_not_photo"     : "⚠️ Upload image.",
        "order_submitted"     : "🎊 <b>Order Submitted! #{{order_id}}</b>",
        "cancelled"           : "❌ Cancelled.",
        "session_expired"     : "⚠️ Session expired.",
        "admin_new_order"     : "🔔 <b>New Order!</b>\n{{name}} - {{taka}}৳",
        "paid_user"           : "🎉 Order #{{order_id}} Paid.",
        "paid_channel"        : "✅ Payment done: {{taka}}৳",
        "reject_user"         : "😔 Order #{{order_id}} Rejected.",
        "reject_channel"      : "❌ Order rejected: {{taka}}৳",
        "admin_paid_confirm"  : "✅ Paid #{{order_id}}",
        "admin_reject_confirm": "❌ Rejected #{{order_id}}",
        "referral_msg"        : "🔗 Link: <code>{{link}}</code>",
        "balance_msg"         : "💰 Balance: {{balance}}৳",
        "withdraw_method_ask" : "Withdraw method?",
        "withdraw_number_ask" : "Number:",
        "withdraw_confirm"    : "✅ Confirm?",
        "withdraw_submitted"  : "🎊 Submitted!",
        "withdraw_paid_user"  : "🎉 Withdrawal done!",
        "withdraw_rejected_user": "😔 Withdrawal rejected."
    }
}

# ================================================================
#                        🗄️ DATABASE LAYER
# ================================================================
def get_conn():
    return sqlite3.connect(DB_PATH)

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS orders (
        order_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, coin_qty TEXT,
        rate TEXT, taka_amount TEXT, method TEXT, number TEXT, status TEXT DEFAULT 'pending',
        created_at TEXT, photo_file_id TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS wallets (
        user_id INTEGER, method TEXT, number TEXT, PRIMARY KEY (user_id, method))""")
    c.execute("""CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, lang TEXT DEFAULT 'bn', referrer_id INTEGER, balance REAL DEFAULT 0, total_earned REAL DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS withdrawals (
        withdraw_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount REAL, method TEXT, number TEXT, status TEXT DEFAULT 'pending', created_at TEXT)""")
    conn.commit()

    default_channels = json.dumps(["@BDincomeTV", "@nscoinpaymentchannel"])
    for key, val in [
        ("price_tiers", json.dumps(DEFAULT_PRICE_TIERS)),
        ("maintenance_on", "0"),
        ("payment_methods", json.dumps({"bKash": True, "Nagad": True, "BEP-20": False})),
        ("referral_bonus", "10"),
        ("min_withdraw", "50"),
        ("required_channels", default_channels)
    ]:
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, val))
    conn.commit()
    conn.close()

def get_setting(key, default=None):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else default

def set_setting(key, value):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO settings (key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.commit()
    conn.close()

def get_required_channels():
    raw = get_setting("required_channels", json.dumps([]))
    return json.loads(raw)

def set_required_channels(channels_list):
    set_setting("required_channels", json.dumps(channels_list))

def get_price_tiers(): return sorted([tuple(tier) for tier in json.loads(get_setting("price_tiers", "[]"))], key=lambda x: -x[0])
def is_maintenance(): return get_setting("maintenance_on", "0") == "1"
def set_maintenance(on: bool): set_setting("maintenance_on", "1" if on else "0")
def get_payment_methods() -> dict: return json.loads(get_setting("payment_methods", "{}"))
def get_enabled_methods() -> list: return [m for m, on in get_payment_methods().items() if on]
def set_payment_method(method: str, enabled: bool):
    m = get_payment_methods()
    m[method] = enabled
    set_setting("payment_methods", json.dumps(m))
def get_announcement() -> str: return get_setting("announcement", "")
def set_announcement(text: str): set_setting("announcement", text)
def get_support_username() -> str: return get_setting("support_username", SUPPORT_USERNAME)
def get_receive_username() -> str: return get_setting("receive_username", RECEIVE_USERNAME)
def get_user_lang(user_id: int) -> str:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else "bn"
def ensure_user_exists(user_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users(user_id, lang) VALUES(?, 'bn')", (user_id,))
    conn.commit()
    conn.close()

# Other DB helpers skipped for brevity (balance, withdrawal, orders remain standard as defined earlier)
def save_order(user_id, username, coin_qty, rate, taka_amount, method, number, photo_file_id=None):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO orders (user_id,username,coin_qty,rate,taka_amount,method,number,created_at,photo_file_id) VALUES(?,?,?,?,?,?,?,?,?)",
              (user_id, username, coin_qty, rate, taka_amount, method, number, datetime.utcnow().isoformat(), photo_file_id))
    conn.commit()
    oid = c.lastrowid
    conn.close()
    return oid

def get_order(order_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM orders WHERE order_id=?", (order_id,))
    row = c.fetchone()
    conn.close()
    return row

def update_status(order_id, status):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE orders SET status=? WHERE order_id=?", (status, order_id))
    conn.commit()
    conn.close()

def get_wallet(user_id, method):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT number FROM wallets WHERE user_id=? AND method=?", (user_id, method))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def save_wallet(user_id, method, number):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO wallets(user_id,method,number) VALUES(?,?,?) ON CONFLICT(user_id,method) DO UPDATE SET number=excluded.number", (user_id, method, number))
    conn.commit()
    conn.close()

# Helpers
def t(user_id: int, key: str, **kwargs) -> str:
    lang = get_user_lang(user_id)
    text = LANG[lang].get(key, LANG["bn"].get(key, key))
    for k, v in kwargs.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text

def method_icon(method: str) -> str: return {"bKash": "💗", "Nagad": "🟠", "BEP-20": "🪙"}.get(method, "💰")
def mask_number(number): return number if len(number) <= 6 else number[:3] + "*" * (len(number) - 6) + number[-3:]
def get_rate(qty):
    for th, r in get_price_tiers():
        if qty >= th: return r
    return None

def calc_taka(qty, rate): return round((qty / 1000) * rate, 2)
def price_list_text(uid):
    tiers = get_price_tiers()
    return "\n".join([f"✅ <b>{tier[0]:,}+</b> কয়েন ➜ <b>{tier[1]}৳</b>" for tier in tiers])

MENU_BUTTONS = [("order", "btn_order"), ("price", "btn_price"), ("support", "btn_support")]

def build_main_menu(user_id: int) -> ReplyKeyboardMarkup:
    L = LANG[get_user_lang(user_id)]
    active = [KeyboardButton(L[lbl]) for key, lbl in MENU_BUTTONS]
    return ReplyKeyboardMarkup([active[i:i + 2] for i in range(0, len(active), 2)], resize_keyboard=True)

# Channels Check
async def check_channel_member(bot, user_id: int):
    channels = get_required_channels()
    if not channels:
        return True
    for channel in channels:
        try:
            member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status in ("left", "kicked"):
                return False
        except telegram.error.BadRequest:
            return False
        except telegram.error.Forbidden:
            return None
        except Exception:
            return None
    return True

def join_keyboard(L):
    channels = get_required_channels()
    buttons = [[InlineKeyboardButton(f"{L['join_btn']} ({ch.lstrip('@')})", url=f"https://t.me/{ch.lstrip('@')}")] for ch in channels]
    buttons.append([InlineKeyboardButton(L["join_check_btn"], callback_data="check_join")])
    return InlineKeyboardMarkup(buttons)

async def _ensure_joined(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id
    if uid == ADMIN_ID: return True
    result = await check_channel_member(context.bot, uid)
    L = LANG[get_user_lang(uid)]
    if result is None:
        await update.message.reply_text(L["join_check_error"], parse_mode="HTML")
        return False
    if not result:
        await update.message.reply_text(L["join_required"], parse_mode="HTML", reply_markup=join_keyboard(L))
        return False
    return True

# Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user_exists(uid)
    await update.message.reply_text("🌐 <b>ভাষা বেছে নিন / Language:</b>", parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🇧🇩 বাংলা", callback_data="setlang_bn"), InlineKeyboardButton("🇬🇧 English", callback_data="setlang_en")]]))

async def check_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    L = LANG[get_user_lang(uid)]
    result = await check_channel_member(context.bot, uid)
    if result is None:
        await query.edit_message_text(L["join_check_error"], parse_mode="HTML")
    elif result:
        await query.edit_message_text(L["join_ok"], parse_mode="HTML")
        await context.bot.send_message(chat_id=uid, text=t(uid, "welcome", price_list=price_list_text(uid)), parse_mode="HTML", reply_markup=build_main_menu(uid))
    else:
        await query.edit_message_text(L["join_fail"], parse_mode="HTML", reply_markup=join_keyboard(L))

async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    lang = query.data.split("_")[1]
    conn = get_conn()
    conn.execute("UPDATE users SET lang=? WHERE user_id=?", (lang, uid))
    conn.commit()
    conn.close()
    
    L = LANG[lang]
    if uid != ADMIN_ID:
        result = await check_channel_member(context.bot, uid)
        if not result:
            await query.edit_message_text(L["join_required"], parse_mode="HTML", reply_markup=join_keyboard(L))
            return
    await query.edit_message_text(L["lang_set"], parse_mode="HTML")
    await context.bot.send_message(chat_id=uid, text=t(uid, "welcome", price_list=price_list_text(uid)), parse_mode="HTML", reply_markup=build_main_menu(uid))

# QTY & Order logic simplified for full code block
QTY, METHOD, NUMBER, PROOF = range(4)
ADMIN_CH_ADD = 100

async def order_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await _ensure_joined(update, context): return ConversationHandler.END
    await update.message.reply_text(t(uid, "order_qty_ask"), parse_mode="HTML", reply_markup=ReplyKeyboardMarkup([["❌ অর্ডার বাতিল করুন"]], resize_keyboard=True))
    return QTY

async def get_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    if text == "❌ অর্ডার বাতিল করুন":
        await update.message.reply_text(t(uid, "cancelled"), reply_markup=build_main_menu(uid))
        return ConversationHandler.END
    if not text.isdigit():
        await update.message.reply_text(t(uid, "order_qty_invalid"), parse_mode="HTML")
        return QTY
    qty = int(text)
    rate = get_rate(qty)
    if not rate:
        await update.message.reply_text(t(uid, "order_qty_low", min_qty=10000), parse_mode="HTML")
        return QTY
    taka = calc_taka(qty, rate)
    context.user_data.update({"qty": qty, "rate": rate, "taka": taka})
    buttons = [[InlineKeyboardButton(f"{m}", callback_data=f"method_{m}")] for m in get_enabled_methods()]
    await update.message.reply_text(t(uid, "order_summary", qty=qty, taka=taka), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))
    return METHOD

async def method_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    method = query.data.split("_", 1)[1]
    context.user_data["method"] = method
    await query.edit_message_text(t(query.from_user.id, "method_new_ask", method=method), parse_mode="HTML")
    return NUMBER

async def get_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["number"] = text
    uid = update.effective_user.id
    d = context.user_data
    save_wallet(uid, d["method"], text)
    await update.message.reply_text(t(uid, "proof_ask", qty=d['qty'], username=get_receive_username()), parse_mode="HTML")
    return PROOF

async def get_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not update.message.photo: return PROOF
    d = context.user_data
    photo_file_id = update.message.photo[-1].file_id
    order_id = save_order(uid, update.effective_user.username, d["qty"], d["rate"], d["taka"], d["method"], d["number"], photo_file_id)
    caption = f"🔔 <b>New Order #{order_id}</b>\n💰 Qty: {d['qty']}\n💵 Tk: {d['taka']}\n💳 {d['method']}: <code>{d['number']}</code>"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Paid", callback_data=f"paid_{order_id}"), InlineKeyboardButton("❌ Reject", callback_data=f"reject_{order_id}")]])
    await context.bot.send_photo(chat_id=ADMIN_ID, photo=photo_file_id, caption=caption, parse_mode="HTML", reply_markup=kb)
    await update.message.reply_text(t(uid, "order_submitted", order_id=order_id), parse_mode="HTML", reply_markup=build_main_menu(uid))
    return ConversationHandler.END

# UNKNOWN COMMAND / RANDOM MESSAGE HANDLER 
async def unknown_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = (
        "❌ <b>ভুল কমান্ড!</b>\n\n"
        "অনুগ্রহ করে নিচের মেনু থেকে একটি অপশন নির্বাচন করুন।\n\n"
        "💎 কয়েন বিক্রি করতে <b>\"💎 কয়েন বিক্রি করুন\"</b> বাটনে চাপুন।"
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=build_main_menu(uid))

# ADMIN PANEL - Force Join Config
def admin_reply_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📢 Force Join Channels")],
        [KeyboardButton("🔚 Exit")]
    ], resize_keyboard=True)

async def admin_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID:
        await update.message.reply_text("🛠️ <b>Admin Panel</b>", parse_mode="HTML", reply_markup=admin_reply_keyboard())

async def admin_channels_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    channels = get_required_channels()
    ch_list = "\n".join([f"✅ {c}" for c in channels]) if channels else "❌ কোনো চ্যানেল সেট করা নেই"
    msg = f"📢 <b>Force Join Channels</b>\n{LINE}\n{ch_list}\n{LINE}\n\nকী করতে চান?"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Channel Add", callback_data="admin_ch_add")],
        [InlineKeyboardButton("➖ Channel Remove", callback_data="admin_ch_rem")]
    ])
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode="HTML", reply_markup=kb)
    else:
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=kb)

async def admin_ch_add_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("➕ চ্যানেলের Username দিন (@ সহ, যেমন: @BDincomeTV):")
    return ADMIN_CH_ADD

async def admin_ch_add_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.startswith("@"):
        channels = get_required_channels()
        if text not in channels:
            channels.append(text)
            set_required_channels(channels)
            await update.message.reply_text(f"✅ {text} যোগ করা হয়েছে!")
        else:
            await update.message.reply_text("⚠️ চ্যানেলটি আগে থেকেই আছে।")
    else:
        await update.message.reply_text("⚠️ সঠিক Username দিন (@ সহ)।")
    await admin_channels_menu(update, context)
    return ConversationHandler.END

async def admin_ch_rem_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    channels = get_required_channels()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"❌ Remove {c}", callback_data=f"delch_{c}")] for c in channels] + [[InlineKeyboardButton("🔙 Back", callback_data="admin_ch_back")]])
    await query.edit_message_text("➖ <b>চ্যানেল রিমুভ করুন</b>\nযেকোনো চ্যানেলের ওপর চাপ দিন:", parse_mode="HTML", reply_markup=kb)

async def global_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    
    if data == "admin_ch_rem": await admin_ch_rem_ask(update, context)
    elif data == "admin_ch_back": await admin_channels_menu(update, context)
    elif data.startswith("delch_"):
        ch = data.split("delch_")[1]
        channels = get_required_channels()
        if ch in channels:
            channels.remove(ch)
            set_required_channels(channels)
            await query.answer(f"✅ {ch} রিমুভ করা হয়েছে!", show_alert=True)
            await admin_ch_rem_ask(update, context)
    elif data.startswith("paid_") or data.startswith("reject_"):
        action, oid = data.split("_")
        update_status(int(oid), action)
        await query.edit_message_caption(caption=f"✅ Order #{oid} marked as {action.upper()}.")

def main():
    init_db()
    start_health_server() # Fixes the Render Binding Error

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^💎 কয়েন বিক্রি করুন$"), order_start)],
        states={
            QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_qty)],
            METHOD: [CallbackQueryHandler(method_choice, pattern="^method_")],
            NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_number)],
            PROOF: [MessageHandler(filters.PHOTO, get_proof)],
        },
        fallbacks=[]
    )
    
    admin_ch_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_ch_add_ask, pattern="^admin_ch_add$")],
        states={ADMIN_CH_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_ch_add_receive)]},
        fallbacks=[]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(set_language, pattern="^setlang_"))
    app.add_handler(CallbackQueryHandler(check_join, pattern="^check_join$"))
    app.add_handler(MessageHandler(filters.Regex("^📢 Force Join Channels$"), admin_channels_menu))
    app.add_handler(MessageHandler(filters.Regex("^🔚 Exit$"), start))
    app.add_handler(CommandHandler("admin", admin_entry))
    
    app.add_handler(conv_handler)
    app.add_handler(admin_ch_conv)
    app.add_handler(CallbackQueryHandler(global_callback_handler))

    # ১) Unknown Command / Random Message Handler (অবশ্যই সবার শেষে রাখতে হবে)
    app.add_handler(MessageHandler(filters.TEXT | filters.COMMAND, unknown_command_handler))

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()