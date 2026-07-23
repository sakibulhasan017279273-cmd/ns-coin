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
#   ⚙️  CONFIG — হোস্টিং করার আগে এখানে সব বসিয়ে নিন (এই একটা জায়গাতেই)
# ================================================================
# 1) BOT TOKEN: @BotFather থেকে পাওয়া টোকেন এখানে দুই কোটেশনের মাঝে বসান।
#    (Environment variable BOT_TOKEN সেট থাকলে সেটাই আগে ব্যবহার হবে,
#     না থাকলে নিচের ডিফল্ট ভ্যালুটা ব্যবহার হবে।)
BOT_TOKEN          = os.environ.get("BOT_TOKEN") or "8697617688:AAGBxf2qZlXN9xjO8Xk-9f6HMURK_JNfAew"

# 2) ADMIN ID: আপনার Telegram user ID (সংখ্যা)। বের করতে @userinfobot ব্যবহার করুন।
ADMIN_ID           = 7163496323

# 3) পেমেন্ট প্রুফ ও Join-gate চ্যানেল (username সহ, @ দিয়ে শুরু)
#    REQUIRED_CHANNELS একাধিক চ্যানেল রাখতে পারে — user-কে সবগুলোতে
#    জয়েন করতে হবে বট ব্যবহার করার আগে। নতুন চ্যানেল যোগ করতে লিস্টে
#    আরেকটা লাইন যোগ করুন, কমা দিয়ে আলাদা করে।
PAYMENT_CHANNEL_ID = "@nscoinpaymentchannel"
REQUIRED_CHANNELS  = [
    "@BDincomeTV",
    "@nscoinpaymentchannel",
]

# 4) Receive/Support username — এগুলো পরে Admin বটম-মেনু থেকেও বদলানো যাবে,
#    এখানে শুধু শুরুর ডিফল্ট ভ্যালু।
RECEIVE_USERNAME   = "sakib173087"
SUPPORT_USERNAME   = "BDincometvadmin_sakib"

DEFAULT_PRICE_TIERS = [
    (500000, 7.90),
    (300000, 7.75),
    (10000,  7.70),
]
DEFAULT_MAINTENANCE_MSG_BN = (
    "🔧 <b>বট বর্তমানে মেইনটেন্যান্সে আছে।</b>\n\n"
    "⏳ কিছুক্ষণ পর আবার চেষ্টা করুন।\n"
    "অসুবিধার জন্য আন্তরিকভাবে দুঃখিত। 🙏"
)
DEFAULT_MAINTENANCE_MSG_EN = (
    "🔧 <b>Bot is currently under maintenance.</b>\n\n"
    "⏳ Please try again after a while.\n"
    "We sincerely apologize for the inconvenience. 🙏"
)
DB_PATH = "orders.db"

# ================================================================
#   RENDER / HOSTING: ছোট্ট HTTP সার্ভার
#   এটা শুধু Render-কে "port খোলা আছে" দেখানোর জন্য, আর UptimeRobot
#   দিয়ে ping করে free service জাগিয়ে রাখার জন্য। বটের আসল কাজের
#   সাথে এর কোনো সম্পর্ক নেই।
# ================================================================
class _HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - Bot is running")

    def log_message(self, format, *args):
        pass  # Render/UptimeRobot এর বার বার হিট করা লগে দেখাতে চাই না

def _run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), _HealthCheckHandler)
    server.serve_forever()

def start_health_server():
    threading.Thread(target=_run_health_server, daemon=True).start()

async def send_db_backup(bot):
    """Sends the current orders.db file to the admin as a Telegram document.
    This is a safety net for hosts (like Render's free tier) that wipe the
    disk on redeploy — admin can manually restore this file if data is lost."""
    try:
        if os.path.exists(DB_PATH):
            with open(DB_PATH, "rb") as f:
                await bot.send_document(
                    chat_id=ADMIN_ID, document=f,
                    filename=f"orders_backup_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.db",
                    caption="💾 Database Backup\n\nমুশকিলে পড়লে এই ফাইলটা ডাউনলোড করে সার্ভারে orders.db হিসেবে রাখুন।"
                )
    except Exception as e:
        logging.error(f"Backup failed: {e}")

async def periodic_backup_loop(app):
    while True:
        await asyncio.sleep(6 * 60 * 60)  # প্রতি ৬ ঘণ্টা পরপর
        await send_db_backup(app.bot)

async def post_init(app):
    asyncio.create_task(periodic_backup_loop(app))

logging.basicConfig(level=logging.INFO)
LINE = "━━━━━━━━━━━━━━━━━━━━━━"

# ================================================================
#      READY-MADE PREMIUM TEMPLATES (Announcement / Broadcast / Rules)
#      Admin can send these with one tap, or edit them below and restart.
# ================================================================
DEFAULT_ANNOUNCEMENT_BN = (
    "🎉 <b>বিশেষ ঘোষণা!</b>\n"
    f"{LINE}\n"
    "✅ আজকের রেট আপডেট হয়েছে\n"
    "✅ দ্রুত ও নিরাপদ পেমেন্ট (২-১৯ মিনিটের মধ্যে)\n"
    "✅ ২৪/৭ সার্ভিস চালু\n\n"
    "📌 এখনই কয়েন সেল করে সুবিধা নিন!"
)

DEFAULT_BROADCAST_BN = (
    "🔥 <b>প্রিয় গ্রাহক,</b>\n\n"
    "আজ আমাদের রেট আরও ভালো হয়েছে! 💎\n"
    "এখনই আপনার কয়েন সেল করুন এবং সবচেয়ে ভালো দাম পান।\n\n"
    "⚡ দ্রুত পেমেন্ট\n"
    "🔒 ১০০% নিরাপদ\n"
    "🎁 রেফার করে বোনাস নিন\n\n"
    "👉 এখনই \"কয়েন বিক্রি করুন\" বাটনে চাপ দিন!"
)

DEFAULT_RULES_BN = (
    "📜 <b>NS Coin Sell — নিয়মাবলী</b>\n"
    f"{LINE}\n"
    "1️⃣ সঠিক ও নিজের নাম্বার/ওয়ালেট দিন\n"
    "2️⃣ পেমেন্ট প্রুফ (স্ক্রিনশট) অবশ্যই আপলোড করুন\n"
    "3️⃣ ভুল তথ্য দিলে পেমেন্ট বাতিল হতে পারে\n"
    "4️⃣ পেমেন্ট সাধারণত ২-১৯ মিনিটের মধ্যে পাঠানো হয়\n"
    "5️⃣ কোনো সমস্যা হলে \"Support\" বাটনে গিয়ে যোগাযোগ করুন\n"
    "6️⃣ প্রতারণামূলক কার্যকলাপ করলে অ্যাকাউন্ট ব্যান করা হবে\n\n"
    "🙏 নিয়ম মেনে লেনদেন করুন, নিরাপদ থাকুন।"
)

DEFAULT_RULES_EN = (
    "📜 <b>NS Coin Sell — Rules</b>\n"
    f"{LINE}\n"
    "1️⃣ Provide your own correct number/wallet\n"
    "2️⃣ Payment proof (screenshot) must be uploaded\n"
    "3️⃣ Wrong info may lead to payment cancellation\n"
    "4️⃣ Payment is usually sent within 2-19 minutes\n"
    "5️⃣ For any issue, contact via the \"Support\" button\n"
    "6️⃣ Fraudulent activity will result in an account ban\n\n"
    "🙏 Follow the rules, stay safe."
)

# ================================================================
#                    🌐 LANGUAGE STRINGS
# ================================================================
LANG = {
    "bn": {
        "pick_lang"           : "🌐 <b>ভাষা বেছে নিন</b>\n\nPlease choose your language:",
        "lang_set"            : "✅ বাংলা ভাষা সেট করা হয়েছে।",
        "join_required"       : (
            "📢 <b>চ্যানেলে যোগ দেওয়া বাধ্যতামূলক!</b>\n"
            f"{LINE}\n"
            "🔰 বট ব্যবহার করতে হলে আমাদের অফিশিয়াল চ্যানেলে যোগ দিন।\n\n"
            "✅ যোগ দেওয়ার পর নিচের বাটনে চাপুন।"
        ),
        "join_btn"            : "📢 চ্যানেলে যোগ দিন",
        "join_check_btn"      : "✅ যোগ দিয়েছি — চেক করুন",
        "join_ok"             : "🎉 চমৎকার! এখন বট ব্যবহার শুরু করুন।",
        "join_fail"           : (
            "❌ আপনি এখনো চ্যানেলে যোগ দেননি।\n"
            "চ্যানেলে যোগ দিন, তারপর আবার চেক করুন।"
        ),
        "join_check_error"    : (
            "⚠️ <b>চ্যানেল যাচাই করা সম্ভব হচ্ছে না।</b>\n\n"
            "বটটি চ্যানেলের অ্যাডমিন হতে হবে। অনুগ্রহ করে অ্যাডমিনের সাথে যোগাযোগ করুন।"
        ),
        # main menu buttons
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
        # order control
        "btn_cancel_order"    : "❌ অর্ডার বাতিল করুন",
        "btn_continue_order"  : "▶️ চালু রাখুন",
        "btn_confirm_cancel"  : "⚠️ হ্যাঁ, বাতিল করুন",
        "confirm_cancel_ask"  : "আপনি কি নিশ্চিতভাবে অর্ডারটি বাতিল করতে চান?",
        "cancel_done"         : "❌ অর্ডার বাতিল করা হয়েছে।",
        "continue_done"       : "👍 সেলিং প্রসেস চালু রয়েছে। অনুগ্রহ করে কত কয়েন বিক্রি করবেন তা সংখ্যায় লিখুন:",
        "timeout_msg"         : (
            "⏱️ ৫ মিনিট কোনো রেসপন্স না পাওয়ায় আপনার অর্ডারটি\n"
            "স্বয়ংক্রিয়ভাবে বাতিল করা হয়েছে।"
        ),
        "no_methods"          : (
            "⚠️ <b>বর্তমানে কোনো পেমেন্ট মেথড চালু নেই।</b>\n\n"
            "অ্যাডমিন শীঘ্রই চালু করবেন। পরে আবার চেষ্টা করুন।"
        ),
        # referral
        "referral_msg"        : (
            "🔗 <b>আপনার রেফারেল লিংক</b>\n"
            f"{LINE}\n"
            "বন্ধুদের এই লিংক শেয়ার করুন:\n\n"
            "👉 <code>{{link}}</code>\n\n"
            f"{LINE}\n"
            "👥 মোট রেফার করেছেন:  <b>{{count}}</b> জন\n"
            "💰 প্রতি রেফারে পাবেন:  <b>{{bonus}}৳</b>\n\n"
            "💡 বেশি রেফার করুন — বেশি আয় করুন!"
        ),
        "referral_bonus_notify": (
            "🎉 <b>রেফারেল বোনাস পেয়েছেন!</b>\n"
            f"{LINE}\n"
            "আপনার রেফার করা একজন user প্রথমবার সফলভাবে কয়েন সেল করেছেন।\n"
            "💰 <b>{{bonus}}৳</b> আপনার balance-এ যোগ হয়েছে!\n\n"
            "আরও রেফার করে আরও আয় করুন 🚀"
        ),
        "referral_joined_notify": (
            "👋 আপনার রেফারেল লিংক ব্যবহার করে একজন নতুন user যোগ দিয়েছেন।\n"
            "🎁 তিনি প্রথমবার কয়েন সেল সম্পন্ন করলেই আপনি বোনাস পাবেন।"
        ),
        # balance
        "balance_msg"         : (
            "💰 <b>আপনার Balance</b>\n"
            f"{LINE}\n"
            "💵 বর্তমান Balance:  <b>{{balance}}৳</b>\n"
            "👥 মোট রেফার:          <b>{{ref_count}}</b> জন\n"
            "💎 মোট আয়:            <b>{{total_earned}}৳</b>\n"
            f"{LINE}\n"
            "💸 Withdraw করতে সর্বনিম্ন <b>{{min_w}}৳</b> লাগবে।"
        ),
        "balance_low"         : (
            "⚠️ আপনার balance <b>{{balance}}৳</b>।\n"
            "Withdraw করতে কমপক্ষে <b>{{min_w}}৳</b> থাকতে হবে।"
        ),
        # withdraw flow
        "withdraw_method_ask" : (
            "💸 <b>Withdraw Request</b>\n"
            f"{LINE}\n"
            "পরিমাণ: <b>{{amount}}৳</b>\n\n"
            "কোন মাধ্যমে টাকা নিতে চান?"
        ),
        "withdraw_number_ask" : "📱 আপনার <b>{{method}}</b> নম্বর দিন (01 দিয়ে শুরু, ১১ ডিজিট):",
        "withdraw_use_saved"  : "✅ সংরক্ষিত নম্বর ব্যবহার করুন ({{masked}})",
        "withdraw_new_number" : "✏️ নতুন নম্বর দিন",
        "withdraw_confirm"    : (
            "✅ <b>Withdraw Confirm করুন</b>\n"
            f"{LINE}\n"
            "💵 পরিমাণ    :  <b>{{amount}}৳</b>\n"
            "{{icon}} মাধ্যম   :  <b>{{method}}</b>\n"
            "📱 নম্বর       :  <code>{{number}}</code>\n"
            f"{LINE}\n"
            "নিশ্চিত করুন?"
        ),
        "withdraw_submitted"  : (
            "🎊 <b>Withdraw Request জমা হয়েছে!</b>\n"
            f"{LINE}\n"
            "💵 পরিমাণ: <b>{{amount}}৳</b>\n"
            "🆔 Request ID: <b>#{{wid}}</b>\n"
            f"{LINE}\n"
            "✨ অ্যাডমিন approve করলে ২৪ ঘণ্টার মধ্যে পেমেন্ট পাঠানো হবে।"
        ),
        "withdraw_paid_user"  : (
            "🎉 <b>Withdrawal সম্পন্ন!</b>\n"
            f"{LINE}\n"
            "✅ আপনার <b>{{amount}}৳</b> withdrawal সফলভাবে পাঠানো হয়েছে।\n"
            "{{icon}} <b>{{method}}</b>  →  <code>{{number}}</code>\n"
            f"{LINE}\n"
            "🙏 ধন্যবাদ! আরও রেফার করে আয় করুন। 💎"
        ),
        "withdraw_rejected_user": (
            "😔 <b>Withdrawal বাতিল হয়েছে</b>\n"
            f"{LINE}\n"
            "❌ আপনার <b>{{amount}}৳</b> withdrawal request বাতিল করা হয়েছে।\n"
            "💰 টাকা আপনার balance-এ ফিরে এসেছে।\n\n"
            "🎧 সমস্যা থাকলে সাপোর্টে যোগাযোগ করুন।"
        ),
        "number_invalid"      : "⚠️ সঠিক মোবাইল নম্বর দিন (01 দিয়ে শুরু, ১১ ডিজিট)।  উদাহরণ: <code>01XXXXXXXXX</code>",
        # welcome
        "welcome"             : (
            "👋 <b>স্বাগতম NS Coin Sell বটে!</b>\n"
            f"{LINE}\n"
            "💹 <b>বর্তমান রেট</b>\n\n"
            "{{price_list}}\n"
            f"{LINE}\n"
            "👇 নিচের মেনু থেকে যা প্রয়োজন বেছে নিন"
        ),
        "price_header"        : (
            "📈 <b>বর্তমান প্রাইস লিস্ট</b>\n"
            f"{LINE}\n"
            "{{price_list}}\n"
            f"{LINE}\n"
            "💎 কয়েন বিক্রি করতে মেনু থেকে <b>\"💎 কয়েন বিক্রি করুন\"</b> চাপুন।"
        ),
        "wallet_empty"        : "👛 <b>আপনার Wallet খালি</b>\n\nঅর্ডার দেওয়ার সময় নম্বর দিলে তা স্বয়ংক্রিয়ভাবে সেভ হবে।",
        "wallet_header"       : "👛 <b>আপনার সংরক্ষিত Wallet</b>\n" + LINE + "\n{{wallets}}\n" + LINE + "\nনতুন অর্ডার দিলে নম্বর পরিবর্তন করতে পারবেন।",
        "history_empty"       : "📜 আপনার কোনো অর্ডার হিস্ট্রি নেই।",
        "history_header"      : "📊 <b>আপনার সাম্প্রতিক অর্ডার</b>\n" + LINE + "\n{{orders}}",
        "support_msg"         : (
            "🎧 <b>কাস্টমার সাপোর্ট</b>\n"
            f"{LINE}\n"
            "কোনো সমস্যা বা প্রশ্ন থাকলে নিচের বাটনে চাপুন।\n"
            "আমাদের টিম সাহায্য করতে সর্বদা প্রস্তুত। 💬"
        ),
        "support_btn"         : "💬 সাপোর্টে মেসেজ করুন",
        "order_qty_ask"       : (
            "💹 <b>বর্তমান রেট</b>\n"
            f"{LINE}\n"
            "{{price_list}}\n"
            f"{LINE}\n\n"
            "✍️ আপনি কত কয়েন বিক্রি করতে চান?\n"
            "📌 শুধু সংখ্যা লিখুন  ➤  <code>50000</code>\n\n"
            "<i>⚠️ ৫ মিনিট নিষ্ক্রিয় থাকলে অর্ডার স্বয়ংক্রিয়ভাবে বাতিল হবে।</i>"
        ),
        "order_qty_invalid"   : "⚠️ শুধু সংখ্যা লিখুন।  উদাহরণ: <code>50000</code>",
        "order_qty_low"       : "❌ সর্বনিম্ন <b>{{min_qty}}</b> কয়েন থেকে অর্ডার নেওয়া হয়।\n\nআবার লিখুন:",
        "order_qty_max"       : "❌ একবারে সর্বোচ্চ <b>{{max_qty}}</b> কয়েন সেল করা যাবে।\n\nকম পরিমাণে আবার লিখুন:",
        "order_daily_limit_reached": (
            "🚫 <b>আজকের সেল লিমিট শেষ হয়ে গেছে!</b>\n"
            "আজকের জন্য নির্ধারিত পরিমাণ কয়েন সেল হয়ে গেছে।\n"
            "🙏 দয়া করে আগামীকাল আবার চেষ্টা করুন।"
        ),
        "order_summary"       : (
            "🧾 <b>অর্ডার সামারি</b>\n"
            f"{LINE}\n"
            "💰 পরিমাণ   :  <b>{{qty}}</b> কয়েন\n"
            "📊 রেট        :  <b>{{rate}}৳</b> / ১০০০ কয়েন\n"
            "💵 পাবেন    :  <b>{{taka}}৳</b>\n"
            f"{LINE}\n"
            "👇 পেমেন্ট মেথড বেছে নিন:"
        ),
        "method_selected"     : "{{icon}} মেথড: <b>{{method}}</b>\n\nসংরক্ষিত তথ্য ব্যবহার করবেন নাকি নতুন দেবেন?",
        "method_new_ask"      : "{{icon}} মেথড: <b>{{method}}</b>\n\n📱 আপনার <b>{{method}}</b> নম্বরটি দিন (01 দিয়ে শুরু, ১১ ডিজিট):",
        "ask_bep20"           : "🪙 মেথড: <b>BEP-20 (USDT/BNB)</b>\n\n✍️ আপনার <b>BEP-20 Wallet Address</b> দিন (0x + 40 hex character):",
        "btn_use_saved"       : "✅ সংরক্ষিত  ({{masked}})",
        "btn_new_number"      : "✏️ নতুন নম্বর / Address দিন",
        "wallet_gone"         : "⚠️ সংরক্ষিত নম্বর খুঁজে পাওয়া যায়নি। নতুন নম্বর দিন:",
        "bep20_invalid"       : "⚠️ অবৈধ অ্যাড্রেস! সঠিক BEP-20 address দিন।\n<code>0x</code> দিয়ে শুরু + ৪০টি hex character (মোট ৪২)।",
        "proof_ask"           : (
            "📤 <b>কয়েন পাঠানোর নির্দেশনা</b>\n"
            f"{LINE}\n"
            "এখন আপনার <b>{{qty}}</b> কয়েন এই ইউজারনেমে পাঠান:\n\n"
            "👉  <code>{{username}}</code>\n"
            f"{LINE}\n"
            "📸 পাঠানো হলে <b>timestamp-সহ স্ক্রিনশট</b> পাঠান।"
        ),
        "proof_not_photo"     : "⚠️ দয়া করে স্ক্রিনশট (ছবি) পাঠান।",
        "order_submitted"     : (
            "🎊 <b>অর্ডার সফলভাবে জমা হয়েছে!</b>\n"
            f"{LINE}\n"
            "🆔 সিরিয়াল নম্বর:  <b>#{{order_id}}</b>\n"
            "⏳ স্ট্যাটাস:  <b>যাচাই চলছে...</b>\n"
            f"{LINE}\n"
            "✨ যাচাই হলে খুব শীঘ্রই পেমেন্ট পাঠানো হবে।\n"
            "📢 সব transaction-এর status ও proof লাইভ দেখতে আমাদের চ্যানেলে জয়েন করুন 👉 {{channel}}\n\n"
            "ধন্যবাদ আমাদের সাথে থাকার জন্য! 🙏"
        ),
        "cancelled"           : "❌ অর্ডার বাতিল করা হয়েছে।",
        "session_expired"     : "⚠️ সেশন মেয়াদ শেষ। আবার শুরু করুন।",
        "admin_new_order"     : (
            "🔔 <b>নতুন অর্ডার এসেছে!</b>\n"
            f"{LINE}\n"
            "👤 গ্রাহক:    <b>{{name}}</b>{{uname}}\n"
            "💰 কয়েন:   <b>{{qty}}</b>\n"
            "📊 রেট:      <b>{{rate}}৳</b> / ১০০০\n"
            "💵 মোট:     <b>{{taka}}৳</b>\n"
            "{{icon}} {{method}}: <code>{{masked_num}}</code>\n"
            "🆔 অর্ডার:  <b>#{{order_id}}</b>\n"
            "🕐 সময়:    {{time}}\n"
            f"{LINE}\n"
            "⏳ স্ট্যাটাস: <b>Pending</b>"
        ),
        "paid_user"           : (
            "🎉 <b>পেমেন্ট সম্পন্ন হয়েছে!</b>\n"
            f"{LINE}\n"
            "✅ আপনার অর্ডার <b>#{{order_id}}</b> এর পেমেন্ট\n"
            "সফলভাবে পাঠানো হয়েছে।\n"
            f"{LINE}\n"
            "🙏 ধন্যবাদ। আবার আসবেন! 💎"
        ),
        "paid_channel"        : (
            "╔══════════════════════╗\n"
            "║  ✅  পেমেন্ট সম্পন্ন  ✅  ║\n"
            "╚══════════════════════╝\n\n"
            "👤 <b>প্রাপক</b>     :  {{name}}\n"
            "💰 <b>কয়েন</b>      :  {{qty}}\n"
            "💵 <b>পরিমাণ</b>  :  <b>{{taka}}৳</b>  ({{icon}} {{method}})\n"
            "📱 <b>নম্বর</b>       :  <code>{{masked_num}}</code>\n"
            "🆔 <b>অর্ডার</b>    :  #{{order_id}}\n"
            "🕐 <b>সময়</b>       :  {{time}}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🏦  <i>NS Coin Sell — দ্রুত ও নিরাপদ সেবা</i>"
        ),
        "reject_user"         : (
            "😔 <b>অর্ডার বাতিল হয়েছে</b>\n"
            f"{LINE}\n"
            "❌ আপনার অর্ডার <b>#{{order_id}}</b> বাতিল করা হয়েছে।\n\n"
            "🎧 কোনো সমস্যা থাকলে সাপোর্টে যোগাযোগ করুন।"
        ),
        "reject_channel"      : (
            "╔══════════════════════╗\n"
            "║  ❌  অর্ডার বাতিল  ❌  ║\n"
            "╚══════════════════════╝\n\n"
            "👤 <b>গ্রাহক</b>      :  {{name}}\n"
            "💰 <b>কয়েন</b>       :  {{qty}}\n"
            "💵 <b>পরিমাণ</b>   :  {{taka}}৳  ({{icon}} {{method}})\n"
            "📱 <b>নম্বর</b>        :  <code>{{masked_num}}</code>\n"
            "🆔 <b>অর্ডার</b>     :  #{{order_id}}\n"
            "🕐 <b>সময়</b>        :  {{time}}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🏦  <i>NS Coin Sell — দ্রুত ও নিরাপদ সেবা</i>"
        ),
        "already_processed"   : "ℹ️ এই অর্ডার ইতোমধ্যে প্রসেস করা হয়েছে।",
        "admin_paid_confirm"  : "✅ অর্ডার <b>#{{order_id}}</b> — Paid সম্পন্ন।",
        "admin_reject_confirm": "❌ অর্ডার <b>#{{order_id}}</b> — বাতিল করা হয়েছে।",
    },

    "en": {
        "pick_lang"           : "🌐 <b>ভাষা বেছে নিন</b>\n\nPlease choose your language:",
        "lang_set"            : "✅ English language has been set.",
        "join_required"       : (
            "📢 <b>Channel membership required!</b>\n"
            f"{LINE}\n"
            "🔰 You must join our official channel to use this bot.\n\n"
            "✅ After joining, tap the button below."
        ),
        "join_btn"            : "📢 Join Channel",
        "join_check_btn"      : "✅ I've Joined — Check",
        "join_ok"             : "🎉 Great! You can now use the bot.",
        "join_fail"           : (
            "❌ You haven't joined the channel yet.\n"
            "Please join and then check again."
        ),
        "join_check_error"    : (
            "⚠️ <b>Channel verification is temporarily unavailable.</b>\n\n"
            "The bot needs to be an admin of the channel. Please contact the admin."
        ),
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
        "btn_continue_order"  : "▶️ Continue Process",
        "btn_confirm_cancel"  : "⚠️ Yes, Cancel",
        "confirm_cancel_ask"  : "Are you sure you want to cancel the order?",
        "cancel_done"         : "❌ Order has been canceled.",
        "continue_done"       : "👍 Selling process is active. Please type the coin amount in numbers:",
        "timeout_msg"         : "⏱️ Your order has been automatically canceled due to 5 minutes of inactivity.",
        "no_methods"          : (
            "⚠️ <b>No payment methods are currently available.</b>\n\n"
            "The admin will enable them soon. Please try again later."
        ),
        "referral_msg"        : (
            "🔗 <b>Your Referral Link</b>\n"
            f"{LINE}\n"
            "Share this link with friends:\n\n"
            "👉 <code>{{link}}</code>\n\n"
            f"{LINE}\n"
            "👥 Total referred:  <b>{{count}}</b> people\n"
            "💰 Bonus per referral:  <b>{{bonus}}৳</b>\n\n"
            "💡 Refer more — earn more!"
        ),
        "referral_bonus_notify": (
            "🎉 <b>Referral Bonus Received!</b>\n"
            f"{LINE}\n"
            "A user you referred just completed their first successful coin sale.\n"
            "💰 <b>{{bonus}}৳</b> has been added to your balance!\n\n"
            "Keep referring to earn more 🚀"
        ),
        "referral_joined_notify": (
            "👋 A new user joined using your referral link.\n"
            "🎁 You'll get the bonus once they complete their first coin sale."
        ),
        "balance_msg"         : (
            "💰 <b>Your Balance</b>\n"
            f"{LINE}\n"
            "💵 Current Balance:  <b>{{balance}}৳</b>\n"
            "👥 Total Referred:    <b>{{ref_count}}</b> people\n"
            "💎 Total Earned:       <b>{{total_earned}}৳</b>\n"
            f"{LINE}\n"
            "💸 Minimum withdrawal: <b>{{min_w}}৳</b>"
        ),
        "balance_low"         : (
            "⚠️ Your balance is <b>{{balance}}৳</b>.\n"
            "You need at least <b>{{min_w}}৳</b> to withdraw."
        ),
        "withdraw_method_ask" : (
            "💸 <b>Withdraw Request</b>\n"
            f"{LINE}\n"
            "Amount: <b>{{amount}}৳</b>\n\n"
            "Choose your withdrawal method:"
        ),
        "withdraw_number_ask" : "📱 Enter your <b>{{method}}</b> number (starts with 01, 11 digits):",
        "withdraw_use_saved"  : "✅ Use saved number ({{masked}})",
        "withdraw_new_number" : "✏️ Enter new number",
        "withdraw_confirm"    : (
            "✅ <b>Confirm Withdrawal</b>\n"
            f"{LINE}\n"
            "💵 Amount    :  <b>{{amount}}৳</b>\n"
            "{{icon}} Method   :  <b>{{method}}</b>\n"
            "📱 Number    :  <code>{{number}}</code>\n"
            f"{LINE}\n"
            "Confirm?"
        ),
        "withdraw_submitted"  : (
            "🎊 <b>Withdrawal Request Submitted!</b>\n"
            f"{LINE}\n"
            "💵 Amount: <b>{{amount}}৳</b>\n"
            "🆔 Request ID: <b>#{{wid}}</b>\n"
            f"{LINE}\n"
            "✨ Payment will be sent within 24 hours after admin approval."
        ),
        "withdraw_paid_user"  : (
            "🎉 <b>Withdrawal Completed!</b>\n"
            f"{LINE}\n"
            "✅ Your <b>{{amount}}৳</b> withdrawal has been sent successfully.\n"
            "{{icon}} <b>{{method}}</b>  →  <code>{{number}}</code>\n"
            f"{LINE}\n"
            "🙏 Thank you! Keep referring to earn more. 💎"
        ),
        "withdraw_rejected_user": (
            "😔 <b>Withdrawal Rejected</b>\n"
            f"{LINE}\n"
            "❌ Your <b>{{amount}}৳</b> withdrawal request has been rejected.\n"
            "💰 The amount has been returned to your balance.\n\n"
            "🎧 Contact support if you have questions."
        ),
        "number_invalid"      : "⚠️ Invalid number! Must start with 01 and be exactly 11 digits.  Example: <code>01XXXXXXXXX</code>",
        "welcome"             : (
            "👋 <b>Welcome to NS Coin Sell Bot!</b>\n"
            f"{LINE}\n"
            "💹 <b>Current Rates</b>\n\n"
            "{{price_list}}\n"
            f"{LINE}\n"
            "👇 Choose an option from the menu below"
        ),
        "price_header"        : (
            "📈 <b>Current Price List</b>\n"
            f"{LINE}\n"
            "{{price_list}}\n"
            f"{LINE}\n"
            "💎 To sell coins tap <b>\"💎 Sell Coins\"</b> in the menu."
        ),
        "wallet_empty"        : "👛 <b>Your Wallet is empty</b>\n\nYour number will be saved automatically when you place an order.",
        "wallet_header"       : "👛 <b>Your Saved Wallet</b>\n" + LINE + "\n{{wallets}}\n" + LINE + "\nYou can change your number when placing a new order.",
        "history_empty"       : "📜 You have no order history.",
        "history_header"      : "📊 <b>Your Recent Orders</b>\n" + LINE + "\n{{orders}}",
        "support_msg"         : (
            "🎧 <b>Customer Support</b>\n"
            f"{LINE}\n"
            "If you have any questions or issues, tap the button below.\n"
            "Our team is always ready to help. 💬"
        ),
        "support_btn"         : "💬 Message Support",
        "order_qty_ask"       : (
            "💹 <b>Current Rates</b>\n"
            f"{LINE}\n"
            "{{price_list}}\n"
            f"{LINE}\n\n"
            "✍️ How many coins do you want to sell?\n"
            "📌 Enter numbers only  ➤  <code>50000</code>\n\n"
            "<i>⚠️ Order will automatically cancel if inactive for 5 minutes.</i>"
        ),
        "order_qty_invalid"   : "⚠️ Please enter numbers only.  Example: <code>50000</code>",
        "order_qty_low"       : "❌ Minimum order is <b>{{min_qty}}</b> coins.\n\nEnter again:",
        "order_qty_max"       : "❌ Maximum <b>{{max_qty}}</b> coins allowed per order.\n\nEnter a smaller amount:",
        "order_daily_limit_reached": (
            "🚫 <b>Today's sell limit has been reached!</b>\n"
            "The daily coin quota has already been sold.\n"
            "🙏 Please try again tomorrow."
        ),
        "order_summary"       : (
            "🧾 <b>Order Summary</b>\n"
            f"{LINE}\n"
            "💰 Amount  :  <b>{{qty}}</b> coins\n"
            "📊 Rate      :  <b>{{rate}}৳</b> / 1000 coins\n"
            "💵 You get  :  <b>{{taka}}৳</b>\n"
            f"{LINE}\n"
            "👇 Choose your payment method:"
        ),
        "method_selected"     : "{{icon}} Method: <b>{{method}}</b>\n\nUse saved details or enter new ones?",
        "method_new_ask"      : "{{icon}} Method: <b>{{method}}</b>\n\n📱 Enter your <b>{{method}}</b> number (must start with 01, 11 digits):",
        "ask_bep20"           : "🪙 Method: <b>BEP-20 (USDT/BNB)</b>\n\n✍️ Enter your <b>BEP-20 Wallet Address</b> (0x + 40 hex characters):",
        "btn_use_saved"       : "✅ Saved  ({{masked}})",
        "btn_new_number"      : "✏️ Enter New Number / Address",
        "wallet_gone"         : "⚠️ Saved number not found. Please enter a new one:",
        "bep20_invalid"       : "⚠️ Invalid address! Enter a valid BEP-20 wallet address.\n<code>0x</code> + 40 hex characters (total 42).",
        "proof_ask"           : (
            "📤 <b>Coin Transfer Instructions</b>\n"
            f"{LINE}\n"
            "Send <b>{{qty}}</b> coins to this username now:\n\n"
            "👉  <code>{{username}}</code>\n"
            f"{LINE}\n"
            "📸 After sending, share a <b>screenshot with timestamp</b>."
        ),
        "proof_not_photo"     : "⚠️ Please send a screenshot (photo).",
        "order_submitted"     : (
            "🎊 <b>Order submitted successfully!</b>\n"
            f"{LINE}\n"
            "🆔 Serial No:  <b>#{{order_id}}</b>\n"
            "⏳ Status:  <b>Under Review...</b>\n"
            f"{LINE}\n"
            "✨ Payment will be sent after verification.\n"
            "📢 Join our channel to see live status & proof of all transactions 👉 {{channel}}\n\n"
            "Thank you for choosing us! 🙏"
        ),
        "cancelled"           : "❌ Order has been cancelled.",
        "session_expired"     : "⚠️ Session expired. Please start again.",
        "already_processed"   : "ℹ️ This order has already been processed.",
        "admin_new_order"     : (
            "🔔 <b>New Order Received!</b>\n"
            f"{LINE}\n"
            "👤 Customer:  <b>{{name}}</b>{{uname}}\n"
            "💰 Coins:       <b>{{qty}}</b>\n"
            "📊 Rate:         <b>{{rate}}৳</b> / 1000\n"
            "💵 Total:        <b>{{taka}}৳</b>\n"
            "{{icon}} {{method}}: <code>{{masked_num}}</code>\n"
            "🆔 Order:      <b>#{{order_id}}</b>\n"
            "🕐 Time:        {{time}}\n"
            f"{LINE}\n"
            "⏳ Status: <b>Pending</b>"
        ),
        "paid_user"           : (
            "🎉 <b>Payment Completed!</b>\n"
            f"{LINE}\n"
            "✅ Payment for order <b>#{{order_id}}</b>\n"
            "has been sent successfully.\n"
            f"{LINE}\n"
            "🙏 Thank you. Come again! 💎"
        ),
        "paid_channel"        : (
            "╔══════════════════════╗\n"
            "║  ✅  PAYMENT DONE  ✅  ║\n"
            "╚══════════════════════╝\n\n"
            "👤 <b>Recipient</b>  :  {{name}}\n"
            "💰 <b>Coins</b>        :  {{qty}}\n"
            "💵 <b>Amount</b>    :  <b>{{taka}}৳</b>  ({{icon}} {{method}})\n"
            "📱 <b>Number</b>    :  <code>{{masked_num}}</code>\n"
            "🆔 <b>Order</b>       :  #{{order_id}}\n"
            "🕐 <b>Time</b>         :  {{time}}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🏦  <i>NS Coin Sell — Fast & Secure Service</i>"
        ),
        "reject_user"         : (
            "😔 <b>Order Rejected</b>\n"
            f"{LINE}\n"
            "❌ Your order <b>#{{order_id}}</b> has been rejected.\n\n"
            "🎧 If you have any questions, please contact support."
        ),
        "reject_channel"      : (
            "╔══════════════════════╗\n"
            "║  ❌  ORDER REJECTED  ❌  ║\n"
            "╚══════════════════════╝\n\n"
            "👤 <b>Customer</b>   :  {{name}}\n"
            "💰 <b>Coins</b>         :  {{qty}}\n"
            "💵 <b>Amount</b>     :  {{taka}}৳  ({{icon}} {{method}})\n"
            "📱 <b>Number</b>     :  <code>{{masked_num}}</code>\n"
            "🆔 <b>Order</b>        :  #{{order_id}}\n"
            "🕐 <b>Time</b>          :  {{time}}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🏦  <i>NS Coin Sell — Fast & Secure Service</i>"
        ),
        "admin_paid_confirm"  : "✅ Order <b>#{{order_id}}</b> — Marked as Paid.",
        "admin_reject_confirm": "❌ Order <b>#{{order_id}}</b> — Rejected.",
    },
}

# ================================================================
#                        🗄️ DATABASE LAYER
# ================================================================
def get_conn():
    return sqlite3.connect(DB_PATH)

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            order_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER,
            username      TEXT,
            coin_qty      TEXT,
            rate          TEXT,
            taka_amount   TEXT,
            method        TEXT,
            number        TEXT,
            status        TEXT DEFAULT 'pending',
            created_at    TEXT,
            photo_file_id TEXT
        )
    """)
    # Migration: add photo_file_id if the table already existed without it
    c.execute("PRAGMA table_info(orders)")
    existing_cols = [row[1] for row in c.fetchall()]
    if "photo_file_id" not in existing_cols:
        c.execute("ALTER TABLE orders ADD COLUMN photo_file_id TEXT")
    c.execute("""
        CREATE TABLE IF NOT EXISTS wallets (
            user_id INTEGER,
            method  TEXT,
            number  TEXT,
            PRIMARY KEY (user_id, method)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id      INTEGER PRIMARY KEY,
            lang         TEXT DEFAULT 'bn',
            referrer_id  INTEGER,
            balance      REAL DEFAULT 0,
            total_earned REAL DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            withdraw_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            amount      REAL,
            method      TEXT,
            number      TEXT,
            status      TEXT DEFAULT 'pending',
            created_at  TEXT
        )
    """)
    # Graceful migrations for existing DBs
    for col, defval in [
        ("referrer_id",  "INTEGER"),
        ("balance",      "REAL DEFAULT 0"),
        ("total_earned", "REAL DEFAULT 0"),
    ]:
        try:
            c.execute(f"ALTER TABLE users ADD COLUMN {col} {defval}")
        except Exception:
            pass
    conn.commit()

    for key, val in [
        ("price_tiers",        json.dumps(DEFAULT_PRICE_TIERS)),
        ("maintenance_on",     "0"),
        ("maintenance_msg_bn", DEFAULT_MAINTENANCE_MSG_BN),
        ("maintenance_msg_en", DEFAULT_MAINTENANCE_MSG_EN),
        ("payment_methods",    json.dumps({"bKash": True, "Nagad": True, "BEP-20": False})),
        ("announcement",       ""),
        ("referral_bonus",     "10"),
        ("min_withdraw",       "50"),
    ]:
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, val))
    conn.commit()
    conn.close()

# ── Settings ──
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
    c.execute("INSERT INTO settings (key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (key, value))
    conn.commit()
    conn.close()

def get_price_tiers():
    raw = get_setting("price_tiers", json.dumps(DEFAULT_PRICE_TIERS))
    return sorted([tuple(tier) for tier in json.loads(raw)], key=lambda x: -x[0])

def set_price_tiers(tiers):
    set_setting("price_tiers", json.dumps(sorted(tiers, key=lambda x: -x[0])))

def is_maintenance():
    return get_setting("maintenance_on", "0") == "1"

def set_maintenance(on: bool):
    set_setting("maintenance_on", "1" if on else "0")

def get_maintenance_msg(lang="bn"):
    key = "maintenance_msg_bn" if lang == "bn" else "maintenance_msg_en"
    return get_setting(key, DEFAULT_MAINTENANCE_MSG_BN if lang == "bn" else DEFAULT_MAINTENANCE_MSG_EN)

# ── Payment Methods ──
def get_payment_methods() -> dict:
    raw = get_setting("payment_methods", json.dumps({"bKash": True, "Nagad": True, "BEP-20": False}))
    return json.loads(raw)

def set_payment_method(method: str, enabled: bool):
    methods = get_payment_methods()
    methods[method] = enabled
    set_setting("payment_methods", json.dumps(methods))

def get_enabled_methods() -> list:
    methods = get_payment_methods()
    return [m for m, on in methods.items() if on]

# ── Announcement ──
def get_announcement() -> str:
    return get_setting("announcement", "")

def set_announcement(text: str):
    set_setting("announcement", text)

# ── Referral & Balance ──
def get_referral_bonus() -> float:
    return float(get_setting("referral_bonus", "5"))

def get_min_withdraw() -> float:
    return float(get_setting("min_withdraw", "50"))

def get_max_order_qty() -> float:
    """০ মানে কোনো লিমিট নেই — একবারে যত ইচ্ছা কয়েন সেল করা যাবে।"""
    return float(get_setting("max_order_qty", "0"))

def get_daily_sell_limit() -> float:
    """০ মানে কোনো লিমিট নেই — প্রতিদিন যত ইচ্ছা কয়েন সেল হতে পারবে।"""
    return float(get_setting("daily_sell_limit", "0"))

def get_today_sold_qty() -> float:
    conn = get_conn()
    c = conn.cursor()
    today = datetime.utcnow().strftime("%Y-%m-%d")
    c.execute("""
        SELECT SUM(CAST(coin_qty AS REAL)) FROM orders
        WHERE status IN ('paid','pending') AND substr(created_at,1,10)=?
    """, (today,))
    total = c.fetchone()[0]
    conn.close()
    return total or 0

def get_support_username() -> str:
    return get_setting("support_username", SUPPORT_USERNAME)

def get_receive_username() -> str:
    return get_setting("receive_username", RECEIVE_USERNAME)

def get_balance(user_id: int) -> float:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0.0

def get_total_earned(user_id: int) -> float:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT total_earned FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0.0

def add_balance(user_id: int, amount: float):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE users SET balance = balance + ?, total_earned = total_earned + ? WHERE user_id=?",
              (amount, amount, user_id))
    conn.commit()
    conn.close()

def deduct_balance(user_id: int, amount: float):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE users SET balance = MAX(0, balance - ?) WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()

def restore_balance(user_id: int, amount: float):
    """Restore balance after a rejected withdrawal."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()

def set_referrer(user_id: int, referrer_id: int) -> bool:
    """Set referrer only if not already set. Returns True if newly set."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE users SET referrer_id=? WHERE user_id=? AND referrer_id IS NULL",
        (referrer_id, user_id)
    )
    affected = c.rowcount
    conn.commit()
    conn.close()
    return affected > 0

def get_referrer(user_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT referrer_id FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row and row[0] else None

def count_paid_orders(user_id: int) -> int:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM orders WHERE user_id=? AND status='paid'", (user_id,))
    count = c.fetchone()[0]
    conn.close()
    return count

def get_referral_count(user_id: int) -> int:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users WHERE referrer_id=?", (user_id,))
    count = c.fetchone()[0]
    conn.close()
    return count

# ── Withdrawals ──
def save_withdrawal(user_id: int, amount: float, method: str, number: str) -> int:
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO withdrawals (user_id, amount, method, number, created_at) VALUES(?,?,?,?,?)",
        (user_id, amount, method, number, datetime.utcnow().isoformat())
    )
    conn.commit()
    wid = c.lastrowid
    conn.close()
    return wid

def get_withdrawal(wid: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM withdrawals WHERE withdraw_id=?", (wid,))
    row = c.fetchone()
    conn.close()
    return row

def update_withdrawal_status(wid: int, status: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE withdrawals SET status=? WHERE withdraw_id=?", (status, wid))
    conn.commit()
    conn.close()

def get_pending_withdrawals():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM withdrawals WHERE status='pending' ORDER BY withdraw_id DESC")
    rows = c.fetchall()
    conn.close()
    return rows

# ── Stats ──
def get_top_sellers(limit: int = 10) -> list:
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT user_id, username, SUM(CAST(coin_qty AS REAL)) AS total_qty
        FROM orders
        WHERE status='paid'
        GROUP BY user_id
        ORDER BY total_qty DESC
        LIMIT ?
    """, (limit,))
    rows = c.fetchall()
    conn.close()
    return [(uid, uname, int(total_qty)) for uid, uname, total_qty in rows]


    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM orders")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM orders WHERE status='pending'")
    pending = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM orders WHERE status='paid'")
    paid = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM orders WHERE status='rejected'")
    rejected = c.fetchone()[0]
    c.execute("SELECT SUM(CAST(taka_amount AS REAL)) FROM orders WHERE status='paid'")
    revenue = c.fetchone()[0] or 0
    c.execute("SELECT SUM(CAST(coin_qty AS REAL)) FROM orders WHERE status='paid'")
    coins_sold = c.fetchone()[0] or 0
    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM withdrawals WHERE status='pending'")
    pending_w = c.fetchone()[0]
    c.execute("SELECT SUM(amount) FROM withdrawals WHERE status='paid'")
    total_withdrawn = c.fetchone()[0] or 0
    conn.close()
    return {
        "total": total, "pending": pending, "paid": paid,
        "rejected": rejected, "revenue": round(revenue, 2),
        "coins_sold": int(coins_sold), "users": total_users,
        "pending_withdrawals": pending_w,
        "total_withdrawn": round(total_withdrawn, 2),
    }

# ── Broadcast ──
def get_all_user_ids() -> list:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]

# ── Orders ──
def save_order(user_id, username, coin_qty, rate, taka_amount, method, number, photo_file_id=None):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO orders (user_id,username,coin_qty,rate,taka_amount,method,number,created_at,photo_file_id) VALUES(?,?,?,?,?,?,?,?,?)",
        (user_id, username, coin_qty, rate, taka_amount, method, number, datetime.utcnow().isoformat(), photo_file_id)
    )
    conn.commit()
    order_id = c.lastrowid
    conn.close()
    return order_id

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

def get_user_orders(user_id, limit=5):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT order_id,coin_qty,taka_amount,method,status,created_at FROM orders WHERE user_id=? ORDER BY order_id DESC LIMIT ?",
        (user_id, limit)
    )
    rows = c.fetchall()
    conn.close()
    return rows

# ── Wallets ──
def save_wallet(user_id, method, number):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO wallets(user_id,method,number) VALUES(?,?,?) ON CONFLICT(user_id,method) DO UPDATE SET number=excluded.number",
              (user_id, method, number))
    conn.commit()
    conn.close()

def get_wallet(user_id, method):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT number FROM wallets WHERE user_id=? AND method=?", (user_id, method))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def get_all_wallets(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT method,number FROM wallets WHERE user_id=?", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

# ── User language ──
def get_user_lang(user_id: int) -> str:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else "bn"

def set_user_lang(user_id: int, lang: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO users(user_id,lang) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET lang=excluded.lang",
              (user_id, lang))
    conn.commit()
    conn.close()

def ensure_user_exists(user_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users(user_id, lang) VALUES(?, 'bn')", (user_id,))
    conn.commit()
    conn.close()

# ================================================================
#                         🛠️ HELPERS
# ================================================================
def t(user_id: int, key: str, **kwargs) -> str:
    lang = get_user_lang(user_id)
    text = LANG[lang].get(key, LANG["bn"].get(key, key))
    for k, v in kwargs.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text

def get_rate(qty):
    for threshold, rate in get_price_tiers():
        if qty >= threshold:
            return rate
    return None

def calc_taka(qty, rate):
    return round((qty / 1000) * rate, 2)

def mask_number(number):
    if len(number) <= 6:
        return number
    if number.startswith("0x"):
        return number[:6] + "..." + number[-4:]
    return number[:3] + "*" * (len(number) - 6) + number[-3:]

def price_list_text(user_id: int) -> str:
    tiers = sorted(get_price_tiers(), key=lambda x: x[0])
    lang = get_user_lang(user_id)
    if lang == "bn":
        return "\n".join([f"✅ <b>{tier[0]:,}+</b> কয়েন  ➜  <b>{tier[1]}৳</b>" for tier in tiers])
    else:
        return "\n".join([f"✅ <b>{tier[0]:,}+</b> coins  ➜  <b>{tier[1]}৳</b>" for tier in tiers])

MENU_BUTTONS = [
    ("order",       "btn_order"),
    ("price",       "btn_price"),
    ("wallet",      "btn_wallet"),
    ("history",     "btn_history"),
    ("support",     "btn_support"),
    ("referral",    "btn_referral"),
    ("balance",     "btn_balance"),
    ("rules",       "btn_rules"),
    ("leaderboard", "btn_leaderboard"),
    ("lang",        "btn_lang"),
]

def get_menu_button_enabled(key: str) -> bool:
    return get_setting(f"menu_btn_{key}", "1") == "1"

def set_menu_button_enabled(key: str, enabled: bool):
    set_setting(f"menu_btn_{key}", "1" if enabled else "0")

def menu_toggle_keyboard():
    buttons = []
    for key, label_key in MENU_BUTTONS:
        label = LANG["bn"][label_key]
        state = "✅" if get_menu_button_enabled(key) else "❌"
        buttons.append([InlineKeyboardButton(f"{state} {label}", callback_data=f"toggle_menu_{key}")])
    return InlineKeyboardMarkup(buttons)

def build_main_menu(user_id: int) -> ReplyKeyboardMarkup:
    lang = get_user_lang(user_id)
    L = LANG[lang]
    active = [KeyboardButton(L[label_key]) for key, label_key in MENU_BUTTONS if get_menu_button_enabled(key)]
    if not active:
        active = [KeyboardButton(L["btn_order"])]  # সবগুলো বন্ধ থাকলেও অন্তত Sell বাটন থাকবে
    rows = [active[i:i + 2] for i in range(0, len(active), 2)]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def order_cancel_menu(user_id: int) -> ReplyKeyboardMarkup:
    L = LANG[get_user_lang(user_id)]
    return ReplyKeyboardMarkup(
        [[KeyboardButton(L["btn_cancel_order"])]],
        resize_keyboard=True
    )

def order_confirm_cancel_menu(user_id: int) -> ReplyKeyboardMarkup:
    L = LANG[get_user_lang(user_id)]
    return ReplyKeyboardMarkup(
        [[KeyboardButton(L["btn_confirm_cancel"]), KeyboardButton(L["btn_continue_order"])]],
        resize_keyboard=True
    )

def method_icon(method: str) -> str:
    return {"bKash": "💗", "Nagad": "🟠", "BEP-20": "🪙"}.get(method, "💰")

_BEP20_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

async def check_channel_member(bot, user_id: int):
    """Returns True only if the user has joined ALL channels in REQUIRED_CHANNELS.
    Returns False if any channel is not joined, None if a definitive check
    couldn't be made (e.g. bot isn't admin in that channel)."""
    for channel in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status in ("left", "kicked"):
                return False
        except telegram.error.BadRequest as e:
            logging.info(f"check_channel_member BadRequest uid={user_id} channel={channel}: {e}")
            return False
        except telegram.error.Forbidden as e:
            logging.warning(f"check_channel_member Forbidden channel={channel}: {e}")
            return None
        except Exception as e:
            logging.warning(f"check_channel_member error uid={user_id} channel={channel}: {e}")
            return None
    return True

def join_keyboard(L):
    buttons = [
        [InlineKeyboardButton(f"{L['join_btn']} ({ch.lstrip('@')})", url=f"https://t.me/{ch.lstrip('@')}")]
        for ch in REQUIRED_CHANNELS
    ]
    buttons.append([InlineKeyboardButton(L["join_check_btn"], callback_data="check_join")])
    return InlineKeyboardMarkup(buttons)

def now_str():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

# ================================================================
#               CONVERSATION STATES
# ================================================================
QTY, METHOD, WALLET_CHOICE, NUMBER, PROOF = range(5)
ADMIN_RATE_INPUT, ADMIN_MSG_INPUT, ADMIN_ANNOUNCE_INPUT, ADMIN_BROADCAST_INPUT, ADMIN_SETTING_INPUT, ADMIN_RULES_INPUT, ADMIN_USERNAME_INPUT = range(100, 107)
WITHDRAW_METHOD, WITHDRAW_NUMBER, WITHDRAW_WALLET_CHOICE = range(200, 203)

# ================================================================
#               GUARDS: MAINTENANCE & CHANNEL JOIN
# ================================================================
async def maintenance_gate_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    uid = update.effective_user.id if update.effective_user else None
    if uid == ADMIN_ID:
        return
    if is_maintenance():
        lang = get_user_lang(uid) if uid else "bn"
        await update.message.reply_text(
            get_maintenance_msg(lang), parse_mode="HTML", reply_markup=ReplyKeyboardRemove()
        )
        raise ApplicationHandlerStop()

async def maintenance_gate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id if update.effective_user else None
    if uid == ADMIN_ID:
        return
    if is_maintenance():
        lang = get_user_lang(uid) if uid else "bn"
        await update.callback_query.answer(
            "🔧 Bot is under maintenance." if lang == "en"
            else "🔧 বট মেইনটেন্যান্সে আছে।",
            show_alert=True
        )
        raise ApplicationHandlerStop()

# ================================================================
#               HANDLERS: START & LANGUAGE
# ================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user_exists(uid)

    # Handle referral arg: /start ref_USERID
    if context.args:
        arg = context.args[0]
        if arg.startswith("ref_"):
            try:
                referrer_id = int(arg[4:])
                if referrer_id != uid:
                    newly_set = set_referrer(uid, referrer_id)
                    if newly_set:
                        # No bonus yet — bonus is credited only after this user's
                        # first successful (paid) coin sale. See button_handler().
                        try:
                            await context.bot.send_message(
                                chat_id=referrer_id,
                                text=LANG[get_user_lang(referrer_id)]["referral_joined_notify"],
                                parse_mode="HTML"
                            )
                        except Exception:
                            pass
            except ValueError:
                pass

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🇧🇩 বাংলা",  callback_data="setlang_bn"),
        InlineKeyboardButton("🇬🇧 English", callback_data="setlang_en"),
    ]])
    await update.message.reply_text(
        LANG["bn"]["pick_lang"], parse_mode="HTML", reply_markup=keyboard
    )

async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    lang = query.data.split("_")[1]
    set_user_lang(uid, lang)
    L = LANG[lang]

    if uid != ADMIN_ID:
        result = await check_channel_member(context.bot, uid)
        if result is None:
            await query.edit_message_text(L["join_check_error"], parse_mode="HTML")
            return
        if not result:
            keyboard = join_keyboard(L)
            await query.edit_message_text(L["join_required"], parse_mode="HTML", reply_markup=keyboard)
            return

    await query.edit_message_text(L["lang_set"], parse_mode="HTML")
    await _send_welcome(query.message.chat_id, uid, context)

async def check_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    L = LANG[get_user_lang(uid)]
    result = await check_channel_member(context.bot, uid)
    if result is None:
        await query.edit_message_text(L["join_check_error"], parse_mode="HTML")
        return
    if result:
        await query.edit_message_text(L["join_ok"], parse_mode="HTML")
        await _send_welcome(query.message.chat_id, uid, context)
    else:
        keyboard = join_keyboard(L)
        await query.edit_message_text(
            L["join_required"] + "\n\n" + L["join_fail"],
            parse_mode="HTML", reply_markup=keyboard
        )

async def change_lang_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🇧🇩 বাংলা",  callback_data="setlang_bn"),
        InlineKeyboardButton("🇬🇧 English", callback_data="setlang_en"),
    ]])
    await update.message.reply_text("🌐 Select language / ভাষা পরিবর্তন করুন:", reply_markup=keyboard)

async def _send_welcome(chat_id: int, uid: int, context):
    base = t(uid, "welcome", price_list=price_list_text(uid))
    announcement = get_announcement().strip()
    full_text = f"📣 <b>নোটিশ / Notice</b>\n{LINE}\n{announcement}\n{LINE}\n\n{base}" if announcement else base
    await context.bot.send_message(
        chat_id=chat_id, text=full_text, parse_mode="HTML", reply_markup=build_main_menu(uid)
    )

async def _ensure_joined(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id
    if uid == ADMIN_ID:
        return True
    result = await check_channel_member(context.bot, uid)
    L = LANG[get_user_lang(uid)]
    if result is None:
        await update.message.reply_text(L["join_check_error"], parse_mode="HTML")
        return False
    if not result:
        keyboard = join_keyboard(L)
        await update.message.reply_text(L["join_required"], parse_mode="HTML", reply_markup=keyboard)
        return False
    return True

# ================================================================
#               HANDLERS: MAIN MENU
# ================================================================
async def price_list_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await _ensure_joined(update, context):
        return
    base = t(uid, "price_header", price_list=price_list_text(uid))
    announcement = get_announcement().strip()
    text = f"📣 <b>নোটিশ / Notice</b>\n{LINE}\n{announcement}\n{LINE}\n\n{base}" if announcement else base
    await update.message.reply_text(text, parse_mode="HTML")

async def wallet_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await _ensure_joined(update, context):
        return
    wallets = get_all_wallets(uid)
    if not wallets:
        await update.message.reply_text(t(uid, "wallet_empty"), parse_mode="HTML")
    else:
        lines = "\n".join([f"{method_icon(m)} <b>{m}</b>:  <code>{mask_number(n)}</code>" for m, n in wallets])
        await update.message.reply_text(t(uid, "wallet_header", wallets=lines), parse_mode="HTML")

async def history_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await _ensure_joined(update, context):
        return
    orders = get_user_orders(uid)
    if not orders:
        await update.message.reply_text(t(uid, "history_empty"))
        return
    status_icon = {"pending": "⏳", "paid": "✅", "rejected": "❌"}
    lines = []
    for oid, qty, taka, meth, status, created in orders:
        icon = status_icon.get(status, "⏳")
        lines.append(f"{icon} <b>#{oid}</b>  —  {int(float(qty)):,} coins  ➜  {taka}৳  ({meth})")
    await update.message.reply_text(t(uid, "history_header", orders="\n".join(lines)), parse_mode="HTML")

async def support_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await _ensure_joined(update, context):
        return
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(t(uid, "support_btn"), url=f"https://t.me/{get_support_username()}")
    ]])
    await update.message.reply_text(t(uid, "support_msg"), parse_mode="HTML", reply_markup=keyboard)

async def referral_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await _ensure_joined(update, context):
        return
    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start=ref_{uid}"
    count = get_referral_count(uid)
    bonus = get_referral_bonus()
    bonus_display = int(bonus) if bonus == int(bonus) else bonus
    await update.message.reply_text(
        t(uid, "referral_msg", link=link, count=count, bonus=bonus_display),
        parse_mode="HTML"
    )

async def leaderboard_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await _ensure_joined(update, context):
        return
    lang = get_user_lang(uid)
    top = get_top_sellers(10)
    if not top:
        text = "🏆 এখনো কোনো বিক্রি হয়নি। প্রথম হয়ে যান!" if lang == "bn" else "🏆 No sales yet. Be the first!"
        await update.message.reply_text(text)
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (seller_id, uname, total_qty) in enumerate(top):
        medal = medals[i] if i < 3 else f"{i+1}️⃣"
        display = f"@{uname}" if uname else f"User {seller_id}"
        unit = "কয়েন" if lang == "bn" else "coins"
        lines.append(f"{medal} <b>{display}</b>  —  ({total_qty:,} {unit})")
    header = "🏆 <b>টপ সেলার লিডারবোর্ড</b>" if lang == "bn" else "🏆 <b>Top Seller Leaderboard</b>"
    text = f"{header}\n{LINE}\n" + "\n".join(lines) + f"\n{LINE}"
    await update.message.reply_text(text, parse_mode="HTML")

async def rules_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await _ensure_joined(update, context):
        return
    lang = get_user_lang(uid)
    default_rules = DEFAULT_RULES_BN if lang == "bn" else DEFAULT_RULES_EN
    rules_text = get_setting("rules_text_" + lang, default_rules)
    await update.message.reply_text(rules_text, parse_mode="HTML")

async def balance_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await _ensure_joined(update, context):
        return
    balance = get_balance(uid)
    total_earned = get_total_earned(uid)
    ref_count = get_referral_count(uid)
    min_w = get_min_withdraw()
    min_w_display = int(min_w) if min_w == int(min_w) else min_w
    balance_display = round(balance, 2)
    earned_display = round(total_earned, 2)

    text = t(uid, "balance_msg",
             balance=balance_display, ref_count=ref_count,
             total_earned=earned_display, min_w=min_w_display)

    if balance >= min_w:
        lang = get_user_lang(uid)
        btn_label = "💸 Withdraw করুন" if lang == "bn" else "💸 Withdraw"
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(btn_label, callback_data="withdraw_start")
        ]])
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await update.message.reply_text(text, parse_mode="HTML")

# ================================================================
#               HANDLERS: WITHDRAW FLOW
# ================================================================
async def withdraw_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    balance = get_balance(uid)
    min_w = get_min_withdraw()

    if balance < min_w:
        lang = get_user_lang(uid)
        min_w_display = int(min_w) if min_w == int(min_w) else min_w
        await query.answer(
            f"Balance কম! কমপক্ষে {min_w_display}৳ দরকার।" if lang == "bn"
            else f"Balance too low! Need at least {min_w_display}৳.",
            show_alert=True
        )
        return ConversationHandler.END

    context.user_data["withdraw_amount"] = balance
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💗 bKash",  callback_data="wm_bKash")],
        [InlineKeyboardButton("🟠 Nagad",  callback_data="wm_Nagad")],
    ])
    await query.edit_message_text(
        t(uid, "withdraw_method_ask", amount=round(balance, 2)),
        parse_mode="HTML", reply_markup=keyboard
    )
    return WITHDRAW_METHOD

async def withdraw_method_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    method = query.data[3:]  # strip "wm_"
    context.user_data["withdraw_method"] = method
    icon = method_icon(method)

    saved = get_wallet(uid, method)
    if saved:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(t(uid, "withdraw_use_saved", masked=mask_number(saved)),
                                  callback_data="wuse_saved")],
            [InlineKeyboardButton(t(uid, "withdraw_new_number"), callback_data="wnew_number")],
        ])
        await query.edit_message_text(
            f"{icon} <b>{method}</b> — সংরক্ষিত নম্বর ব্যবহার করবেন?",
            parse_mode="HTML", reply_markup=keyboard
        )
        return WITHDRAW_WALLET_CHOICE
    else:
        await query.edit_message_text(
            t(uid, "withdraw_number_ask", method=method), parse_mode="HTML"
        )
        return WITHDRAW_NUMBER

async def withdraw_wallet_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    method = context.user_data.get("withdraw_method")

    if query.data == "wuse_saved":
        number = get_wallet(uid, method)
        if not number:
            await query.edit_message_text(t(uid, "wallet_gone"), parse_mode="HTML")
            return WITHDRAW_NUMBER
        context.user_data["withdraw_number"] = number
        return await _withdraw_confirm(query, uid, context)
    else:
        await query.edit_message_text(
            t(uid, "withdraw_number_ask", method=method), parse_mode="HTML"
        )
        return WITHDRAW_NUMBER

async def withdraw_get_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    if not (text.isdigit() and len(text) == 11 and text.startswith("01")):
        await update.message.reply_text(t(uid, "number_invalid"), parse_mode="HTML")
        return WITHDRAW_NUMBER
    context.user_data["withdraw_number"] = text
    method = context.user_data.get("withdraw_method")
    save_wallet(uid, method, text)

    amount = context.user_data.get("withdraw_amount", 0)
    icon = method_icon(method)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm", callback_data="wconfirm"),
         InlineKeyboardButton("❌ Cancel",  callback_data="wcancel")],
    ])
    await update.message.reply_text(
        t(uid, "withdraw_confirm", amount=round(amount, 2),
          icon=icon, method=method, number=text),
        parse_mode="HTML", reply_markup=keyboard
    )
    return WITHDRAW_WALLET_CHOICE

async def _withdraw_confirm(query_or_msg, uid: int, context):
    method = context.user_data.get("withdraw_method")
    number = context.user_data.get("withdraw_number")
    amount = context.user_data.get("withdraw_amount", 0)
    icon = method_icon(method)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm", callback_data="wconfirm"),
         InlineKeyboardButton("❌ Cancel",  callback_data="wcancel")],
    ])
    text = t(uid, "withdraw_confirm", amount=round(amount, 2),
             icon=icon, method=method, number=number)
    if hasattr(query_or_msg, "edit_message_text"):
        await query_or_msg.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await query_or_msg.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
    return WITHDRAW_WALLET_CHOICE

async def withdraw_confirm_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    if query.data == "wcancel":
        context.user_data.clear()
        lang = get_user_lang(uid)
        await query.edit_message_text("❌ Withdraw বাতিল করা হয়েছে।" if lang == "bn" else "❌ Withdrawal cancelled.")
        return ConversationHandler.END

    # Confirm
    amount = context.user_data.get("withdraw_amount", 0)
    method = context.user_data.get("withdraw_method")
    number = context.user_data.get("withdraw_number")

    # Deduct balance first
    deduct_balance(uid, amount)
    wid = save_withdrawal(uid, amount, method, number)
    context.user_data.clear()

    await query.edit_message_text(
        t(uid, "withdraw_submitted", amount=round(amount, 2), wid=wid),
        parse_mode="HTML"
    )

    # Notify admin
    user = query.from_user
    uname = f" (@{user.username})" if user.username else ""
    icon = method_icon(method)
    admin_text = (
        f"💸 <b>নতুন Withdraw Request!</b>\n{LINE}\n"
        f"👤 User: <b>{user.first_name}</b>{uname} [<code>{uid}</code>]\n"
        f"💵 পরিমাণ: <b>{round(amount, 2)}৳</b>\n"
        f"{icon} মাধ্যম: <b>{method}</b>\n"
        f"📱 নম্বর: <code>{number}</code>\n"
        f"🆔 Request ID: <b>#W{wid}</b>\n"
        f"🕐 সময়: {now_str()}"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"appw_{wid}"),
        InlineKeyboardButton("❌ Reject",  callback_data=f"rejw_{wid}"),
    ]])
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID, text=admin_text, parse_mode="HTML", reply_markup=keyboard
        )
    except Exception as e:
        logging.error(f"Admin withdraw notify error: {e}")

    return ConversationHandler.END

async def withdraw_cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    context.user_data.clear()
    lang = get_user_lang(uid)
    await update.message.reply_text(
        "❌ Withdraw বাতিল করা হয়েছে।" if lang == "bn" else "❌ Withdrawal cancelled.",
        reply_markup=build_main_menu(uid)
    )
    return ConversationHandler.END

# ================================================================
#               HANDLERS: ORDER FLOW
# ================================================================
async def order_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    context.user_data["order_user_id"] = uid  # stored for timeout fallback
    if not await _ensure_joined(update, context):
        return ConversationHandler.END
    enabled = get_enabled_methods()
    if not enabled:
        await update.message.reply_text(t(uid, "no_methods"), parse_mode="HTML")
        return ConversationHandler.END
    await update.message.reply_text(
        t(uid, "order_qty_ask", price_list=price_list_text(uid)),
        parse_mode="HTML", reply_markup=order_cancel_menu(uid)
    )
    return QTY

async def handle_order_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    L = LANG[get_user_lang(uid)]
    if text == L["btn_cancel_order"]:
        await update.message.reply_text(L["confirm_cancel_ask"], reply_markup=order_confirm_cancel_menu(uid))
        return QTY
    if text == L["btn_confirm_cancel"]:
        context.user_data.clear()
        await update.message.reply_text(L["cancel_done"], reply_markup=build_main_menu(uid))
        return ConversationHandler.END
    if text == L["btn_continue_order"]:
        await update.message.reply_text(L["continue_done"], reply_markup=order_cancel_menu(uid))
        return QTY
    return await get_qty(update, context)

async def get_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.replace(",", "").strip()
    if not text.isdigit():
        await update.message.reply_text(t(uid, "order_qty_invalid"), parse_mode="HTML",
                                        reply_markup=order_cancel_menu(uid))
        return QTY
    qty = int(text)
    rate = get_rate(qty)
    if rate is None:
        min_qty = get_price_tiers()[-1][0]
        await update.message.reply_text(t(uid, "order_qty_low", min_qty=f"{min_qty:,}"),
                                        parse_mode="HTML", reply_markup=order_cancel_menu(uid))
        return QTY
    max_qty = get_max_order_qty()
    if max_qty > 0 and qty > max_qty:
        await update.message.reply_text(t(uid, "order_qty_max", max_qty=f"{int(max_qty):,}"),
                                        parse_mode="HTML", reply_markup=order_cancel_menu(uid))
        return QTY
    daily_limit = get_daily_sell_limit()
    if daily_limit > 0 and (get_today_sold_qty() + qty) > daily_limit:
        await update.message.reply_text(t(uid, "order_daily_limit_reached"),
                                        parse_mode="HTML", reply_markup=order_cancel_menu(uid))
        return QTY
    taka = calc_taka(qty, rate)
    context.user_data.update({"qty": qty, "rate": rate, "taka": taka})
    enabled = get_enabled_methods()
    method_labels = {
        "bKash":  ("💗", "bKash Personal"),
        "Nagad":  ("🟠", "Nagad Personal"),
        "BEP-20": ("🪙", "BEP-20 (USDT/BNB)"),
    }
    buttons = [
        [InlineKeyboardButton(f"{method_labels[m][0]} {method_labels[m][1]}", callback_data=f"method_{m}")]
        for m in enabled if m in method_labels
    ]
    await update.message.reply_text(
        t(uid, "order_summary", qty=f"{qty:,}", rate=rate, taka=f"{taka:,}"),
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons)
    )
    return METHOD

async def method_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    method = query.data.split("_", 1)[1]
    context.user_data["method"] = method
    icon = method_icon(method)
    if method == "BEP-20":
        await query.edit_message_text(t(uid, "ask_bep20"), parse_mode="HTML")
        return NUMBER
    saved = get_wallet(uid, method)
    if saved:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(t(uid, "btn_use_saved", masked=mask_number(saved)), callback_data="usesaved")],
            [InlineKeyboardButton(t(uid, "btn_new_number"), callback_data="newnumber")],
        ])
        await query.edit_message_text(t(uid, "method_selected", icon=icon, method=method),
                                      parse_mode="HTML", reply_markup=keyboard)
        return WALLET_CHOICE
    else:
        await query.edit_message_text(t(uid, "method_new_ask", icon=icon, method=method), parse_mode="HTML")
        return NUMBER

async def wallet_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    method = context.user_data.get("method")
    if not method:
        await query.edit_message_text(t(uid, "session_expired"), parse_mode="HTML")
        return ConversationHandler.END
    if query.data == "usesaved":
        number = get_wallet(uid, method)
        if not number:
            await query.edit_message_text(t(uid, "wallet_gone"), parse_mode="HTML")
            return NUMBER
        context.user_data["number"] = number
        d = context.user_data
        await query.edit_message_text(
            t(uid, "proof_ask", qty=f"{d['qty']:,}", username=get_receive_username()), parse_mode="HTML")
        return PROOF
    else:
        icon = method_icon(method)
        if method == "BEP-20":
            await query.edit_message_text(t(uid, "ask_bep20"), parse_mode="HTML")
        else:
            await query.edit_message_text(t(uid, "method_new_ask", icon=icon, method=method), parse_mode="HTML")
        return NUMBER

async def get_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    L = LANG[get_user_lang(uid)]
    if text in (L["btn_cancel_order"], L["btn_confirm_cancel"], L["btn_continue_order"]):
        return await handle_order_navigation(update, context)
    method = context.user_data.get("method")
    if not method:
        context.user_data.clear()
        await update.message.reply_text(t(uid, "session_expired"), reply_markup=build_main_menu(uid))
        return ConversationHandler.END
    if method in ("bKash", "Nagad"):
        if not (text.isdigit() and len(text) == 11 and text.startswith("01")):
            await update.message.reply_text(t(uid, "number_invalid"), parse_mode="HTML")
            return NUMBER
    elif method == "BEP-20":
        if not _BEP20_RE.match(text):
            await update.message.reply_text(t(uid, "bep20_invalid"), parse_mode="HTML")
            return NUMBER
    context.user_data["number"] = text
    save_wallet(uid, method, text)
    d = context.user_data
    await update.message.reply_text(
        t(uid, "proof_ask", qty=f"{d['qty']:,}", username=get_receive_username()), parse_mode="HTML")
    return PROOF

async def get_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not update.message.photo:
        await update.message.reply_text(t(uid, "proof_not_photo"))
        return PROOF
    d = context.user_data
    if not all(k in d for k in ["qty", "rate", "taka", "method", "number"]):
        context.user_data.clear()
        await update.message.reply_text(t(uid, "session_expired"), reply_markup=build_main_menu(uid))
        return ConversationHandler.END
    user = update.effective_user
    photo_file_id = update.message.photo[-1].file_id
    order_id = save_order(user.id, user.username or user.first_name,
                          d["qty"], d["rate"], d["taka"], d["method"], d["number"], photo_file_id)
    icon = method_icon(d["method"])
    uname_part = f" (@{user.username})" if user.username else ""
    caption = t(uid, "admin_new_order",
                name=user.first_name, uname=uname_part,
                qty=f"{d['qty']:,}", rate=d['rate'], taka=f"{d['taka']:,}",
                icon=icon, method=d['method'], masked_num=mask_number(d['number']),
                order_id=order_id, time=now_str())
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Paid",   callback_data=f"paid_{order_id}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"reject_{order_id}"),
    ]])
    await context.bot.send_photo(chat_id=ADMIN_ID, photo=photo_file_id,
                                 caption=caption, parse_mode="HTML", reply_markup=keyboard)
    await update.message.reply_text(
        t(uid, "order_submitted", order_id=order_id, channel=f"@{PAYMENT_CHANNEL_ID.lstrip('@')}"),
        parse_mode="HTML", reply_markup=build_main_menu(uid)
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    context.user_data.clear()
    await update.message.reply_text(t(uid, "cancelled"), reply_markup=build_main_menu(uid))
    return ConversationHandler.END

async def handle_conversation_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Priority: stored user ID (set at order_start) → effective_user from update
    uid = context.user_data.get("order_user_id")
    if not uid:
        try:
            if update and update.effective_user:
                uid = update.effective_user.id
        except Exception:
            pass

    if uid:
        try:
            lang = get_user_lang(uid)
            await context.bot.send_message(
                chat_id=uid,
                text=LANG[lang]["timeout_msg"],
                reply_markup=build_main_menu(uid)
            )
        except telegram.error.TelegramError as e:
            logging.warning("Timeout msg failed for uid=%s: %s", uid, e)
    context.user_data.clear()
    return ConversationHandler.END

# ================================================================
#               HANDLERS: ADMIN PAID / REJECT + WITHDRAW
# ================================================================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ আপনার অনুমতি নেই।", show_alert=True)
        return
    await query.answer()
    parts = query.data.split("_", 1)
    action = parts[0]
    id_str = parts[1]

    # ── Coin order paid/reject ──
    if action in ("paid", "reject"):
        order_id = int(id_str)
        order = get_order(order_id)
        if not order:
            await query.edit_message_caption(caption="❌ অর্ডার খুঁজে পাওয়া যায়নি।")
            return
        _, user_id, username, qty, rate, taka, method, number, status, created_at, photo_file_id = order
        if status != "pending":
            await query.answer("ℹ️ ইতোমধ্যে প্রসেস করা হয়েছে।", show_alert=True)
            return
        icon = method_icon(method)
        display_name = username or str(user_id)
        if action == "paid":
            update_status(order_id, "paid")
            channel_msg = t(user_id, "paid_channel",
                            name=display_name, qty=qty, taka=taka,
                            icon=icon, method=method, masked_num=mask_number(number),
                            order_id=order_id, time=now_str())
            await query.edit_message_caption(
                caption=t(user_id, "admin_paid_confirm", order_id=order_id), parse_mode="HTML")
            # Referral bonus: only credited once this buyer's FIRST sale is approved
            if count_paid_orders(user_id) == 1:
                referrer_id = get_referrer(user_id)
                if referrer_id:
                    bonus = get_referral_bonus()
                    add_balance(referrer_id, bonus)
                    try:
                        await context.bot.send_message(
                            chat_id=referrer_id,
                            text=LANG[get_user_lang(referrer_id)]["referral_bonus_notify"].replace(
                                "{{bonus}}", str(int(bonus) if bonus == int(bonus) else bonus)
                            ),
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
        else:
            update_status(order_id, "rejected")
            channel_msg = t(user_id, "reject_channel",
                            name=display_name, qty=qty, taka=taka,
                            icon=icon, method=method, masked_num=mask_number(number),
                            order_id=order_id, time=now_str())
            await query.edit_message_caption(
                caption=t(user_id, "admin_reject_confirm", order_id=order_id), parse_mode="HTML")
        try:
            await context.bot.send_message(chat_id=PAYMENT_CHANNEL_ID, text=channel_msg, parse_mode="HTML")
        except Exception as e:
            logging.error(f"Payment channel error: {e}")
        try:
            key = "paid_user" if action == "paid" else "reject_user"
            await context.bot.send_message(chat_id=user_id,
                                           text=t(user_id, key, order_id=order_id), parse_mode="HTML")
        except Exception:
            pass

    # ── Withdrawal approve/reject ──
    elif action in ("appw", "rejw"):
        wid = int(id_str)
        withdrawal = get_withdrawal(wid)
        if not withdrawal:
            await query.edit_message_text("❌ Withdrawal খুঁজে পাওয়া যায়নি।")
            return
        _, user_id, amount, method, number, w_status, created_at = withdrawal
        if w_status != "pending":
            await query.answer("ℹ️ ইতোমধ্যে প্রসেস করা হয়েছে।", show_alert=True)
            return
        icon = method_icon(method)
        if action == "appw":
            update_withdrawal_status(wid, "paid")
            await query.edit_message_text(
                f"✅ Withdrawal <b>#W{wid}</b> Approved!\n💵 {amount}৳ → {icon} {method}: <code>{number}</code>",
                parse_mode="HTML"
            )
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=t(user_id, "withdraw_paid_user",
                           amount=round(amount, 2), icon=icon, method=method, number=number),
                    parse_mode="HTML"
                )
            except Exception:
                pass
        else:
            update_withdrawal_status(wid, "rejected")
            restore_balance(user_id, amount)  # refund
            await query.edit_message_text(
                f"❌ Withdrawal <b>#W{wid}</b> Rejected. {amount}৳ balance ফেরত দেওয়া হয়েছে।",
                parse_mode="HTML"
            )
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=t(user_id, "withdraw_rejected_user", amount=round(amount, 2)),
                    parse_mode="HTML"
                )
            except Exception:
                pass

# ================================================================
#               ADMIN CONTROL PANEL
# ================================================================
# ================================================================
#      ADMIN BOTTOM MENU (persistent buttons, like the user menu)
# ================================================================
ABTN_RATE        = "📊 রেট পরিবর্তন"
ABTN_MAINT       = "🔧 Maintenance টগল"
ABTN_PAYMENT     = "💳 পেমেন্ট মেথড"
ABTN_ANNOUNCE    = "📣 Announcement"
ABTN_BROADCAST   = "📢 Broadcast"
ABTN_REF         = "⚙️ Referral/Withdraw সেটিংস"
ABTN_STATS       = "📈 Stats"
ABTN_WITHDRAWALS = "💸 Withdrawals"
ABTN_MAINTMSG_BN = "📝 Maintenance মেসেজ (BN)"
ABTN_MAINTMSG_EN = "📝 Maintenance Message (EN)"
ABTN_RULES_BN    = "📜 নিয়মাবলী (BN)"
ABTN_RULES_EN    = "📜 Rules (EN)"
ABTN_VIEW        = "ℹ️ সব সেটিংস দেখুন"
ABTN_SUPPORT_UN  = "🎧 Support Username"
ABTN_RECEIVE_UN  = "💳 Receive Username"
ABTN_BACKUP      = "💾 এখনই Backup নিন"
ABTN_MENU_TOGGLE = "🔘 User মেনু বাটন On/Off"
ABTN_EXIT        = "🔚 Admin মেনু থেকে বের হন"

def admin_reply_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(ABTN_RATE),        KeyboardButton(ABTN_MAINT)],
            [KeyboardButton(ABTN_PAYMENT),      KeyboardButton(ABTN_ANNOUNCE)],
            [KeyboardButton(ABTN_BROADCAST),    KeyboardButton(ABTN_REF)],
            [KeyboardButton(ABTN_STATS),        KeyboardButton(ABTN_WITHDRAWALS)],
            [KeyboardButton(ABTN_MAINTMSG_BN),  KeyboardButton(ABTN_MAINTMSG_EN)],
            [KeyboardButton(ABTN_RULES_BN),     KeyboardButton(ABTN_RULES_EN)],
            [KeyboardButton(ABTN_SUPPORT_UN),   KeyboardButton(ABTN_RECEIVE_UN)],
            [KeyboardButton(ABTN_VIEW),         KeyboardButton(ABTN_BACKUP)],
            [KeyboardButton(ABTN_MENU_TOGGLE)],
            [KeyboardButton(ABTN_EXIT)],
        ],
        resize_keyboard=True
    )

def admin_panel_keyboard():
    maint_label = "🟢 Maintenance চালু করুন" if not is_maintenance() else "🔴 Maintenance বন্ধ করুন"
    pending_w = len(get_pending_withdrawals())
    w_label = f"💸 Pending Withdrawals ({pending_w})" if pending_w else "💸 Withdrawals দেখুন"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 রেট পরিবর্তন করুন",             callback_data="admin_rate")],
        [InlineKeyboardButton(maint_label,                          callback_data="admin_toggle_maint")],
        [InlineKeyboardButton("💳 পেমেন্ট মেথড সেটিংস",          callback_data="admin_payment_methods")],
        [InlineKeyboardButton("📣 Announcement সেট করুন",         callback_data="admin_announce")],
        [InlineKeyboardButton("⚙️ Referral Bonus / Min Withdraw",  callback_data="admin_ref_settings")],
        [InlineKeyboardButton("📢 সবাইকে Broadcast করুন",         callback_data="admin_broadcast")],
        [InlineKeyboardButton("📈 Stats দেখুন",                   callback_data="admin_stats")],
        [InlineKeyboardButton(w_label,                              callback_data="admin_withdrawals")],
        [InlineKeyboardButton("📝 Maintenance মেসেজ (বাংলা)",    callback_data="admin_maintmsg_bn")],
        [InlineKeyboardButton("📝 Maintenance Message (English)",  callback_data="admin_maintmsg_en")],
        [InlineKeyboardButton("📜 নিয়মাবলী (বাংলা)",            callback_data="admin_rulesmsg_bn")],
        [InlineKeyboardButton("📜 Rules (English)",                callback_data="admin_rulesmsg_en")],
        [InlineKeyboardButton("ℹ️ বর্তমান সেটিংস দেখুন",          callback_data="admin_view")],
    ])

def payment_methods_keyboard():
    methods = get_payment_methods()
    labels = {"bKash": "💗 bKash", "Nagad": "🟠 Nagad", "BEP-20": "🪙 BEP-20 (Binance)"}
    buttons = []
    for method, enabled in methods.items():
        status = "✅ চালু" if enabled else "❌ বন্ধ"
        buttons.append([InlineKeyboardButton(f"{labels.get(method, method)}: {status}",
                                              callback_data=f"toggle_method_{method}")])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_back")])
    return InlineKeyboardMarkup(buttons)

async def admin_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ আপনার অনুমতি নেই।")
        return ConversationHandler.END
    await update.message.reply_text(
        "🛠️ <b>Admin Control Panel</b>\nনিচের মেনু থেকে যা পরিবর্তন করতে চান বেছে নিন 👇",
        parse_mode="HTML", reply_markup=admin_reply_keyboard()
    )
    return ConversationHandler.END

async def admin_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tiers = sorted(get_price_tiers(), key=lambda x: x[0])
    tiers_txt = "\n".join([f"  • {tier[0]:,}+ কয়েন  ➜  {tier[1]}৳ / ১০০০" for tier in tiers])
    maint = "🔴 চালু (ON)" if is_maintenance() else "🟢 বন্ধ (OFF)"
    methods = get_payment_methods()
    methods_txt = "\n".join([f"  • {'✅' if on else '❌'} {m}" for m, on in methods.items()])
    ann = get_announcement() or "(কোনো announcement নেই)"
    bonus = get_referral_bonus()
    min_w = get_min_withdraw()
    msg = (
        f"ℹ️ <b>বর্তমান সেটিংস</b>\n{LINE}\n"
        f"💹 রেট:\n{tiers_txt}\n{LINE}\n"
        f"🛠️ Maintenance: <b>{maint}</b>\n{LINE}\n"
        f"💳 পেমেন্ট মেথড:\n{methods_txt}\n{LINE}\n"
        f"🔗 Referral Bonus: <b>{int(bonus) if bonus==int(bonus) else bonus}৳</b>\n"
        f"💸 Min Withdraw: <b>{int(min_w) if min_w==int(min_w) else min_w}৳</b>\n{LINE}\n"
        f"📣 Announcement:\n{ann}"
    )
    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=admin_panel_keyboard())

async def admin_toggle_maint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    set_maintenance(not is_maintenance())
    state = "চালু ✅" if is_maintenance() else "বন্ধ ❌"
    await query.edit_message_text(
        f"🛠️ Maintenance Mode এখন <b>{state}</b>।",
        parse_mode="HTML", reply_markup=admin_panel_keyboard()
    )

async def admin_payment_methods(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "💳 <b>পেমেন্ট মেথড সেটিংস</b>\n\nযে মেথড চালু/বন্ধ করতে চান সেটিতে চাপুন:",
        parse_mode="HTML", reply_markup=payment_methods_keyboard()
    )

async def admin_toggle_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    method = query.data.split("toggle_method_", 1)[1]
    methods = get_payment_methods()
    if method in methods:
        new_state = not methods[method]
        set_payment_method(method, new_state)
        state_txt = "✅ চালু" if new_state else "❌ বন্ধ"
        await query.answer(f"{method}: {state_txt}", show_alert=True)
    await query.edit_message_text(
        "💳 <b>পেমেন্ট মেথড সেটিংস</b>\n\nযে মেথড চালু/বন্ধ করতে চান সেটিতে চাপুন:",
        parse_mode="HTML", reply_markup=payment_methods_keyboard()
    )

async def admin_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🛠️ <b>Admin Control Panel</b>",
        parse_mode="HTML", reply_markup=admin_panel_keyboard()
    )

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    s = get_stats()
    msg = (
        f"📈 <b>Bot Statistics</b>\n{LINE}\n"
        f"👥 মোট Users            :  <b>{s['users']:,}</b>\n"
        f"📦 মোট Orders           :  <b>{s['total']:,}</b>\n"
        f"{LINE}\n"
        f"⏳ Pending Orders      :  <b>{s['pending']}</b>\n"
        f"✅ Paid Orders           :  <b>{s['paid']}</b>\n"
        f"❌ Rejected Orders    :  <b>{s['rejected']}</b>\n"
        f"{LINE}\n"
        f"💰 মোট Revenue        :  <b>{s['revenue']:,}৳</b>\n"
        f"🪙 বিক্রিত কয়েন          :  <b>{s['coins_sold']:,}</b>\n"
        f"{LINE}\n"
        f"💸 Pending Withdrawals: <b>{s['pending_withdrawals']}</b>\n"
        f"💵 Total Withdrawn     :  <b>{s['total_withdrawn']:,}৳</b>"
    )
    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=admin_panel_keyboard())

async def admin_withdrawals_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pending = get_pending_withdrawals()
    if not pending:
        await query.edit_message_text(
            "✅ কোনো pending withdrawal নেই।",
            reply_markup=admin_panel_keyboard()
        )
        return
    lines = []
    buttons = []
    for row in pending[:10]:
        wid, uid, amount, method, number, status, created_at = row
        icon = method_icon(method)
        lines.append(f"<b>#W{wid}</b>  [{uid}]  {amount}৳  {icon}{method}  <code>{number}</code>")
        buttons.append([
            InlineKeyboardButton(f"✅ W{wid}", callback_data=f"appw_{wid}"),
            InlineKeyboardButton(f"❌ W{wid}", callback_data=f"rejw_{wid}"),
        ])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_back")])
    msg = f"💸 <b>Pending Withdrawals</b>\n{LINE}\n" + "\n".join(lines)
    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))

async def admin_announce_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    current = get_announcement() or "(কোনো announcement নেই)"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ প্রিমিয়াম ডিফল্ট Announcement পাঠান", callback_data="admin_announce_default")],
        [InlineKeyboardButton("✍️ নিজে লিখুন",                          callback_data="admin_announce_custom")],
        [InlineKeyboardButton("🔙 Back",                                  callback_data="admin_back")],
    ])
    await query.edit_message_text(
        f"📣 <b>বর্তমান Announcement:</b>\n{LINE}\n{current}\n{LINE}\n\n"
        f"👇 ডিফল্ট প্রিমিয়াম মেসেজ এক ট্যাপে পাঠাতে পারেন, অথবা নিজে লিখতে পারেন।\n\n"
        f"<b>ডিফল্ট প্রিভিউ:</b>\n{DEFAULT_ANNOUNCEMENT_BN}",
        parse_mode="HTML",
        reply_markup=keyboard
    )

async def admin_announce_send_default(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("✅ পাঠানো হয়েছে!")
    set_announcement(DEFAULT_ANNOUNCEMENT_BN)
    await query.edit_message_text(
        "✅ প্রিমিয়াম ডিফল্ট Announcement সেট হয়ে গেছে!\n\nএটি welcome ও price list-এ দেখাবে।",
        parse_mode="HTML"
    )
    await context.bot.send_message(chat_id=query.from_user.id, text="🛠️ Admin Panel:", reply_markup=admin_reply_keyboard())

async def admin_announce_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    current = get_announcement() or "(কোনো announcement নেই)"
    await query.edit_message_text(
        f"📣 <b>বর্তমান Announcement:</b>\n{LINE}\n{current}\n{LINE}\n\n"
        f"নতুন Announcement লিখুন (HTML সাপোর্ট করে)।\n"
        f"সরাতে চাইলে <code>clear</code> পাঠান।\n"
        f"বাতিল করতে /cancel লিখুন।",
        parse_mode="HTML"
    )
    return ADMIN_ANNOUNCE_INPUT

async def admin_announce_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.lower() == "clear":
        set_announcement("")
        await update.message.reply_text("✅ Announcement সরানো হয়েছে।")
    else:
        set_announcement(text)
        await update.message.reply_text(
            "✅ Announcement সেট হয়েছে!\n\nএটি welcome ও price list-এ দেখাবে।"
        )
    await update.message.reply_text("🛠️ Admin Panel:", reply_markup=admin_reply_keyboard())
    return ConversationHandler.END

async def admin_menu_ref(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bonus = get_referral_bonus()
    min_w = get_min_withdraw()
    max_qty = get_max_order_qty()
    daily_limit = get_daily_sell_limit()
    sold_today = get_today_sold_qty()
    await update.message.reply_text(
        f"⚙️ <b>Referral, Withdraw ও Sell Limit সেটিংস</b>\n{LINE}\n"
        f"🔗 Referral Bonus:  <b>{int(bonus) if bonus==int(bonus) else bonus}৳</b>\n"
        f"💸 Min Withdraw:   <b>{int(min_w) if min_w==int(min_w) else min_w}৳</b>\n"
        f"📦 Max Order (একবারে):  <b>{'Unlimited' if max_qty==0 else f'{int(max_qty):,} কয়েন'}</b>\n"
        f"📅 Daily Sell Limit:      <b>{'Unlimited' if daily_limit==0 else f'{int(daily_limit):,} কয়েন'}</b>\n"
        f"   (আজ বিক্রি হয়েছে: {int(sold_today):,} কয়েন)\n"
        f"{LINE}\n"
        f"নতুন সেটিং পাঠান এই ফরম্যাটে (যেকোনো একটা বা সবগুলো একসাথে, প্রতিটা নতুন লাইনে):\n"
        f"<code>bonus-10\nminwithdraw-50\nmaxorder-500000\ndailylimit-2000000</code>\n\n"
        f"💡 লিমিট বন্ধ করতে (unlimited করতে) মান হিসেবে <code>0</code> দিন।\n"
        f"বাতিল করতে /cancel লিখুন।",
        parse_mode="HTML"
    )
    return ADMIN_SETTING_INPUT

async def admin_ref_settings_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    bonus = get_referral_bonus()
    min_w = get_min_withdraw()
    max_qty = get_max_order_qty()
    daily_limit = get_daily_sell_limit()
    await query.edit_message_text(
        f"⚙️ <b>Referral, Withdraw ও Sell Limit সেটিংস</b>\n{LINE}\n"
        f"🔗 Referral Bonus:  <b>{int(bonus) if bonus==int(bonus) else bonus}৳</b>\n"
        f"💸 Min Withdraw:   <b>{int(min_w) if min_w==int(min_w) else min_w}৳</b>\n"
        f"📦 Max Order (একবারে):  <b>{'Unlimited' if max_qty==0 else f'{int(max_qty):,}'}</b>\n"
        f"📅 Daily Sell Limit:      <b>{'Unlimited' if daily_limit==0 else f'{int(daily_limit):,}'}</b>\n\n"
        f"{LINE}\n"
        f"নতুন সেটিং পাঠান এই ফরম্যাটে:\n"
        f"<code>bonus-10\nminwithdraw-50\nmaxorder-500000\ndailylimit-2000000</code>\n\n"
        f"(শুধু একটি পরিবর্তন করতে চাইলে শুধু সেই লাইনটি পাঠান। ০ দিলে লিমিট বন্ধ হয়ে যাবে।)\n"
        f"বাতিল করতে /cancel লিখুন।",
        parse_mode="HTML"
    )
    return ADMIN_SETTING_INPUT

async def admin_ref_settings_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    changed = []
    try:
        for line in text.splitlines():
            line = line.strip().lower()
            if not line:
                continue
            # সহজ, ফরম্যাট-মুক্ত পার্সিং: key আর value যেকোনো - / : / = দিয়ে আলাদা করা যাবে
            m = re.match(r'^([a-z_]+)\s*[-:=]\s*([\d.]+)$', line)
            if not m:
                continue
            key, val_str = m.group(1), float(m.group(2))
            if key in ("bonus", "referralbonus"):
                set_setting("referral_bonus", str(val_str))
                changed.append(f"✅ Referral Bonus: {val_str}৳")
            elif key in ("minwithdraw", "withdraw"):
                set_setting("min_withdraw", str(val_str))
                changed.append(f"✅ Min Withdraw: {val_str}৳")
            elif key in ("maxorder", "maxqty", "orderlimit"):
                set_setting("max_order_qty", str(val_str))
                changed.append(f"✅ Max Order: {'Unlimited' if val_str==0 else f'{int(val_str):,} কয়েন'}")
            elif key in ("dailylimit", "dailysell", "dailyqty"):
                set_setting("daily_sell_limit", str(val_str))
                changed.append(f"✅ Daily Sell Limit: {'Unlimited' if val_str==0 else f'{int(val_str):,} কয়েন'}")
    except Exception:
        await update.message.reply_text(
            "⚠️ ফরম্যাট ভুল।\n<code>bonus-10\nminwithdraw-50\nmaxorder-500000\ndailylimit-2000000</code>",
            parse_mode="HTML"
        )
        return ADMIN_SETTING_INPUT
    if changed:
        await update.message.reply_text("✅ আপডেট হয়েছে:\n" + "\n".join(changed))
    else:
        await update.message.reply_text("⚠️ কোনো পরিবর্তন হয়নি। ফরম্যাট চেক করুন।")
    await update.message.reply_text("🛠️ Admin Panel:", reply_markup=admin_reply_keyboard())
    return ConversationHandler.END

async def admin_broadcast_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_count = len(get_all_user_ids())
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ প্রিমিয়াম ডিফল্ট Broadcast পাঠান", callback_data="admin_broadcast_default")],
        [InlineKeyboardButton("✍️ নিজে লিখুন",                        callback_data="admin_broadcast_custom")],
        [InlineKeyboardButton("🔙 Back",                                callback_data="admin_back")],
    ])
    await query.edit_message_text(
        f"📢 <b>Broadcast Message</b>\n{LINE}\n"
        f"👥 মোট <b>{user_count}</b> জন user-কে message পাঠানো হবে।\n\n"
        f"👇 ডিফল্ট প্রিমিয়াম মেসেজ এক ট্যাপে পাঠাতে পারেন, অথবা নিজে লিখতে পারেন।\n\n"
        f"<b>ডিফল্ট প্রিভিউ:</b>\n{DEFAULT_BROADCAST_BN}",
        parse_mode="HTML",
        reply_markup=keyboard
    )

async def _run_broadcast(bot, chat_id, text):
    user_ids = get_all_user_ids()
    sent, failed = 0, 0
    status_msg = await bot.send_message(chat_id=chat_id, text=f"⏳ Broadcast শুরু... মোট {len(user_ids)} জন।")
    broadcast_text = f"📢 <b>NS Coin Sell — নোটিশ</b>\n{LINE}\n{text}"
    for uid in user_ids:
        try:
            await bot.send_message(chat_id=uid, text=broadcast_text, parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await status_msg.edit_text(
        f"✅ Broadcast সম্পন্ন!\n✔️ সফল: <b>{sent}</b>\n✖️ ব্যর্থ: <b>{failed}</b>",
        parse_mode="HTML"
    )

async def admin_broadcast_send_default(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("📢 পাঠানো শুরু হচ্ছে...")
    await query.edit_message_text("📢 প্রিমিয়াম ডিফল্ট Broadcast পাঠানো হচ্ছে...")
    await _run_broadcast(context.bot, query.from_user.id, DEFAULT_BROADCAST_BN)
    await context.bot.send_message(chat_id=query.from_user.id, text="🛠️ Admin Panel:", reply_markup=admin_reply_keyboard())

async def admin_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_count = len(get_all_user_ids())
    await query.edit_message_text(
        f"📢 <b>Broadcast Message</b>\n{LINE}\n"
        f"👥 মোট <b>{user_count}</b> জন user-কে message পাঠানো হবে।\n\n"
        f"এখন আপনার message লিখুন (HTML সাপোর্ট করে)।\n"
        f"বাতিল করতে /cancel লিখুন।",
        parse_mode="HTML"
    )
    return ADMIN_BROADCAST_INPUT

async def admin_broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    await _run_broadcast(context.bot, update.effective_chat.id, text)
    await update.message.reply_text("🛠️ Admin Panel:", reply_markup=admin_reply_keyboard())
    return ConversationHandler.END

def parse_amount_token(s):
    """Parses a number that may use k/K (thousand) or m/M (million) suffixes,
    e.g. '10k' -> 10000, '1.5m' -> 1500000, '10000' -> 10000."""
    s = s.strip().lower().replace(",", "")
    match = re.match(r'^(\d+(?:\.\d+)?)\s*(k|m)?$', s)
    if not match:
        raise ValueError(f"Invalid amount: {s}")
    num = float(match.group(1))
    suffix = match.group(2)
    if suffix == "k":
        num *= 1000
    elif suffix == "m":
        num *= 1000000
    return int(num)

def parse_rate_line(line):
    """Accepts flexible formats:
    - 'MIN-RATE'                  e.g. '10000-7.70'
    - 'MIN to MAX-RATE'           e.g. '10k to 500k-7.5'
    - 'MIN To MAX ➡️ RATE'         e.g. '10K To 500K ➡️ 7.50' (arrow/emoji separators)
    Only the minimum value is kept, since rates are stored as ascending
    minimum-threshold tiers."""
    line = line.strip()
    # Normalize any arrow-style separator (➡️, ➡, →, ->, =>) to a plain dash
    line = re.sub(r'\s*(➡️|➡|→|=>|->)\s*', '-', line)
    parts = line.rsplit("-", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid: {line}")
    left, rate_str = parts
    rate = float(rate_str.strip())
    first_token = re.split(r'\s+to\s+', left.strip(), flags=re.IGNORECASE)[0]
    min_amount = parse_amount_token(first_token)
    return (min_amount, rate)

async def admin_rate_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tiers = sorted(get_price_tiers(), key=lambda x: x[0])
    example = "\n".join([f"{tier[0]}-{tier[1]}" for tier in tiers])
    await query.edit_message_text(
        f"📊 <b>নতুন রেট টিয়ার পাঠান</b>\n{LINE}\n"
        f"ফরম্যাট:  <code>মিনিমাম_কয়েন-রেট</code>\n"
        f"অথবা রেঞ্জ:  <code>মিনিমাম to ম্যাক্সিমাম-রেট</code>\n"
        f"(k = হাজার, m = মিলিয়ন লেখা যাবে, যেমন <code>10k to 500k-7.5</code>)\n\n"
        f"বর্তমান রেট:\n<code>{example}</code>\n\n"
        f"পুরো লিস্ট একবারে পাঠান।\nবাতিল করতে /cancel লিখুন।",
        parse_mode="HTML"
    )
    return ADMIN_RATE_INPUT

async def admin_rate_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    new_tiers = []
    try:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            new_tiers.append(parse_rate_line(line))
        if not new_tiers:
            raise ValueError("Empty")
    except Exception:
        await update.message.reply_text(
            "⚠️ ফরম্যাট ভুল। উদাহরণ:\n"
            "<code>10000-7.70\n300000-7.75\n500000-7.90</code>\n\n"
            "অথবা রেঞ্জ আকারে:\n"
            "<code>10k to 500k-7.5\n500k to 10m-7.70</code>",
            parse_mode="HTML"
        )
        return ADMIN_RATE_INPUT
    set_price_tiers(new_tiers)
    await update.message.reply_text("✅ রেট আপডেট হয়েছে!")
    await update.message.reply_text("🛠️ Admin Panel:", reply_markup=admin_reply_keyboard())
    return ConversationHandler.END

async def admin_maintmsg_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang_key = query.data.split("_")[-1]
    context.user_data["maintmsg_lang"] = lang_key
    label = "বাংলা" if lang_key == "bn" else "English"
    await query.edit_message_text(
        f"📝 বর্তমান Maintenance মেসেজ ({label}):\n{LINE}\n{get_maintenance_msg(lang_key)}\n{LINE}\n\n"
        f"নতুন মেসেজ পাঠান (HTML সাপোর্ট করে)।\nবাতিল করতে /cancel লিখুন।",
        parse_mode="HTML"
    )
    return ADMIN_MSG_INPUT

async def admin_maintmsg_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang_key = context.user_data.get("maintmsg_lang", "bn")
    key = "maintenance_msg_bn" if lang_key == "bn" else "maintenance_msg_en"
    set_setting(key, update.message.text)
    label = "বাংলা" if lang_key == "bn" else "English"
    await update.message.reply_text(f"✅ Maintenance মেসেজ ({label}) আপডেট হয়েছে!")
    await update.message.reply_text("🛠️ Admin Panel:", reply_markup=admin_reply_keyboard())
    return ConversationHandler.END

async def admin_rulesmsg_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang_key = query.data.split("_")[-1]
    context.user_data["rulesmsg_lang"] = lang_key
    label = "বাংলা" if lang_key == "bn" else "English"
    default_rules = DEFAULT_RULES_BN if lang_key == "bn" else DEFAULT_RULES_EN
    current = get_setting("rules_text_" + lang_key, default_rules)
    await query.edit_message_text(
        f"📜 বর্তমান নিয়মাবলী ({label}):\n{LINE}\n{current}\n{LINE}\n\n"
        f"নতুন নিয়মাবলী পাঠান (HTML সাপোর্ট করে)।\n"
        f"ডিফল্ট প্রিমিয়াম ভার্সনে ফিরতে <code>reset</code> পাঠান।\n"
        f"বাতিল করতে /cancel লিখুন।",
        parse_mode="HTML"
    )
    return ADMIN_RULES_INPUT

async def admin_rulesmsg_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang_key = context.user_data.get("rulesmsg_lang", "bn")
    key = "rules_text_" + lang_key
    text = update.message.text.strip()
    label = "বাংলা" if lang_key == "bn" else "English"
    if text.lower() == "reset":
        default_rules = DEFAULT_RULES_BN if lang_key == "bn" else DEFAULT_RULES_EN
        set_setting(key, default_rules)
        await update.message.reply_text(f"✅ নিয়মাবলী ({label}) ডিফল্ট প্রিমিয়াম ভার্সনে ফিরিয়ে দেওয়া হয়েছে!")
    else:
        set_setting(key, text)
        await update.message.reply_text(f"✅ নিয়মাবলী ({label}) আপডেট হয়েছে!")
    await update.message.reply_text("🛠️ Admin Panel:", reply_markup=admin_reply_keyboard())
    return ConversationHandler.END

# ── Bottom-menu (ReplyKeyboard) admin action wrappers ──
async def admin_menu_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tiers = sorted(get_price_tiers(), key=lambda x: x[0])
    example = "\n".join([f"{tier[0]}-{tier[1]}" for tier in tiers])
    await update.message.reply_text(
        f"📊 <b>নতুন রেট টিয়ার পাঠান</b>\n{LINE}\n"
        f"ফরম্যাট:  <code>মিনিমাম_কয়েন-রেট</code>\n"
        f"অথবা রেঞ্জ:  <code>মিনিমাম to ম্যাক্সিমাম-রেট</code>\n"
        f"(k = হাজার, m = মিলিয়ন লেখা যাবে, যেমন <code>10k to 500k-7.5</code>)\n\n"
        f"বর্তমান রেট:\n<code>{example}</code>\n\n"
        f"পুরো লিস্ট একবারে পাঠান।\nবাতিল করতে /cancel লিখুন।",
        parse_mode="HTML"
    )
    return ADMIN_RATE_INPUT

async def admin_menu_maint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_maintenance(not is_maintenance())
    state = "চালু ✅" if is_maintenance() else "বন্ধ ❌"
    await update.message.reply_text(f"🛠️ Maintenance Mode এখন <b>{state}</b>।", parse_mode="HTML")
    return ConversationHandler.END

async def admin_menu_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💳 <b>পেমেন্ট মেথড সেটিংস</b>\n\nযে মেথড চালু/বন্ধ করতে চান সেটিতে চাপুন:",
        parse_mode="HTML", reply_markup=payment_methods_keyboard()
    )
    return ConversationHandler.END

async def admin_menu_announce(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = get_announcement() or "(কোনো announcement নেই)"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ প্রিমিয়াম ডিফল্ট Announcement পাঠান", callback_data="admin_announce_default")],
        [InlineKeyboardButton("✍️ নিজে লিখুন",                          callback_data="admin_announce_custom")],
    ])
    await update.message.reply_text(
        f"📣 <b>বর্তমান Announcement:</b>\n{LINE}\n{current}\n{LINE}\n\n"
        f"👇 ডিফল্ট প্রিমিয়াম মেসেজ এক ট্যাপে পাঠাতে পারেন, অথবা নিজে লিখতে পারেন।\n\n"
        f"<b>ডিফল্ট প্রিভিউ:</b>\n{DEFAULT_ANNOUNCEMENT_BN}",
        parse_mode="HTML", reply_markup=keyboard
    )
    return ConversationHandler.END

async def admin_menu_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_count = len(get_all_user_ids())
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ প্রিমিয়াম ডিফল্ট Broadcast পাঠান", callback_data="admin_broadcast_default")],
        [InlineKeyboardButton("✍️ নিজে লিখুন",                        callback_data="admin_broadcast_custom")],
    ])
    await update.message.reply_text(
        f"📢 <b>Broadcast Message</b>\n{LINE}\n"
        f"👥 মোট <b>{user_count}</b> জন user-কে message পাঠানো হবে।\n\n"
        f"👇 ডিফল্ট প্রিমিয়াম মেসেজ এক ট্যাপে পাঠাতে পারেন, অথবা নিজে লিখতে পারেন।\n\n"
        f"<b>ডিফল্ট প্রিভিউ:</b>\n{DEFAULT_BROADCAST_BN}",
        parse_mode="HTML", reply_markup=keyboard
    )
    return ConversationHandler.END

async def admin_menu_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = get_stats()
    msg = (
        f"📈 <b>Bot Statistics</b>\n{LINE}\n"
        f"👥 মোট Users            :  <b>{s['users']:,}</b>\n"
        f"📦 মোট Orders           :  <b>{s['total']:,}</b>\n"
        f"{LINE}\n"
        f"⏳ Pending Orders      :  <b>{s['pending']}</b>\n"
        f"✅ Paid Orders           :  <b>{s['paid']}</b>\n"
        f"❌ Rejected Orders    :  <b>{s['rejected']}</b>\n"
        f"{LINE}\n"
        f"💰 মোট Revenue        :  <b>{s['revenue']:,}৳</b>\n"
        f"🪙 বিক্রিত কয়েন          :  <b>{s['coins_sold']:,}</b>\n"
        f"{LINE}\n"
        f"💸 Pending Withdrawals: <b>{s['pending_withdrawals']}</b>\n"
        f"💵 Total Withdrawn     :  <b>{s['total_withdrawn']:,}৳</b>"
    )
    await update.message.reply_text(msg, parse_mode="HTML")
    return ConversationHandler.END

async def admin_menu_withdrawals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pending = get_pending_withdrawals()
    if not pending:
        await update.message.reply_text("✅ কোনো pending withdrawal নেই।")
        return ConversationHandler.END
    lines = []
    buttons = []
    for row in pending[:10]:
        wid, uid, amount, method, number, status, created_at = row
        icon = method_icon(method)
        lines.append(f"<b>#W{wid}</b>  [{uid}]  {amount}৳  {icon}{method}  <code>{number}</code>")
        buttons.append([
            InlineKeyboardButton(f"✅ W{wid}", callback_data=f"appw_{wid}"),
            InlineKeyboardButton(f"❌ W{wid}", callback_data=f"rejw_{wid}"),
        ])
    msg = f"💸 <b>Pending Withdrawals</b>\n{LINE}\n" + "\n".join(lines)
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))
    return ConversationHandler.END

async def admin_menu_maintmsg_bn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["maintmsg_lang"] = "bn"
    await update.message.reply_text(
        f"📝 বর্তমান Maintenance মেসেজ (বাংলা):\n{LINE}\n{get_maintenance_msg('bn')}\n{LINE}\n\n"
        f"নতুন মেসেজ পাঠান (HTML সাপোর্ট করে)।\nবাতিল করতে /cancel লিখুন।",
        parse_mode="HTML"
    )
    return ADMIN_MSG_INPUT

async def admin_menu_maintmsg_en(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["maintmsg_lang"] = "en"
    await update.message.reply_text(
        f"📝 Current Maintenance Message (English):\n{LINE}\n{get_maintenance_msg('en')}\n{LINE}\n\n"
        f"Send new message (HTML supported).\nType /cancel to cancel.",
        parse_mode="HTML"
    )
    return ADMIN_MSG_INPUT

async def admin_menu_rulesmsg_bn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["rulesmsg_lang"] = "bn"
    current = get_setting("rules_text_bn", DEFAULT_RULES_BN)
    await update.message.reply_text(
        f"📜 বর্তমান নিয়মাবলী (বাংলা):\n{LINE}\n{current}\n{LINE}\n\n"
        f"নতুন নিয়মাবলী পাঠান (HTML সাপোর্ট করে)।\n"
        f"ডিফল্ট প্রিমিয়াম ভার্সনে ফিরতে <code>reset</code> পাঠান।\n"
        f"বাতিল করতে /cancel লিখুন।",
        parse_mode="HTML"
    )
    return ADMIN_RULES_INPUT

async def admin_menu_rulesmsg_en(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["rulesmsg_lang"] = "en"
    current = get_setting("rules_text_en", DEFAULT_RULES_EN)
    await update.message.reply_text(
        f"📜 Current Rules (English):\n{LINE}\n{current}\n{LINE}\n\n"
        f"Send new rules (HTML supported).\n"
        f"Send <code>reset</code> to restore the default premium version.\n"
        f"Type /cancel to cancel.",
        parse_mode="HTML"
    )
    return ADMIN_RULES_INPUT

async def admin_menu_support_un(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["username_key"] = "support_username"
    current = get_support_username()
    await update.message.reply_text(
        f"🎧 বর্তমান Support Username: <code>@{current}</code>\n\n"
        f"নতুন Telegram username পাঠান (@ ছাড়া, যেমন <code>BDincometvadmin_sakib</code>)।\n"
        f"বাতিল করতে /cancel লিখুন।",
        parse_mode="HTML"
    )
    return ADMIN_USERNAME_INPUT

async def admin_menu_receive_un(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["username_key"] = "receive_username"
    current = get_receive_username()
    await update.message.reply_text(
        f"💳 বর্তমান Receive Username/Address: <code>{current}</code>\n\n"
        f"নতুন Telegram username (@ ছাড়া, যেমন <code>sakib173087</code>)\n"
        f"অথবা BEP-20 wallet address (যেমন <code>0xAbC123...</code>, মোট ৪২ ক্যারেক্টার) পাঠান।\n"
        f"বাতিল করতে /cancel লিখুন।",
        parse_mode="HTML"
    )
    return ADMIN_USERNAME_INPUT

async def admin_username_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = context.user_data.get("username_key", "support_username")
    label = "Support Username" if key == "support_username" else "Receive Username/Address"
    raw = update.message.text.strip()
    if key == "receive_username" and _BEP20_RE.match(raw):
        new_value = raw  # BEP-20 wallet address — as-is, case matters
        display = new_value
    else:
        new_value = raw.lstrip("@")
        if not re.match(r'^[A-Za-z0-9_]{4,32}$', new_value):
            await update.message.reply_text(
                "⚠️ ভুল ফরম্যাট। হয় Telegram username দিন (@ ছাড়া, ৪-৩২ ক্যারেক্টার, শুধু অক্ষর/সংখ্যা/_),\n"
                "অথবা BEP-20 wallet address দিন (<code>0x</code> + ৪০টি hex ক্যারেক্টার)।",
                parse_mode="HTML"
            )
            return ADMIN_USERNAME_INPUT
        display = f"@{new_value}"
    set_setting(key, new_value)
    await update.message.reply_text(f"✅ {label} আপডেট হয়েছে: <code>{display}</code>", parse_mode="HTML")
    await update.message.reply_text("🛠️ Admin Panel:", reply_markup=admin_reply_keyboard())
    return ConversationHandler.END

async def admin_menu_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tiers = sorted(get_price_tiers(), key=lambda x: x[0])
    tiers_txt = "\n".join([f"  • {tier[0]:,}+ কয়েন  ➜  {tier[1]}৳ / ১০০০" for tier in tiers])
    maint = "🔴 চালু (ON)" if is_maintenance() else "🟢 বন্ধ (OFF)"
    methods = get_payment_methods()
    methods_txt = "\n".join([f"  • {'✅' if on else '❌'} {m}" for m, on in methods.items()])
    ann = get_announcement() or "(কোনো announcement নেই)"
    bonus = get_referral_bonus()
    min_w = get_min_withdraw()
    msg = (
        f"ℹ️ <b>বর্তমান সেটিংস</b>\n{LINE}\n"
        f"💹 রেট:\n{tiers_txt}\n{LINE}\n"
        f"🛠️ Maintenance: <b>{maint}</b>\n{LINE}\n"
        f"💳 পেমেন্ট মেথড:\n{methods_txt}\n{LINE}\n"
        f"🔗 Referral Bonus: <b>{int(bonus) if bonus==int(bonus) else bonus}৳</b>\n"
        f"💸 Min Withdraw: <b>{int(min_w) if min_w==int(min_w) else min_w}৳</b>\n{LINE}\n"
        f"🎧 Support Username: <b>@{get_support_username()}</b>\n"
        f"💳 Receive Username: <b>@{get_receive_username()}</b>\n{LINE}\n"
        f"📣 Announcement:\n{ann}"
    )
    await update.message.reply_text(msg, parse_mode="HTML")
    return ConversationHandler.END

async def admin_menu_toggle_screen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔘 <b>User মেনু বাটন On/Off</b>\n\nযেই বাটন চালু/বন্ধ করতে চান সেটাতে চাপুন। "
        "বন্ধ (❌) করা বাটন সাধারণ ইউজারদের মেনুতে আর দেখাবে না।",
        parse_mode="HTML", reply_markup=menu_toggle_keyboard()
    )
    return ConversationHandler.END

async def admin_toggle_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    key = query.data.split("toggle_menu_", 1)[1]
    new_state = not get_menu_button_enabled(key)
    set_menu_button_enabled(key, new_state)
    await query.answer("✅ চালু" if new_state else "❌ বন্ধ")
    await query.edit_message_reply_markup(reply_markup=menu_toggle_keyboard())

async def admin_menu_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Backup পাঠানো হচ্ছে...")
    await send_db_backup(context.bot)
    return ConversationHandler.END

async def admin_menu_exit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔚 Admin মেনু থেকে বের হলেন।", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

async def admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ বাতিল করা হয়েছে।", reply_markup=admin_reply_keyboard())
    return ConversationHandler.END

# ================================================================
#               MESSAGE ROUTER
# ================================================================
async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    lang = get_user_lang(uid)
    L = LANG[lang]
    text = update.message.text
    if text == L["btn_order"]:
        return await order_start(update, context)
    if text == L["btn_price"]:
        return await price_list_menu(update, context)
    if text == L["btn_wallet"]:
        return await wallet_menu(update, context)
    if text == L["btn_history"]:
        return await history_menu(update, context)
    if text == L["btn_support"]:
        return await support_menu(update, context)
    if text == L["btn_lang"]:
        return await change_lang_menu(update, context)
    if text == L["btn_referral"]:
        return await referral_menu(update, context)
    if text == L["btn_balance"]:
        return await balance_menu(update, context)
    if text == L["btn_rules"]:
        return await rules_menu(update, context)
    if text == L["btn_leaderboard"]:
        return await leaderboard_menu(update, context)

# ================================================================
#                            MAIN
# ================================================================
def main():
    if not BOT_TOKEN or BOT_TOKEN == "PUT_YOUR_BOT_TOKEN_HERE":
        print("❌ BOT_TOKEN সেট করা নেই!")
        print("   ফাইলের একদম উপরে CONFIG সেকশনে BOT_TOKEN বসান, অথবা")
        print('   export BOT_TOKEN="আপনার_টোকেন"  কমান্ড দিয়ে চালান।')
        return
    init_db()
    start_health_server()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # Maintenance gates (highest priority)
    app.add_handler(MessageHandler(filters.ALL, maintenance_gate_message), group=-1)
    app.add_handler(CallbackQueryHandler(maintenance_gate_callback), group=-1)

    # Language & join callbacks
    app.add_handler(CallbackQueryHandler(set_language, pattern="^setlang_"))
    app.add_handler(CallbackQueryHandler(check_join,   pattern="^check_join$"))

    # Order conversation
    _nav_labels = []
    for lang in LANG:
        for key in ("btn_cancel_order", "btn_confirm_cancel", "btn_continue_order"):
            _nav_labels.append(re.escape(LANG[lang][key]))

    order_conv = ConversationHandler(
        entry_points=[
            CommandHandler("order", order_start),
            MessageHandler(
                filters.TEXT & ~filters.COMMAND &
                filters.Regex("^(" + "|".join(re.escape(LANG[l]["btn_order"]) for l in LANG) + ")$"),
                order_start
            ),
        ],
        states={
            QTY:          [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_order_navigation)],
            METHOD:       [CallbackQueryHandler(method_choice, pattern="^method_"),
                           MessageHandler(filters.TEXT & ~filters.COMMAND, handle_order_navigation)],
            WALLET_CHOICE:[CallbackQueryHandler(wallet_choice, pattern="^(usesaved|newnumber)$"),
                           MessageHandler(filters.TEXT & ~filters.COMMAND, handle_order_navigation)],
            NUMBER:       [MessageHandler(filters.TEXT & ~filters.COMMAND, get_number)],
            PROOF:        [MessageHandler(filters.PHOTO, get_proof),
                           MessageHandler(filters.TEXT & ~filters.COMMAND, handle_order_navigation)],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, handle_conversation_timeout),
                CallbackQueryHandler(handle_conversation_timeout),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=300,
    )

    # Withdraw conversation
    withdraw_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(withdraw_start, pattern="^withdraw_start$")],
        states={
            WITHDRAW_METHOD: [
                CallbackQueryHandler(withdraw_method_choice, pattern="^wm_"),
            ],
            WITHDRAW_WALLET_CHOICE: [
                CallbackQueryHandler(withdraw_wallet_choice, pattern="^(wuse_saved|wnew_number)$"),
                CallbackQueryHandler(withdraw_confirm_action, pattern="^(wconfirm|wcancel)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_get_number),
            ],
            WITHDRAW_NUMBER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_get_number),
            ],
        },
        fallbacks=[CommandHandler("cancel", withdraw_cancel_conv)],
    )

    # Admin conversation
    admin_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_rate_start,        pattern="^admin_rate$"),
            CallbackQueryHandler(admin_maintmsg_start,    pattern="^admin_maintmsg_"),
            CallbackQueryHandler(admin_rulesmsg_start,    pattern="^admin_rulesmsg_"),
            CallbackQueryHandler(admin_announce_start,    pattern="^admin_announce_custom$"),
            CallbackQueryHandler(admin_broadcast_start,   pattern="^admin_broadcast_custom$"),
            CallbackQueryHandler(admin_ref_settings_start, pattern="^admin_ref_settings$"),
            MessageHandler(filters.User(user_id=ADMIN_ID) & filters.Regex(f"^{re.escape(ABTN_RATE)}$"),        admin_menu_rate),
            MessageHandler(filters.User(user_id=ADMIN_ID) & filters.Regex(f"^{re.escape(ABTN_MAINT)}$"),       admin_menu_maint),
            MessageHandler(filters.User(user_id=ADMIN_ID) & filters.Regex(f"^{re.escape(ABTN_PAYMENT)}$"),     admin_menu_payment),
            MessageHandler(filters.User(user_id=ADMIN_ID) & filters.Regex(f"^{re.escape(ABTN_ANNOUNCE)}$"),    admin_menu_announce),
            MessageHandler(filters.User(user_id=ADMIN_ID) & filters.Regex(f"^{re.escape(ABTN_BROADCAST)}$"),   admin_menu_broadcast),
            MessageHandler(filters.User(user_id=ADMIN_ID) & filters.Regex(f"^{re.escape(ABTN_REF)}$"),         admin_menu_ref),
            MessageHandler(filters.User(user_id=ADMIN_ID) & filters.Regex(f"^{re.escape(ABTN_STATS)}$"),       admin_menu_stats),
            MessageHandler(filters.User(user_id=ADMIN_ID) & filters.Regex(f"^{re.escape(ABTN_WITHDRAWALS)}$"), admin_menu_withdrawals),
            MessageHandler(filters.User(user_id=ADMIN_ID) & filters.Regex(f"^{re.escape(ABTN_MAINTMSG_BN)}$"), admin_menu_maintmsg_bn),
            MessageHandler(filters.User(user_id=ADMIN_ID) & filters.Regex(f"^{re.escape(ABTN_MAINTMSG_EN)}$"), admin_menu_maintmsg_en),
            MessageHandler(filters.User(user_id=ADMIN_ID) & filters.Regex(f"^{re.escape(ABTN_RULES_BN)}$"),    admin_menu_rulesmsg_bn),
            MessageHandler(filters.User(user_id=ADMIN_ID) & filters.Regex(f"^{re.escape(ABTN_RULES_EN)}$"),    admin_menu_rulesmsg_en),
            MessageHandler(filters.User(user_id=ADMIN_ID) & filters.Regex(f"^{re.escape(ABTN_SUPPORT_UN)}$"),  admin_menu_support_un),
            MessageHandler(filters.User(user_id=ADMIN_ID) & filters.Regex(f"^{re.escape(ABTN_RECEIVE_UN)}$"),  admin_menu_receive_un),
            MessageHandler(filters.User(user_id=ADMIN_ID) & filters.Regex(f"^{re.escape(ABTN_VIEW)}$"),        admin_menu_view),
            MessageHandler(filters.User(user_id=ADMIN_ID) & filters.Regex(f"^{re.escape(ABTN_BACKUP)}$"),      admin_menu_backup),
            MessageHandler(filters.User(user_id=ADMIN_ID) & filters.Regex(f"^{re.escape(ABTN_MENU_TOGGLE)}$"), admin_menu_toggle_screen),
            MessageHandler(filters.User(user_id=ADMIN_ID) & filters.Regex(f"^{re.escape(ABTN_EXIT)}$"),        admin_menu_exit),
        ],
        states={
            ADMIN_RATE_INPUT:      [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_rate_receive)],
            ADMIN_MSG_INPUT:       [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_maintmsg_receive)],
            ADMIN_ANNOUNCE_INPUT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_announce_receive)],
            ADMIN_BROADCAST_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_send)],
            ADMIN_SETTING_INPUT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_ref_settings_receive)],
            ADMIN_RULES_INPUT:     [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_rulesmsg_receive)],
            ADMIN_USERNAME_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_username_receive)],
        },
        fallbacks=[CommandHandler("cancel", admin_cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_entry, filters=filters.User(user_id=ADMIN_ID)))
    app.add_handler(order_conv)
    app.add_handler(withdraw_conv)
    app.add_handler(admin_conv)

    # Admin panel callbacks
    app.add_handler(CallbackQueryHandler(admin_toggle_maint,    pattern="^admin_toggle_maint$"))
    app.add_handler(CallbackQueryHandler(admin_view,            pattern="^admin_view$"))
    app.add_handler(CallbackQueryHandler(admin_stats,           pattern="^admin_stats$"))
    app.add_handler(CallbackQueryHandler(admin_payment_methods, pattern="^admin_payment_methods$"))
    app.add_handler(CallbackQueryHandler(admin_announce_menu,          pattern="^admin_announce$"))
    app.add_handler(CallbackQueryHandler(admin_announce_send_default,  pattern="^admin_announce_default$"))
    app.add_handler(CallbackQueryHandler(admin_broadcast_menu,         pattern="^admin_broadcast$"))
    app.add_handler(CallbackQueryHandler(admin_broadcast_send_default, pattern="^admin_broadcast_default$"))
    app.add_handler(CallbackQueryHandler(admin_toggle_method,   pattern="^toggle_method_"))
    app.add_handler(CallbackQueryHandler(admin_toggle_menu_button, pattern="^toggle_menu_"))
    app.add_handler(CallbackQueryHandler(admin_back,            pattern="^admin_back$"))
    app.add_handler(CallbackQueryHandler(admin_withdrawals_list, pattern="^admin_withdrawals$"))
    app.add_handler(CallbackQueryHandler(button_handler,        pattern="^(paid|reject|appw|rejw)_"))

    # Menu router
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))

    print("✅ Bot চালু হয়েছে...")
    app.run_polling()

if __name__ == "__main__":
    main()
