import os
import re
import base64
import imaplib
import email as email_lib
import logging
import calendar as pycalendar
from io import BytesIO
from datetime import datetime, timedelta, date
from queue import Queue
from zoneinfo import ZoneInfo
from email.header import decode_header

from flask import Flask, request
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Dispatcher,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    Filters,
    CallbackContext,
)
from pymongo import MongoClient, ReturnDocument
from apscheduler.schedulers.background import BackgroundScheduler
import requests

try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request as GoogleRequest
    from googleapiclient.discovery import build
    GOOGLE_LIBS_AVAILABLE = True
except ImportError:
    GOOGLE_LIBS_AVAILABLE = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TZ = ZoneInfo("Europe/Istanbul")
UTC = ZoneInfo("UTC")

# ---------------------------------------------------------------------------
# Ortam degiskenleri (Render dashboard > Environment sekmesinden ayarlanir)
# ---------------------------------------------------------------------------
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "degistir-bu-gizli-yolu")
MONGO_URI = os.environ["MONGO_URI"]

EMAILJS_SERVICE_ID = os.environ.get("EMAILJS_SERVICE_ID")
EMAILJS_TEMPLATE_ID = os.environ.get("EMAILJS_TEMPLATE_ID")
EMAILJS_PUBLIC_KEY = os.environ.get("EMAILJS_PUBLIC_KEY")
EMAILJS_PRIVATE_KEY = os.environ.get("EMAILJS_PRIVATE_KEY")
USER_EMAIL = os.environ.get("USER_EMAIL")

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN")
GOOGLE_CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "primary")

# SMS opsiyoneldir, kalici ucretsiz bir servis yoktur.
TWILIO_SID = os.environ.get("TWILIO_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER")
TWILIO_TO_NUMBER = os.environ.get("TWILIO_TO_NUMBER")

# --- Mail izleme ayarlari (birden fazla hesap, herhangi bir IMAP saglayicisi) ---
# Bilinen saglayicilarin IMAP sunucusu, e-posta adresinin @'dan sonraki
# kismina bakilarak otomatik bulunur - kullanicinin sunucu adresi girmesine
# gerek kalmaz. Listede olmayan bir domain icin IMAP_HOST_N ile elle
# belirtilebilir (ozel/kurumsal mail sunuculari icin).
KNOWN_IMAP_HOSTS = {
    "gmail.com": "imap.gmail.com",
    "googlemail.com": "imap.gmail.com",
    "yandex.com": "imap.yandex.com",
    "yandex.ru": "imap.yandex.com",
    "yandex.com.tr": "imap.yandex.com",
    "yahoo.com": "imap.mail.yahoo.com",
    "yahoo.co.uk": "imap.mail.yahoo.com",
    "yahoo.com.tr": "imap.mail.yahoo.com",
    "icloud.com": "imap.mail.me.com",
    "me.com": "imap.mail.me.com",
    "mac.com": "imap.mail.me.com",
    "zoho.com": "imap.zoho.com",
    "zoho.eu": "imap.zoho.eu",
    "gmx.com": "imap.gmx.com",
    "gmx.net": "imap.gmx.net",
    "gmx.de": "imap.gmx.net",
    "mail.ru": "imap.mail.ru",
    "aol.com": "imap.aol.com",
}

# Bu saglayicilar artik basit sifre/uygulama sifresiyle IMAP kabul etmiyor,
# OAuth gerekiyor - bunlar icin Outlook bolumundeki yontem kullanilmali.
OAUTH_ONLY_DOMAINS = {"outlook.com", "hotmail.com", "live.com", "msn.com"}


def guess_imap_host(address, explicit_host=None):
    if explicit_host:
        return explicit_host
    domain = address.split("@")[-1].lower().strip()
    if domain in OAUTH_ONLY_DOMAINS:
        return None
    return KNOWN_IMAP_HOSTS.get(domain)


def load_imap_accounts(max_accounts=30):
    """
    IMAP_ADDRESS_1 / IMAP_APP_PASSWORD_1, IMAP_ADDRESS_2 / IMAP_APP_PASSWORD_2 ...
    seklinde numaralanmis, herhangi bir mail saglayicisina ait hesaplari okur.
    Gerekirse IMAP_HOST_N ile sunucu adresi elle verilebilir (bilinmeyen/ozel
    domainler icin), IMAP_LABEL_N ile bildirimde gorunecek isim degistirilebilir.
    """
    accounts = []
    for i in range(1, max_accounts + 1):
        addr = os.environ.get(f"IMAP_ADDRESS_{i}")
        pwd = os.environ.get(f"IMAP_APP_PASSWORD_{i}")
        if not (addr and pwd):
            continue
        explicit_host = os.environ.get(f"IMAP_HOST_{i}")
        host = guess_imap_host(addr, explicit_host)
        if not host:
            logger.error(
                "IMAP_ADDRESS_%s (%s) icin sunucu belirlenemedi - "
                "IMAP_HOST_%s ekleyin ya da bu adres Outlook/Hotmail ise "
                "OAuth yontemini kullanin.", i, addr, i,
            )
            continue
        label = os.environ.get(f"IMAP_LABEL_{i}") or addr
        accounts.append({"key": f"imap_{i}", "host": host, "address": addr, "password": pwd, "label": label})
    return accounts


IMAP_ACCOUNTS = load_imap_accounts()

# Mail eklerinin (resim/pdf/word vb.) Telegram'a gonderilecek azami boyutu.
MAIL_ATTACHMENT_MAX_MB = float(os.environ.get("MAIL_ATTACHMENT_MAX_MB", "20"))
MAIL_ATTACHMENT_MAX_BYTES = int(MAIL_ATTACHMENT_MAX_MB * 1024 * 1024)


def load_outlook_refresh_tokens(max_accounts=30):
    """
    OUTLOOK_REFRESH_TOKEN_1, OUTLOOK_REFRESH_TOKEN_2 ... seklinde numaralanmis
    (her biri ayri bir Microsoft hesabi icin) refresh token'lari okur.
    Numarasiz OUTLOOK_REFRESH_TOKEN da (tek hesap icin) desteklenir.
    """
    tokens = []
    single = os.environ.get("OUTLOOK_REFRESH_TOKEN")
    if single:
        tokens.append(single)
    for i in range(1, max_accounts + 1):
        val = os.environ.get(f"OUTLOOK_REFRESH_TOKEN_{i}")
        if val:
            tokens.append(val)
    return tokens


OUTLOOK_CLIENT_ID = os.environ.get("OUTLOOK_CLIENT_ID")
OUTLOOK_REFRESH_TOKENS_ENV = load_outlook_refresh_tokens()

# Bos birakilirsa TUM gelen mailler Telegram'a duser. Doldurulursa (virgulle
# ayrilmis kelimeler) sadece konu/gonderen/govdede bu kelimelerden birini
# iceren mailler dusurulur. Vize takibi icin ornek:
# EMAIL_KEYWORDS=vize,visa,consulate,embassy,randevu,appointment,sefaret,konsolosluk
EMAIL_KEYWORDS = [
    k.strip().lower()
    for k in os.environ.get("EMAIL_KEYWORDS", "").split(",")
    if k.strip()
]
MAIL_CHECK_INTERVAL_SECONDS = int(os.environ.get("MAIL_CHECK_INTERVAL_SECONDS", "120"))

# ---------------------------------------------------------------------------
# Kurulum
# ---------------------------------------------------------------------------
app = Flask(__name__)
bot = Bot(token=TELEGRAM_TOKEN)
dispatcher = Dispatcher(bot, Queue(), workers=0, use_context=True)

mongo = MongoClient(MONGO_URI)
db = mongo["hatirlatici"]
reminders = db["reminders"]
config = db["config"]
mail_state = db["mail_state"]
counters = db["counters"]

# Buton akisi sirasinda kullanicinin nerede oldugunu tutan bellek ici durum.
# (Render tek worker ile calistigi surece sorunsuzdur; servis yeniden
# baslarsa yarim kalan bir ekleme islemi sifirlanir, kullanici tekrar basa
# donup devam eder.)
PENDING = {}

ALERT_OPTIONS = [
    (0, "Tam zamaninda"),
    (60, "1 saat once"),
    (180, "3 saat once"),
    (1440, "1 gun once"),
    (10080, "1 hafta once"),
]
DEFAULT_ALERTS = {0, 1440}


# ---------------------------------------------------------------------------
# Kucuk yardimcilar
# ---------------------------------------------------------------------------
def get_primary_chat_id():
    doc = config.find_one({"_id": "config"})
    return doc.get("chat_id") if doc else None


def set_primary_chat_id(chat_id):
    config.update_one({"_id": "config"}, {"$set": {"chat_id": chat_id}}, upsert=True)


def next_seq_id(chat_id):
    """Her kullanici icin 1'den baslayan, kolay yazilabilir bir hatirlatici ID'si uretir."""
    doc = counters.find_one_and_update(
        {"_id": f"chat_{chat_id}"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc["seq"]


def offset_label(minutes):
    for m, label in ALERT_OPTIONS:
        if m == minutes:
            return label
    return f"{minutes} dk once"


def alert_message(text, remind_at_local, offset_min):
    when = remind_at_local.strftime("%d.%m.%Y %H:%M")
    if offset_min == 0:
        return f"⏰ Hatirlatma zamani geldi!\n{text}\n({when})"
    return f"⏰ {offset_label(offset_min)}: {text}\n({when})"


# ---------------------------------------------------------------------------
# Bildirim gonderimi (Telegram / e-posta / SMS / takvim)
# ---------------------------------------------------------------------------
def get_calendar_service():
    if not GOOGLE_LIBS_AVAILABLE:
        return None
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN):
        return None
    creds = Credentials(
        None,
        refresh_token=GOOGLE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/calendar.events"],
    )
    try:
        creds.refresh(GoogleRequest())
        return build("calendar", "v3", credentials=creds, cache_discovery=False)
    except Exception as e:
        logger.error("Google Calendar servisi olusturulamadi: %s", e)
        return None


def add_calendar_event(text, dt_local):
    service = get_calendar_service()
    if not service:
        logger.info("Takvim entegrasyonu ayarli degil, atlaniyor.")
        return
    event = {
        "summary": text,
        "start": {"dateTime": dt_local.isoformat(), "timeZone": "Europe/Istanbul"},
        "end": {"dateTime": dt_local.isoformat(), "timeZone": "Europe/Istanbul"},
        "reminders": {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": 0}],
        },
    }
    try:
        service.events().insert(calendarId=GOOGLE_CALENDAR_ID, body=event).execute()
    except Exception as e:
        logger.error("Takvim etkinligi eklenemedi: %s", e)


def send_email(subject, body):
    if not (EMAILJS_SERVICE_ID and EMAILJS_TEMPLATE_ID and EMAILJS_PUBLIC_KEY and USER_EMAIL):
        return
    payload = {
        "service_id": EMAILJS_SERVICE_ID,
        "template_id": EMAILJS_TEMPLATE_ID,
        "user_id": EMAILJS_PUBLIC_KEY,
        "accessToken": EMAILJS_PRIVATE_KEY,
        "template_params": {"to_email": USER_EMAIL, "subject": subject, "message": body},
    }
    try:
        r = requests.post("https://api.emailjs.com/api/v1.0/email/send", json=payload, timeout=10)
        if r.status_code != 200:
            logger.error("EmailJS hatasi: %s %s", r.status_code, r.text)
    except Exception as e:
        logger.error("E-posta gonderilemedi: %s", e)


def send_sms(body):
    if not (TWILIO_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER and TWILIO_TO_NUMBER):
        return
    try:
        r = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json",
            data={"To": TWILIO_TO_NUMBER, "From": TWILIO_FROM_NUMBER, "Body": body},
            auth=(TWILIO_SID, TWILIO_AUTH_TOKEN),
            timeout=10,
        )
        if r.status_code >= 300:
            logger.error("SMS hatasi: %s %s", r.status_code, r.text)
    except Exception as e:
        logger.error("SMS gonderilemedi: %s", e)


# ---------------------------------------------------------------------------
# Takvim (inline calendar) klavyesi
# ---------------------------------------------------------------------------
GUN_BASLIKLARI = ["Pzt", "Sal", "Car", "Per", "Cum", "Cmt", "Paz"]
AY_ADLARI = [
    "", "Ocak", "Subat", "Mart", "Nisan", "Mayis", "Haziran",
    "Temmuz", "Agustos", "Eylul", "Ekim", "Kasim", "Aralik",
]


def build_calendar_keyboard(year, month):
    today = datetime.now(TZ).date()
    rows = []

    header = [
        InlineKeyboardButton("◀", callback_data=f"cal|{year}|{month}|prev"),
        InlineKeyboardButton(f"{AY_ADLARI[month]} {year}", callback_data="noop"),
        InlineKeyboardButton("▶", callback_data=f"cal|{year}|{month}|next"),
    ]
    rows.append(header)
    rows.append([InlineKeyboardButton(g, callback_data="noop") for g in GUN_BASLIKLARI])

    for week in pycalendar.monthcalendar(year, month):
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="noop"))
                continue
            this_date = date(year, month, day)
            if this_date < today:
                row.append(InlineKeyboardButton("·", callback_data="noop"))
            else:
                row.append(InlineKeyboardButton(str(day), callback_data=f"day|{year}|{month}|{day}"))
        rows.append(row)

    rows.append([InlineKeyboardButton("\U0001f3e0 Ana Menu", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def build_hour_keyboard():
    rows = []
    hours = list(range(24))
    for i in range(0, 24, 6):
        rows.append(
            [InlineKeyboardButton(f"{h:02d}", callback_data=f"hour|{h}") for h in hours[i:i + 6]]
        )
    rows.append([InlineKeyboardButton("\U0001f3e0 Ana Menu", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def build_minute_keyboard():
    minutes = [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55]
    rows = []
    for i in range(0, len(minutes), 4):
        rows.append(
            [InlineKeyboardButton(f":{m:02d}", callback_data=f"min|{m}") for m in minutes[i:i + 4]]
        )
    rows.append([InlineKeyboardButton("\U0001f3e0 Ana Menu", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def build_alert_keyboard(chat_id):
    selected = PENDING.get(chat_id, {}).get("alerts", set())
    rows = []
    for minutes, label in ALERT_OPTIONS:
        mark = "✅" if minutes in selected else "⬜"
        rows.append([InlineKeyboardButton(f"{mark} {label}", callback_data=f"alerttoggle|{minutes}")])
    rows.append([InlineKeyboardButton("Devam Et ➡️", callback_data="alertdone")])
    rows.append([InlineKeyboardButton("\U0001f3e0 Ana Menu", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def build_main_menu_keyboard():
    rows = [
        [InlineKeyboardButton("➕ Hatirlatici Ekle", callback_data="add")],
        [InlineKeyboardButton("\U0001f4cb Tum Hatirlaticilari Gor", callback_data="list")],
        [InlineKeyboardButton("\U0001f5d1 Hatirlatici Sil", callback_data="delprompt")],
    ]
    return InlineKeyboardMarkup(rows)


def build_reminder_list_text(chat_id):
    items = list(reminders.find({"chat_id": chat_id}).sort("remind_at", 1).limit(50))
    if not items:
        return "Henuz hatirlatici eklemediniz."
    lines = ["Tum hatirlaticilariniz:\n"]
    for it in items:
        dt_local = it["remind_at"].replace(tzinfo=UTC).astimezone(TZ)
        alerts = it.get("alerts", [])
        done = bool(alerts) and all(a.get("sent") for a in alerts)
        durum = " (tamamlandi)" if done else ""
        seq = it.get("seq_id", "?")
        lines.append(f"#{seq} | {dt_local.strftime('%d.%m.%Y %H:%M')} | {it['text']}{durum}")
    lines.append("\nSilmek icin Ana Menu > Hatirlatici Sil'den ID'yi (# olmadan) yazin.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Telegram: baslangic (Telegram'in kendi "Start" dugmesi bu komutu otomatik
# gonderir, kullanici bunu elle yazmaz)
# ---------------------------------------------------------------------------
def start(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    set_primary_chat_id(chat_id)
    update.message.reply_text(
        "Merhaba! Ben is ajandanizim. Hatirlaticilarinizi ve gelen onemli "
        "mailleri buradan takip edeceksiniz - hepsi asagidaki butonlarla.",
        reply_markup=build_main_menu_keyboard(),
    )


# ---------------------------------------------------------------------------
# Buton yonlendiricisi
# ---------------------------------------------------------------------------
def replace_ui(query, text, reply_markup=None):
    """
    Butonlu ekrani her zaman sohbetin EN ALTINDA tutmak icin: eski mesaji
    silip ayni icerikle yeni bir mesaj gonderir. Sadece markup'i degistirip
    eski mesaji yerinde birakmak (edit), araya baska bildirimler (mail vb.)
    girdiginde menunun yukarida "kaybolmus" gibi gorunmesine yol aciyordu.
    """
    chat_id = query.message.chat_id
    try:
        query.message.delete()
    except Exception:
        pass
    try:
        bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
    except Exception as e:
        logger.error("Mesaj gonderilemedi: %s", e)


def button_router(update: Update, context: CallbackContext):
    query = update.callback_query
    chat_id = query.message.chat_id
    data = query.data

    if data == "noop":
        query.answer()
        return

    if data == "menu":
        PENDING.pop(chat_id, None)
        query.answer()
        replace_ui(query, "Ana Menu:", build_main_menu_keyboard())
        return

    if data == "add":
        query.answer()
        today = datetime.now(TZ)
        PENDING[chat_id] = {}
        replace_ui(query, "Hatirlaticinin tarihini secin:", build_calendar_keyboard(today.year, today.month))
        return

    if data.startswith("cal|"):
        _, year, month, direction = data.split("|")
        year, month = int(year), int(month)
        if direction == "prev":
            month -= 1
            if month == 0:
                month = 12
                year -= 1
        else:
            month += 1
            if month == 13:
                month = 1
                year += 1
        query.answer()
        replace_ui(query, "Hatirlaticinin tarihini secin:", build_calendar_keyboard(year, month))
        return

    if data.startswith("day|"):
        _, year, month, day = data.split("|")
        PENDING.setdefault(chat_id, {})
        PENDING[chat_id].update({"year": int(year), "month": int(month), "day": int(day)})
        query.answer()
        replace_ui(query, "Simdi saati secin:", build_hour_keyboard())
        return

    if data.startswith("hour|"):
        _, hour = data.split("|")
        PENDING.setdefault(chat_id, {})
        PENDING[chat_id]["hour"] = int(hour)
        query.answer()
        replace_ui(query, "Simdi dakikayi secin (5 dakikalik araliklarla):", build_minute_keyboard())
        return

    if data.startswith("min|"):
        _, minute = data.split("|")
        PENDING.setdefault(chat_id, {})
        PENDING[chat_id]["minute"] = int(minute)
        PENDING[chat_id]["alerts"] = set(DEFAULT_ALERTS)
        query.answer()
        replace_ui(
            query,
            "Ne zaman hatirlatayim? (Birden fazla secebilirsiniz)",
            build_alert_keyboard(chat_id),
        )
        return

    if data.startswith("alerttoggle|"):
        _, minutes = data.split("|")
        minutes = int(minutes)
        pending = PENDING.setdefault(chat_id, {})
        alerts = pending.setdefault("alerts", set())
        if minutes in alerts:
            alerts.discard(minutes)
        else:
            alerts.add(minutes)
        query.answer()
        replace_ui(
            query,
            "Ne zaman hatirlatayim? (Birden fazla secebilirsiniz)",
            build_alert_keyboard(chat_id),
        )
        return

    if data == "alertdone":
        pending = PENDING.get(chat_id, {})
        if not pending.get("alerts"):
            query.answer("En az bir secenek isaretleyin.", show_alert=True)
            return
        pending["awaiting_text"] = True
        query.answer()
        replace_ui(
            query,
            "Son adim: bu hatirlatici icin kisa bir aciklama yazip gonderin\n"
            "(ornek: Vize randevusu - Alman Konsoloslugu)",
        )
        return

    if data == "list":
        query.answer()
        replace_ui(query, build_reminder_list_text(chat_id), build_main_menu_keyboard())
        return

    if data == "delprompt":
        PENDING[chat_id] = {"awaiting_delete_id": True}
        query.answer()
        replace_ui(
            query,
            "Silmek istediginiz hatirlaticinin ID numarasini yazip gonderin "
            "(# olmadan, sadece sayi - orn. 3).\n\n"
            "ID'leri 'Tum Hatirlaticilari Gor' ekraninda gorebilirsiniz.",
        )
        return

    query.answer()


def handle_text_input(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    pending = PENDING.get(chat_id)

    if pending and pending.get("awaiting_delete_id"):
        raw = update.message.text.strip().lstrip("#")
        try:
            seq_id = int(raw)
        except ValueError:
            update.message.reply_text("Lutfen sadece ID numarasini yazin (orn. 3).")
            return
        result = reminders.delete_one({"chat_id": chat_id, "seq_id": seq_id})
        PENDING.pop(chat_id, None)
        if result.deleted_count:
            update.message.reply_text(f"#{seq_id} silindi.", reply_markup=build_main_menu_keyboard())
        else:
            update.message.reply_text(
                f"#{seq_id} ID'li bir hatirlatici bulunamadi.", reply_markup=build_main_menu_keyboard()
            )
        return

    if not pending or not pending.get("awaiting_text"):
        update.message.reply_text(
            "Butonlari kullanarak devam edebilirsiniz \U0001f447",
            reply_markup=build_main_menu_keyboard(),
        )
        return

    text = update.message.text.strip()
    if not text:
        update.message.reply_text("Bos aciklama olmaz, tekrar yazin lutfen.")
        return

    try:
        dt_local = datetime(
            pending["year"], pending["month"], pending["day"],
            pending["hour"], pending["minute"], tzinfo=TZ,
        )
    except Exception:
        update.message.reply_text("Bir sorun olustu, lutfen bastan baslayin.", reply_markup=build_main_menu_keyboard())
        PENDING.pop(chat_id, None)
        return

    alerts_doc = [{"offset_min": m, "sent": False} for m in sorted(pending["alerts"], reverse=True)]
    seq_id = next_seq_id(chat_id)

    doc = {
        "chat_id": chat_id,
        "seq_id": seq_id,
        "text": text,
        "remind_at": dt_local.astimezone(UTC),
        "alerts": alerts_doc,
        "created_at": datetime.now(UTC),
    }
    reminders.insert_one(doc)
    add_calendar_event(text, dt_local)

    secilenler = ", ".join(offset_label(m) for m in sorted(pending["alerts"]))
    PENDING.pop(chat_id, None)

    update.message.reply_text(
        f"Hatirlatici eklendi (ID: #{seq_id}):\n{dt_local.strftime('%d.%m.%Y %H:%M')} - {text}\n"
        f"Bildirim zamanlari: {secilenler}\n"
        f"Takvime de islendi (baglantiliysa).",
        reply_markup=build_main_menu_keyboard(),
    )


dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CallbackQueryHandler(button_router))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text_input))


# ---------------------------------------------------------------------------
# Flask uc noktalari
# ---------------------------------------------------------------------------
@app.route(f"/webhook/{WEBHOOK_SECRET}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "OK"


@app.route("/")
def health():
    # UptimeRobot bu adresi periyodik yoklayarak servisi uyanik tutar.
    return "Is ajandasi botu calisiyor."


# ---------------------------------------------------------------------------
# Periyodik kontrol: zamani gelen hatirlaticilari gonderir
# ---------------------------------------------------------------------------
def check_due_reminders():
    now_utc = datetime.now(UTC)
    docs = list(reminders.find({"alerts.sent": False}))
    for r in docs:
        remind_at = r["remind_at"].replace(tzinfo=UTC)
        remind_at_local = remind_at.astimezone(TZ)
        changed = False
        for alert in r["alerts"]:
            if alert["sent"]:
                continue
            trigger_time = remind_at - timedelta(minutes=alert["offset_min"])
            if now_utc >= trigger_time:
                msg = alert_message(r["text"], remind_at_local, alert["offset_min"])
                try:
                    bot.send_message(chat_id=r["chat_id"], text=msg)
                except Exception as e:
                    logger.error("Telegram mesaji gonderilemedi: %s", e)
                send_email("Hatirlatici", msg)
                send_sms(msg)
                alert["sent"] = True
                changed = True
        if changed:
            reminders.update_one({"_id": r["_id"]}, {"$set": {"alerts": r["alerts"]}})


# ---------------------------------------------------------------------------
# Mail izleme: Gmail / Yandex (IMAP) ve Outlook (Microsoft Graph / OAuth)
# ---------------------------------------------------------------------------
def decode_mime_words(s):
    if not s:
        return ""
    try:
        parts = decode_header(s)
    except Exception:
        return s
    decoded = ""
    for text, enc in parts:
        if isinstance(text, bytes):
            try:
                decoded += text.decode(enc or "utf-8", errors="replace")
            except Exception:
                decoded += text.decode("utf-8", errors="replace")
        else:
            decoded += text
    return decoded


def get_email_body_snippet(msg, limit=300):
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if ctype == "text/plain" and "attachment" not in disp:
                charset = part.get_content_charset() or "utf-8"
                try:
                    body = part.get_payload(decode=True).decode(charset, errors="replace")
                except Exception:
                    body = ""
                if body:
                    break
        if not body:
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        html = part.get_payload(decode=True).decode(charset, errors="replace")
                        body = re.sub("<[^<]+?>", " ", html)
                    except Exception:
                        pass
                    break
    else:
        charset = msg.get_content_charset() or "utf-8"
        try:
            payload = msg.get_payload(decode=True)
            body = payload.decode(charset, errors="replace") if payload else str(msg.get_payload())
        except Exception:
            body = str(msg.get_payload())
    body = " ".join(body.split())
    return body[:limit]


def mail_matches_filter(sender, subject, body):
    if not EMAIL_KEYWORDS:
        return True
    haystack = f"{subject} {sender} {body}".lower()
    return any(k in haystack for k in EMAIL_KEYWORDS)


def extract_attachments(msg):
    """
    Bir e-postadaki tum dosyalari (ek olarak eklenmis veya govde icine
    gomulmus resimler dahil - ornegin OTP kodu resmi) cikarir. Dosya adi
    olan her parcayi bir ek olarak kabul eder.
    """
    attachments = []
    if not msg.is_multipart():
        return attachments
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype in ("multipart/mixed", "multipart/alternative", "multipart/related"):
            continue
        filename = part.get_filename()
        if not filename:
            continue
        try:
            data = part.get_payload(decode=True)
        except Exception:
            data = None
        if not data:
            continue
        attachments.append({
            "filename": decode_mime_words(filename),
            "data": data,
            "content_type": ctype,
        })
    return attachments


def send_telegram_file(chat_id, filename, data, content_type):
    bio = BytesIO(data)
    bio.name = filename or "dosya"
    try:
        if content_type and content_type.startswith("image/"):
            bot.send_photo(chat_id=chat_id, photo=bio, caption=filename)
        else:
            bot.send_document(chat_id=chat_id, document=bio, filename=filename)
    except Exception as e:
        logger.error("Dosya gonderilemedi (%s): %s", filename, e)


def notify_new_mail(provider, sender, subject, body, attachments=None):
    if not mail_matches_filter(sender, subject, body):
        return
    chat_id = get_primary_chat_id()
    if not chat_id:
        return
    text = f"\U0001f4e7 Yeni e-posta ({provider})\nKimden: {sender}\nKonu: {subject}\n\n{body[:300]}"
    try:
        bot.send_message(chat_id=chat_id, text=text)
    except Exception as e:
        logger.error("Mail bildirimi gonderilemedi: %s", e)

    for att in (attachments or []):
        if len(att["data"]) > MAIL_ATTACHMENT_MAX_BYTES:
            try:
                bot.send_message(
                    chat_id=chat_id,
                    text=f"(Ek dosya '{att['filename']}' {MAIL_ATTACHMENT_MAX_MB:.0f} MB sinirindan buyuk oldugu icin gonderilemedi)",
                )
            except Exception:
                pass
            continue
        send_telegram_file(chat_id, att["filename"], att["data"], att["content_type"])


def get_last_uid(account_key):
    doc = mail_state.find_one({"_id": account_key})
    return doc.get("last_uid") if doc else None


def set_last_uid(account_key, uid):
    mail_state.update_one({"_id": account_key}, {"$set": {"last_uid": uid}}, upsert=True)


def poll_imap_account(account_key, host, user, password, label):
    if not (user and password):
        return
    try:
        imap = imaplib.IMAP4_SSL(host, 993)
        try:
            imap.login(user, password)
            imap.select("INBOX")
            last_uid = get_last_uid(account_key)
            if last_uid:
                status, data = imap.uid("search", None, f"(UID {last_uid + 1}:*)")
            else:
                status, data = imap.uid("search", None, "ALL")
            uids = [u for u in data[0].split() if u]
            if not last_uid:
                # ilk kurulumda gecmisi bombardimana tutmamak icin son 5 mail
                uids = uids[-5:]
            max_uid = last_uid
            for uid in uids:
                uid_int = int(uid)
                if last_uid and uid_int <= last_uid:
                    continue
                status, msg_data = imap.uid("fetch", uid, "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                msg = email_lib.message_from_bytes(raw)
                subject = decode_mime_words(msg.get("Subject", ""))
                sender = decode_mime_words(msg.get("From", ""))
                body = get_email_body_snippet(msg)
                attachments = extract_attachments(msg)
                notify_new_mail(label, sender, subject, body, attachments)
                if not max_uid or uid_int > max_uid:
                    max_uid = uid_int
            if max_uid:
                set_last_uid(account_key, max_uid)
        finally:
            try:
                imap.logout()
            except Exception:
                pass
    except Exception as e:
        logger.error("%s IMAP hatasi: %s", label, e)


def get_outlook_refresh_token_value(idx, env_value):
    doc = mail_state.find_one({"_id": f"outlook_token_{idx}"})
    if doc and doc.get("refresh_token"):
        return doc["refresh_token"]
    return env_value


def get_outlook_access_token(idx, env_value):
    refresh_token = get_outlook_refresh_token_value(idx, env_value)
    if not (OUTLOOK_CLIENT_ID and refresh_token):
        return None
    try:
        r = requests.post(
            "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
            data={
                "client_id": OUTLOOK_CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": "https://graph.microsoft.com/Mail.Read offline_access",
            },
            timeout=15,
        )
        data = r.json()
        if "access_token" not in data:
            logger.error("Outlook #%s token yenilenemedi: %s", idx, data)
            return None
        new_refresh = data.get("refresh_token")
        if new_refresh:
            mail_state.update_one(
                {"_id": f"outlook_token_{idx}"}, {"$set": {"refresh_token": new_refresh}}, upsert=True
            )
        return data["access_token"]
    except Exception as e:
        logger.error("Outlook #%s token istegi basarisiz: %s", idx, e)
        return None


def fetch_outlook_attachments(token, message_id):
    attachments = []
    try:
        r = requests.get(
            f"https://graph.microsoft.com/v1.0/me/messages/{message_id}/attachments",
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
        if r.status_code != 200:
            logger.error("Outlook ek dosyalari alinamadi: %s %s", r.status_code, r.text)
            return attachments
        for item in r.json().get("value", []):
            content_bytes = item.get("contentBytes")
            if not content_bytes:
                continue
            try:
                data = base64.b64decode(content_bytes)
            except Exception:
                continue
            attachments.append({
                "filename": item.get("name") or "dosya",
                "data": data,
                "content_type": item.get("contentType") or "application/octet-stream",
            })
    except Exception as e:
        logger.error("Outlook ek dosyalari istegi basarisiz: %s", e)
    return attachments


def poll_outlook_account(idx, env_value):
    token = get_outlook_access_token(idx, env_value)
    if not token:
        return

    doc = mail_state.find_one({"_id": f"outlook_{idx}"})
    last_check = None
    if doc and doc.get("last_check"):
        last_check = doc["last_check"].replace(tzinfo=UTC)

    try:
        r = requests.get(
            "https://graph.microsoft.com/v1.0/me/messages",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "$orderby": "receivedDateTime desc",
                "$top": "15",
                "$select": "id,subject,from,receivedDateTime,bodyPreview,hasAttachments",
            },
            timeout=15,
        )
        if r.status_code != 200:
            logger.error("Graph API hatasi (Outlook #%s): %s %s", idx, r.status_code, r.text)
            return
        messages = r.json().get("value", [])
    except Exception as e:
        logger.error("Outlook #%s mesajlari alinamadi: %s", idx, e)
        return

    newest_seen = last_check
    for m in reversed(messages):
        try:
            received = datetime.fromisoformat(m["receivedDateTime"].replace("Z", "+00:00"))
        except Exception:
            continue
        if last_check and received <= last_check:
            continue
        sender = ""
        try:
            sender = m.get("from", {}).get("emailAddress", {}).get("address", "")
        except Exception:
            pass
        attachments = fetch_outlook_attachments(token, m["id"]) if m.get("hasAttachments") else []
        notify_new_mail(f"Outlook #{idx}", sender, m.get("subject", ""), m.get("bodyPreview", ""), attachments)
        if not newest_seen or received > newest_seen:
            newest_seen = received

    if newest_seen:
        mail_state.update_one(
            {"_id": f"outlook_{idx}"}, {"$set": {"last_check": newest_seen}}, upsert=True
        )


def check_new_mail():
    for acc in IMAP_ACCOUNTS:
        poll_imap_account(acc["key"], acc["host"], acc["address"], acc["password"], acc["label"])
    for idx, token_env in enumerate(OUTLOOK_REFRESH_TOKENS_ENV, start=1):
        poll_outlook_account(idx, token_env)


scheduler = BackgroundScheduler(timezone="UTC")
scheduler.add_job(check_due_reminders, "interval", seconds=60, max_instances=1)
scheduler.add_job(check_new_mail, "interval", seconds=MAIL_CHECK_INTERVAL_SECONDS, max_instances=1)
scheduler.start()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
