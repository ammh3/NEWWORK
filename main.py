import asyncio
import json
import os
import re
import time
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
POLL_INTERVAL   = int(os.getenv("POLL_INTERVAL", "8"))

cookies_file = "panel_cookies.json"
seen_messages = set()
bot_ref = None
last_alert_time = 0

# Browser state
pw = None
browser = None
context = None
page = None

async def safe_send(bot, chat_id, text):
    for _ in range(3):
        try:
            await bot.send_message(chat_id, text, parse_mode="Markdown", read_timeout=15, write_timeout=15)
            return True
        except Exception:
            await asyncio.sleep(1)
    return False

async def admin_alert(bot, text):
    global last_alert_time
    now = time.time()
    if now - last_alert_time < 3600:
        return
    last_alert_time = now
    await safe_send(bot, ADMIN_ID, text)

def mask(p):
    p = p.strip()
    return p if len(p) <= 6 else f"{p[:4]}*****{p[-3:]}"

def extract_otp(t):
    m = re.search(r'\b(\d{4,8})\b', t)
    return m.group(1) if m else "N/A"

# ========== BROWSER MANAGEMENT — CRASH RECOVERY ✅ ==========
async def start_browser():
    """Start fresh browser — memory optimized + sandbox disabled"""
    global pw, browser, context, page
    
    # Close old if exists
    try:
        if page: await page.close()
        if context: await context.close()
        if browser: await browser.close()
        if pw: await pw.stop()
    except: pass
    
    pw = await async_playwright().start()
    
    browser = await pw.chromium.launch(
        headless=True,
        args=[
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-gpu',
            '--disable-software-rasterizer',
            '--disable-extensions',
            '--disable-default-apps',
            '--disable-sync',
            '--disable-translate',
            '--disable-background-networking',
            '--disable-background-timer-throttling',
            '--disable-renderer-backgrounding',
            '--disable-blink-features=AutomationControlled',
            '--mute-audio',
            '--no-first-run',
            '--no-zygote',
            '--memory-pressure-off',
            '--single-process',
            '--aggressive-cache-discard',
            '--max_old_space_size=256',
        ]
    )
    
    context = await browser.new_context(
        viewport={'width':1024,'height':768},
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
        locale='en-US',
        device_scale_factor=1,
        is_mobile=False,
        has_touch=False,
        java_script_enabled=True
    )
    
    # Anti-detect
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
        Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
        window.chrome = {runtime: {}};
    """)
    
    # Block images + CSS to save memory ⚡
    await context.route("**/*", lambda route: 
        route.abort() if route.request.resource_type in ['image', 'stylesheet', 'font', 'media'] else route.continue_()
    )
    
    page = await context.new_page()
    
    # Load cookies
    try:
        with open(cookies_file) as f:
            await context.add_cookies(json.load(f))
    except: pass
    
    print("🌐 New browser started (memory optimized)", flush=True)

async def save_cookies():
    try:
        with open(cookies_file, 'w') as f:
            json.dump(await context.cookies(), f)
    except: pass

async def do_login():
    try:
        print("🔐 Logging in...", flush=True)
        await page.goto(LOGIN_URL, timeout=25000, wait_until='domcontentloaded')
        await asyncio.sleep(1.2)
        
        u = page.locator('input[type="text"]').first
        await u.click()
        await asyncio.sleep(0.2)
        await u.fill(PANEL_USER)
        await asyncio.sleep(0.3)
        
        p = page.locator('input[type="password"]').first
        await p.click()
        await asyncio.sleep(0.2)
        await p.fill(PANEL_PASS)
        await asyncio.sleep(0.3)
        
        await page.locator('button, input[type="submit"]').first.click()
        await asyncio.sleep(2)
        
        await save_cookies()
        print("✅ Login OK", flush=True)
        return True
    except Exception as e:
        print(f"❌ Login failed: {e}", flush=True)
        return False

async def check_login():
    """Returns: True=logged_in, False=need_login, None=page_crashed"""
    try:
        await page.goto(OTP_SUMMARY_URL, timeout=20000, wait_until='domcontentloaded')
        await asyncio.sleep(0.6)
        content = await page.content()
        if 'Please enter your login details' in content:
            return False
        return True
    except Exception as e:
        if 'crashed' in str(e).lower() or 'closed' in str(e).lower():
            return None  # Page crashed — need full restart
        return False

# ========== MAIN POLL — CRASH SAFE ✅ ==========
async def run_bot():
    global seen_messages
    
    await start_browser()
    
    # Initial login
    status = await check_login()
    if status == False:
        await do_login()
    elif status is None:
        await start_browser()
        await do_login()
    
    print(f"✅ BOT ONLINE — Har {POLL_INTERVAL}s check ⚡", flush=True)
    await safe_send(bot_ref, ADMIN_ID, f"✅ Bot chalu — har {POLL_INTERVAL}s mein check karega!")
    
    err_count = 0
    crash_count = 0
    
    while True:
        try:
            # Check if page is alive
            try:
                await page.goto(OTP_SUMMARY_URL, timeout=20000, wait_until='domcontentloaded')
                await asyncio.sleep(0.5)
            except Exception as e:
                if 'crashed' in str(e).lower() or 'closed' in str(e).lower():
                    crash_count += 1
                    print(f"💥 Page crashed ({crash_count}) — restarting browser...", flush=True)
                    await start_browser()
                    await do_login()
                    if crash_count >= 3:
                        await admin_alert(bot_ref, f"⚠️ Browser baar-baar crash ho raha ({crash_count} baar) — recover ho raha hai")
                        crash_count = 0
                    await asyncio.sleep(5)
                    continue
                raise
            
            crash_count = 0  # Reset — sab theek hai
            
            content = await page.content()
            if 'Please enter your login details' in content:
                print("🔄 Session expired — re-login", flush=True)
                ok = await do_login()
                if not ok:
                    await asyncio.sleep(5)
                    continue
                await page.goto(OTP_SUMMARY_URL, timeout=20000, wait_until='domcontentloaded')
                await asyncio.sleep(0.5)

            rows = page.locator('table tbody tr')
            total = await rows.count()
            print(f"📊 Check: {total} rows", flush=True)

            for i in range(total):
                row = rows.nth(i)
                cols = row.locator('td')
                if await cols.count() < 5: continue
                num = (await cols.nth(0).inner_text()).strip()

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
                if not clicked:
                    continue

                try:
                    await page.wait_for_load_state('domcontentloaded', timeout=8000)
                    await asyncio.sleep(0.7)
                    detail_rows = page.locator('table tbody tr')
                    for j in range(await detail_rows.count()):
                        dcols = detail_rows.nth(j).locator('td')
                        if await dcols.count() >= 5:
                            dt = (await dcols.nth(0).inner_text()).strip()
                            ph = (await dcols.nth(1).inner_text()).strip()
                            se = (await dcols.nth(2).inner_text()).strip()
                            ms = (await dcols.nth(-1).inner_text()).strip()
                            if ms and len(ms) > 3 and dt:
                                key = f"{dt}|{ph}|{ms[:40]}"
                                if key not in seen_messages:
                                    seen_messages.add(key)
                                    otp = extract_otp(ms)
                                    masked = mask(ph)
                                    text = f"🔐 NEW OTP RECEIVED\n📱 Phone: `{masked}`\n🕐 Time: {dt}\n✉️ Sender: `{se}`\n🔢 Code: `{otp}`\n📝 Message:\n`{ms[:300]}`"
                                    await safe_send(bot_ref, CHANNEL_ID, text)
                                    await safe_send(bot_ref, NEW_CHANNEL_ID, text)
                                    print(f"✅ SENT: {masked} | {otp}", flush=True)
                    await page.go_back()
                    await page.wait_for_load_state('domcontentloaded', timeout=8000)
                    await asyncio.sleep(0.2)
                except Exception as e:
                    print(f"Detail err: {e}", flush=True)
                    try: await page.goto(OTP_SUMMARY_URL, timeout=15000, wait_until='domcontentloaded')
                    except: pass

            import random
            if random.random() < 0.15:
                await save_cookies()

            if len(seen_messages) > 500:
                seen_messages = set(list(seen_messages)[-250:])
            err_count = 0

        except Exception as e:
            err_count += 1
            print(f"Poll err ({err_count}/5): {e}", flush=True)
            
            # Agar page crash → browser restart
            if 'crashed' in str(e).lower() or 'closed' in str(e).lower():
                print("💥 Crash detected — full browser restart", flush=True)
                await start_browser()
                await do_login()
            
            if err_count >= 5:
                await admin_alert(bot_ref, "⚠️ Network issues — recover ho raha hai")
                err_count = 0
                try:
                    await start_browser()
                    await do_login()
                except: pass

        await asyncio.sleep(POLL_INTERVAL)

# ========== COMMANDS ==========
async def start_cmd(u: Update, c: ContextTypes):
    await u.message.reply_text(f"✅ Bot chalu hai!\n⏱️ Har {POLL_INTERVAL}s check\n📡 Dono channels pe OTP\n🔁 Repeat nahi hoga\n🔐 Crash auto-recovery ON", parse_mode="Markdown")

async def status_cmd(u: Update, c: ContextTypes):
    if u.effective_user.id != ADMIN_ID: return
    await u.message.reply_text(f"✅ Bot Running\n⏱️ Check: {POLL_INTERVAL}s\n🛡️ Crash recovery: ON\n📡 Channels: 2 active\n🖼️ Images blocked (memory save)", parse_mode="Markdown")

async def restart_cmd(u: Update, c: ContextTypes):
    if u.effective_user.id != ADMIN_ID: return
    await u.message.reply_text("🔄 Browser restart ho raha hai...")
    await start_browser()
    await do_login()
    await u.message.reply_text("✅ Browser restart + login done!")

async def relogin_cmd(u: Update, c: ContextTypes):
    if u.effective_user.id != ADMIN_ID: return
    try: os.remove(cookies_file)
    except: pass
    await u.message.reply_text("✅ Cookie delete — auto re-login hoga")

async def main():
    global bot_ref
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN missing!", flush=True); return

    app = Application.builder().token(BOT_TOKEN).read_timeout(30).write_timeout(30).connect_timeout(30).build()
    bot_ref = app.bot

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("restart", restart_cmd))
    app.add_handler(CommandHandler("relogin", relogin_cmd))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    asyncio.create_task(run_bot())

    await asyncio.Event().wait()

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: print("STOPPED", flush=True)
