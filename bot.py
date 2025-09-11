from telegram import Update, Bot
from telegram.ext import Updater, Dispatcher, CommandHandler, CallbackContext
from flask import Flask, request
import json, os, time, hmac, hashlib, requests, threading
from datetime import datetime, timedelta

# ===== CONFIG =====
BOT_TOKEN = os.getenv("BOT_TOKEN", "8277819223:AAEzjJdDdWR2H0Dhfn_8B8NG3iQtUJYFAL0")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://premiumbot.deta.app/telegram")
PREMIUM_GROUP_LINK = "https://t.me/+5fmB-ojP74NhNWE1"

# Razorpay test keys
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "rzp_test_RFzw6gJnEXKlRr")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "zg1M9tqs7kqDTQnj5H2B9cC6")

MEMBERSHIP_AMOUNT_RUPEES = 50
WEBHOOK_SECRET = "Ravindra@01"
MEMBERS_DB = "members.json"

# ===== Helpers =====
def load_members():
    if os.path.exists(MEMBERS_DB):
        with open(MEMBERS_DB, "r") as f:
            return json.load(f)
    return {}

def save_members(data):
    with open(MEMBERS_DB, "w") as f:
        json.dump(data, f, indent=4)

def create_payment_link(telegram_id, amount_rupees=MEMBERSHIP_AMOUNT_RUPEES):
    amount_paise = int(amount_rupees * 100)
    payload = {
        "amount": amount_paise,
        "currency": "INR",
        "description": "Skill & Opportunity Premium Hub membership",
        "reference_id": f"tg{telegram_id}{int(time.time())}",
        "notes": {"telegram_id": str(telegram_id)}
    }
    resp = requests.post(
        "https://api.razorpay.com/v1/payment_links",
        auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
        json=payload,
        timeout=15
    )
    resp.raise_for_status()
    data = resp.json()
    return data, data.get("short_url")

# ===== Telegram Setup =====
bot = Bot(BOT_TOKEN)
updater = Updater(BOT_TOKEN, use_context=True)
dispatcher: Dispatcher = updater.dispatcher

def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "👋 Welcome to Skill & Opportunity Payment Bot!\n\n"
        "Click or type /joinpremium to get your payment link 🔗"
    )

def join_premium(update: Update, context: CallbackContext):
    user = update.effective_user
    try:
        _, short_url = create_payment_link(user.id)
        if short_url:
            update.message.reply_text(
                f"Hello {user.first_name}! 🔥\n\n"
                f"Pay ₹{MEMBERSHIP_AMOUNT_RUPEES} using this link:\n{short_url}\n\n"
                "After payment you'll automatically receive the invite link ✅."
            )
        else:
            update.message.reply_text("❌ Payment link creation failed.")
    except Exception as e:
        update.message.reply_text("⚠️ Error creating payment link. Contact admin.")

dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("joinpremium", join_premium))

# ===== Flask App =====
app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "🏠 Flask bot is running", 200

@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    update_json = request.get_json(force=True)
    update = Update.de_json(update_json, bot)
    dispatcher.process_update(update)
    return "ok", 200

@app.route("/razorpay", methods=["POST"])
def razorpay_webhook():
    raw = request.get_data()
    header_sig = request.headers.get("X-Razorpay-Signature")
    if not header_sig:
        return "", 400

    generated = hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(generated, header_sig):
        return "", 401

    payload = json.loads(raw.decode("utf-8"))
    if payload.get("event") == "payment_link.paid":
        payment_link_obj = payload["payload"]["payment_link"]["entity"]
        tg_id = payment_link_obj["notes"].get("telegram_id")
        if tg_id:
            members = load_members()
            now = datetime.utcnow()

            # agar already member hai aur expire future me hai to extend
            if tg_id in members and datetime.strptime(members[tg_id]["expiry"], "%Y-%m-%d") >= now:
                old_expiry = datetime.strptime(members[tg_id]["expiry"], "%Y-%m-%d")
                new_expiry = old_expiry + timedelta(days=30)
                bot.send_message(
                    chat_id=int(tg_id),
                    text=f"✅ Renewal successful! Your membership has been extended till {new_expiry.strftime('%Y-%m-%d')}."
                )
            else:
                new_expiry = now + timedelta(days=30)
                bot.send_message(
                    chat_id=int(tg_id),
                    text=f"✅ Payment confirmed. Membership valid till {new_expiry.strftime('%Y-%m-%d')}.\nJoin Premium: {PREMIUM_GROUP_LINK}"
                )

            members[tg_id] = {
                "joined_at": now.isoformat(),
                "expiry": new_expiry.strftime("%Y-%m-%d"),
                "payment_link_id": payment_link_obj.get("id")
            }
            save_members(members)

    return "", 200

# ===== Reminder System =====
def reminder_job():
    while True:
        members = load_members()
        now = datetime.utcnow()
        for tg_id, data in members.items():
            expiry = datetime.strptime(data["expiry"], "%Y-%m-%d")
            days_left = (expiry - now).days
            if 0 < days_left <= 3:  # 3 din pehle se daily reminder
                try:
                    # Naya payment link generate karo renewal ke liye
                    _, short_url = create_payment_link(tg_id)

                    bot.send_message(
                        chat_id=int(tg_id),
                        text=(
                            f"⚠️ Reminder: Your Premium membership will expire on "
                            f"{expiry.strftime('%Y-%m-%d')} ({days_left} days left).\n\n"
                            f"💳 Renew now to continue uninterrupted access:\n{short_url}"
                        )
                    )
                except Exception as e:
                    print("Reminder send error:", e)
        time.sleep(24 * 3600)  # har 24 ghante me run hoga

# Background thread start karo
threading.Thread(target=reminder_job, daemon=True).start()

if __name__ == "__main__":
    bot.set_webhook(WEBHOOK_URL)
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
