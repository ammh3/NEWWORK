import asyncio
import json
import os
import random
import re
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from playwright.async_api import async_playwright

BOT_TOKEN       = os.getenv("BOT_TOKEN", "")
CHANNEL_ID      = os.getenv("CHANNEL_ID", "-1004427004477")
NEW_CHANNEL_ID  = os.getenv("NEW_CHANNEL_ID", "-1003250473765")
ADMIN_ID        = int(os.getenv("ADMIN_ID", "8473160748"))
PANEL_USER      = os.getenv("PANEL_USER", "5260101")
PANEL_PASS      = os.getenv("PANEL_PASS", "Shoaibpanel@123!!!")
LOGIN_URL       = "https://mysmsportal.com/index.php"
OTP_SUMMARY_URL = "https://mysmsportal.com/index.php?opt=shw_sts_today"
POLL_INTERVAL   = int(os.getenv("POLL_INTERVAL", "15"))
NUMBERS_PER_USER = 4
EXPIRE_MINUTES   = 10

ASSIGNMENTS_FILE = "user_assignments.json"
NUMBERS_FILE     = "panel_numbers.json"
ACTIVE_OTPS_FILE = "active_otp_numbers.json"
cookies_file = "panel_cookies.json"
seen_messages = set()

# Playwright state — ready hone tak None rahega
pw_ready = False
page = None
context = None
browser = None

async def safe_send(bot, chat_id, text):
    for _ in range(3):
        try:
            await bot.send_message(chat_id, text, parse_mode="Markdown", read_timeout=15, write_timeout=15)
            return True
        except Exception:
            await asyncio.sleep(2)
    return False

def load_data():
    a, n, o = {}, [], {}
    try:
        with open(ASSIGNMENTS_FILE) as f:
            raw = json.load(f)
            for uid, d in raw.items():
                a[uid] = {"numbers": d, "assigned_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")} if isinstance(d, list) else d
    except: pass
    try:
        with open(NUMBERS_FILE) as f: n = json.load(f)
    except: pass
    try:
        with open(ACTIVE_OTPS_FILE) as f: o = json.load(f)
    except: pass
    return a, n, o

def save_data(a, n, o):
    with open(ASSIGNMENTS_FILE, 'w') as f: json.dump(a, f)
    with open(NUMBERS_FILE, 'w') as f: json.dump(n, f)
    with open(ACTIVE_OTPS_FILE, 'w') as f: json.dump(o, f)

def get_assigned(a):
    return set(num for d in a.values() for num in d["numbers"])

def get_available(a, n):
    used = get_assigned(a)
    return [x for x in n if x not in used]

def cleanup_expired(a, o):
    now = datetime.now()
    exp, freed = [], 0
    for uid, d in a.items():
        at = datetime.strptime(d["assigned_at"], "%Y-%m-%d %H:%M:%S")
        if (now - at).total_seconds()/60 >= EXPIRE_MINUTES and not any(x in o for x in d["numbers"]):
            exp.append(uid); freed += len(d["numbers"])
    for uid in exp: del a[uid]
    return freed, len(exp)

def assign_nums(uid, a, n, o, cnt=NUMBERS_PER_USER):
    uid = str(uid)
    if uid in a and a[uid]["numbers"]: return a[uid]["numbers"], False
    avail = get_available(a, n)
    if not avail: return None, True
    chosen = random.sample(avail, min(cnt, len(avail)))
    a[uid] = {"numbers": chosen, "assigned_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    save_data(a, n, o)
    return chosen, True

def mask(p):
    p = p.strip()
    return p if len(p) <= 6 else f"{p[:4]}*****{p[-3:]}"

def extract_otp(t):
    m = re.search(r'\b(\d{4,8})\b', t)
    return m.group(1) if m else "N/A"

# ========== PLAYWRIGHT — BACKGROUND MEIN SETUP HOGA ==========
async def pw_setup_and_login():
    global pw_ready, page, context, browser
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True, args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu'])
        context = await browser.new_context(viewport={'width':1366,'height':768}, user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0.0.0 Safari/537.36')
        page = await context.new_page()
        try:
            with open(cookies_file) as f: await context.add_cookies(json.load(f))
        except: pass
        
        # Login
        try:
            await page.goto(OTP_SUMMARY_URL, timeout=20000, wait_until='domcontentloaded')
            await asyncio.sleep(0.5)
            if 'Please enter your login details' in await page.content():
                await page.goto(LOGIN_URL, timeout=20000, wait_until='networkidle')
                await asyncio.sleep(1)
                u = page.locator('input[type="text"]').first
                await u.click()
                for c in PANEL_USER: await u.type(c, delay=30)
                await asyncio.sleep(0.3)
                p = page.locator('input[type="password"]').first
                await p.click()
                for c in PANEL_PASS: await p.type(c, delay=30)
                await asyncio.sleep(0.3)
                await page.locator('button, input[type="submit"]').first.click()
                await asyncio.sleep(1.5)
                with open(cookies_file,'w') as f: json.dump(await context.cookies(), f)
        except: pass
        
        pw_ready = True
        print("✅ Playwright ready — OTP polling shuru", flush=True)
    except Exception as e:
        print(f"Playwright setup error: {e}", flush=True)
        # Retry after 10 sec
        await asyncio.sleep(10)
        asyncio.create_task(pw_setup_and_login())

async def save_cookies():
    try:
        with open(cookies_file,'w') as f: json.dump(await context.cookies(), f)
    except: pass

async def fetch_otps():
    if not pw_ready or page is None:
        return []
    a, n, o = load_data()
    assigned = get_assigned(a)
    if not assigned:
        return []
    all_msgs = []
    try:
        await page.goto(OTP_SUMMARY_URL, timeout=20000, wait_until='domcontentloaded')
        await asyncio.sleep(0.8)
        if 'Please enter your login details' in await page.content():
            # Re-login
            await page.goto(LOGIN_URL, timeout=20000, wait_until='networkidle')
            await asyncio.sleep(1)
            u = page.locator('input[type="text"]').first
            await u.click()
            for c in PANEL_USER: await u.type(c, delay=30)
            await asyncio.sleep(0.3)
            p = page.locator('input[type="password"]').first
            await p.click()
            for c in PANEL_PASS: await p.type(c, delay=30)
            await asyncio.sleep(0.3)
            await page.locator('button, input[type="submit"]').first.click()
            await asyncio.sleep(1.5)
            await save_cookies()
            await page.goto(OTP_SUMMARY_URL, timeout=20000, wait_until='domcontentloaded')
            await asyncio.sleep(0.8)
        
        rows = page.locator('table tbody tr')
        total = await rows.count()
        for i in range(total):
            row = rows.nth(i)
            cols = row.locator('td')
            if await cols.count() < 5: continue
            num = (await cols.nth(0).inner_text()).strip()
            if num not in assigned: continue  # Sirf assigned check
            
            form = row.locator('form').first
            clicked = False
            if await form.count() > 0:
                try: await form.click(); clicked = True
                except: pass
            if not clicked:
                btn = row.locator('button:has-text("Select"), input[value*="Select"]').first
                if await btn.count() > 0:
                    try: await btn.click(); clicked = True
                    except: pass
            if clicked:
                try:
                    await page.wait_for_load_state('domcontentloaded', timeout=8000)
                    await asyncio.sleep(1)
                    dr = page.locator('table tbody tr')
                    for j in range(await dr.count()):
                        dc = dr.nth(j).locator('td')
                        if await dc.count() >= 5:
                            dt = (await dc.nth(0).inner_text()).strip()
                            ph = (await dc.nth(1).inner_text()).strip()
                            se = (await dc.nth(2).inner_text()).strip()
                            ms = (await dc.nth(-1).inner_text()).strip()
                            if ms and len(ms) > 3 and dt:
                                all_msgs.append({"datetime":dt,"phone":ph,"sender":se,"message":ms})
                    await page.go_back()
                    await page.wait_for_load_state('domcontentloaded', timeout=8000)
                    await asyncio.sleep(0.5)
                except:
                    try:
                        await page.goto(OTP_SUMMARY_URL, timeout=15000, wait_until='domcontentloaded')
                        await asyncio.sleep(0.5)
                    except: pass
        if random.random() < 0.2: await save_cookies()
        return all_msgs
    except:
        return []

async def send_otp(bot, msg, a, o):
    otp = extract_otp(msg['message'])
    masked_num = mask(msg['phone'])
    full = msg['phone']
    o[full] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ct = f"🔐 NEW OTP RECEIVED\n📱 Phone: `{masked_num}`\n🕐 Time: {msg['datetime']}\n✉️ Sender: `{msg['sender']}`\n🔢 Code: `{otp}`\n📝 Message:\n`{msg['message'][:300]}`"
    await safe_send(bot, CHANNEL_ID, ct)
    await safe_send(bot, NEW_CHANNEL_ID, ct)
    uid = None
    for u, d in a.items():
        if full in d["numbers"]:
            uid = int(u); break
    if uid:
        dm = f"🔐 YOUR OTP ARRIVED ✅\n\n📱 Number: `{full}`\n🕐 Time: {msg['datetime']}\n✉️ Sender: `{msg['sender']}`\n🔢 OTP Code: `{otp}`\n\n📝 Full Message:\n`{msg['message'][:300]}`"
        await safe_send(bot, uid, dm)
    print(f"OTP: {masked_num} | {otp}", flush=True)

async def poll_loop(bot):
    global seen_messages
    err = 0; cc = 0
    while True:
        try:
            msgs = await fetch_otps()
            a, n, o = load_data()
            cc += 1
            if cc >= 5:
                cc = 0
                freed, users = cleanup_expired(a, o)
                if freed > 0:
                    print(f"FREED: {freed} numbers ({users} users)", flush=True)
                    save_data(a, n, o)
            if msgs is None:
                err += 1
                if err >= 5:
                    await safe_send(bot, ADMIN_ID, "⚠️ FETCH FAILING")
                    err = 0
            else:
                err = 0
                for m in msgs:
                    key = f"{m['datetime']}|{m['phone']}|{m['message'][:40]}"
                    if key not in seen_messages:
                        seen_messages.add(key)
                        await send_otp(bot, m, a, o)
                save_data(a, n, o)
            if len(seen_messages) > 500:
                seen_messages = set(list(seen_messages)[-250:])
        except Exception as e:
            print(f"Poll err: {e}", flush=True)
        await asyncio.sleep(POLL_INTERVAL)

# ========== COMMANDS ==========
async def start_cmd(u: Update, c: ContextTypes):
    uid = u.effective_user.id
    name = u.effective_user.first_name
    a, n, o = load_data()
    if not n:
        await u.message.reply_text("⚠️ Numbers abhi upload nahi hue! Admin jald karega 🙏", parse_mode="Markdown")
        return
    nums, is_new = assign_nums(uid, a, n, o)
    if nums is None:
        await u.message.reply_text("😔 Saare numbers use ho chuke! Jaldi expire honge 🙏", parse_mode="Markdown")
        return
    nt = "\n".join([f"  {i+1}. `{x}`" for i, x in enumerate(nums)])
    if is_new:
        await u.message.reply_text(f"🎉 Welcome {name}!\n\n✅ Tere {len(nums)} numbers:\n{nt}\n\n⏰ 10min mein OTP aaya → permanent!\n❌ Nahi aaya → /refresh\n\n🔄 /refresh | 📱 /mynumber", parse_mode="Markdown")
    else:
        await u.message.reply_text(f"👋 Welcome back {name}!\n\n📱 Tere numbers:\n{nt}\n\n🔄 Naye chahiye? /refresh", parse_mode="Markdown")

async def mynumber_cmd(u: Update, c: ContextTypes):
    uid = str(u.effective_user.id)
    a, _, o = load_data()
    if uid in a:
        nt = ""
        for i, x in enumerate(a[uid]["numbers"]):
            st = "✅ LOCKED" if x in o else "⏳ Waiting"
            nt += f"  {i+1}. `{x}` — {st}\n"
        await u.message.reply_text(f"📱 Tere Numbers:\n{nt}\n🔄 Naye chahiye? /refresh", parse_mode="Markdown")
    else:
        await u.message.reply_text("❌ Pehle /start karein", parse_mode="Markdown")

async def refresh_cmd(u: Update, c: ContextTypes):
    uid = str(u.effective_user.id)
    a, n, o = load_data()
    if uid not in a:
        await u.message.reply_text("❌ Pehle /start karein!")
        return
    old = a[uid]
    if any(x in o for x in old["numbers"]):
        await u.message.reply_text("✅ Tere number par OTP aaya hai — permanent tera hai ✅", parse_mode="Markdown")
        return
    del a[uid]
    new, _ = assign_nums(uid, a, n, o)
    if new is None:
        a[uid] = old; save_data(a, n, o)
        await u.message.reply_text("😔 Naye numbers nahi available!")
        return
    nt = "\n".join([f"  {i+1}. `{x}`" for i, x in enumerate(new)])
    await u.message.reply_text(f"🔄 Refreshed!\n✅ Naye numbers:\n{nt}", parse_mode="Markdown")

async def upload_cmd(u: Update, c: ContextTypes):
    if u.effective_user.id != ADMIN_ID: return await u.message.reply_text("❌ Admin only!")
    if not u.message.text or len(u.message.text.split()) < 2:
        return await u.message.reply_text("📝 `/uploadnumbers +591xxx, +591yyy...`", parse_mode="Markdown")
    txt = u.message.text.replace('/uploadnumbers', '').strip()
    nn = [x.strip() for x in re.split(r'[,\n]+', txt) if x.strip().startswith('+')]
    if not nn: return await u.message.reply_text("❌ Koi valid number nahi!")
    a, ex, o = load_data()
    added = sum(1 for x in nn if x not in ex)
    ex.extend(x for x in nn if x not in ex)
    save_data(a, ex, o)
    avail = len(get_available(a, ex))
    await u.message.reply_text(f"✅ Uploaded!\nNaye: {added} | Total: {len(ex)} | Available: {avail} | Locked: {len(o)}", parse_mode="Markdown")

async def freeuser_cmd(u: Update, c: ContextTypes):
    if u.effective_user.id != ADMIN_ID: return await u.message.reply_text("❌ Admin only!")
    p = u.message.text.split()
    if len(p) < 2: return await u.message.reply_text("Use: `/freeuser user_id`")
    a, n, o = load_data()
    t = p[1].strip()
    if t in a:
        f = len(a[t]["numbers"]); del a[t]; save_data(a, n, o)
        await u.message.reply_text(f"✅ Freed: {f}")
    else: await u.message.reply_text("❌ Nahi mila!")

async def stats_cmd(u: Update, c: ContextTypes):
    if u.effective_user.id != ADMIN_ID: return await u.message.reply_text("❌ Admin only!")
    a, n, o = load_data()
    pw_status = "✅ Ready" if pw_ready else "⏳ Loading..."
    await u.message.reply_text(f"📊 Total: {len(n)} | Available: {len(get_available(a,n))} | Users: {len(a)} | Locked: {len(o)}\n🌐 Browser: {pw_status}", parse_mode="Markdown")

async def clearseen_cmd(u: Update, c: ContextTypes):
    if u.effective_user.id != ADMIN_ID: return await u.message.reply_text("❌ Admin only!")
    global seen_messages; seen_messages = set()
    await u.message.reply_text("✅ Cleared!")

async def relogin_cmd(u: Update, c: ContextTypes):
    if u.effective_user.id != ADMIN_ID: return await u.message.reply_text("❌ Admin only!")
    try: os.remove(cookies_file)
    except: pass
    await u.message.reply_text("✅ Cookie deleted — auto re-login next poll")

async def status_cmd(u: Update, c: ContextTypes):
    if u.effective_user.id != ADMIN_ID: return await u.message.reply_text("❌ Admin only!")
    pw_status = "✅ Ready" if pw_ready else "⏳ Loading..."
    await u.message.reply_text(f"✅ Bot running\n🌐 Browser: {pw_status}")

# ========== MAIN — BOT PEHLE START HOGA ⚡ ==========
async def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN missing!", flush=True); return
    
    a, n, o = load_data(); save_data(a, n, o)
    print(f"LOADED: {len(n)} numbers | {len(a)} users", flush=True)
    
    # ✅ STEP 1: Telegram bot TURANT start — commands instant
    app = Application.builder().token(BOT_TOKEN).read_timeout(30).write_timeout(30).connect_timeout(30).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("mynumber", mynumber_cmd))
    app.add_handler(CommandHandler("refresh", refresh_cmd))
    app.add_handler(CommandHandler("uploadnumbers", upload_cmd))
    app.add_handler(CommandHandler("freeuser", freeuser_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("clearseen", clearseen_cmd))
    app.add_handler(CommandHandler("relogin", relogin_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    
    print("✅ BOT ONLINE — /start turant kaam karega ⚡", flush=True)
    
    # ✅ STEP 2: Playwright BACKGROUND mein setup hoga — bot ko block nahi karega
    asyncio.create_task(pw_setup_and_login())
    
    # ✅ STEP 3: Poll loop bhi background mein (Playwright ready hone ke baad hi kaam karega)
    asyncio.create_task(poll_loop(app.bot))
    
    # Keep running
    await asyncio.Event().wait()

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: print("STOPPED", flush=True)
