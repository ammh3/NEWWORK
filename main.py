import asyncio
import json
import os
import random
import re
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes.DEFAULT_TYPE
from playwright.async_api import async_playwright

# ========== CONFIG — 10 MINUTE AUTO-FREE ⚡ ==========
BOT_TOKEN       = os.getenv("BOT_TOKEN", "")
CHANNEL_ID      = os.getenv("CHANNEL_ID", "-1004427004477")
NEW_CHANNEL_ID  = os.getenv("NEW_CHANNEL_ID", "-1003250473765")
ADMIN_ID        = int(os.getenv("ADMIN_ID", "8473160748"))
PANEL_USER      = os.getenv("PANEL_USER", "5260101")
PANEL_PASS      = os.getenv("PANEL_PASS", "Shoaibpanel@123!!!")
LOGIN_URL       = "https://mysmsportal.com/index.php"
OTP_SUMMARY_URL = "https://mysmsportal.com/index.php?opt=shw_sts_today"
POLL_INTERVAL   = int(os.getenv("POLL_INTERVAL", "8"))
NUMBERS_PER_USER = 4
EXPIRE_MINUTES   = 10  # ⏰ 10 MINUTES — agar OTP nahi aaya toh FREE

ASSIGNMENTS_FILE = "user_assignments.json"
NUMBERS_FILE     = "panel_numbers.json"
ACTIVE_OTPS_FILE = "active_otp_numbers.json"

browser = None
context = None
page = None
seen_messages = set()
cookies_file = "panel_cookies.json"

# ========== DATA MANAGEMENT ==========
def load_data():
    assignments = {}
    numbers = []
    active_otps = {}
    
    try:
        with open(ASSIGNMENTS_FILE) as f: 
            raw = json.load(f)
            for uid, data in raw.items():
                if isinstance(data, list):
                    assignments[uid] = {
                        "numbers": data,
                        "assigned_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                else:
                    assignments[uid] = data
    except: pass
    
    try:
        with open(NUMBERS_FILE) as f: numbers = json.load(f)
    except: pass
    
    try:
        with open(ACTIVE_OTPS_FILE) as f: active_otps = json.load(f)
    except: pass
    
    return assignments, numbers, active_otps

def save_data(assignments, numbers, active_otps):
    with open(ASSIGNMENTS_FILE, 'w') as f: json.dump(assignments, f, indent=2)
    with open(NUMBERS_FILE, 'w') as f: json.dump(numbers, f, indent=2)
    with open(ACTIVE_OTPS_FILE, 'w') as f: json.dump(active_otps, f, indent=2)

def get_used_numbers(assignments):
    used = set()
    for data in assignments.values():
        used.update(data["numbers"])
    return used

def get_available_numbers(assignments, numbers):
    used = get_used_numbers(assignments)
    return [n for n in numbers if n not in used]

def mark_number_active(number, active_otps):
    """Mark number as OTP received — will NOT expire"""
    active_otps[number] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return active_otps

def cleanup_expired(assignments, active_otps):
    """
    Free numbers after EXPIRE_MINUTES if NO OTP received:
    ✅ OTP aaya → kabhi free nahi hoga
    ❌ OTP nahi aaya → 10 min baad FREE
    """
    now = datetime.now()
    expired_users = []
    freed_count = 0
    
    for uid, data in assignments.items():
        assigned_at = datetime.strptime(data["assigned_at"], "%Y-%m-%d %H:%M:%S")
        minutes_passed = (now - assigned_at).total_seconds() / 60
        
        if minutes_passed >= EXPIRE_MINUTES:
            # Check: is ANY number of this user received OTP?
            has_active = any(num in active_otps for num in data["numbers"])
            
            if not has_active:
                # ❌ No OTP on ANY number → FREE ALL
                expired_users.append(uid)
                freed_count += len(data["numbers"])
    
    for uid in expired_users:
        del assignments[uid]
    
    return freed_count, len(expired_users)

def assign_numbers(user_id, assignments, numbers, active_otps, count=NUMBERS_PER_USER):
    user_id = str(user_id)
    
    if user_id in assignments and len(assignments[user_id]["numbers"]) > 0:
        return assignments[user_id]["numbers"], False
    
    available = get_available_numbers(assignments, numbers)
    
    if len(available) < count:
        if not available:
            return None, True
        chosen = random.sample(available, len(available))
    else:
        chosen = random.sample(available, count)
    
    assignments[user_id] = {
        "numbers": chosen,
        "assigned_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    save_data(assignments, numbers, active_otps)
    return chosen, True

def free_user_numbers(user_id, assignments, numbers, active_otps):
    user_id = str(user_id)
    if user_id in assignments:
        freed = len(assignments[user_id]["numbers"])
        del assignments[user_id]
        save_data(assignments, numbers, active_otps)
        return freed
    return 0

# ========== HIDE NUMBER ==========
def mask_phone(phone):
    phone = phone.strip()
    if len(phone) <= 6: return phone
    return f"{phone[:4]}*****{phone[-3:]}"

# ========== BROWSER ==========
async def setup_browser():
    global browser, context, page
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=True,
        args=['--no-sandbox','--disable-blink-features=AutomationControlled',
              '--disable-dev-shm-usage','--disable-setuid-sandbox',
              '--no-first-run','--no-zygote','--disable-gpu']
    )
    context = await browser.new_context(
        viewport={'width':1366,'height':768},
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
        locale='en-US'
    )
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: ()=>undefined});
        window.chrome={runtime:{}};
    """)
    page = await context.new_page()
    try:
        with open(cookies_file) as f: await context.add_cookies(json.load(f))
        print("🍪 Cookies loaded")
    except: print("ℹ️ No cookies")
    return page

async def save_cookies():
    with open(cookies_file,'w') as f: json.dump(await context.cookies(), f)

# ========== LOGIN ==========
async def is_logged_in():
    try:
        await page.goto(OTP_SUMMARY_URL, timeout=20000, wait_until='domcontentloaded')
        await asyncio.sleep(0.5)
        return 'Please enter your login details' not in await page.content()
    except: return False

async def do_login():
    print("🔐 Logging in...")
    try:
        await page.goto(LOGIN_URL, timeout=20000, wait_until='networkidle')
        await asyncio.sleep(1)
        user = page.locator('input[type="text"]').first
        await user.click()
        for c in PANEL_USER: await user.type(c, delay=random.randint(20,50))
        await asyncio.sleep(0.3)
        pwd = page.locator('input[type="password"]').first
        await pwd.click()
        for c in PANEL_PASS: await pwd.type(c, delay=random.randint(20,50))
        await asyncio.sleep(0.3)
        await page.locator('button, input[type="submit"]').first.click()
        await asyncio.sleep(1.5)
        await save_cookies()
        ok = await is_logged_in()
        print("✅ LOGIN OK" if ok else "❌ Login failed")
        return ok
    except Exception as e:
        print(f"❌ Login error: {e}")
        return False

# ========== OTP FETCH ==========
async def get_all_messages():
    all_messages = []
    try:
        await page.goto(OTP_SUMMARY_URL, timeout=20000, wait_until='domcontentloaded')
        await asyncio.sleep(0.8)
        
        if 'Please enter your login details' in await page.content():
            print("⚠️ Re-login...")
            if not await do_login(): return None, "login_fail"
            await page.goto(OTP_SUMMARY_URL, timeout=20000, wait_until='domcontentloaded')
            await asyncio.sleep(0.8)

        rows = page.locator('table tbody tr')
        n = await rows.count()
        if n == 0: return [], "empty"
        
        print(f"📊 Found {n} numbers")

        for i in range(n):
            row = rows.nth(i)
            cols = row.locator('td')
            nc = await cols.count()
            if nc < 5: continue
            
            number = (await cols.nth(0).inner_text()).strip()
            if not number: continue
            
            print(f"   🔍 Checking: {number}")

            form = row.locator('form').first
            if await form.count() > 0:
                print("   ✅ Form found — opening...")
                try:
                    await form.click()
                    await page.wait_for_load_state('domcontentloaded', timeout=8000)
                    await asyncio.sleep(1)
                    
                    detail_rows = page.locator('table tbody tr')
                    m_count = await detail_rows.count()
                    for j in range(m_count):
                        d_cols = detail_rows.nth(j).locator('td')
                        if await d_cols.count() >= 5:
                            dt = (await d_cols.nth(0).inner_text()).strip()
                            ph = (await d_cols.nth(1).inner_text()).strip()
                            se = (await d_cols.nth(2).inner_text()).strip()
                            ms = (await d_cols.nth(-1).inner_text()).strip()
                            if ms and len(ms) > 3 and dt:
                                all_messages.append({"datetime":dt,"phone":ph,"sender":se,"message":ms})
                    
                    await page.go_back()
                    await page.wait_for_load_state('domcontentloaded', timeout=8000)
                    await asyncio.sleep(0.5)
                except Exception as e:
                    print(f"   ⚠️ Error: {e}")
                    try:
                        await page.goto(OTP_SUMMARY_URL, timeout=15000, wait_until='domcontentloaded')
                        await asyncio.sleep(0.5)
                    except: pass
            else:
                btn = row.locator('button:has-text("Select"), input[value*="Select"]').first
                if await btn.count() > 0:
                    print("   ✅ Button found — opening...")
                    try:
                        await btn.click()
                        await page.wait_for_load_state('domcontentloaded', timeout=8000)
                        await asyncio.sleep(1)
                        
                        detail_rows = page.locator('table tbody tr')
                        m_count = await detail_rows.count()
                        for j in range(m_count):
                            d_cols = detail_rows.nth(j).locator('td')
                            if await d_cols.count() >= 5:
                                dt = (await d_cols.nth(0).inner_text()).strip()
                                ph = (await d_cols.nth(1).inner_text()).strip()
                                se = (await d_cols.nth(2).inner_text()).strip()
                                ms = (await d_cols.nth(-1).inner_text()).strip()
                                if ms and len(ms) > 3 and dt:
                                    all_messages.append({"datetime":dt,"phone":ph,"sender":se,"message":ms})
                        
                        await page.go_back()
                        await page.wait_for_load_state('domcontentloaded', timeout=8000)
                        await asyncio.sleep(0.5)
                    except Exception as e:
                        print(f"   ⚠️ Click error: {e}")
                        try:
                            await page.goto(OTP_SUMMARY_URL, timeout=15000, wait_until='domcontentloaded')
                            await asyncio.sleep(0.5)
                        except: pass

        if random.random() < 0.2: await save_cookies()
        return all_messages, "ok"
        
    except Exception as e:
        print(f"❌ Fetch error: {e}")
        return None, "error"

# ========== EXTRACT OTP ==========
def extract_otp(txt):
    m = re.search(r'\b(\d{4,8})\b', txt)
    return m.group(1) if m else "N/A"

# ========== SEND OTP ==========
async def send_otp(bot, msg, assignments, active_otps):
    otp = extract_otp(msg['message'])
    masked = mask_phone(msg['phone'])
    full = msg['phone']
    
    # ✅ OTP AAYA → MARK AS ACTIVE, KABHI FREE NAHI HOGA
    active_otps = mark_number_active(full, active_otps)
    
    channel_text = (
        f"🔐 NEW OTP RECEIVED\n"
        f"📱 Phone: `{masked}`\n"
        f"🕐 Time: {msg['datetime']}\n"
        f"✉️ Sender: `{msg['sender']}`\n"
        f"🔢 Code: `{otp}`\n"
        f"📝 Message:\n`{msg['message'][:300]}`"
    )
    
    asyncio.create_task(bot.send_message(CHANNEL_ID, channel_text, parse_mode="Markdown"))
    asyncio.create_task(bot.send_message(NEW_CHANNEL_ID, channel_text, parse_mode="Markdown"))
    
    # Find user
    user_id = None
    for uid, data in assignments.items():
        if full in data["numbers"]:
            user_id = int(uid)
            break
    
    if user_id:
        dm_text = (
            f"🔐 YOUR OTP ARRIVED ✅\n\n"
            f"📱 Number: `{full}`\n"
            f"🕐 Time: {msg['datetime']}\n"
            f"✉️ Sender: `{msg['sender']}`\n"
            f"🔢 OTP Code: `{otp}`\n\n"
            f"📝 Full Message:\n`{msg['message'][:300]}`\n\n"
            f"⚡ Fast Delivery | 🤖 Auto Bot"
        )
        asyncio.create_task(bot.send_message(user_id, dm_text, parse_mode="Markdown"))
        print(f"✅ DM to {user_id}: {full} | {otp}")
    
    print(f"✅ Channels: {masked} | {otp}")
    return active_otps

# ========== POLL LOOP — 10 MINUTE AUTO-FREE ⚡ ==========
async def poll_loop(bot):
    global seen_messages
    print(f"\n🔄 POLLING STARTED — EVERY {POLL_INTERVAL} SEC ⚡\n")
    print(f"👤 Numbers per user: {NUMBERS_PER_USER}")
    print(f"⏰ Auto-free: {EXPIRE_MINUTES} MINUTES (no OTP = free)\n")
    
    err = 0
    cleanup_counter = 0
    
    while True:
        msgs, st = await get_all_messages()
        assignments, numbers, active_otps = load_data()
        
        # Auto-cleanup check every 5th poll (~every 40 sec)
        cleanup_counter += 1
        if cleanup_counter >= 5:
            cleanup_counter = 0
            freed, users = cleanup_expired(assignments, active_otps)
            if freed > 0:
                print(f"🧹 AUTO-CLEANUP: {freed} numbers freed from {users} users (no OTP in {EXPIRE_MINUTES}min)\n")
                save_data(assignments, numbers, active_otps)
        
        if msgs is None:
            err += 1
            print(f"⚠️ Error {err}/5")
            if err >= 5:
                asyncio.create_task(bot.send_message(ADMIN_ID, "⚠️ FETCH FAILING\nUse /relogin"))
                err = 0
        else:
            err = 0
            new = 0
            for m in msgs:
                key = f"{m['datetime']}|{m['phone']}|{m['message'][:40]}"
                if key not in seen_messages:
                    seen_messages.add(key)
                    active_otps = await send_otp(bot, m, assignments, active_otps)
                    new += 1
            
            save_data(assignments, numbers, active_otps)
            
            if new: print(f"🔔 {new} NEW sent!\n")
            else: print(f"ℹ️ No new\n")
        
        if len(seen_messages) > 500:
            seen_messages = set(list(seen_messages)[-250:])
        await asyncio.sleep(max(5, POLL_INTERVAL))

# ========== USER COMMANDS ==========
async def start_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    user_id = u.effective_user.id
    name = u.effective_user.first_name
    assignments, numbers, active_otps = load_data()
    
    if not numbers:
        await u.message.reply_text(
            "⚠️ *Numbers abhi upload nahi hue!*\nAdmin jald hi upload karega 🙏",
            parse_mode="Markdown"
        )
        return
    
    user_nums, is_new = assign_numbers(user_id, assignments, numbers, active_otps)
    
    if user_nums is None:
        await u.message.reply_text(
            "😔 *Saare numbers use ho chuke!*\nJaldi hi expire honge — try again 🙏",
            parse_mode="Markdown"
        )
        return
    
    nums_text = "\n".join([f"  {i+1}. `{num}`" for i, num in enumerate(user_nums)])
    
    if is_new:
        await u.message.reply_text(
            f"🎉 *Welcome {name}!*\n\n"
            f"✅ Tere {len(user_nums)} numbers assign ho gaye:\n{nums_text}\n\n"
            f"⏰ *Rules:*\n"
            f"   ✅ 10 minute mein OTP aaya → permanent tera!\n"
            f"   ❌ Nahi aaya → auto-free, naye le lena /refresh se\n\n"
            f"🔄 Naye chahiye? /refresh\n"
            f"📱 Dekhne ke liye: /mynumber",
            parse_mode="Markdown"
        )
    else:
        await u.message.reply_text(
            f"👋 *Welcome back {name}!*\n\n"
            f"📱 Tere numbers:\n{nums_text}\n\n"
            f"⏰ 10 min mein OTP nahi aaya toh naye le /refresh se ✅",
            parse_mode="Markdown"
        )

async def mynumber_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    user_id = u.effective_user.id
    assignments, _, active_otps = load_data()
    
    if str(user_id) in assignments:
        data = assignments[str(user_id)]
        user_nums = data["numbers"]
        assigned_at = data["assigned_at"]
        
        nums_text = ""
        for i, num in enumerate(user_nums):
            if num in active_otps:
                status = "✅ LOCKED (OTP aaya)"
            else:
                status = "⏳ Waiting (10min timer)"
            nums_text += f"  {i+1}. `{num}` — {status}\n"
        
        await u.message.reply_text(
            f"📱 *Tere Numbers:*\n{nums_text}"
            f"📅 Assigned: {assigned_at}\n\n"
            f"🔄 Naye chahiye? /refresh",
            parse_mode="Markdown"
        )
    else:
        await u.message.reply_text(
            "❌ Abhi koi number nahi!\nPane ke liye /start karein",
            parse_mode="Markdown"
        )

async def refresh_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    user_id = u.effective_user.id
    assignments, numbers, active_otps = load_data()
    
    if str(user_id) not in assignments:
        await u.message.reply_text("❌ Pehle /start use karein!")
        return
    
    old_data = assignments[str(user_id)]
    old_nums = old_data["numbers"]
    
    # Check if ANY number got OTP → don't allow refresh
    has_any_otp = any(num in active_otps for num in old_nums)
    if has_any_otp:
        await u.message.reply_text(
            "✅ *Tere number par OTP aaya hai!*\n"
            "Isliye refresh nahi kar sakte — yeh permanent tera hai ✅",
            parse_mode="Markdown"
        )
        return
    
    # Free old & assign new
    del assignments[str(user_id)]
    new_nums, is_new = assign_numbers(user_id, assignments, numbers, active_otps)
    
    if new_nums is None:
        assignments[str(user_id)] = {
            "numbers": old_nums,
            "assigned_at": old_data["assigned_at"]
        }
        save_data(assignments, numbers, active_otps)
        await u.message.reply_text("😔 Abhi naye numbers nahi available!")
        return
    
    nums_text = "\n".join([f"  {i+1}. `{num}`" for i, num in enumerate(new_nums)])
    
    await u.message.reply_text(
        f"🔄 *Refreshed! Naye numbers mil gaye ✅*\n\n{nums_text}\n\n"
        f"⏰ Ab 10 minute wait karein\n"
        f"❌ Fir bhi nahi aaya? Dobara /refresh",
        parse_mode="Markdown"
    )

# ========== ADMIN COMMANDS ==========
async def upload_numbers_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN_ID:
        await u.message.reply_text("❌ Admin only!")
        return
    
    if not u.message.text or len(u.message.text.split()) < 2:
        await u.message.reply_text(
            "📝 *Use:*\n`/uploadnumbers +591xxx, +591yyy, ...`\n\nYa har line mein ek number",
            parse_mode="Markdown"
        )
        return
    
    text = u.message.text.replace('/uploadnumbers', '').strip()
    raw = re.split(r'[,\n]+', text)
    new_nums = []
    for num in raw:
        num = num.strip()
        if num and num.startswith('+'):
            new_nums.append(num)
    
    if not new_nums:
        await u.message.reply_text("❌ Koi valid number nahi mila!")
        return
    
    assignments, existing, active_otps = load_data()
    added = 0
    for num in new_nums:
        if num not in existing:
            existing.append(num)
            added += 1
    
    save_data(assignments, existing, active_otps)
    available = get_available_numbers(assignments, existing)
    
    await u.message.reply_text(
        f"✅ *Uploaded!*\n"
        f"📥 Naye: `{added}`\n"
        f"📊 Total: `{len(existing)}`\n"
        f"🆓 Available: `{len(available)}`\n"
        f"👤 Users: `{len(assignments)}`\n"
        f"✅ Locked (OTP aaya): `{len(active_otps)}`",
        parse_mode="Markdown"
    )

async def freeuser_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN_ID:
        await u.message.reply_text("❌ Admin only!")
        return
    parts = u.message.text.split()
    if len(parts) < 2:
        await u.message.reply_text("Use: `/freeuser user_id`")
        return
    target = parts[1].strip()
    assignments, numbers, active_otps = load_data()
    freed = free_user_numbers(target, assignments, numbers, active_otps)
    if freed > 0:
        await u.message.reply_text(f"✅ User {target} ke {freed} numbers free!")
    else:
        await u.message.reply_text("❌ Nahi mila!")

async def freenumber_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN_ID:
        await u.message.reply_text("❌ Admin only!")
        return
    parts = u.message.text.split()
    if len(parts) < 2:
        await u.message.reply_text("Use: `/freenumber +591xxxx`")
        return
    target = parts[1].strip()
    assignments, numbers, active_otps = load_data()
    
    found = False
    for uid, data in list(assignments.items()):
        if target in data["numbers"]:
            data["numbers"].remove(target)
            found = True
            if not data["numbers"]:
                del assignments[uid]
            break
    
    if found:
        save_data(assignments, numbers, active_otps)
        await u.message.reply_text(f"✅ {target} free ho gaya!")
    else:
        await u.message.reply_text("❌ Number assigned nahi hai!")

async def cleanunused_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN_ID:
        await u.message.reply_text("❌ Admin only!")
        return
    
    m = await u.message.reply_text("🧹 Cleaning...")
    assignments, numbers, active_otps = load_data()
    freed, users = cleanup_expired(assignments, active_otps)
    save_data(assignments, numbers, active_otps)
    
    await m.edit_text(
        f"🧹 *Cleanup Done!*\n\n"
        f"📱 Freed: `{freed}` numbers\n"
        f"👤 Users removed: `{users}`\n"
        f"🆓 Available: `{len(get_available_numbers(assignments, numbers))}`",
        parse_mode="Markdown"
    )

async def stats_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN_ID:
        await u.message.reply_text("❌ Admin only!")
        return
    assignments, numbers, active_otps = load_data()
    available = get_available_numbers(assignments, numbers)
    await u.message.reply_text(
        f"📊 *System Stats*\n\n"
        f"📱 Total: `{len(numbers)}`\n"
        f"🆓 Available: `{len(available)}`\n"
        f"👤 Users: `{len(assignments)}`\n"
        f"✅ Locked: `{len(active_otps)}`\n"
        f"🔢 Per user: `{NUMBERS_PER_USER}`\n"
        f"⏰ Auto-free: `{EXPIRE_MINUTES} min`",
        parse_mode="Markdown"
    )

async def assignments_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN_ID:
        await u.message.reply_text("❌ Admin only!")
        return
    assignments, _ = load_data()
    if not assignments:
        await u.message.reply_text("ℹ️ Koi nahi hai")
        return
    text = "📋 *Assignments:*\n\n"
    for uid, data in list(assignments.items())[:20]:
        status = "🔒" if any(n in active_otps for n in data["numbers"]) else "⏳"
        nums_short = ", ".join([mask_phone(n) for n in data["numbers"]])
        text += f"{status} `{uid}` → {nums_short}\n"
    await u.message.reply_text(text, parse_mode="Markdown")

async def clearseen_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    global seen_messages
    if u.effective_user.id != ADMIN_ID:
        await u.message.reply_text("❌ Admin only!")
        return
    seen_messages = set()
    await u.message.reply_text("✅ Cleared!")

async def relogin_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN_ID:
        await u.message.reply_text("❌ Admin only!")
        return
    try: os.remove(cookies_file)
    except: pass
    ok = await do_login()
    await u.message.reply_text("✅ Done" if ok else "❌ Failed")

async def status_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN_ID:
        await u.message.reply_text("❌ Admin only!")
        return
    msgs, _ = await get_all_messages()
    await u.message.reply_text(f"✅ Working\nMessages: {len(msgs) if msgs else 0}")

async def testfetch_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN_ID:
        await u.message.reply_text("❌ Admin only!")
        return
    m = await u.message.reply_text("⏳ Fetching...")
    msgs, _ = await get_all_messages()
    if msgs:
        await m.edit_text(f"✅ {len(msgs)} found\nLatest: {mask_phone(msgs[-1]['phone'])} | {extract_otp(msgs[-1]['message'])}")
    else:
        await m.edit_text("❌ Nothing found")

# ========== MAIN ==========
async def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN missing")
        return
    
    assignments, numbers, active_otps = load_data()
    save_data(assignments, numbers, active_otps)
    print(f"📊 Loaded: {len(numbers)} numbers, {len(assignments)} users")
    print(f"✅ Locked: {len(active_otps)} | ⏰ Auto-free: {EXPIRE_MINUTES}min")
    
    await setup_browser()
    if not await is_logged_in():
        await do_login()
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    # User commands
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("mynumber", mynumber_cmd))
    app.add_handler(CommandHandler("refresh", refresh_cmd))
    
    # Admin commands
    app.add_handler(CommandHandler("uploadnumbers", upload_numbers_cmd))
    app.add_handler(CommandHandler("freeuser", freeuser_cmd))
    app.add_handler(CommandHandler("freenumber", freenumber_cmd))
    app.add_handler(CommandHandler("cleanunused", cleanunused_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("assignments", assignments_cmd))
    app.add_handler(CommandHandler("clearseen", clearseen_cmd))
    app.add_handler(CommandHandler("relogin", relogin_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("testfetch", testfetch_cmd))
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    print(f"\n✅ BOT ONLINE — 10MIN AUTO-FREE ACTIVE ⚡\n")
    await poll_loop(app.bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Stopped")
