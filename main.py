import asyncio
import json
import os
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
POLL_INTERVAL   = int(os.getenv("POLL_INTERVAL", "6"))  # ⚡ 6 SECOND

cookies_file = "panel_cookies.json"
seen_messages = set()
bot_ref = None

async def safe_send(bot, chat_id, text):
    for _ in range(3):
        try:
            await bot.send_message(chat_id, text, parse_mode="Markdown", read_timeout=15, write_timeout=15)
            return True
        except Exception:
            await asyncio.sleep(1)
    return False

def mask(p):
    p = p.strip()
    return p if len(p) <= 6 else f"{p[:4]}*****{p[-3:]}"

def extract_otp(t):
    m = re.search(r'\b(\d{4,8})\b', t)
    return m.group(1) if m else "N/A"

async def run_bot():
    global seen_messages
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True, args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu'])
    context = await browser.new_context(viewport={'width':1366,'height':768}, user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0.0.0 Safari/537.36')
    page = await context.new_page()
    try:
        with open(cookies_file) as f: await context.add_cookies(json.load(f))
    except: pass

    async def login():
        try:
            await page.goto(LOGIN_URL, timeout=20000, wait_until='networkidle')
            await asyncio.sleep(1)
            u = page.locator('input[type="text"]').first
            await u.click()
            for c in PANEL_USER: await u.type(c, delay=20)
            await asyncio.sleep(0.2)
            p = page.locator('input[type="password"]').first
            await p.click()
            for c in PANEL_PASS: await p.type(c, delay=20)
            await asyncio.sleep(0.2)
            await page.locator('button, input[type="submit"]').first.click()
            await asyncio.sleep(1.5)
            with open(cookies_file,'w') as f: json.dump(await context.cookies(), f)
            return True
        except Exception as e:
            print(f"Login err: {e}", flush=True)
            return False

    try:
        await page.goto(OTP_SUMMARY_URL, timeout=20000, wait_until='domcontentloaded')
        await asyncio.sleep(0.5)
        if 'Please enter your login details' in await page.content():
            await login()
    except: await login()

    print(f"✅ BOT ONLINE — Har {POLL_INTERVAL}s mein check ⚡", flush=True)
    await safe_send(bot_ref, ADMIN_ID, f"✅ Bot chalu — har {POLL_INTERVAL} second mein check karega!")

    err_count = 0
    while True:
        try:
            await page.goto(OTP_SUMMARY_URL, timeout=20000, wait_until='domcontentloaded')
            await asyncio.sleep(0.5)

            if 'Please enter your login details' in await page.content():
                print("🔄 Re-login...", flush=True)
                await login()
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
                    await asyncio.sleep(0.8)
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
                    await asyncio.sleep(0.3)
                except Exception as e:
                    print(f"Detail err: {e}", flush=True)
                    try: await page.goto(OTP_SUMMARY_URL, timeout=15000, wait_until='domcontentloaded')
                    except: pass

            if len(seen_messages) > 500:
                seen_messages = set(list(seen_messages)[-250:])
            err_count = 0

        except Exception as e:
            err_count += 1
            print(f"Poll err ({err_count}/5): {e}", flush=True)
            if err_count >= 5:
                await safe_send(bot_ref, ADMIN_ID, "⚠️ Bar-bar error aa raha — re-login ho raha hai")
                err_count = 0
                try: await login()
                except: pass

        await asyncio.sleep(POLL_INTERVAL)

async def start_cmd(u: Update, c: ContextTypes):
    await u.message.reply_text(f"✅ Bot chalu hai!\n⏱️ Har {POLL_INTERVAL} second mein check karega\n📡 Dono channels pe OTP jayega\n🔁 Repeat nahi hoga", parse_mode="Markdown")

async def status_cmd(u: Update, c: ContextTypes):
    if u.effective_user.id != ADMIN_ID: return
    await u.message.reply_text(f"✅ Bot Running\n⏱️ Check every: {POLL_INTERVAL}s\n📡 Channel 1: `{CHANNEL_ID}`\n📡 Channel 2: `{NEW_CHANNEL_ID}`\n🔁 Duplicate protection: ON", parse_mode="Markdown")

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
    app.add_handler(CommandHandler("relogin", relogin_cmd))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    asyncio.create_task(run_bot())

    await asyncio.Event().wait()

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: print("STOPPED", flush=True)
