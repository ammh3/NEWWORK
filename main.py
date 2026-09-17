import asyncio
import json
import os
import random
import re
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from playwright.async_api import async_playwright

# ========== CONFIG ==========
BOT_TOKEN       = os.getenv("BOT_TOKEN", "")
CHANNEL_ID      = os.getenv("CHANNEL_ID", "-1004427004477")
ADMIN_ID        = int(os.getenv("ADMIN_ID", "8473160748"))
PANEL_USER      = os.getenv("PANEL_USER", "5260101")
PANEL_PASS      = os.getenv("PANEL_PASS", "Shoaibpanel@123!!!")
LOGIN_URL       = "https://mysmsportal.com/index.php"
OTP_SUMMARY_URL = "https://mysmsportal.com/index.php?opt=shw_sts_today"
POLL_INTERVAL   = int(os.getenv("POLL_INTERVAL", "25"))

browser = None
context = None
page = None
seen_messages = set()
cookies_file = "panel_cookies.json"

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
        await page.goto(OTP_SUMMARY_URL, timeout=30000, wait_until='domcontentloaded')
        await asyncio.sleep(1.5)
        content = await page.content()
        return 'Please enter your login details' not in content
    except: return False

async def do_login():
    print("🔐 Logging in...")
    try:
        await page.goto(LOGIN_URL, timeout=30000, wait_until='networkidle')
        await asyncio.sleep(2)
        user = page.locator('input[type="text"]').first
        await user.click()
        await asyncio.sleep(0.5)
        for c in PANEL_USER: await user.type(c, delay=random.randint(50,100))
        await asyncio.sleep(1)
        pwd = page.locator('input[type="password"]').first
        await pwd.click()
        await asyncio.sleep(0.5)
        for c in PANEL_PASS: await pwd.type(c, delay=random.randint(50,100))
        await asyncio.sleep(1)
        await page.locator('button, input[type="submit"]').first.click()
        await asyncio.sleep(3)
        await save_cookies()
        ok = await is_logged_in()
        print("✅ LOGIN OK" if ok else "❌ Login failed")
        return ok
    except Exception as e:
        print(f"❌ Login error: {e}")
        return False

# ========== OTP FETCH — PURE LOCATOR, NO query_selector_all ==========
async def get_all_messages():
    all_messages = []
    try:
        await page.goto(OTP_SUMMARY_URL, timeout=30000, wait_until='domcontentloaded')
        await asyncio.sleep(2)
        
        if 'Please enter your login details' in await page.content():
            print("⚠️ Re-login...")
            if not await do_login(): return None, "login_fail"
            await page.goto(OTP_SUMMARY_URL, timeout=30000, wait_until='domcontentloaded')
            await asyncio.sleep(2)

        rows = page.locator('table tr')
        n = await rows.count()
        if n <= 1:
            print("ℹ️ No data")
            return [], "empty"
        
        print(f"📊 Found {n-1} numbers")

        for i in range(1, n):
            row = rows.nth(i)
            cols = row.locator('td')
            nc = await cols.count()
            if nc < 5: continue
            
            number = (await cols.nth(0).inner_text()).strip()
            if not number: continue
            
            print(f"   🔍 Checking: {number}")

            # Button dhoondho
            cell = cols.nth(4)
            btn = cell.locator('button:has-text("Select")')
            if await btn.count() == 0: btn = cell.locator('button')
            if await btn.count() == 0: btn = cell.locator('a')

            if await btn.count() > 0:
                print(f"   ✅ Clicking...")
                try:
                    await btn.click()
                    await asyncio.sleep(2.5)
                    
                    # Messages padho
                    msgs = page.locator('table tr')
                    nm = await msgs.count()
                    for j in range(1, nm):
                        mcol = msgs.nth(j).locator('td')
                        if await mcol.count() >= 3:
                            all_messages.append({
                                "datetime": (await mcol.nth(0).inner_text()).strip(),
                                "phone": number,
                                "sender": (await mcol.nth(1).inner_text()).strip(),
                                "message": (await mcol.nth(-1).inner_text()).strip()
                            })
                    
                    await page.go_back()
                    await asyncio.sleep(2)
                except Exception as e:
                    print(f"   ⚠️ Click error: {e}")
                    try: await page.goto(OTP_SUMMARY_URL, timeout=20000); await asyncio.sleep(1)
                    except: pass
            else:
                print(f"   ⚠️ No button")

        if random.random() < 0.2: await save_cookies()
        return all_messages, "ok"
        
    except Exception as e:
        print(f"❌ Fetch error: {e}")
        return None, f"error"

# ========== FORMAT ==========
def extract_otp(txt):
    m = re.search(r'(\d{4,8})', txt)
    return m.group(1) if m else "????"

async def send_channel(bot, msg):
    otp = extract_otp(msg['message'])
    text = (
        f"🔐 OTP\n"
        f"📱 {msg['phone']}\n"
        f"🔢 Code: `{otp}`\n"
        f"📝 {msg['message'][:100]}"
    )
    await bot.send_message(CHANNEL_ID, text, parse_mode="Markdown")
    print(f"✅ SENT: {msg['phone']} — {otp}")

# ========== POLL ==========
async def poll_loop(bot):
    global seen_messages
    print("\n🔄 POLLING STARTED\n")
    err = 0
    while True:
        msgs, st = await get_all_messages()
        if msgs is None:
            err += 1
            print(f"⚠️ Error {err}/5")
            if err >= 5:
                await bot.send_message(ADMIN_ID, f"⚠️ FETCH FAILING\nUse /relogin")
                err = 0
        else:
            err = 0
            new = 0
            for m in msgs:
                key = f"{m['phone']}:{m['datetime']}"
                if key not in seen_messages:
                    seen_messages.add(key)
                    await send_channel(bot, m)
                    new += 1
            if new: print(f"🔔 {new} NEW OTPs!")
            else: print(f"ℹ️ No new")
        
        if len(seen_messages) > 3000:
            seen_messages = set(list(seen_messages)[-1500:])
        await asyncio.sleep(max(15, POLL_INTERVAL))

# ========== COMMANDS ==========
async def start(u, c): await u.message.reply_text("✅ Bot Online\n/status /testfetch /relogin /clearseen")
async def clearseen(u, c):
    global seen_messages
    seen_messages = set()
    await u.message.reply_text("✅ Cache cleared")
async def relogin(u, c):
    try: os.remove(cookies_file)
    except: pass
    ok = await do_login()
    await u.message.reply_text("✅ Done" if ok else "❌ Failed")
async def status(u, c):
    msgs, _ = await get_all_messages()
    await u.message.reply_text(f"✅ Working\nMessages: {len(msgs) if msgs else 0}\nCache: {len(seen_messages)}")
async def testfetch(u, c):
    m = await u.message.reply_text("⏳...")
    msgs, _ = await get_all_messages()
    if msgs: await m.edit_text(f"✅ {len(msgs)} found\nLatest: {msgs[-1]['phone']} — {extract_otp(msgs[-1]['message'])}")
    else: await m.edit_text("❌ Nothing found")

# ========== MAIN ==========
async def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN missing")
        return
    await setup_browser()
    if not await is_logged_in():
        await do_login()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clearseen", clearseen))
    app.add_handler(CommandHandler("relogin", relogin))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("testfetch", testfetch))
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    await poll_loop(app.bot)

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: print("\n🛑 Stopped")
