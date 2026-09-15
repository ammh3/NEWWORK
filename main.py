# Mediatel OTP Bot - Complete Virtual Number Service
import asyncio
import json
import re
import os
import random
import string
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update, CopyTextButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ============================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
PUBLIC_CHANNEL_ID  = -1004427004477
ADMIN_ID           = 8473160748
ADMIN_USERNAME     = "@TERRABYTEEE"
CHANNEL_USERNAME   = "SGPAIDOTPBOT"
GET_NUMBER_URL     = "https://t.me/SGPAIDOTPBOT"
MEDIATEL_URL       = "https://mediateluk.com/sms/index.php?opt=shw_sts_today_det"

# Free user limits
FREE_NUMBERS       = 2
PREMIUM_NUMBERS    = 4
FREE_DAILY_LIMIT   = 1
RESERVATION_MINS   = 10
# ============================================================

# File storage
KEYS_FILE      = "keys.json"
USERS_FILE     = "users.json"
NUMBERS_FILE   = "numbers.json"
ASSIGNED_FILE  = "assigned.json"
SERVICES_FILE  = "services.json"
COOKIE_FILE    = "cookies.json"

def load_json(f):
    try:
        with open(f) as fp: return json.load(fp)
    except: return {}

def load_json_list(f):
    try:
        with open(f) as fp: return json.load(fp)
    except: return []

def save_json(f, d):
    with open(f, "w") as fp: json.dump(d, fp, indent=2)

def get_keys():     return load_json(KEYS_FILE)
def get_users():    return load_json(USERS_FILE)
def get_assigned(): return load_json(ASSIGNED_FILE)
def get_services(): return load_json(SERVICES_FILE)
def save_keys(k):     save_json(KEYS_FILE, k)
def save_users(u):    save_json(USERS_FILE, u)
def save_assigned(a): save_json(ASSIGNED_FILE, a)
def save_services(s): save_json(SERVICES_FILE, s)

def load_cookies():
    try:
        with open(COOKIE_FILE) as f:
            c = json.load(f)
            return c.get("PHPSESSID", os.getenv("PHPSESSID","")), c.get("CF_CLEARANCE", os.getenv("CF_CLEARANCE",""))
    except:
        return os.getenv("PHPSESSID",""), os.getenv("CF_CLEARANCE","")

def save_cookies_to_file(phpsessid, cf_clearance):
    save_json(COOKIE_FILE, {"PHPSESSID": phpsessid, "CF_CLEARANCE": cf_clearance})

# ── Number Pool ───────────────────────────────────────────────

def get_numbers():
    return load_json(NUMBERS_FILE)

def save_numbers(n):
    save_json(NUMBERS_FILE, n)

def add_numbers_to_pool(numbers, service, country):
    """Admin adds numbers to pool"""
    pool = get_numbers()
    key  = f"{service}:{country}"
    if key not in pool:
        pool[key] = {"service": service, "country": country, "numbers": []}
    existing = [n["number"] for n in pool[key]["numbers"]]
    added = 0
    for num in numbers:
        num = num.strip()
        if num and num not in existing:
            pool[key]["numbers"].append({"number": num, "assigned_to": None, "assigned_at": None})
            added += 1
    save_numbers(pool)
    return added

def get_available_services():
    """Get services that have available numbers"""
    pool     = get_numbers()
    assigned = get_assigned()
    services = {}

    # Clean expired assignments first
    clean_expired_assignments()
    assigned = get_assigned()

    assigned_numbers = set(a["number"] for a in assigned.values())

    for key, data in pool.items():
        available = [n for n in data["numbers"] if n["number"] not in assigned_numbers]
        if available:
            service = data["service"]
            country = data["country"]
            if service not in services:
                services[service] = {}
            if country not in services[service]:
                services[service][country] = 0
            services[service][country] += len(available)

    return services

def clean_expired_assignments():
    """Release expired number reservations"""
    assigned = get_assigned()
    now      = datetime.now()
    expired  = []

    for uid, data in assigned.items():
        for num_data in data.get("numbers", []):
            assigned_at = datetime.strptime(num_data["assigned_at"], "%Y-%m-%d %H:%M:%S")
            if (now - assigned_at).total_seconds() > RESERVATION_MINS * 60:
                expired.append((uid, num_data["number"]))

    if expired:
        for uid, num in expired:
            if uid in assigned:
                assigned[uid]["numbers"] = [n for n in assigned[uid]["numbers"] if n["number"] != num]
                if not assigned[uid]["numbers"]:
                    del assigned[uid]
        save_assigned(assigned)

    return len(expired)

def assign_numbers(user_id, service, country, count):
    """Assign unique numbers to user"""
    clean_expired_assignments()
    pool     = get_numbers()
    assigned = get_assigned()
    uid      = str(user_id)

    key = f"{service}:{country}"
    if key not in pool:
        return []

    # Get all currently assigned numbers
    all_assigned = set()
    for data in assigned.values():
        for n in data.get("numbers", []):
            all_assigned.add(n["number"])

    # Get available numbers
    available = [n["number"] for n in pool[key]["numbers"] if n["number"] not in all_assigned]
    if len(available) < count:
        count = len(available)
    if count == 0:
        return []

    # Pick random numbers
    selected = random.sample(available, count)
    now      = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if uid not in assigned:
        assigned[uid] = {"numbers": [], "service": service, "country": country}

    for num in selected:
        assigned[uid]["numbers"].append({
            "number":      num,
            "service":     service,
            "country":     country,
            "assigned_at": now,
        })

    save_assigned(assigned)
    return selected

def get_user_numbers(user_id):
    """Get currently assigned numbers for user"""
    clean_expired_assignments()
    assigned = get_assigned()
    uid      = str(user_id)
    if uid not in assigned:
        return []
    return assigned[uid].get("numbers", [])

# ── Key System ────────────────────────────────────────────────

def generate_key():
    chars = string.ascii_uppercase + string.digits
    r = ''.join(random.choices(chars, k=12))
    return f"SG-{r[:4]}-{r[4:8]}-{r[8:12]}"

def create_key(days=0, max_otps=0):
    key  = generate_key()
    keys = get_keys()
    keys[key] = {
        "days": days, "max_otps": max_otps, "used_otps": 0,
        "active": True, "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "activated_by": None, "expires_at": None,
    }
    save_keys(keys)
    return key

def activate_key(user_id, key_str):
    keys  = get_keys()
    users = get_users()
    uid   = str(user_id)
    if key_str not in keys:
        return False, f"❌ Invalid key! Contact {ADMIN_USERNAME}"
    k = keys[key_str]
    if not k["active"]:
        return False, f"❌ Key deactivated! Contact {ADMIN_USERNAME}"
    if k["activated_by"] and k["activated_by"] != uid:
        return False, "❌ Key already used by another user!"
    expires_at = None
    if k["days"] > 0:
        expires_at = (datetime.now() + timedelta(days=k["days"])).strftime("%Y-%m-%d %H:%M:%S")
    keys[key_str]["activated_by"] = uid
    keys[key_str]["expires_at"]   = expires_at
    save_keys(keys)
    if uid not in users:
        users[uid] = {}
    users[uid].update({
        "key": key_str, "expires_at": expires_at,
        "max_otps": k["max_otps"], "used_otps": 0, "is_premium": True,
        "activated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "daily_requests": {}, "total_requests": 0,
    })
    save_users(users)
    validity = f"{k['days']} days" if k["days"] > 0 else "Unlimited"
    return True, f"✅ *Key Activated!*\n\n⏰ Validity: *{validity}*\n\n🎉 You now have Premium access!"

def check_access(user_id):
    if user_id == ADMIN_ID: return True, "admin"
    users = get_users()
    uid   = str(user_id)
    if uid not in users: return False, "no_key"
    u = users[uid]
    if not u.get("is_premium"): return False, "no_key"
    keys = get_keys()
    key  = u.get("key")
    if key and (key not in keys or not keys[key]["active"]): return False, "deactivated"
    if u.get("expires_at"):
        if datetime.now() > datetime.strptime(u["expires_at"], "%Y-%m-%d %H:%M:%S"):
            return False, "expired"
    return True, "ok"

def is_premium(user_id):
    if user_id == ADMIN_ID: return True
    has_access, _ = check_access(user_id)
    return has_access

def check_daily_limit(user_id):
    """Check if free user can request numbers today"""
    if is_premium(user_id): return True
    users = get_users()
    uid   = str(user_id)
    today = datetime.now().strftime("%Y-%m-%d")
    if uid not in users:
        users[uid] = {"daily_requests": {}, "is_premium": False}
        save_users(users)
    daily = users[uid].get("daily_requests", {})
    return daily.get(today, 0) < FREE_DAILY_LIMIT

def increment_daily_requests(user_id):
    users = get_users()
    uid   = str(user_id)
    today = datetime.now().strftime("%Y-%m-%d")
    if uid not in users:
        users[uid] = {"daily_requests": {}, "is_premium": False}
    if "daily_requests" not in users[uid]:
        users[uid]["daily_requests"] = {}
    users[uid]["daily_requests"][today] = users[uid]["daily_requests"].get(today, 0) + 1
    save_users(users)

def get_premium_users():
    users = get_users()
    return [int(uid) for uid in users if check_access(int(uid))[0]]

# ── Country & Service Maps ─────────────────────────────────────

country_map = {
    "1":("US","🇺🇸","English"),"7":("RU","🇷🇺","Russian"),"20":("EG","🇪🇬","Arabic"),
    "27":("ZA","🇿🇦","English"),"30":("GR","🇬🇷","Greek"),"31":("NL","🇳🇱","Dutch"),
    "32":("BE","🇧🇪","French"),"33":("FR","🇫🇷","French"),"34":("ES","🇪🇸","Spanish"),
    "36":("HU","🇭🇺","Hungarian"),"39":("IT","🇮🇹","Italian"),"40":("RO","🇷🇴","Romanian"),
    "41":("CH","🇨🇭","German"),"43":("AT","🇦🇹","German"),"44":("GB","🇬🇧","English"),
    "45":("DK","🇩🇰","Danish"),"46":("SE","🇸🇪","Swedish"),"47":("NO","🇳🇴","Norwegian"),
    "48":("PL","🇵🇱","Polish"),"49":("DE","🇩🇪","German"),"51":("PE","🇵🇪","Spanish"),
    "52":("MX","🇲🇽","Spanish"),"54":("AR","🇦🇷","Spanish"),"55":("BR","🇧🇷","Portuguese"),
    "56":("CL","🇨🇱","Spanish"),"57":("CO","🇨🇴","Spanish"),"58":("VE","🇻🇪","Spanish"),
    "60":("MY","🇲🇾","Malay"),"61":("AU","🇦🇺","English"),"62":("ID","🇮🇩","Indonesian"),
    "63":("PH","🇵🇭","Filipino"),"64":("NZ","🇳🇿","English"),"65":("SG","🇸🇬","English"),
    "66":("TH","🇹🇭","Thai"),"81":("JP","🇯🇵","Japanese"),"82":("KR","🇰🇷","Korean"),
    "84":("VN","🇻🇳","Vietnamese"),"86":("CN","🇨🇳","Chinese"),"91":("IN","🇮🇳","Hindi"),
    "92":("PK","🇵🇰","Urdu"),"93":("AF","🇦🇫","Pashto"),"94":("LK","🇱🇰","Sinhala"),
    "95":("MM","🇲🇲","Burmese"),"98":("IR","🇮🇷","Persian"),"212":("MA","🇲🇦","Arabic"),
    "213":("DZ","🇩🇿","Arabic"),"216":("TN","🇹🇳","Arabic"),"218":("LY","🇱🇾","Arabic"),
    "220":("GM","🇬🇲","English"),"221":("SN","🇸🇳","French"),"223":("ML","🇲🇱","French"),
    "224":("GN","🇬🇳","French"),"225":("CI","🇨🇮","French"),"233":("GH","🇬🇭","English"),
    "234":("NG","🇳🇬","English"),"237":("CM","🇨🇲","French"),"249":("SD","🇸🇩","Arabic"),
    "250":("RW","🇷🇼","Kinyarwanda"),"251":("ET","🇪🇹","Amharic"),"254":("KE","🇰🇪","Swahili"),
    "255":("TZ","🇹🇿","Swahili"),"256":("UG","🇺🇬","English"),"258":("MZ","🇲🇿","Portuguese"),
    "260":("ZM","🇿🇲","English"),"263":("ZW","🇿🇼","English"),"351":("PT","🇵🇹","Portuguese"),
    "353":("IE","🇮🇪","English"),"358":("FI","🇫🇮","Finnish"),"370":("LT","🇱🇹","Lithuanian"),
    "371":("LV","🇱🇻","Latvian"),"372":("EE","🇪🇪","Estonian"),"374":("AM","🇦🇲","Armenian"),
    "375":("BY","🇧🇾","Belarusian"),"380":("UA","🇺🇦","Ukrainian"),"381":("RS","🇷🇸","Serbian"),
    "385":("HR","🇭🇷","Croatian"),"420":("CZ","🇨🇿","Czech"),"421":("SK","🇸🇰","Slovak"),
    "591":("BO","🇧🇴","Spanish"),"593":("EC","🇪🇨","Spanish"),"595":("PY","🇵🇾","Spanish"),
    "598":("UY","🇺🇾","Spanish"),"673":("BN","🇧🇳","Malay"),"675":("PG","🇵🇬","English"),
    "679":("FJ","🇫🇯","English"),"852":("HK","🇭🇰","Cantonese"),"855":("KH","🇰🇭","Khmer"),
    "856":("LA","🇱🇦","Lao"),"880":("BD","🇧🇩","Bengali"),"886":("TW","🇹🇼","Chinese"),
    "960":("MV","🇲🇻","Dhivehi"),"961":("LB","🇱🇧","Arabic"),"962":("JO","🇯🇴","Arabic"),
    "964":("IQ","🇮🇶","Arabic"),"965":("KW","🇰🇼","Arabic"),"966":("SA","🇸🇦","Arabic"),
    "967":("YE","🇾🇪","Arabic"),"968":("OM","🇴🇲","Arabic"),"971":("AE","🇦🇪","Arabic"),
    "972":("IL","🇮🇱","Hebrew"),"973":("BH","🇧🇭","Arabic"),"974":("QA","🇶🇦","Arabic"),
    "975":("BT","🇧🇹","Dzongkha"),"976":("MN","🇲🇳","Mongolian"),"977":("NP","🇳🇵","Nepali"),
    "992":("TJ","🇹🇯","Tajik"),"993":("TM","🇹🇲","Turkmen"),"994":("AZ","🇦🇿","Azerbaijani"),
    "995":("GE","🇬🇪","Georgian"),"996":("KG","🇰🇬","Kyrgyz"),"998":("UZ","🇺🇿","Uzbek"),
}

service_icons = {
    "whatsapp":"📱","telegram":"✈️","google":"🔍","facebook":"📘","instagram":"📸",
    "twitter":"🐦","tiktok":"🎵","shopee":"🛒","lazada":"🛍️","grab":"🚗","gojek":"🟢",
    "uber":"🚕","imo":"💬","viber":"📲","line":"💚","snapchat":"👻","wechat":"🟩",
    "linkedin":"💼","amazon":"📦","netflix":"🎬","paypal":"💰","binance":"🟡",
    "coinbase":"🔵","discord":"🎮","microsoft":"🪟","apple":"🍎","yahoo":"💜",
    "airbnb":"🏠","notice":"🔔","verify":"✅","auth":"🔐","dola":"💳",
}

def detect_country(phone):
    clean = phone.lstrip('+')
    for code in sorted(country_map.keys(), key=len, reverse=True):
        if clean.startswith(code): return country_map[code]
    return ("??","🌍","Unknown")

def mask_phone(phone):
    phone = phone.strip()
    if len(phone) >= 10: return phone[:5] + "••" + phone[-4:]
    return phone

def extract_otp(message):
    match = re.search(
        r'(?:code|كود|رمز|código|код|验证码|verification code|'
        r'WhatsApp code|code is|OTP|pin|kode|passcode|'
        r'confirmation code|access code|security code|'
        r'is your|Your .* code|Developer\):)[\s\W:-]*(\d{3,8})',
        message, re.IGNORECASE | re.UNICODE
    )
    if match: return re.sub(r'[- ]', '', match.group(1))
    match = re.search(r'\b(\d{4,8})\b', message)
    return re.sub(r'[- ]', '', match.group(1)) if match else "N/A"

def get_service_icon(service):
    name = service.lower()
    for key, icon in service_icons.items():
        if key in name: return icon
    return "💬"

def mask_otp(otp):
    if otp == "N/A": return "****"
    if len(otp) <= 2: return otp + "***"
    return otp[:2] + "*" * (len(otp) - 2)

def build_full_message(sender, phone, full_msg):
    country_code, flag, language = detect_country(phone)
    masked    = mask_phone(phone)
    otp       = extract_otp(full_msg)
    svc_icon  = get_service_icon(sender)
    clean_msg = full_msg.replace('\n', ' ').strip()
    text = f"{flag} {country_code} | {svc_icon} {masked} | 🌐 {language}\n\n{svc_icon} {clean_msg}"
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔔 Channel ↗", url=f"https://t.me/{CHANNEL_USERNAME}"),
            InlineKeyboardButton(f"🛡 📋 {otp}", copy_text=CopyTextButton(otp)),
        ],
        [InlineKeyboardButton("📞 Get Number ↗", url=GET_NUMBER_URL)]
    ])
    return text, keyboard, otp

def build_preview_message(sender, phone, otp):
    country_code, flag, language = detect_country(phone)
    masked     = mask_phone(phone)
    svc_icon   = get_service_icon(sender)
    hidden_otp = mask_otp(otp)
    text = f"{flag} {country_code} | {svc_icon} {masked} | 🌐 {language}"
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔔 Channel ↗", url=f"https://t.me/{CHANNEL_USERNAME}"),
            InlineKeyboardButton(f"🛡 {hidden_otp} 🔒", callback_data="locked"),
        ],
        [InlineKeyboardButton("📞 Get Number ↗", url=GET_NUMBER_URL)]
    ])
    return text, keyboard

async def broadcast_otp(bot, sender, phone, full_msg):
    text_full, kb_full, otp = build_full_message(sender, phone, full_msg)
    text_prev, kb_prev      = build_preview_message(sender, phone, otp)

    # Send to public channel (half OTP)
    try:
        await bot.send_message(chat_id=PUBLIC_CHANNEL_ID, text=text_prev, reply_markup=kb_prev)
    except Exception as e:
        print(f"❌ Channel: {e}")

    # Send full OTP to admin
    try:
        await bot.send_message(chat_id=ADMIN_ID, text=f"👑 *ADMIN*\n\n{text_full}", reply_markup=kb_full, parse_mode="Markdown")
    except Exception as e:
        print(f"❌ Admin: {e}")

    # Send to users who have this number assigned
    assigned = get_assigned()
    for uid, data in assigned.items():
        for num_data in data.get("numbers", []):
            if num_data["number"] == phone or num_data["number"] == phone.lstrip('+'):
                try:
                    await bot.send_message(
                        chat_id=int(uid),
                        text=f"📨 *OTP Received!*\n\n{text_full}",
                        reply_markup=kb_full,
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    print(f"❌ User {uid}: {e}")

    # Send to all premium users
    for uid in get_premium_users():
        if uid == ADMIN_ID: continue
        try:
            await bot.send_message(chat_id=uid, text=text_full, reply_markup=kb_full)
        except Exception as e:
            print(f"❌ Premium user {uid}: {e}")

    print(f"✅ {mask_phone(phone)} | {sender} | OTP: {otp}")

# ── Mediatel Scraper ──────────────────────────────────────────

def fetch_mediatel_otps():
    try:
        phpsessid, cf_clearance = load_cookies()
        session = requests.Session()
        session.cookies.set("PHPSESSID",    phpsessid,    domain="mediateluk.com")
        session.cookies.set("cf_clearance", cf_clearance, domain="mediateluk.com")
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://mediateluk.com/sms/index.php",
        })
        resp = session.get(MEDIATEL_URL, timeout=15)
        if resp.status_code != 200:
            print(f"❌ Mediatel fetch failed: {resp.status_code}")
            return []
        soup  = BeautifulSoup(resp.text, 'html.parser')
        table = soup.find('table')
        if not table: return []
        otps = []
        rows = table.find_all('tr')[1:]
        for row in rows:
            cols = row.find_all('td')
            if len(cols) >= 6:
                otps.append({
                    "datetime": cols[0].text.strip(),
                    "phone":    cols[1].text.strip(),
                    "sender":   cols[2].text.strip(),
                    "range":    cols[3].text.strip(),
                    "status":   cols[4].text.strip(),
                    "message":  cols[5].text.strip(),
                })
        return otps
    except Exception as e:
        print(f"❌ Scrape error: {e}")
        return []

async def poll_mediatel(bot):
    seen = set()
    print("✅ Mediatel polling started — every 10 seconds!")
    existing = fetch_mediatel_otps()
    for otp in existing:
        key = f"{otp['phone']}:{otp['datetime']}:{otp['message'][:20]}"
        seen.add(key)
    print(f"📌 Loaded {len(existing)} existing OTPs as seen")

    while True:
        try:
            otps = fetch_mediatel_otps()
            for otp in otps:
                key = f"{otp['phone']}:{otp['datetime']}:{otp['message'][:20]}"
                if key not in seen:
                    seen.add(key)
                    await broadcast_otp(bot, otp["sender"], otp["phone"], otp["message"])
                    await asyncio.sleep(0.5)
        except Exception as e:
            print(f"❌ Poll error: {e}")
        await asyncio.sleep(10)

# ── Bot Commands ──────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id  = update.effective_user.id
    name     = update.effective_user.first_name
    premium  = is_premium(user_id)

    if user_id == ADMIN_ID:
        badge = "👑 Admin"
    elif premium:
        badge = "⭐ Premium"
    else:
        badge = "🆓 Free"

    services = get_available_services()
    svc_list = "\n".join([f"• {get_service_icon(s)} {s}" for s in services.keys()]) if services else "No services available"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📞 Get Number", callback_data="get_number"),
         InlineKeyboardButton("📊 My Status", callback_data="my_status")],
        [InlineKeyboardButton("💰 Buy Premium", url=f"https://t.me/{ADMIN_USERNAME.lstrip('@')}"),
         InlineKeyboardButton("📣 Channel", url=f"https://t.me/{CHANNEL_USERNAME}")],
    ])

    await update.message.reply_text(
        f"👋 Welcome *{name}*!\n\n"
        f"🤖 *SG OTP Zone Bot*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏅 Status: *{badge}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📱 *Available Services:*\n{svc_list}\n\n"
        f"📌 *Commands:*\n"
        f"/getnumber — Get virtual numbers\n"
        f"/mynumbers — See your assigned numbers\n"
        f"/activate KEY — Activate premium key\n"
        f"/mystatus — Check your status\n\n"
        f"💰 *Buy Premium:* {ADMIN_USERNAME}",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

async def getnumber_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id  = update.effective_user.id
    premium  = is_premium(user_id)
    services = get_available_services()

    if not services:
        await update.message.reply_text("❌ No numbers available right now! Try again later.")
        return

    if not premium and not check_daily_limit(user_id):
        await update.message.reply_text(
            f"❌ *Daily limit reached!*\n\n"
            f"Free users can request numbers *1 time per day*.\n\n"
            f"💰 Upgrade to Premium for unlimited requests!\n"
            f"Contact: {ADMIN_USERNAME}",
            parse_mode="Markdown"
        )
        return

    # Show service selection
    buttons = []
    for service in services.keys():
        icon = get_service_icon(service)
        total = sum(services[service].values())
        buttons.append([InlineKeyboardButton(f"{icon} {service} ({total})", callback_data=f"select_service:{service}")])
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])

    await update.message.reply_text(
        "⚙️ *Select Service:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def mynumbers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    numbers = get_user_numbers(user_id)

    if not numbers:
        await update.message.reply_text(
            "❌ No numbers assigned!\n\nUse /getnumber to get numbers.",
        )
        return

    msg = "📱 *Your Assigned Numbers:*\n\n"
    for n in numbers:
        assigned_at = datetime.strptime(n["assigned_at"], "%Y-%m-%d %H:%M:%S")
        expires_at  = assigned_at + timedelta(minutes=RESERVATION_MINS)
        remaining   = max(0, int((expires_at - datetime.now()).total_seconds() / 60))
        country_code, flag, _ = detect_country(n["number"])
        msg += f"{flag} `{n['number']}`\n"
        msg += f"📱 {n['service']} | ⏰ {remaining} min left\n\n"

    await update.message.reply_text(msg, parse_mode="Markdown")

# ── Callback Handlers ─────────────────────────────────────────

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    user_id = query.from_user.id
    data    = query.data
    await query.answer()

    if data == "get_number":
        await getnumber_command_callback(query, user_id)

    elif data == "my_status":
        await mystatus_callback(query, user_id)

    elif data == "cancel":
        await query.edit_message_text("❌ Cancelled.")

    elif data == "locked":
        await query.answer("🔒 Get premium to see full OTP!", show_alert=True)

    elif data.startswith("select_service:"):
        service  = data.split(":", 1)[1]
        services = get_available_services()
        if service not in services:
            await query.edit_message_text("❌ Service not available!")
            return
        countries = services[service]
        buttons   = []
        for country, count in countries.items():
            country_code, flag, _ = detect_country_by_name(country)
            buttons.append([InlineKeyboardButton(
                f"{flag} {country} ({count})",
                callback_data=f"select_country:{service}:{country}"
            )])
        buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="get_number")])
        await query.edit_message_text(
            f"🌍 *Select Country for {service}:*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif data.startswith("select_country:"):
        parts   = data.split(":", 2)
        service = parts[1]
        country = parts[2]
        premium = is_premium(user_id)
        count   = PREMIUM_NUMBERS if premium else FREE_NUMBERS

        if not premium and not check_daily_limit(user_id):
            await query.edit_message_text(
                f"❌ *Daily limit reached!*\n\nContact {ADMIN_USERNAME} for premium.",
                parse_mode="Markdown"
            )
            return

        numbers = assign_numbers(user_id, service, country, count)
        if not numbers:
            await query.edit_message_text("❌ No numbers available for this selection!")
            return

        if not premium:
            increment_daily_requests(user_id)

        country_code, flag, language = detect_country_by_name(country)
        svc_icon = get_service_icon(service)
        expires  = (datetime.now() + timedelta(minutes=RESERVATION_MINS)).strftime("%H:%M")

        msg = (
            f"✅ *Numbers Assigned!*\n\n"
            f"📱 Service: *{service}*\n"
            f"{flag} Country: *{country}*\n"
            f"⏰ Reserved: *{RESERVATION_MINS} min* (until {expires})\n\n"
        )

        buttons = []
        for num in numbers:
            c, f2, _ = detect_country(num)
            msg += f"{f2} `{num}`\n"
            buttons.append([InlineKeyboardButton(f"{f2} 📋 {num}", copy_text=CopyTextButton(num))])

        msg += f"\n📨 *OTPs will be forwarded automatically!*"
        buttons.append([InlineKeyboardButton("📊 My Numbers", callback_data="my_numbers")])
        buttons.append([InlineKeyboardButton("🔄 Get More", callback_data=f"select_country:{service}:{country}")])

        await query.edit_message_text(
            msg, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif data == "my_numbers":
        numbers = get_user_numbers(user_id)
        if not numbers:
            await query.edit_message_text("❌ No numbers assigned!")
            return
        msg = "📱 *Your Numbers:*\n\n"
        for n in numbers:
            assigned_at = datetime.strptime(n["assigned_at"], "%Y-%m-%d %H:%M:%S")
            expires_at  = assigned_at + timedelta(minutes=RESERVATION_MINS)
            remaining   = max(0, int((expires_at - datetime.now()).total_seconds() / 60))
            c, flag, _  = detect_country(n["number"])
            msg += f"{flag} `{n['number']}` — ⏰ {remaining} min left\n"
        await query.edit_message_text(msg, parse_mode="Markdown")


async def getnumber_command_callback(query, user_id):
    premium  = is_premium(user_id)
    services = get_available_services()

    if not services:
        await query.edit_message_text("❌ No numbers available right now!")
        return

    if not premium and not check_daily_limit(user_id):
        await query.edit_message_text(
            f"❌ *Daily limit reached!*\n\nContact {ADMIN_USERNAME} for premium.",
            parse_mode="Markdown"
        )
        return

    buttons = []
    for service in services.keys():
        icon  = get_service_icon(service)
        total = sum(services[service].values())
        buttons.append([InlineKeyboardButton(f"{icon} {service} ({total})", callback_data=f"select_service:{service}")])
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])

    await query.edit_message_text(
        "⚙️ *Select Service:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def mystatus_callback(query, user_id):
    premium = is_premium(user_id)
    users   = get_users()
    uid     = str(user_id)
    today   = datetime.now().strftime("%Y-%m-%d")

    if user_id == ADMIN_ID:
        status = "👑 Admin — Full Access"
    elif premium:
        u       = users.get(uid, {})
        expires = u.get("expires_at") or "Unlimited"
        status  = f"⭐ Premium Active\n⏰ Expires: {expires}"
    else:
        daily_used = users.get(uid, {}).get("daily_requests", {}).get(today, 0)
        remaining  = max(0, FREE_DAILY_LIMIT - daily_used)
        status     = f"🆓 Free User\n📊 Daily requests left: {remaining}/{FREE_DAILY_LIMIT}"

    await query.edit_message_text(
        f"📊 *Your Status*\n\n{status}\n\n"
        f"💰 Upgrade: {ADMIN_USERNAME}",
        parse_mode="Markdown"
    )


def detect_country_by_name(country_name):
    """Detect country info from country name string"""
    for code, (cc, flag, lang) in country_map.items():
        if cc.lower() in country_name.lower():
            return cc, flag, lang
    return "??", "🌍", "Unknown"


# ── Admin Commands ────────────────────────────────────────────

async def start_command_handler(update, context):
    await start_command(update, context)

async def addnumbers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin adds numbers: /addnumbers WhatsApp Nigeria +2348001234567 +2348001234568"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!"); return

    if len(context.args) < 3:
        await update.message.reply_text(
            "Usage: `/addnumbers SERVICE COUNTRY NUMBER1 NUMBER2...`\n\n"
            "Example:\n`/addnumbers WhatsApp Nigeria +2348001234567 +2348001234568`",
            parse_mode="Markdown"
        ); return

    service  = context.args[0]
    country  = context.args[1]
    numbers  = context.args[2:]
    added    = add_numbers_to_pool(numbers, service, country)

    await update.message.reply_text(
        f"✅ Added *{added}* numbers to pool!\n\n"
        f"📱 Service: *{service}*\n"
        f"🌍 Country: *{country}*",
        parse_mode="Markdown"
    )

async def poolstatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!"); return

    pool     = get_numbers()
    assigned = get_assigned()
    assigned_nums = set()
    for data in assigned.values():
        for n in data.get("numbers", []):
            assigned_nums.add(n["number"])

    msg = "📊 *Number Pool Status:*\n\n"
    for key, data in pool.items():
        total     = len(data["numbers"])
        available = len([n for n in data["numbers"] if n["number"] not in assigned_nums])
        msg += f"📱 *{data['service']}* — {data['country']}\n"
        msg += f"Total: {total} | Available: {available} | Assigned: {total-available}\n\n"

    if not pool:
        msg = "❌ No numbers in pool!\n\nUse /addnumbers to add numbers."

    await update.message.reply_text(msg, parse_mode="Markdown")

async def genkey_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!"); return
    days = 0; max_otps = 0
    for arg in context.args:
        if arg.startswith("days="):
            try: days = int(arg.split("=")[1])
            except: pass
        elif arg.startswith("otps="):
            try: max_otps = int(arg.split("=")[1])
            except: pass
    key = create_key(days=days, max_otps=max_otps)
    await update.message.reply_text(
        f"✅ *New Key Generated!*\n\n"
        f"🔑 `{key}`\n\n"
        f"⏰ Validity: *{'Unlimited' if days == 0 else f'{days} days'}*\n"
        f"📊 Credits: *{'Unlimited' if max_otps == 0 else max_otps}*",
        parse_mode="Markdown"
    )

async def activate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(f"❌ Usage: `/activate YOUR-KEY`\n\nBuy from {ADMIN_USERNAME}", parse_mode="Markdown")
        return
    key_str = context.args[0].strip().upper()
    success, msg = activate_key(update.effective_user.id, key_str)
    await update.message.reply_text(msg, parse_mode="Markdown")

async def mystatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    premium = is_premium(user_id)
    users   = get_users()
    uid     = str(user_id)
    today   = datetime.now().strftime("%Y-%m-%d")

    if user_id == ADMIN_ID:
        status = "👑 Admin — Full Access"
    elif premium:
        u       = users.get(uid, {})
        expires = u.get("expires_at") or "Unlimited"
        status  = f"⭐ Premium Active\n⏰ Expires: {expires}"
    else:
        daily_used = users.get(uid, {}).get("daily_requests", {}).get(today, 0)
        remaining  = max(0, FREE_DAILY_LIMIT - daily_used)
        status     = f"🆓 Free\n📊 Requests left today: {remaining}"

    await update.message.reply_text(
        f"📊 *Your Status*\n\n{status}",
        parse_mode="Markdown"
    )

async def listkeys_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!"); return
    keys = get_keys()
    if not keys:
        await update.message.reply_text("No keys yet!"); return
    msg = "🔑 *Keys (last 10):*\n\n"
    for key, d in list(keys.items())[-10:]:
        status = "✅" if d["active"] else "❌"
        user   = d.get("activated_by") or "Not used"
        msg   += f"{status} `{key}`\n👤 {user}\n\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def revokekey_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!"); return
    if not context.args:
        await update.message.reply_text("Usage: `/revokekey KEY`", parse_mode="Markdown"); return
    key_str = context.args[0].strip().upper()
    keys = get_keys()
    if key_str not in keys:
        await update.message.reply_text("❌ Key not found!"); return
    keys[key_str]["active"] = False
    save_keys(keys)
    await update.message.reply_text(f"✅ Key `{key_str}` revoked!", parse_mode="Markdown")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!"); return
    users   = get_users()
    keys    = get_keys()
    premium = get_premium_users()
    pool    = get_numbers()
    total_numbers = sum(len(d["numbers"]) for d in pool.values())
    await update.message.reply_text(
        f"📊 *Bot Stats*\n\n"
        f"👥 Total users: *{len(users)}*\n"
        f"⭐ Premium users: *{len(premium)}*\n"
        f"🔑 Total keys: *{len(keys)}*\n"
        f"📱 Numbers in pool: *{total_numbers}*\n"
        f"⚡ Polling: Every 10 seconds",
        parse_mode="Markdown"
    )

async def updatecookie_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!"); return
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage:\n"
            "`/updatecookie PHPSESSID value`\n"
            "`/updatecookie CF_CLEARANCE value`\n"
            "`/updatecookie ALL phpsessid cf_clearance`",
            parse_mode="Markdown"
        ); return
    cookie_type  = context.args[0].upper()
    phpsessid, cf_clearance = load_cookies()
    if cookie_type == "PHPSESSID":
        phpsessid = context.args[1]
    elif cookie_type == "CF_CLEARANCE":
        cf_clearance = context.args[1]
    elif cookie_type == "ALL":
        if len(context.args) < 3:
            await update.message.reply_text("❌ Need both values!"); return
        phpsessid    = context.args[1]
        cf_clearance = context.args[2]
    save_cookies_to_file(phpsessid, cf_clearance)
    await update.message.reply_text("✅ *Cookies updated!*", parse_mode="Markdown")

async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!"); return
    await update.message.reply_text("⏳ Broadcasting sample OTPs...")
    samples = [
        ("NOTICE",   "+59165875223", "Codigo de verificacion: 770349."),
        ("WhatsApp", "+628812345678", "Kode WhatsApp Anda 170-854."),
        ("Google",   "+919876543210", "Your Google code is 234567"),
        ("AWS",      "+447712345678", "Your AWS code is 12345"),
        ("VERIFY",   "+923312345678", "Your code is 456789."),
    ]
    for sender, phone, msg in samples:
        await broadcast_otp(context.bot, sender, phone, msg)
        await asyncio.sleep(1)
    await update.message.reply_text("✅ Done!")


async def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # User commands
    app.add_handler(CommandHandler("start",        start_command))
    app.add_handler(CommandHandler("getnumber",    getnumber_command))
    app.add_handler(CommandHandler("mynumbers",    mynumbers_command))
    app.add_handler(CommandHandler("activate",     activate_command))
    app.add_handler(CommandHandler("mystatus",     mystatus_command))

    # Admin commands
    app.add_handler(CommandHandler("genkey",       genkey_command))
    app.add_handler(CommandHandler("listkeys",     listkeys_command))
    app.add_handler(CommandHandler("revokekey",    revokekey_command))
    app.add_handler(CommandHandler("addnumbers",   addnumbers_command))
    app.add_handler(CommandHandler("poolstatus",   poolstatus_command))
    app.add_handler(CommandHandler("stats",        stats_command))
    app.add_handler(CommandHandler("updatecookie", updatecookie_command))
    app.add_handler(CommandHandler("test",         test_command))

    # Button handler
    app.add_handler(CallbackQueryHandler(button_handler))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    print("✅ Bot active!")
    await poll_mediatel(app.bot)

if __name__ == "__main__":
    asyncio.run(main())
