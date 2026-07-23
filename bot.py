import os
import re
import time
import json
import socket
import html as html_lib
import base64
import mimetypes
import imaplib
import email as email_lib
import logging
import calendar as pycalendar
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from datetime import datetime, timedelta, date
from queue import Queue
from zoneinfo import ZoneInfo
from email.header import decode_header

# Google Sheets/Takvim API'si (httplib2 uzerinden) ve baska hicbir zaman
# asimi belirtilmemis diger aglar icin genel bir guvenlik agi: sunucu tarafi
# bir sekilde yanit vermezse thread sonsuza kadar degil, en fazla bu sure
# kadar bekler. Bu, botun bazen "kasilmasinin" olasi nedenlerinden biriydi -
# 4 webhook thread'inden biri boyle bir cagriya takilirsa butonlar yanitsiz
# kalabiliyordu.
socket.setdefaulttimeout(25)

from flask import Flask, request
from telegram import (
    Update,
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
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
    from google.oauth2.service_account import Credentials as ServiceAccountCredentials
    from googleapiclient.discovery import build
    GOOGLE_LIBS_AVAILABLE = True
except ImportError:
    GOOGLE_LIBS_AVAILABLE = False

try:
    from mrz.checker.td3 import TD3CodeChecker
    MRZ_LIB_AVAILABLE = True
except ImportError:
    MRZ_LIB_AVAILABLE = False

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

# Ekip erisimi: bu kod bos birakilirsa botu Telegram'da acan HERKES otomatik
# yetkilenir (tek kisilik kullanim icin varsayilan). Birden fazla kisinin
# (siz + ekip arkadaslariniz) ayni botu, ayni paylasili hatirlatici/pasaport
# verisini gorerek kullanmasini istiyorsaniz bir deger girin - yeni katilan
# herkes Telegram'da "Start"a bastiktan sonra bu kodu yazarak eklenir.
TEAM_ACCESS_CODE = os.environ.get("TEAM_ACCESS_CODE", "").strip()

EMAILJS_SERVICE_ID = os.environ.get("EMAILJS_SERVICE_ID")
EMAILJS_TEMPLATE_ID = os.environ.get("EMAILJS_TEMPLATE_ID")
EMAILJS_PUBLIC_KEY = os.environ.get("EMAILJS_PUBLIC_KEY")
EMAILJS_PRIVATE_KEY = os.environ.get("EMAILJS_PRIVATE_KEY")
USER_EMAIL = os.environ.get("USER_EMAIL")
# Birden fazla adrese hatirlatici e-postasi gitsin isterseniz virgulle ayirin:
# USER_EMAIL=birinci@gmail.com,ikinci@gmail.com,ucuncu@gmail.com
USER_EMAILS = [e.strip() for e in (USER_EMAIL or "").split(",") if e.strip()]

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN")
GOOGLE_CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "primary")

# --- Pasaport -> Google Sheets entegrasyonu (opsiyonel) ---
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
SHEETS_SPREADSHEET_ID = os.environ.get("SHEETS_SPREADSHEET_ID")
OCR_SPACE_API_KEY = os.environ.get("OCR_SPACE_API_KEY")
MASTER_SHEET_NAME = os.environ.get("MASTER_SHEET_NAME", "RANDEVU ALINMIŞLAR")
ACCOUNTS_SHEET_NAME = os.environ.get("ACCOUNTS_SHEET_NAME", "hesap tanımla")

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
# OAuth gerektiriyor. Bu bot Outlook/Hotmail desteklemiyor (sadece Gmail,
# Yandex ve benzeri uygulama sifresiyle IMAP'e izin veren saglayicilar).
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
                "IMAP_HOST_%s ekleyin (Outlook/Hotmail bu bot tarafindan "
                "desteklenmiyor).", i, addr, i,
            )
            continue
        label = os.environ.get(f"IMAP_LABEL_{i}") or addr
        accounts.append({"key": f"imap_{i}", "host": host, "address": addr, "password": pwd, "label": label})
    return accounts


IMAP_ACCOUNTS = load_imap_accounts()

# Mail eklerinin (resim/pdf/word vb.) Telegram'a gonderilecek azami boyutu.
MAIL_ATTACHMENT_MAX_MB = float(os.environ.get("MAIL_ATTACHMENT_MAX_MB", "20"))
MAIL_ATTACHMENT_MAX_BYTES = int(MAIL_ATTACHMENT_MAX_MB * 1024 * 1024)


# Bos birakilirsa TUM gelen mailler Telegram'a duser. Doldurulursa (virgulle
# ayrilmis kelimeler) sadece konu/gonderen/govdede bu kelimelerden birini
# iceren mailler dusurulur. Vize takibi icin ornek:
# EMAIL_KEYWORDS=vize,visa,consulate,embassy,randevu,appointment,sefaret,konsolosluk
EMAIL_KEYWORDS = [
    k.strip().lower()
    for k in os.environ.get("EMAIL_KEYWORDS", "").split(",")
    if k.strip()
]
MAIL_CHECK_INTERVAL_SECONDS = int(os.environ.get("MAIL_CHECK_INTERVAL_SECONDS", "15"))

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
team = db["team"]
sheet_state = db["sheet_state"]

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
    """
    Hatirlatici ID'si artik EKIP genelinde tek bir sayaçtan uretiliyor (chat
    basina degil) - boylece birden fazla kisi ayni botu kullansa da ID'ler
    çakismaz, herkes ayni numarayla ayni kaydi konusabilir.
    """
    doc = counters.find_one_and_update(
        {"_id": "reminder_seq"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc["seq"]


# --- Ekip erisimi (birden fazla kisi ayni paylasili veriyi gorsun/yonetsin) ---
def is_authorized(chat_id):
    if not TEAM_ACCESS_CODE:
        return True
    return team.find_one({"_id": chat_id}) is not None


def authorize_chat(chat_id):
    team.update_one({"_id": chat_id}, {"$set": {"joined_at": datetime.now(UTC)}}, upsert=True)


def get_authorized_chat_ids():
    if not TEAM_ACCESS_CODE:
        primary = get_primary_chat_id()
        return [primary] if primary else []
    return [doc["_id"] for doc in team.find({})]


def broadcast_message(text, **kwargs):
    for cid in get_authorized_chat_ids():
        try:
            bot.send_message(chat_id=cid, text=text, **kwargs)
        except Exception as e:
            logger.error("Mesaj gonderilemedi (chat_id=%s): %s", cid, e)


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
    if not (EMAILJS_SERVICE_ID and EMAILJS_TEMPLATE_ID and EMAILJS_PUBLIC_KEY and USER_EMAILS):
        missing = [
            name for name, val in [
                ("EMAILJS_SERVICE_ID", EMAILJS_SERVICE_ID),
                ("EMAILJS_TEMPLATE_ID", EMAILJS_TEMPLATE_ID),
                ("EMAILJS_PUBLIC_KEY", EMAILJS_PUBLIC_KEY),
                ("USER_EMAIL", USER_EMAILS),
            ] if not val
        ]
        logger.warning(
            "E-posta gonderilemedi: su ortam degiskenleri eksik/bos: %s", ", ".join(missing)
        )
        return
    logger.info("E-posta gonderimi baslatiliyor (%d alici): %s", len(USER_EMAILS), ", ".join(USER_EMAILS))
    # USER_EMAIL virgulle birden fazla adres icerebilir - her birine ayri
    # gonderiyoruz (EmailJS'in ucretsiz sablonlari tek aliciya gore kurulu).
    for addr in USER_EMAILS:
        payload = {
            "service_id": EMAILJS_SERVICE_ID,
            "template_id": EMAILJS_TEMPLATE_ID,
            "user_id": EMAILJS_PUBLIC_KEY,
            "accessToken": EMAILJS_PRIVATE_KEY,
            "template_params": {"to_email": addr, "subject": subject, "message": body},
        }
        try:
            r = requests.post("https://api.emailjs.com/api/v1.0/email/send", json=payload, timeout=10)
            if r.status_code != 200:
                logger.error("EmailJS hatasi (%s): %s %s", addr, r.status_code, r.text)
            else:
                logger.info("EmailJS basarili (%s): %s", addr, r.text)
        except Exception as e:
            logger.error("E-posta gonderilemedi (%s): %s", addr, e)


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


TIME_ENTRY_KEYBOARD = InlineKeyboardMarkup(
    [[InlineKeyboardButton("\U0001f3e0 Ana Menu", callback_data="menu")]]
)


def build_alert_keyboard(chat_id):
    selected = PENDING.get(chat_id, {}).get("alerts", set())
    rows = []
    for minutes, label in ALERT_OPTIONS:
        mark = "✅" if minutes in selected else "⬜"
        rows.append([InlineKeyboardButton(f"{mark} {label}", callback_data=f"alerttoggle|{minutes}")])
    rows.append([InlineKeyboardButton("Devam Et ➡️", callback_data="alertdone")])
    rows.append([InlineKeyboardButton("\U0001f3e0 Ana Menu", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


MENU_BUTTON_TEXT = "☰ Menü"

# Sohbetin en altinda, mesajlar arasinda kaybolmayan sabit klavye. Ana
# menuyu her zaman tek dokunusla acmak icin.
PERSISTENT_KEYBOARD = ReplyKeyboardMarkup([[MENU_BUTTON_TEXT]], resize_keyboard=True)


def build_main_menu_keyboard():
    rows = [
        [InlineKeyboardButton("➕ Hatirlatici Ekle", callback_data="add")],
        [InlineKeyboardButton("\U0001f4cb Tum Hatirlaticilari Gor", callback_data="list")],
        [InlineKeyboardButton("\U0001f5d1 Hatirlatici Sil", callback_data="delprompt")],
        [InlineKeyboardButton("\U0001f6c2 Pasaport Ekle", callback_data="passport_add")],
        [InlineKeyboardButton("✅ Randevu Aldim", callback_data="appt_start")],
        [InlineKeyboardButton("\U0001f4cb Pasaport Kayitlarini Gor", callback_data="passport_list_start")],
        [InlineKeyboardButton("\U0001f50d Kayit Ara (Isim/ID)", callback_data="search_start")],
        [InlineKeyboardButton("\U0001f4ca Rapor Al", callback_data="report_start")],
    ]
    return InlineKeyboardMarkup(rows)


PASSPORT_MANUAL_FIELDS = [
    ("vize_turu", "Vize Turu nedir? (orn. Hollanda-Ankara-Aile Ziyareti)"),
    ("islemi_yapan", "Islemi yapan kimin adina? (orn. ISMAIL)"),
    ("yonlendiren_kisi", "Yonlendiren kisi kim? (referans/yonlendiren yoksa '-' yazin)"),
    ("mail", "Basvuru icin kullanilacak mail adresi nedir?"),
    ("sifre", "Bu mail hesabinin sifresi nedir?"),
    ("tel", "Telefon numarasi nedir?"),
]

# OCR/MRZ basarisiz olursa veya kullanici "Elle Gir" secerse, kimlik
# alanlari da tek tek soru olarak sorulur.
IDENTITY_FIELD_PROMPTS = [
    ("isim", "Isim nedir?"),
    ("soyisim", "Soyisim nedir?"),
    ("pasaport_no", "Pasaport No nedir?"),
    ("dogum_tarihi", "Dogum Tarihi nedir? (GG.AA.YYYY, orn. 05.03.1990)"),
    ("pasaport_skt", "Pasaport Son Kullanma Tarihi nedir? (GG.AA.YYYY)"),
    ("uyruk", "Uyruk nedir?"),
    ("kimlik_no", "Kimlik No nedir?"),
]

DATE_PATTERN = re.compile(r"^\s*(0[1-9]|[12]\d|3[01])\.(0[1-9]|1[0-2])\.(20\d{2})\s*$")

FIELD_LABELS = {
    "isim": "Isim", "soyisim": "Soyisim", "pasaport_no": "Pasaport No",
    "dogum_tarihi": "Dogum Tarihi", "pasaport_skt": "Pasaport SKT", "uyruk": "Uyruk",
    "kimlik_no": "Kimlik No", "vize_turu": "Vize Turu", "islemi_yapan": "Islemi Yapan",
    "yonlendiren_kisi": "Yonlendiren Kisi", "mail": "Mail", "sifre": "Sifre", "tel": "Tel",
}


def build_country_keyboard(service, callback_prefix):
    """
    Google Sheets'teki ulke sayfalarini CANLI okuyup buton listesi olusturur.
    Sayfa isimlerini PENDING["country_list"] icine kaydedip index kullanarak
    referans veriyoruz - boylece Turkce karakter/bosluk/'|' iceren sayfa
    isimleri callback_data'da sorun cikarmaz.
    """
    names = list_country_sheets(service)
    rows = [[InlineKeyboardButton(name, callback_data=f"{callback_prefix}|{i}")] for i, name in enumerate(names)]
    rows.append([InlineKeyboardButton("\U0001f3e0 Ana Menu", callback_data="menu")])
    return InlineKeyboardMarkup(rows), names


def build_reminder_list_text(chat_id):
    # Hatirlaticilar artik ekip genelinde paylasili: kim ekledi olursa olsun
    # tum yetkili sohbetler ayni listeyi gorur.
    items = list(reminders.find({}).sort("remind_at", 1).limit(50))
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

    if not is_authorized(chat_id):
        PENDING[chat_id] = {"awaiting_access_code": True}
        update.message.reply_text(
            "Bu, paylasilan bir is ajandasi botu. Devam etmek icin size verilen "
            "erisim kodunu yazip gonderin."
        )
        return

    set_primary_chat_id(chat_id)
    update.message.reply_text(
        "Merhaba! Ben is ajandanizim. Hatirlaticilarinizi ve gelen onemli "
        "mailleri buradan takip edeceksiniz - hepsi asagidaki butonlarla.\n\n"
        f"Klavyenizdeki '{MENU_BUTTON_TEXT}' butonuna her an basarak ana menuye donebilirsiniz.",
        reply_markup=PERSISTENT_KEYBOARD,
    )
    update.message.reply_text("Ana Menu:", reply_markup=build_main_menu_keyboard())


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

    if not is_authorized(chat_id):
        query.answer("Bu botu kullanmak icin once erisim kodunu girin.", show_alert=True)
        return

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
        PENDING[chat_id].update({
            "year": int(year), "month": int(month), "day": int(day),
            "awaiting_time_text": True,
        })
        query.answer()
        replace_ui(
            query,
            "Saati yazip gonderin (SS:DD formatinda, ornek: 09:15 ya da 14:30):",
            TIME_ENTRY_KEYBOARD,
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

    # --- Pasaport ekleme akisi ---
    if data == "passport_add":
        query.answer()
        service = get_sheets_service()
        if not service:
            replace_ui(
                query,
                "Google Sheets baglantisi kurulu degil. Lutfen once GOOGLE_SERVICE_ACCOUNT_JSON "
                "ve SHEETS_SPREADSHEET_ID ayarlarini tamamlayin.",
                build_main_menu_keyboard(),
            )
            return
        keyboard, names = build_country_keyboard(service, "country_select")
        if not names:
            replace_ui(query, "Tabloda hicbir ulke sayfasi bulunamadi.", build_main_menu_keyboard())
            return
        PENDING[chat_id] = {"country_list": names}
        replace_ui(query, "Hangi ulke icin pasaport eklenecek?", keyboard)
        return

    if data.startswith("country_select|"):
        _, idx = data.split("|")
        pending = PENDING.get(chat_id, {})
        names = pending.get("country_list", [])
        try:
            country = names[int(idx)]
        except Exception:
            query.answer("Gecersiz secim, tekrar deneyin.", show_alert=True)
            return
        query.answer()
        PENDING[chat_id] = {"country_sheet": country, "awaiting_passport_photo": True}
        replace_ui(
            query,
            f"'{country}' sayfasina eklenecek. Simdi pasaportun fotografini gonderin "
            "(net cekilmis, MRZ satirlarinin - alttaki iki satirin - gorunur oldugu bir foto).",
        )
        return

    if data == "passport_manual_start":
        pending = PENDING.get(chat_id, {})
        country = pending.get("country_sheet")
        if not country:
            query.answer("Once bir ulke secmelisiniz.", show_alert=True)
            return
        query.answer()
        queue = list(IDENTITY_FIELD_PROMPTS) + list(PASSPORT_MANUAL_FIELDS)
        PENDING[chat_id] = {
            "country_sheet": country,
            "passport_fields": {},
            "manual_queue": queue,
            "manual_index": 0,
            "awaiting_passport_manual": True,
        }
        replace_ui(query, queue[0][1])
        return

    if data == "passport_confirm_yes":
        pending = PENDING.get(chat_id, {})
        country = pending.get("country_sheet")
        mrz_fields = pending.get("mrz_fields")
        if not country or not mrz_fields:
            query.answer("Bir sorun olustu, bastan baslayin.", show_alert=True)
            replace_ui(query, "Ana Menu:", build_main_menu_keyboard())
            return
        query.answer()
        queue = list(PASSPORT_MANUAL_FIELDS)
        PENDING[chat_id] = {
            "country_sheet": country,
            "passport_fields": dict(mrz_fields),
            "manual_queue": queue,
            "manual_index": 0,
            "awaiting_passport_manual": True,
        }
        replace_ui(query, queue[0][1])
        return

    if data == "passport_confirm_no":
        pending = PENDING.get(chat_id, {})
        country = pending.get("country_sheet")
        query.answer()
        PENDING[chat_id] = {"country_sheet": country, "awaiting_passport_photo": True}
        replace_ui(query, "Tamam, pasaportun fotografini tekrar gonderin.")
        return

    # --- Randevu Aldim akisi ---
    if data == "appt_start":
        query.answer()
        service = get_sheets_service()
        if not service:
            replace_ui(
                query,
                "Google Sheets baglantisi kurulu degil. Lutfen once GOOGLE_SERVICE_ACCOUNT_JSON "
                "ve SHEETS_SPREADSHEET_ID ayarlarini tamamlayin.",
                build_main_menu_keyboard(),
            )
            return
        keyboard, names = build_country_keyboard(service, "appt_country")
        if not names:
            replace_ui(query, "Tabloda hicbir ulke sayfasi bulunamadi.", build_main_menu_keyboard())
            return
        PENDING[chat_id] = {"country_list": names}
        replace_ui(query, "Hangi ulke sayfasindaki kayit icin randevu alindi?", keyboard)
        return

    if data.startswith("appt_country|"):
        _, idx = data.split("|")
        pending = PENDING.get(chat_id, {})
        names = pending.get("country_list", [])
        try:
            country = names[int(idx)]
        except Exception:
            query.answer("Gecersiz secim, tekrar deneyin.", show_alert=True)
            return
        query.answer()
        PENDING[chat_id] = {"country_sheet": country, "awaiting_appt_id": True}

        # Kullanicinin ID'yi tabloya bakip ezberlemesine gerek kalmasin diye,
        # bekleyen (henuz randevu alinmamis) kayitlarin ID + isim listesini
        # burada gosteriyoruz.
        service = get_sheets_service()
        records_text = ""
        if service:
            try:
                records = list_country_records(service, country, only_pending=True)
            except Exception as e:
                logger.error("Kayit listesi alinamadi: %s", e)
                records = []
            if records:
                lines = [f"#{rid} - {isim}" if isim else f"#{rid}" for rid, isim, _ in records[:50]]
                records_text = "\n\nBekleyen kayitlar:\n" + "\n".join(lines)
            else:
                records_text = "\n\n(Bu sayfada bekleyen kayit gorunmuyor.)"

        replace_ui(
            query,
            f"'{country}' sayfasindaki kaydin ID numarasini yazip gonderin (sadece sayi, orn. 5)."
            f"{records_text}",
        )
        return

    # --- Pasaport kayitlarini goruntuleme (sadece listeleme, duzenlemez) ---
    if data == "passport_list_start":
        query.answer()
        service = get_sheets_service()
        if not service:
            replace_ui(
                query,
                "Google Sheets baglantisi kurulu degil. Lutfen once GOOGLE_SERVICE_ACCOUNT_JSON "
                "ve SHEETS_SPREADSHEET_ID ayarlarini tamamlayin.",
                build_main_menu_keyboard(),
            )
            return
        keyboard, names = build_country_keyboard(service, "passport_list_country")
        if not names:
            replace_ui(query, "Tabloda hicbir ulke sayfasi bulunamadi.", build_main_menu_keyboard())
            return
        PENDING[chat_id] = {"country_list": names}
        replace_ui(query, "Hangi ulke sayfasindaki kayitlari gormek istiyorsun?", keyboard)
        return

    if data.startswith("passport_list_country|"):
        _, idx = data.split("|")
        pending = PENDING.get(chat_id, {})
        names = pending.get("country_list", [])
        try:
            country = names[int(idx)]
        except Exception:
            query.answer("Gecersiz secim, tekrar deneyin.", show_alert=True)
            return
        query.answer()
        PENDING.pop(chat_id, None)

        service = get_sheets_service()
        try:
            records = list_country_records(service, country, only_pending=False)
        except Exception as e:
            logger.error("Kayit listesi alinamadi: %s", e)
            records = []

        if not records:
            text = f"'{country}' sayfasinda kayit bulunamadi."
        else:
            lines = []
            for rid, isim, sonuc in records[:80]:
                durum = "✅ Randevu Alindi" if sonuc else "\U0001f7e1 Bekliyor"
                lines.append(f"#{rid} - {isim or '(isimsiz)'} - {durum}")
            text = f"'{country}' sayfasindaki kayitlar:\n\n" + "\n".join(lines)

        replace_ui(query, text, build_main_menu_keyboard())
        return

    # --- Isim/ID ile tum sayfalarda arama ---
    if data == "search_start":
        query.answer()
        service = get_sheets_service()
        if not service:
            replace_ui(
                query,
                "Google Sheets baglantisi kurulu degil. Lutfen once GOOGLE_SERVICE_ACCOUNT_JSON "
                "ve SHEETS_SPREADSHEET_ID ayarlarini tamamlayin.",
                build_main_menu_keyboard(),
            )
            return
        PENDING[chat_id] = {"awaiting_search_query": True}
        replace_ui(query, "Aranacak ismi (veya ID numarasini) yazip gonderin:")
        return

    # --- Tarih araligi rapor (Excel) ---
    if data == "report_start":
        query.answer()
        service = get_sheets_service()
        if not service:
            replace_ui(
                query,
                "Google Sheets baglantisi kurulu degil. Lutfen once GOOGLE_SERVICE_ACCOUNT_JSON "
                "ve SHEETS_SPREADSHEET_ID ayarlarini tamamlayin.",
                build_main_menu_keyboard(),
            )
            return
        PENDING[chat_id] = {"awaiting_report_range": True}
        replace_ui(
            query,
            "Rapor icin tarih araligini yazip gonderin\n"
            "(GG.AA.YYYY-GG.AA.YYYY, orn: 01.07.2026-31.07.2026):",
        )
        return

    # --- Mukerrer pasaport onayi ---
    if data in ("dup_confirm_yes", "dup_confirm_no"):
        query.answer()
        pending = PENDING.get(chat_id, {})
        if not pending.get("awaiting_duplicate_confirm"):
            return
        if data == "dup_confirm_no":
            PENDING.pop(chat_id, None)
            replace_ui(query, "Kayit iptal edildi.", build_main_menu_keyboard())
            return
        service = get_sheets_service()
        country = pending.get("country_sheet")
        if not service or not country:
            PENDING.pop(chat_id, None)
            replace_ui(query, "Google Sheets baglantisi kurulu degil.", build_main_menu_keyboard())
            return
        result = write_passport_row(service, country, pending.get("passport_fields", {}))
        PENDING.pop(chat_id, None)
        if result:
            next_id, _ = result
            replace_ui(
                query,
                f"Kayit '{country}' sayfasina eklendi (ID: {next_id}, bekleme listesi - sari).",
                build_main_menu_keyboard(),
            )
        else:
            replace_ui(query, "Kayit eklenemedi, sayfa basliklari okunamadi.", build_main_menu_keyboard())
        return

    query.answer()


TIME_PATTERN = re.compile(r"^\s*([01]?\d|2[0-3])\s*[:.]\s*([0-5]\d)\s*$")


def handle_text_input(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    pending = PENDING.get(chat_id)
    raw_text = update.message.text.strip()

    if pending and pending.get("awaiting_access_code"):
        if TEAM_ACCESS_CODE and raw_text == TEAM_ACCESS_CODE:
            authorize_chat(chat_id)
            PENDING.pop(chat_id, None)
            update.message.reply_text(
                "Katildiniz! Artik hatirlaticilari, pasaport kayitlarini ve mail "
                "bildirimlerini gorebilir, ekleyip duzenleyebilirsiniz.",
                reply_markup=PERSISTENT_KEYBOARD,
            )
            update.message.reply_text("Ana Menu:", reply_markup=build_main_menu_keyboard())
        else:
            update.message.reply_text("Kod yanlis, tekrar deneyin.")
        return

    if not is_authorized(chat_id):
        update.message.reply_text(
            "Bu botu kullanmak icin once Telegram'in 'Start' dugmesine basip erisim kodunu girin."
        )
        return

    if raw_text == MENU_BUTTON_TEXT:
        PENDING.pop(chat_id, None)
        update.message.reply_text("Ana Menu:", reply_markup=build_main_menu_keyboard())
        return

    if pending and pending.get("awaiting_time_text"):
        m = TIME_PATTERN.match(raw_text)
        if not m:
            update.message.reply_text(
                "Format hatali. Saat:Dakika seklinde yazin, ornek: 09:15"
            )
            return
        pending["hour"] = int(m.group(1))
        pending["minute"] = int(m.group(2))
        pending.pop("awaiting_time_text", None)
        pending["alerts"] = set(DEFAULT_ALERTS)
        update.message.reply_text(
            "Ne zaman hatirlatayim? (Birden fazla secebilirsiniz)",
            reply_markup=build_alert_keyboard(chat_id),
        )
        return

    if pending and pending.get("awaiting_delete_id"):
        raw = update.message.text.strip().lstrip("#")
        try:
            seq_id = int(raw)
        except ValueError:
            update.message.reply_text("Lutfen sadece ID numarasini yazin (orn. 3).")
            return
        # Ekip genelinde paylasili: kim ekledi olursa olsun ID ile silinebilir.
        result = reminders.delete_one({"seq_id": seq_id})
        PENDING.pop(chat_id, None)
        if result.deleted_count:
            update.message.reply_text(f"#{seq_id} silindi.", reply_markup=build_main_menu_keyboard())
        else:
            update.message.reply_text(
                f"#{seq_id} ID'li bir hatirlatici bulunamadi.", reply_markup=build_main_menu_keyboard()
            )
        return

    if pending and pending.get("awaiting_passport_manual"):
        key, _prompt = pending["manual_queue"][pending["manual_index"]]
        value = raw_text
        if key in ("dogum_tarihi", "pasaport_skt") and not DATE_PATTERN.match(value):
            update.message.reply_text("Format hatali. GG.AA.YYYY seklinde yazin, orn. 05.03.1990")
            return
        pending["passport_fields"][key] = value
        pending["manual_index"] += 1
        if pending["manual_index"] < len(pending["manual_queue"]):
            _next_key, next_prompt = pending["manual_queue"][pending["manual_index"]]
            update.message.reply_text(next_prompt)
            return
        service = get_sheets_service()
        country = pending["country_sheet"]
        if not service:
            update.message.reply_text("Google Sheets baglantisi kurulu degil.", reply_markup=build_main_menu_keyboard())
            PENDING.pop(chat_id, None)
            return

        # Mukerrer pasaport kontrolu: ayni pasaport numarasi baska bir
        # kayitta (herhangi bir ulke sayfasinda) varsa, eklemeden once
        # kullaniciya sorulur.
        pasaport_no = pending["passport_fields"].get("pasaport_no", "")
        dup = find_passport_duplicate(service, pasaport_no) if pasaport_no else None
        if dup:
            dup_country, dup_id = dup
            pending["awaiting_passport_manual"] = False
            pending["awaiting_duplicate_confirm"] = True
            update.message.reply_text(
                f"⚠️ Bu pasaport numarasi zaten '{dup_country}' sayfasinda ID #{dup_id} ile kayitli. "
                f"Yine de yeni bir kayit eklemek istiyor musunuz?",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Evet, yine de ekle", callback_data="dup_confirm_yes")],
                    [InlineKeyboardButton("Iptal", callback_data="dup_confirm_no")],
                ]),
            )
            return

        result = write_passport_row(service, country, pending["passport_fields"])
        PENDING.pop(chat_id, None)
        if result:
            next_id, _ = result
            update.message.reply_text(
                f"Kayit '{country}' sayfasina eklendi (ID: {next_id}, bekleme listesi - sari).",
                reply_markup=build_main_menu_keyboard(),
            )
        else:
            update.message.reply_text(
                "Kayit eklenemedi, sayfa basliklari okunamadi.", reply_markup=build_main_menu_keyboard()
            )
        return

    if pending and pending.get("awaiting_search_query"):
        PENDING.pop(chat_id, None)
        service = get_sheets_service()
        if not service:
            update.message.reply_text("Google Sheets baglantisi kurulu degil.", reply_markup=build_main_menu_keyboard())
            return
        results = search_records(service, raw_text)
        if not results:
            update.message.reply_text(
                f"'{raw_text}' icin sonuc bulunamadi.", reply_markup=build_main_menu_keyboard()
            )
            return
        lines = [f"'{raw_text}' icin {len(results)} sonuc bulundu:", ""]
        for country, rid, isim, sonuc in results[:30]:
            durum = "✅ " + sonuc if sonuc else "🟡 Beklemede"
            lines.append(f"#{rid} - {isim or '(isimsiz)'} - {country} - {durum}")
        update.message.reply_text("\n".join(lines), reply_markup=build_main_menu_keyboard())
        return

    if pending and pending.get("awaiting_report_range"):
        m = re.match(
            r"^\s*(\d{2})\.(\d{2})\.(\d{4})\s*-\s*(\d{2})\.(\d{2})\.(\d{4})\s*$", raw_text
        )
        if not m:
            update.message.reply_text(
                "Format hatali. GG.AA.YYYY-GG.AA.YYYY seklinde yazin, orn: 01.07.2026-31.07.2026"
            )
            return
        try:
            start_date = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            end_date = date(int(m.group(6)), int(m.group(5)), int(m.group(4)))
        except ValueError:
            update.message.reply_text("Gecersiz tarih. Lutfen tekrar deneyin.")
            return
        PENDING.pop(chat_id, None)
        if end_date < start_date:
            update.message.reply_text(
                "Bitis tarihi baslangictan once olamaz.", reply_markup=build_main_menu_keyboard()
            )
            return
        service = get_sheets_service()
        if not service:
            update.message.reply_text("Google Sheets baglantisi kurulu degil.", reply_markup=build_main_menu_keyboard())
            return
        update.message.reply_text("Rapor hazirlaniyor, birkac saniye surebilir...")
        bio = generate_appointment_report(service, start_date, end_date)
        if bio is None:
            update.message.reply_text(
                "Bu tarih araliginda kayit bulunamadi (ya da openpyxl kurulu degil).",
                reply_markup=build_main_menu_keyboard(),
            )
            return
        fname = f"rapor_{start_date.strftime('%d.%m.%Y')}_{end_date.strftime('%d.%m.%Y')}.xlsx"
        update.message.reply_document(document=bio, filename=fname)
        update.message.reply_text("Ana Menu:", reply_markup=build_main_menu_keyboard())
        return

    if pending and pending.get("awaiting_appt_id"):
        raw = raw_text.lstrip("#")
        try:
            target_id = int(raw)
        except ValueError:
            update.message.reply_text("Lutfen sadece ID numarasini yazin (orn. 5).")
            return
        service = get_sheets_service()
        country = pending["country_sheet"]
        if not service:
            update.message.reply_text("Google Sheets baglantisi kurulu degil.", reply_markup=build_main_menu_keyboard())
            PENDING.pop(chat_id, None)
            return
        row_index, headers, row_values = find_row_by_id(service, country, target_id)
        if row_index is None:
            update.message.reply_text(
                f"'{country}' sayfasinda ID {target_id} bulunamadi.", reply_markup=build_main_menu_keyboard()
            )
            PENDING.pop(chat_id, None)
            return
        pending.update({
            "row_index": row_index, "headers": headers, "row_values": row_values,
            "awaiting_appt_id": False, "awaiting_appt_referans": True,
        })
        update.message.reply_text("Referans numarasini yazin (yoksa '-' yazabilirsiniz).")
        return

    if pending and pending.get("awaiting_appt_referans"):
        pending["referans"] = raw_text
        pending["awaiting_appt_referans"] = False
        pending["awaiting_appt_date"] = True
        update.message.reply_text("Randevu gunu nedir? (GG.AA.YYYY, orn. 14.09.2026)")
        return

    if pending and pending.get("awaiting_appt_date"):
        if not DATE_PATTERN.match(raw_text):
            update.message.reply_text("Format hatali. GG.AA.YYYY seklinde yazin, orn. 14.09.2026")
            return
        pending["randevu_gunu"] = raw_text
        pending["awaiting_appt_date"] = False
        pending["awaiting_appt_time"] = True
        update.message.reply_text("Randevu saati nedir? (SS:DD, orn. 09:30)")
        return

    if pending and pending.get("awaiting_appt_time"):
        m = TIME_PATTERN.match(raw_text)
        if not m:
            update.message.reply_text("Format hatali. Saat:Dakika seklinde yazin, orn. 09:30")
            return
        saat = f"{int(m.group(1)):02d}:{m.group(2)}"
        service = get_sheets_service()
        country = pending["country_sheet"]
        if not service:
            update.message.reply_text("Google Sheets baglantisi kurulu degil.", reply_markup=build_main_menu_keyboard())
            PENDING.pop(chat_id, None)
            return
        row_index, headers = pending["row_index"], pending["headers"]
        extra_fields = {
            "referans": pending.get("referans", ""),
            "randevu_gunu": pending.get("randevu_gunu", ""),
            "saat": saat,
            "islem_sonucu": "Randevu Alindi",
        }
        updated_row = apply_extra_fields_to_row(
            service, country, row_index, headers, pending["row_values"], extra_fields
        )
        set_row_color(service, country, row_index, "red")
        copy_to_master(service, headers, updated_row, extra_fields)

        # Tablodaki randevu gunu/saatini hatirlatici sistemiyle birlestir: bu
        # akistan gecen her randevu icin otomatik bir Telegram/takvim
        # hatirlaticisi da olusturulur - ayrica elle hatirlatici eklemeye
        # gerek kalmaz.
        reminder_note = ""
        try:
            gun, ay, yil = pending["randevu_gunu"].split(".")
            dt_local = datetime(int(yil), int(ay), int(gun), int(m.group(1)), int(m.group(2)), tzinfo=TZ)
            data_map = {}
            for h, v in zip(headers, updated_row):
                key = match_header_to_field(h)
                if key:
                    data_map[key] = v
            kisi = f"{data_map.get('isim', '')} {data_map.get('soyisim', '')}".strip()
            reminder_desc = f"Vize Randevusu - {country}" + (f" ({kisi})" if kisi else "")
            new_seq = next_seq_id(chat_id)
            alerts_doc = [{"offset_min": mo, "sent": False} for mo in sorted(DEFAULT_ALERTS, reverse=True)]
            reminders.insert_one({
                "chat_id": chat_id,
                "seq_id": new_seq,
                "text": reminder_desc,
                "remind_at": dt_local.astimezone(UTC),
                "alerts": alerts_doc,
                "created_at": datetime.now(UTC),
            })
            add_calendar_event(reminder_desc, dt_local)
            reminder_note = f"\n\nBu randevu icin hatirlatici da otomatik olusturuldu (ID: #{new_seq})."
        except Exception as e:
            logger.error("Randevudan hatirlatici olusturulamadi: %s", e)

        PENDING.pop(chat_id, None)
        update.message.reply_text(
            f"Randevu bilgisi islendi. '{country}' sayfasinda satir kirmiziya boyandi ve "
            f"'{MASTER_SHEET_NAME}' sayfasina kopyalandi.{reminder_note}",
            reply_markup=build_main_menu_keyboard(),
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


def handle_photo_message(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    pending = PENDING.get(chat_id)

    if not is_authorized(chat_id):
        update.message.reply_text(
            "Bu botu kullanmak icin once Telegram'in 'Start' dugmesine basip erisim kodunu girin."
        )
        return

    if not pending or not pending.get("awaiting_passport_photo"):
        update.message.reply_text(
            "Once Ana Menu > \U0001f6c2 Pasaport Ekle ile bir ulke secin, sonra fotografi gonderin.",
            reply_markup=build_main_menu_keyboard(),
        )
        return

    country = pending.get("country_sheet")
    update.message.reply_text("Fotograf isleniyor, birkac saniye surebilir...")

    try:
        photo = update.message.photo[-1]
        tg_file = context.bot.get_file(photo.file_id)
        photo_bytes = bytes(tg_file.download_as_bytearray())
    except Exception as e:
        logger.error("Pasaport fotografi indirilemedi: %s", e)
        update.message.reply_text("Fotograf indirilemedi, lutfen tekrar gonderin.")
        return

    raw_text = ocr_space_extract_text(photo_bytes)
    mrz_lines = extract_mrz_lines(raw_text) if raw_text else None
    fields, valid = (None, False)
    if mrz_lines:
        fields, valid = parse_mrz(mrz_lines)

    if not fields:
        update.message.reply_text(
            "Pasaporttaki MRZ satirlari (en alttaki iki satir) okunamadi. Daha net, duz "
            "acili bir fotoyla tekrar deneyebilir ya da bilgileri elle girebilirsiniz.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("\U0001f501 Tekrar Cek", callback_data="passport_confirm_no")],
                [InlineKeyboardButton("✍️ Elle Gir", callback_data="passport_manual_start")],
            ]),
        )
        return

    pending["mrz_fields"] = fields
    pending.pop("awaiting_passport_photo", None)
    ozet = "\n".join(f"{FIELD_LABELS.get(k, k)}: {v or '(bos)'}" for k, v in fields.items())
    uyari = "" if valid else "\n\n⚠️ Kontrol basamagi dogrulanamadi, bilgileri dikkatlice kontrol edin."
    update.message.reply_text(
        f"Pasaporttan okunanlar ('{country}' sayfasi icin):\n{ozet}{uyari}\n\nDogru mu?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Dogru, Devam Et", callback_data="passport_confirm_yes")],
            [InlineKeyboardButton("\U0001f501 Tekrar Cek", callback_data="passport_confirm_no")],
            [InlineKeyboardButton("✍️ Elle Gir", callback_data="passport_manual_start")],
        ]),
    )


dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CallbackQueryHandler(button_router))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text_input))
dispatcher.add_handler(MessageHandler(Filters.photo, handle_photo_message))


def handle_dispatcher_error(update, context):
    """
    Herhangi bir buton/mesaj isleyicisinde beklenmeyen bir hata olursa,
    kullanici sessiz kalmis bir bot yerine (eskiden oldugu gibi) en azindan
    bir hata mesaji gorsun ve Render loglarina tam traceback dussun.
    """
    logger.error("Beklenmeyen hata: %s", context.error, exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_chat:
            context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Bir seyler ters gitti, tekrar dener misiniz? Sorun devam ederse Render loglarina bakilmasi gerekebilir.",
            )
    except Exception:
        pass


dispatcher.add_error_handler(handle_dispatcher_error)


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
                broadcast_message(msg)
                send_email("Hatirlatici", msg)
                send_sms(msg)
                alert["sent"] = True
                changed = True
        if changed:
            reminders.update_one({"_id": r["_id"]}, {"$set": {"alerts": r["alerts"]}})


# ---------------------------------------------------------------------------
# Mail izleme: Gmail / Yandex ve uygulama sifresiyle IMAP destekleyen diger saglayicilar
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


def looks_like_html(s):
    return bool(re.search(r"<\s*(html|!doctype|div|table|span|br|p|body)\b", s, re.IGNORECASE))


def html_to_text(raw_html):
    # script/style bloklarini icerigiyle birlikte kaldir
    text = re.sub(r"(?is)<(script|style)\b.*?</\1\s*>", " ", raw_html)
    # kalan tum etiketleri kaldir
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    # &nbsp; &amp; gibi HTML karakter kodlarini normal metne cevir
    text = html_lib.unescape(text)
    return " ".join(text.split())


def get_email_body_snippet(msg, limit=4000):
    body = ""
    html_fallback = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp.lower():
                continue
            if ctype == "text/plain" and not body:
                charset = part.get_content_charset() or "utf-8"
                try:
                    candidate = part.get_payload(decode=True).decode(charset, errors="replace")
                except Exception:
                    candidate = ""
                # Bazi gonderenler "duz metin" alaninin icine de ham HTML
                # koyuyor - bunu fark edip yine de HTML gibi temizleyelim.
                if candidate and not looks_like_html(candidate):
                    body = candidate
                elif candidate:
                    html_fallback = html_fallback or candidate
            elif ctype == "text/html" and not html_fallback:
                charset = part.get_content_charset() or "utf-8"
                try:
                    html_fallback = part.get_payload(decode=True).decode(charset, errors="replace")
                except Exception:
                    pass
    else:
        ctype = msg.get_content_type()
        charset = msg.get_content_charset() or "utf-8"
        try:
            payload = msg.get_payload(decode=True)
            raw = payload.decode(charset, errors="replace") if payload else str(msg.get_payload())
        except Exception:
            raw = str(msg.get_payload())
        if ctype == "text/html" or looks_like_html(raw):
            html_fallback = raw
        else:
            body = raw

    if not body and html_fallback:
        body = html_to_text(html_fallback)
    elif body and looks_like_html(body):
        body = html_to_text(body)

    body = " ".join(body.split())
    return body[:limit]


def mail_matches_filter(sender, subject, body):
    if not EMAIL_KEYWORDS:
        return True
    haystack = f"{subject} {sender} {body}".lower()
    return any(k in haystack for k in EMAIL_KEYWORDS)


def extract_attachments(msg):
    """
    Bir e-postadaki tum dosyalari cikarir: hem normal ekler (PDF, Word vb.)
    hem de govde icine gomulu, dosya adi OLMAYAN resimler (ornegin bazi
    saglayicilarin OTP kodu resimleri - bunlar sadece Content-ID ile
    referanslanir, ayri bir dosya adi tasimaz).
    """
    attachments = []
    if not msg.is_multipart():
        return attachments
    counter = 0
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype in ("multipart/mixed", "multipart/alternative", "multipart/related", "text/plain", "text/html"):
            continue
        filename = part.get_filename()
        content_id = part.get("Content-ID")
        if not filename and not content_id:
            continue
        try:
            data = part.get_payload(decode=True)
        except Exception:
            data = None
        if not data:
            continue
        if filename:
            filename = decode_mime_words(filename)
        else:
            counter += 1
            ext = mimetypes.guess_extension(ctype.split(";")[0].strip()) or ""
            filename = f"resim_{counter}{ext}"
        attachments.append({
            "filename": filename,
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


def send_long_message(text, max_len=3800):
    # Telegram tek mesajda en fazla 4096 karakter kabul ediyor - uzun mail
    # govdelerinin sessizce basarisiz olup hic gitmemesi yerine, gerekirse
    # birden fazla mesaja bolerek gonderiyoruz.
    if len(text) <= max_len:
        broadcast_message(text)
        return
    chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)]
    total = len(chunks)
    for i, chunk in enumerate(chunks, 1):
        suffix = f"\n\n[{i}/{total}]" if total > 1 else ""
        broadcast_message(chunk + suffix)


def notify_new_mail(provider, sender, subject, body, attachments=None):
    if not mail_matches_filter(sender, subject, body):
        return
    chat_ids = get_authorized_chat_ids()
    if not chat_ids:
        return
    body_clean = (body or "").strip() or "(govde metni yok)"
    text = f"\U0001f4e7 Yeni e-posta ({provider})\nKimden: {sender}\nKonu: {subject}\n\n{body_clean}"
    send_long_message(text)

    for att in (attachments or []):
        if len(att["data"]) > MAIL_ATTACHMENT_MAX_BYTES:
            broadcast_message(
                f"(Ek dosya '{att['filename']}' {MAIL_ATTACHMENT_MAX_MB:.0f} MB sinirindan buyuk oldugu icin gonderilemedi)"
            )
            continue
        for cid in chat_ids:
            send_telegram_file(cid, att["filename"], att["data"], att["content_type"])


def get_last_uid(account_key):
    doc = mail_state.find_one({"_id": account_key})
    return doc.get("last_uid") if doc else None


def set_last_uid(account_key, uid):
    mail_state.update_one({"_id": account_key}, {"$set": {"last_uid": uid}}, upsert=True)


def poll_imap_account(account_key, host, user, password, label):
    if not (user and password):
        return
    try:
        # timeout onemli: yavas/yanit vermeyen tek bir hesap butun
        # check_new_mail turunu kilitleyip diger hesaplarin da gecikmesine
        # yol acmasin diye.
        imap = imaplib.IMAP4_SSL(host, 993, timeout=10)
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


def check_new_mail():
    # Hesaplar SIRAYLA degil PARALEL taranir - hesap sayisi arttikca (10-15+)
    # sirayla tarama toplam sureyi kolayca 15sn'nin uzerine tasiyip
    # scheduler'in "maximum number of running instances reached" diyerek
    # sonraki turleri atlamasina (ve mail bildiriminin dakikalarca gecikmesine)
    # yol aciyordu. Paralel taramada toplam sure en yavas hesap kadar olur,
    # hesaplarin toplami kadar degil.
    if not IMAP_ACCOUNTS:
        return
    max_workers = min(len(IMAP_ACCOUNTS), 15)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(
                poll_imap_account, acc["key"], acc["host"], acc["address"], acc["password"], acc["label"]
            )
            for acc in IMAP_ACCOUNTS
        ]
        for f in as_completed(futures):
            try:
                f.result()
            except Exception as e:
                logger.error("IMAP hesap taramasi beklenmeyen hata: %s", e)


# =============================================================================
# PASAPORT -> GOOGLE SHEETS ENTEGRASYONU
# =============================================================================
SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def get_sheets_service():
    if not GOOGLE_LIBS_AVAILABLE:
        return None
    if not (GOOGLE_SERVICE_ACCOUNT_JSON and SHEETS_SPREADSHEET_ID):
        return None
    try:
        info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
        creds = ServiceAccountCredentials.from_service_account_info(info, scopes=SHEETS_SCOPES)
        return build("sheets", "v4", credentials=creds, cache_discovery=False)
    except Exception as e:
        logger.error("Sheets servisi olusturulamadi: %s", e)
        return None


# --- Sutun basligi eslestirme (sayfalar arasi farkli isimlendirmeleri toparlar) ---
FIELD_SYNONYMS = {
    "id": ["id", "sira"],
    "isim": ["isim", "ad"],
    "soyisim": ["soyisim", "soyad"],
    "pasaport_no": ["pasaport no"],
    "dogum_tarihi": ["dogum tarihi"],
    "pasaport_skt": ["pasaport skt", "pasaport son kullanma tarihi"],
    "uyruk": ["uyruk"],
    "kimlik_no": ["kimlik no"],
    "vize_turu": ["vize turu"],
    "islemi_yapan": ["islemi yapan", "islem yapan"],
    "yonlendiren_kisi": ["yonlendiren kisi", "yonlendiren", "yonlendirici"],
    "mail": ["mail", "mail adresi", "e posta"],
    "sifre": ["sifre", "parola"],
    "tel": ["tel", "telefon"],
    "referans": ["referans"],
    "randevu_gunu": ["randevu gunu"],
    "saat": ["saat"],
    "islem_sonucu": ["islem sonucu"],
}


def normalize_header(s):
    s = (s or "").strip().lower()
    s = (
        s.replace("ı", "i").replace("i̇", "i").replace("ş", "s").replace("ğ", "g")
        .replace("ü", "u").replace("ö", "o").replace("ç", "c")
    )
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return s


def match_header_to_field(header_text):
    norm = normalize_header(header_text)
    for field_key, variants in FIELD_SYNONYMS.items():
        if norm in variants:
            return field_key
    return None


def colnum_to_letter(n):
    letters = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters or "A"


def get_sheet_metadata(service):
    return service.spreadsheets().get(spreadsheetId=SHEETS_SPREADSHEET_ID).execute()


def list_country_sheets(service):
    """
    Google Sheets'teki sayfa isimlerini CANLI okur - yeni bir ulke sayfasi
    eklendiginde bot kodunda hicbir degisiklik gerekmez, otomatik gorur.
    """
    meta = get_sheet_metadata(service)
    names = []
    for sh in meta.get("sheets", []):
        title = sh["properties"]["title"]
        if title in (MASTER_SHEET_NAME, ACCOUNTS_SHEET_NAME):
            continue
        names.append(title)
    return names


def get_sheet_id_map(service):
    meta = get_sheet_metadata(service)
    return {sh["properties"]["title"]: sh["properties"]["sheetId"] for sh in meta.get("sheets", [])}


def get_sheet_grid(service, sheet_name):
    """
    Sayfadaki KULLANILAN tum hucreleri (satir x sutun) tek seferde okur.
    Sadece 'A:A' gibi tek bir sutuna bakmak yaniltici olabiliyor: yeni
    eklenen 'ID' sutunu bos oldugu icin Sheets API o sutunu erken kesebiliyor
    ve bot mevcut verilerin/basliklarin uzerine yaziyordu. Butun sayfayi
    okuyup satir sayisini oradan hesaplamak bu sorunu onler.
    """
    result = service.spreadsheets().values().get(
        spreadsheetId=SHEETS_SPREADSHEET_ID, range=f"'{sheet_name}'"
    ).execute()
    return result.get("values", [])


def get_header_row(service, sheet_name, grid=None):
    """
    Baslik satirinin HER ZAMAN 1. satir olacagini varsaymiyoruz - bazi
    sayfalarda 1. satir bir baslik/banner olabilir, gercek sutun adlari
    (ID, isim, soyisim...) 2. satirda olabilir. Ilk birkac satiri tarayip
    tanidik alan adiyla EN COK eslesen satiyi baslik satiri kabul ediyoruz.
    Donus: (baslik_satiri_1_indeksli, baslik_degerleri_listesi)
    """
    if grid is None:
        grid = get_sheet_grid(service, sheet_name)
    if not grid:
        return 1, []
    best_idx, best_score = 0, -1
    for i, row in enumerate(grid[:5]):
        score = sum(1 for cell in row if match_header_to_field(cell))
        if score > best_score:
            best_score, best_idx = score, i
    return best_idx + 1, grid[best_idx]


def get_sheet_headers(service, sheet_name):
    _, headers = get_header_row(service, sheet_name)
    return headers


def get_next_id_and_row(service, sheet_name, grid=None):
    if grid is None:
        grid = get_sheet_grid(service, sheet_name)
    header_row_idx, headers = get_header_row(service, sheet_name, grid)

    id_col = None
    for i, h in enumerate(headers):
        if match_header_to_field(h) == "id":
            id_col = i
            break

    max_id = 0
    if id_col is not None:
        for row in grid[header_row_idx:]:
            if len(row) > id_col and row[id_col]:
                try:
                    v = int(float(str(row[id_col]).strip()))
                    if v > max_id:
                        max_id = v
                except Exception:
                    continue

    # Bir SONRAKI bos satir, sayfada HERHANGI bir sutunda veri olan son
    # satirdan sonra gelir - sadece ID sutununa degil, tum tabloya bakiyoruz.
    next_row_index = len(grid) + 1
    return max_id + 1, next_row_index


COLOR_MAP = {
    "yellow": {"red": 1.0, "green": 0.93, "blue": 0.55},
    "red": {"red": 0.96, "green": 0.5, "blue": 0.5},
}


def set_row_color(service, sheet_name, row_index, color_name):
    sheet_ids = get_sheet_id_map(service)
    sheet_id = sheet_ids.get(sheet_name)
    if sheet_id is None:
        return
    color = COLOR_MAP.get(color_name)
    if not color:
        return
    body = {
        "requests": [{
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": row_index - 1,
                    "endRowIndex": row_index,
                },
                "cell": {"userEnteredFormat": {"backgroundColor": color}},
                "fields": "userEnteredFormat.backgroundColor",
            }
        }]
    }
    try:
        service.spreadsheets().batchUpdate(spreadsheetId=SHEETS_SPREADSHEET_ID, body=body).execute()
    except Exception as e:
        logger.error("Satir renklendirilemedi: %s", e)


def write_passport_row(service, sheet_name, field_values):
    grid = get_sheet_grid(service, sheet_name)
    header_row_idx, headers = get_header_row(service, sheet_name, grid)
    if not headers:
        return None
    next_id, row_index = get_next_id_and_row(service, sheet_name, grid)
    row = []
    for h in headers:
        key = match_header_to_field(h)
        if key == "id":
            row.append(next_id)
        elif key and key in field_values:
            row.append(field_values[key])
        else:
            row.append("")
    service.spreadsheets().values().update(
        spreadsheetId=SHEETS_SPREADSHEET_ID,
        range=f"'{sheet_name}'!A{row_index}",
        valueInputOption="USER_ENTERED",
        body={"values": [row]},
    ).execute()
    set_row_color(service, sheet_name, row_index, "yellow")
    # Bot kendi ekledigi satiri "gorulmus" olarak isaretler, boylece asagidaki
    # periyodik pasaport-satiri tarama isi bu satiri TEKRAR bildirim olarak
    # gondermez (bot zaten kullaniciya kayit eklendi mesaji gosteriyor).
    set_seen_row_count(sheet_name, row_index)
    return next_id, row_index


def find_row_by_id(service, sheet_name, target_id):
    grid = get_sheet_grid(service, sheet_name)
    header_row_idx, headers = get_header_row(service, sheet_name, grid)
    id_col = None
    for i, h in enumerate(headers):
        if match_header_to_field(h) == "id":
            id_col = i
            break
    if id_col is None:
        return None, None, None
    for i, row in enumerate(grid, start=1):
        if i <= header_row_idx or len(row) <= id_col or not row[id_col]:
            continue
        try:
            v = int(float(str(row[id_col]).strip()))
        except Exception:
            continue
        if v == target_id:
            return i, headers, row
    return None, None, None


def get_seen_row_count(sheet_name):
    doc = sheet_state.find_one({"_id": sheet_name})
    return doc.get("row_count", 0) if doc else 0


def set_seen_row_count(sheet_name, count):
    sheet_state.update_one({"_id": sheet_name}, {"$set": {"row_count": count}}, upsert=True)


def check_new_passport_rows():
    """
    Google Sheets'e (bot uzerinden ya da elle) yeni bir pasaport satiri
    eklenip eklenmedigini periyodik olarak kontrol eder, yeni satirlar icin
    Telegram bildirimi gonderir. Botun kendi ekledigi satirlar write_passport_row
    icinde zaten "gorulmus" olarak isaretlendigi icin burada tekrar bildirilmez.
    """
    service = get_sheets_service()
    if not service:
        return
    try:
        countries = list_country_sheets(service)
    except Exception as e:
        logger.error("Ulke sayfalari listelenemedi: %s", e)
        return

    grids = _fetch_all_country_grids(service, countries)
    for country in countries:
        entry = grids.get(country)
        if not entry:
            continue
        try:
            grid, header_row_idx, headers = entry
            current_count = len(grid)
            seen_count = get_seen_row_count(country)

            if seen_count == 0:
                # Bu sayfa icin ilk calisma - mevcut durumu baz alip
                # gecmis kayitlar icin bildirim gondermiyoruz.
                set_seen_row_count(country, current_count)
                continue

            if current_count <= seen_count:
                continue

            id_col = next((i for i, h in enumerate(headers) if match_header_to_field(h) == "id"), None)
            isim_col = next((i for i, h in enumerate(headers) if match_header_to_field(h) == "isim"), None)
            soyisim_col = next((i for i, h in enumerate(headers) if match_header_to_field(h) == "soyisim"), None)

            def cell(row, idx):
                return row[idx] if idx is not None and idx < len(row) and row[idx] else ""

            for row in grid[seen_count:current_count]:
                rid = cell(row, id_col)
                if not rid:
                    continue
                isim = f"{cell(row, isim_col)} {cell(row, soyisim_col)}".strip()
                text = f"\U0001f195 Yeni pasaport kaydi: '{country}' sayfasi - #{rid}"
                if isim:
                    text += f" - {isim}"
                broadcast_message(text)

            set_seen_row_count(country, current_count)
        except Exception as e:
            logger.error("'%s' sayfasi pasaport taramasi basarisiz: %s", country, e)


def list_country_records(service, sheet_name, only_pending=True):
    """
    'Randevu Aldim' akisinda ID'yi ezbere yazmak zorunda kalmamak icin,
    ulke sayfasindaki mevcut kayitlari (ID + isim soyisim) listeler.
    only_pending=True ise zaten randevu alinmis (Islem Sonucu dolu) satirlari
    listeden cikarir.
    """
    grid = get_sheet_grid(service, sheet_name)
    header_row_idx, headers = get_header_row(service, sheet_name, grid)

    def col_of(field_key):
        for i, h in enumerate(headers):
            if match_header_to_field(h) == field_key:
                return i
        return None

    id_col = col_of("id")
    isim_col = col_of("isim")
    soyisim_col = col_of("soyisim")
    sonuc_col = col_of("islem_sonucu")
    if id_col is None:
        return []

    def cell(row, idx):
        return row[idx] if idx is not None and idx < len(row) and row[idx] else ""

    records = []
    for row in grid[header_row_idx:]:
        rid = cell(row, id_col)
        if not rid:
            continue
        sonuc = cell(row, sonuc_col)
        if only_pending and sonuc:
            continue
        isim = f"{cell(row, isim_col)} {cell(row, soyisim_col)}".strip()
        records.append((rid, isim, sonuc))
    return records


def _fetch_all_country_grids(service, countries, max_workers=8):
    """
    Birden fazla ulke sayfasini SIRAYLA degil PARALEL okur. Arama, rapor,
    mukerrer kontrol, gunluk ozet ve yeni-kayit taramasi gibi TUM sayfalari
    gezen islemler bunu kullanir - ulke sayisi arttikca (10-15+) sirayla
    okuma toplam sureyi katlayarak uzatip botun butonlara yanit verirken
    "kasilmasina" yol aciyordu. Paralelde toplam sure en yavas TEK sayfa
    kadar olur, sayfalarin toplami kadar degil.
    Donus: {ulke: (grid, header_row_idx, headers)} sozlugu (okunamayan
    sayfalar sozlukte yer almaz).
    """
    results = {}
    if not countries:
        return results
    with ThreadPoolExecutor(max_workers=min(len(countries), max_workers)) as executor:
        futures = {executor.submit(get_sheet_grid, service, c): c for c in countries}
        for f in as_completed(futures):
            country = futures[f]
            try:
                grid = f.result()
                header_row_idx, headers = get_header_row(service, country, grid)
                results[country] = (grid, header_row_idx, headers)
            except Exception as e:
                logger.error("'%s' sayfasi okunamadi (paralel): %s", country, e)
    return results


def search_records(service, query_text):
    """
    Isim (soyisim dahil) veya ID numarasina gore TUM ulke sayfalarinda arama
    yapar. Sonuc: (ulke, id, isim_soyisim, islem_sonucu) tuple listesi.
    """
    q = (query_text or "").strip().lower()
    q_id = None
    try:
        q_id = int(float(q))
    except (ValueError, TypeError):
        pass

    results = []
    countries = list_country_sheets(service)
    grids = _fetch_all_country_grids(service, countries)
    for country in countries:
        entry = grids.get(country)
        if not entry:
            continue
        grid, header_row_idx, headers = entry

        id_col = next((i for i, h in enumerate(headers) if match_header_to_field(h) == "id"), None)
        isim_col = next((i for i, h in enumerate(headers) if match_header_to_field(h) == "isim"), None)
        soyisim_col = next((i for i, h in enumerate(headers) if match_header_to_field(h) == "soyisim"), None)
        sonuc_col = next((i for i, h in enumerate(headers) if match_header_to_field(h) == "islem_sonucu"), None)
        if id_col is None:
            continue

        def cell(row, idx):
            return row[idx] if idx is not None and idx < len(row) and row[idx] else ""

        for row in grid[header_row_idx:]:
            rid = cell(row, id_col)
            if not rid:
                continue
            isim_full = f"{cell(row, isim_col)} {cell(row, soyisim_col)}".strip()
            match = False
            if q_id is not None:
                try:
                    if int(float(str(rid).strip())) == q_id:
                        match = True
                except Exception:
                    pass
            if not match and q and q in isim_full.lower():
                match = True
            if match:
                results.append((country, rid, isim_full, cell(row, sonuc_col)))
    return results


def find_passport_duplicate(service, pasaport_no):
    """
    Girilen pasaport numarasi HERHANGI bir ulke sayfasinda zaten kayitli mi
    diye kontrol eder. Bulursa (ulke, id) dondurur, bulamazsa None.
    """
    target = re.sub(r"[^A-Z0-9]", "", (pasaport_no or "").upper())
    if not target:
        return None
    countries = list_country_sheets(service)
    grids = _fetch_all_country_grids(service, countries)
    for country in countries:
        entry = grids.get(country)
        if not entry:
            continue
        grid, header_row_idx, headers = entry

        pn_col = next((i for i, h in enumerate(headers) if match_header_to_field(h) == "pasaport_no"), None)
        id_col = next((i for i, h in enumerate(headers) if match_header_to_field(h) == "id"), None)
        if pn_col is None:
            continue
        for row in grid[header_row_idx:]:
            if len(row) <= pn_col or not row[pn_col]:
                continue
            val = re.sub(r"[^A-Z0-9]", "", str(row[pn_col]).upper())
            if val == target:
                rid = row[id_col] if id_col is not None and id_col < len(row) else "?"
                return country, rid
    return None


def generate_appointment_report(service, start_date, end_date):
    """
    start_date/end_date (date nesneleri) araligindaki randevu gunune sahip
    kayitlari TUM ulke sayfalarindan toplayip bir Excel workbook'u (BytesIO)
    olarak dondurur. Hic kayit yoksa None dondurur.
    """
    try:
        from openpyxl import Workbook
    except ImportError:
        return None

    rows_out = []
    countries = list_country_sheets(service)
    grids = _fetch_all_country_grids(service, countries)
    for country in countries:
        entry = grids.get(country)
        if not entry:
            continue
        grid, header_row_idx, headers = entry

        id_col = next((i for i, h in enumerate(headers) if match_header_to_field(h) == "id"), None)
        isim_col = next((i for i, h in enumerate(headers) if match_header_to_field(h) == "isim"), None)
        soyisim_col = next((i for i, h in enumerate(headers) if match_header_to_field(h) == "soyisim"), None)
        vize_col = next((i for i, h in enumerate(headers) if match_header_to_field(h) == "vize_turu"), None)
        gun_col = next((i for i, h in enumerate(headers) if match_header_to_field(h) == "randevu_gunu"), None)
        saat_col = next((i for i, h in enumerate(headers) if match_header_to_field(h) == "saat"), None)
        referans_col = next((i for i, h in enumerate(headers) if match_header_to_field(h) == "referans"), None)
        sonuc_col = next((i for i, h in enumerate(headers) if match_header_to_field(h) == "islem_sonucu"), None)
        if id_col is None or gun_col is None:
            continue

        def cell(row, idx):
            return row[idx] if idx is not None and idx < len(row) and row[idx] else ""

        for row in grid[header_row_idx:]:
            rid = cell(row, id_col)
            gun = cell(row, gun_col)
            if not rid or not gun:
                continue
            try:
                d = datetime.strptime(gun, "%d.%m.%Y").date()
            except Exception:
                continue
            if not (start_date <= d <= end_date):
                continue
            rows_out.append((d, [
                country, rid,
                cell(row, isim_col), cell(row, soyisim_col),
                cell(row, vize_col), gun, cell(row, saat_col),
                cell(row, referans_col), cell(row, sonuc_col),
            ]))

    if not rows_out:
        return None

    rows_out.sort(key=lambda item: item[0])

    wb = Workbook()
    ws = wb.active
    ws.title = "Randevu Raporu"
    ws.append(["Ulke", "ID", "Isim", "Soyisim", "Vize Turu", "Randevu Gunu", "Saat", "Referans", "Islem Sonucu"])
    for _d, row_data in rows_out:
        ws.append(row_data)

    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio


PASSPORT_EXPIRY_WARN_DAYS = int(os.environ.get("PASSPORT_EXPIRY_WARN_DAYS", "180"))


def send_daily_digest():
    """
    Her sabah (DAILY_DIGEST_HOUR:DAILY_DIGEST_MINUTE, Turkiye saati) calisir:
    bugunku randevu sayisi, bekleyen kayit sayisi ve suresi yakinda dolacak
    pasaportlari ozetleyen bir Telegram mesaji gonderir.
    """
    service = get_sheets_service()
    if not service:
        return
    today = datetime.now(TZ).date()
    total_appt_today = 0
    total_pending = 0
    expiring_soon = []

    countries = list_country_sheets(service)
    grids = _fetch_all_country_grids(service, countries)
    for country in countries:
        entry = grids.get(country)
        if not entry:
            continue
        grid, header_row_idx, headers = entry

        id_col = next((i for i, h in enumerate(headers) if match_header_to_field(h) == "id"), None)
        isim_col = next((i for i, h in enumerate(headers) if match_header_to_field(h) == "isim"), None)
        soyisim_col = next((i for i, h in enumerate(headers) if match_header_to_field(h) == "soyisim"), None)
        sonuc_col = next((i for i, h in enumerate(headers) if match_header_to_field(h) == "islem_sonucu"), None)
        gun_col = next((i for i, h in enumerate(headers) if match_header_to_field(h) == "randevu_gunu"), None)
        skt_col = next((i for i, h in enumerate(headers) if match_header_to_field(h) == "pasaport_skt"), None)
        if id_col is None:
            continue

        def cell(row, idx):
            return row[idx] if idx is not None and idx < len(row) and row[idx] else ""

        for row in grid[header_row_idx:]:
            rid = cell(row, id_col)
            if not rid:
                continue
            sonuc = cell(row, sonuc_col)
            if not sonuc:
                total_pending += 1
            gun = cell(row, gun_col)
            if gun:
                try:
                    d = datetime.strptime(gun, "%d.%m.%Y").date()
                    if d == today:
                        total_appt_today += 1
                except Exception:
                    pass
            skt = cell(row, skt_col)
            if skt:
                try:
                    d2 = datetime.strptime(skt, "%d.%m.%Y").date()
                    days_left = (d2 - today).days
                    if 0 <= days_left <= PASSPORT_EXPIRY_WARN_DAYS:
                        isim = f"{cell(row, isim_col)} {cell(row, soyisim_col)}".strip()
                        expiring_soon.append((country, rid, isim, skt, days_left))
                except Exception:
                    pass

    lines = [
        f"\U0001f4c5 Gunluk Ozet ({today.strftime('%d.%m.%Y')})",
        "",
        f"Bugun randevusu olan: {total_appt_today}",
        f"Bekleyen (randevu alinmamis) kayit: {total_pending}",
    ]
    if expiring_soon:
        expiring_soon.sort(key=lambda x: x[4])
        lines.append("")
        lines.append(f"⚠️ Pasaport suresi yakinda dolacaklar ({len(expiring_soon)}):")
        for country, rid, isim, skt, days_left in expiring_soon[:20]:
            lines.append(f"#{rid} - {isim or '(isimsiz)'} - {country} - SKT: {skt} ({days_left} gun)")
    broadcast_message("\n".join(lines))


def copy_to_master(service, headers, row_values, extra_fields):
    data = {}
    for h, v in zip(headers, row_values):
        key = match_header_to_field(h)
        if key:
            data[key] = v
    data.update(extra_fields)

    master_grid = get_sheet_grid(service, MASTER_SHEET_NAME)
    _, master_headers = get_header_row(service, MASTER_SHEET_NAME, master_grid)
    if not master_headers:
        return
    _, next_row = get_next_id_and_row(service, MASTER_SHEET_NAME, master_grid)
    new_row = []
    for h in master_headers:
        key = match_header_to_field(h)
        if key == "id":
            new_row.append(next_row - 1)
        elif key and key in data:
            new_row.append(data[key])
        else:
            new_row.append("")
    service.spreadsheets().values().update(
        spreadsheetId=SHEETS_SPREADSHEET_ID,
        range=f"'{MASTER_SHEET_NAME}'!A{next_row}",
        valueInputOption="USER_ENTERED",
        body={"values": [new_row]},
    ).execute()


def apply_extra_fields_to_row(service, sheet_name, row_index, headers, row_values, extra_fields):
    """
    Var olan bir satirdaki (orn. randevu bilgileri) belirli sutunlari,
    diger sutunlara dokunmadan gunceller. row_values, find_row_by_id'den
    gelen mevcut hucre degerleridir - eksik hucreler bos string sayilir.
    Guncellenmis satiri geri dondurur (master'a kopyalarken kullanilabilsin diye).
    """
    row_values = list(row_values) + [""] * max(0, len(headers) - len(row_values))
    for i, h in enumerate(headers):
        key = match_header_to_field(h)
        if key and key in extra_fields:
            row_values[i] = extra_fields[key]
    last_col = colnum_to_letter(len(headers))
    try:
        service.spreadsheets().values().update(
            spreadsheetId=SHEETS_SPREADSHEET_ID,
            range=f"'{sheet_name}'!A{row_index}:{last_col}{row_index}",
            valueInputOption="USER_ENTERED",
            body={"values": [row_values]},
        ).execute()
    except Exception as e:
        logger.error("Satir guncellenemedi: %s", e)
    return row_values


# --- Pasaport OCR (OCR.space) + MRZ ayristirma ---
def ocr_space_extract_text(image_bytes):
    if not OCR_SPACE_API_KEY:
        return None
    try:
        r = requests.post(
            "https://api.ocr.space/parse/image",
            files={"file": ("pasaport.jpg", image_bytes)},
            data={"apikey": OCR_SPACE_API_KEY, "language": "eng", "OCREngine": "2", "scale": "true"},
            timeout=30,
        )
        result = r.json()
        if result.get("IsErroredOnProcessing"):
            logger.error("OCR hatasi: %s", result.get("ErrorMessage"))
            return None
        parsed = result.get("ParsedResults", [])
        if not parsed:
            return None
        return parsed[0].get("ParsedText", "")
    except Exception as e:
        logger.error("OCR istegi basarisiz: %s", e)
        return None


def extract_mrz_lines(raw_text):
    if not raw_text:
        return None
    candidates = []
    for line in raw_text.splitlines():
        cleaned = line.strip().upper().replace(" ", "")
        if re.fullmatch(r"[A-Z0-9<]{30,44}", cleaned):
            candidates.append(cleaned)
    if len(candidates) < 2:
        return None
    last_two = candidates[-2:]
    return [c.ljust(44, "<")[:44] for c in last_two]


def mrz_date_to_ddmmyyyy(yymmdd, is_expiry=False):
    try:
        yy = int(yymmdd[0:2])
        mm = yymmdd[2:4]
        dd = yymmdd[4:6]
    except Exception:
        return ""
    current_yy = datetime.now().year % 100
    if is_expiry:
        year = 2000 + yy
    else:
        year = 2000 + yy if yy <= current_yy else 1900 + yy
    return f"{dd}.{mm}.{year}"


def parse_mrz(lines):
    if not MRZ_LIB_AVAILABLE:
        return None, False
    try:
        checker = TD3CodeChecker("\n".join(lines))
        raw = checker.fields()
        valid = bool(checker)
    except Exception as e:
        logger.error("MRZ ayristirma hatasi: %s", e)
        return None, False

    # onemli: checker.fields() bir sozluk DEGIL, ozellikleri nokta ile
    # okunan bir nesne dondurur (raw.name, raw.surname gibi) - .get() yok.
    def g(*names):
        for n in names:
            v = getattr(raw, n, None)
            if v:
                return v
        return ""

    fields = {
        "isim": g("name").replace("<", " ").strip(),
        "soyisim": g("surname").replace("<", " ").strip(),
        "pasaport_no": g("document_number").replace("<", "").strip(),
        "dogum_tarihi": mrz_date_to_ddmmyyyy(g("birth_date"), is_expiry=False),
        "pasaport_skt": mrz_date_to_ddmmyyyy(g("expiry_date"), is_expiry=True),
        "uyruk": g("nationality").strip(),
        "kimlik_no": g("personal_number", "optional_data", "optional_data_2").replace("<", "").strip(),
    }
    return fields, valid


REMINDER_CHECK_INTERVAL_SECONDS = int(os.environ.get("REMINDER_CHECK_INTERVAL_SECONDS", "20"))
# Pasaport eklemeleri hatirlatici/mail kadar saniye hassasiyeti gerektirmedigi
# icin varsayilan biraz daha gevsek - Sheets API'ye gereksiz yuk binmesin.
PASSPORT_CHECK_INTERVAL_SECONDS = int(os.environ.get("PASSPORT_CHECK_INTERVAL_SECONDS", "60"))
# Gunluk ozet bildiriminin gonderilecegi saat/dakika (Turkiye saati).
DAILY_DIGEST_HOUR = int(os.environ.get("DAILY_DIGEST_HOUR", "9"))
DAILY_DIGEST_MINUTE = int(os.environ.get("DAILY_DIGEST_MINUTE", "0"))

scheduler = BackgroundScheduler(timezone="UTC")
scheduler.add_job(check_due_reminders, "interval", seconds=REMINDER_CHECK_INTERVAL_SECONDS, max_instances=1)
scheduler.add_job(check_new_mail, "interval", seconds=MAIL_CHECK_INTERVAL_SECONDS, max_instances=1)
if GOOGLE_LIBS_AVAILABLE:
    scheduler.add_job(check_new_passport_rows, "interval", seconds=PASSPORT_CHECK_INTERVAL_SECONDS, max_instances=1)
    # Scheduler'in kendisi UTC calisiyor; kullanilan APScheduler surumu
    # zoneinfo nesnelerini (TZ) cron trigger'da kabul etmiyor (sadece pytz
    # destekliyor), bu yuzden Turkiye saatini (sabit UTC+3, 2016'dan beri
    # yaz/kis saati uygulamiyor) burada elle UTC'ye ceviriyoruz.
    _digest_utc_hour = (DAILY_DIGEST_HOUR - 3) % 24
    scheduler.add_job(
        send_daily_digest, "cron",
        hour=_digest_utc_hour, minute=DAILY_DIGEST_MINUTE,
        max_instances=1,
    )
scheduler.start()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
