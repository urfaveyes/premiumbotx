from telegram import Update, Bot
from telegram.ext import Updater, Dispatcher, CommandHandler, CallbackContext
from flask import Flask, request
import json, os, time, hmac, hashlib, requests
from datetime import datetime, timedelta
import base64
import firebase_admin
from firebase_admin import credentials, firestore

# ===== CONFIG =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("https://premiumbotx-3.onrender.com/telegram")
PREMIUM_GROUP_LINK = "https://t.me/+5fmB-ojP74NhNWE1"

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET")
MEMBERSHIP_AMOUNT_RUPEES = 50
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "WEBHOOK_SECRET")

MEMBERS_DB = "members.json"
FIREBASE_CREDENTIALS = os.getenv("FIREBASE_CREDENTIALS")
FIREBASE_CREDENTIALS_B64 = os.getenv("FIREBASE_CREDENTIALS_B64")

# ===== Firebase / Firestore Setup =====
cred_dict = None
if FIREBASE_CREDENTIALS:
    try:
        cred_dict = json.loads(FIREBASE_CREDENTIALS)
    except Exception:
        cred_dict = json.loads(FIREBASE_CREDENTIALS.replace('\\n', '\n'))
elif FIREBASE_CREDENTIALS_B64:
    cred_dict = json.loads(base64.b64decode(FIREBASE_CREDENTIALS_B64).decode())

if cred_dict:
    cred = credentials.Certificate(cred_dict)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("Firestore connected")
else:
    db = None
    print("Firestore disabled, falling back to file storage")

# ===== Helpers =====
def load_members():
    if db:
        docs = db.collection("members").stream()
        out = {}
        for doc in docs:
            d = doc.to_dict()
            tg = str(d.get("telegram_id") or doc.id)
            out[tg] = {
                "joined_at": d.get("joined_at"),
                "expiry": d.get("expiry"),
                "payment_link_id": d.get("payment_link_id")
            }
        return out
    else:
        if os.path.exists(MEMBERS_DB):
            with open(MEMBERS_DB, "r") as f:
                return json.load(f)
        return {}

def upsert_single_member(tg_id, joined_at_iso, expiry_str, payment_link_id=None):
    if db:
        db.collection("members").document(str(tg_id)).set({
            "telegram_id": str(tg_id),
            "joined_at": joined_at_iso,
            "expiry": expiry_str,
            "payment_link_id": payment_link_id
        }, merge=True)
    else:
        members = load_members()
        members[str(tg_id)] = {
            "joined_at": joined_at_iso,
            "expiry": expiry_str,
            "payment_link_id": payment_link_id
        }
        with open(MEMBERS_DB, "w") as f:
            json.dump(members, f, indent=4)

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
print("DEBUG BOT_TOKEN:", repr(BOT_TOKEN))

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
        print("create_payment_link error:", e)

dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("joinpremium", join_premium))

# ===== Flask App =====
app = Flask(__name__)

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
            now = datetime.utcnow()
            try:
                if db:
                    doc_ref = db.collection("members").document(str(tg_id))
                    doc = doc_ref.get()
                    if doc.exists and "expiry" in doc.to_dict():
                        old_expiry = datetime.strptime(doc.to_dict()["expiry"], "%Y-%m-%d")
                        new_expiry = old_expiry + timedelta(days=30) if old_expiry >= now else now + timedelta(days=30)
                    else:
                        new_expiry = now + timedelta(days=30)
                else:
                    members = load_members()
                    if str(tg_id) in members and datetime.strptime(members[str(tg_id)]["expiry"], "%Y-%m-%d") >= now:
                        old_expiry = datetime.strptime(members[str(tg_id)]["expiry"], "%Y-%m-%d")
                        new_expiry = old_expiry + timedelta(days=30)
                    else:
                        new_expiry = now + timedelta(days=30)

                bot.send_message(
                    chat_id=int(tg_id),
                    text=f"✅ Payment confirmed. Membership valid till {new_expiry.strftime('%Y-%m-%d')}.\nJoin Premium: {PREMIUM_GROUP_LINK}"
                )
                upsert_single_member(tg_id, now.isoformat(), new_expiry.strftime("%Y-%m-%d"), payment_link_obj.get("id"))
            except Exception as e:
                print("Error in razorpay webhook:", e)
    return "", 200

# ===== Reminder Route (cron-triggered) =====
@app.route("/run-reminders", methods=["GET"])
def run_reminders():
    now = datetime.utcnow()
    if db:
        docs = db.collection("members").stream()
        iterator = ((doc.id, doc.to_dict()) for doc in docs)
    else:
        members = load_members()
        iterator = members.items()

    for tg_id, data in iterator:
        try:
            expiry = datetime.strptime(data["expiry"], "%Y-%m-%d")
            days_left = (expiry - now).days
            if 0 < days_left <= 3:
                _, short_url = create_payment_link(tg_id)
                bot.send_message(
                    chat_id=int(tg_id),
                    text=(f"⚠️ Reminder: Your Premium membership will expire on {expiry.strftime('%Y-%m-%d')} ({days_left} days left).\n\n"
                          f"💳 Renew now: {short_url}")
                )
        except Exception as e:
            print("Reminder send error for", tg_id, e)
    return "ok", 200

# ===== Run app =====
if __name__ == "__main__":
    if WEBHOOK_URL:
        bot.set_webhook(WEBHOOK_URL)
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False, use_reloader=False)
