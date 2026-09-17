import asyncio
import json
import os
import random
import re
from datetime import datetime
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# ============================================================
# CONFIGURATION - Railway Environment Variables
# ============================================================
BOT_TOKEN       = os.getenv("BOT_TOKEN", "")
CHANNEL_ID      = os.getenv("CHANNEL_ID", "-1004427004477")
ADMIN_ID        = int(os.getenv("ADMIN_ID", "8473160748"))
PANEL_USER      = os.getenv("PANEL_USER", "5260101")
PANEL_PASS      = os.getenv("PANEL_PASS", "Shoaibpanel@123!!!")
LOGIN_URL       = "https://mysmsportal.com/index.php"
OTP_SUMMARY_URL = "https://mysmsportal.com/index.php?opt=shw_sts_today"
POLL_INTERVAL   = int(os.getenv("POLL_INTERVAL", "25"))

# Global
browser = None
context = None
page = None
seen_messages = set()
cookies_file = "panel_cookies.json"

# ============================================================
# BROWSER SETUP - STEALTH MODE
# ============================================================
async def setup_browser():
    global browser, context, page
    
    pw = await async_playwright().start()
    
    browser = await pw.chromium.launch(
        headless=True,
        args=[
            '--no-sandbox',
            '--disable-blink-features=AutomationControlled',
            '--disable-dev-shm-usage',
            '--disable-setuid-sandbox',
            '--no-first-run',
            '--no-zygote',
            '--disable-gpu',
            '--window-size=1366,768',
        ]
    )
    
    context = await browser.new_context(
        viewport={'width': 1366, 'height': 768},
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
        locale='en-US',
        timezone_id='Europe/London',
    )
    
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        window.chrome = {runtime: {}};
        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
    """)
    
    page = await context.new_page()
    
    try:
        with open(cookies_file) as f:
            cookies = json.load(f)
            await context.add_cookies(cookies)
        print("🍪 Saved cookies loaded")
    except:
        print("ℹ️ No saved cookies")
    
    return page

async def save_cookies():
    cookies = await context.cookies()
    with open(cookies_file, 'w') as f:
        json.dump(cookies, f)

# ============================================================
# LOGIN
# ============================================================
async def is_logged_in():
    try:
        await page.goto(OTP_SUMMARY_URL, timeout=30000, wait_until='domcontentloaded')
        await asyncio.sleep(random.uniform(1, 2))
        
        content = await page.content()
        
        if 'Please enter your login details' in content:
            return False
        
        if 'Standard rate SMS' in content or 'Today\'s figures' in content:
            return True
            
        return True
        
    except Exception as e:
        print(f"⚠️ Login check error: {e}")
        return False

async def do_login():
    print("🔐 Logging in to mysmsportal.com...")
    
    try:
        await page.goto(LOGIN_URL, timeout=30000, wait_until='networkidle')
        await asyncio.sleep(random.uniform(2, 3))
        
        user_input = await page.wait_for_selector('input[type="text"]', timeout=10000)
        await user_input.click()
        await asyncio.sleep(random.uniform(0.3, 0.7))
        await user_input.fill('')
        for char in PANEL_USER:
            await user_input.type(char, delay=random.randint(40, 120))
            await asyncio.sleep(random.uniform(0.01, 0.05))
        
        await asyncio.sleep(random.uniform(0.5, 1))
        
        pass_input = await page.wait_for_selector('input[type="password"]', timeout=5000)
        await pass_input.click()
        await asyncio.sleep(random.uniform(0.3, 0.7))
        for char in PANEL_PASS:
            await pass_input.type(char, delay=random.randint(40, 120))
            await asyncio.sleep(random.uniform(0.01, 0.05))
        
        await asyncio.sleep(random.uniform(0.5, 1))
        
        login_btn = await page.wait_for_selector('button, input[type="submit"]', timeout=5000)
        await login_btn.click()
        
        await asyncio.sleep(random.uniform(3, 5))
        await page.wait_for_load_state('networkidle', timeout=15000)
        
        await save_cookies()
        
        if await is_logged_in():
            print("✅ LOGIN SUCCESSFUL!")
            return True
        else:
            print("❌ Login may have failed")
            try:
                await page.screenshot(path='login_fail.png')
            except:
                pass
            return False
            
    except Exception as e:
        print(f"❌ Login error: {e}")
        try:
            await page.screenshot(path='login_error.png')
        except:
            pass
        return False

# ============================================================
# OTP FETCHING
# ============================================================
async def get_all_messages():
    all_messages = []
    
    try:
        await page.goto(OTP_SUMMARY_URL, timeout=30000, wait_until='domcontentloaded')
        await asyncio.sleep(random.uniform(2, 3))
        
        content = await page.content()
        if 'Please enter your login details' in content:
            print("⚠️ Session expired — re-logging in")
            success = await do_login()
            if not success:
                return None, "login_failed"
            await page.goto(OTP_SUMMARY_URL, timeout=30000, wait_until='domcontentloaded')
            await asyncio.sleep(random.uniform(2, 3))
        
        rows = await page.query_selector_all('table tr')
        
        if len(rows) <= 1:
            print("ℹ️ No data in summary table yet")
            return [], "no_data"
        
        print(f"📊 Found {len(rows)-1} numbers in summary")
        
        for i in range(1, len(rows)):
            cols = await rows[i].query_selector_all('td')
            
            if len(cols) < 5:
                continue
            
            number = (await cols[0].inner_text()).strip()
            sender = (await cols[1].inner_text()).strip()
            msg_count = (await cols[2].inner_text()).strip()
            
            if not number:
                continue
            
            print(f"   🔍 Checking number: {number} ({msg_count} messages)")
            
            details_link = await cols[4].query_selector('a')
            
            if details_link:
                href = await details_link.get_attribute('href')
                
                if href:
                    details_url = href if href.startswith('http') else f"https://mysmsportal.com/{href.lstrip('/')}"
                    
                    try:
                        await page.goto(details_url, timeout=20000, wait_until='domcontentloaded')
                        await asyncio.sleep(random.uniform(1.5, 2.5))
                        
                        detail_rows = await page.query_selector_all('table tr')
                        
                        for j in range(1, len(detail_rows)):
                            detail_cols = await detail_rows[j].query_selector_all('td')
                            
                            if len(detail_cols) >= 3:
                                msg_entry = {
                                    "datetime": (await detail_cols[0].inner_text()).strip(),
                                    "phone": number,
                                    "sender": (await detail_cols[1].inner_text()).strip() if len(detail_cols) > 1 else sender,
                                    "message": (await detail_cols[-1].inner_text()).strip(),
                                }
                                
                                if msg_entry["message"]:
                                    all_messages.append(msg_entry)
                        
                        await page.go_back()
                        await asyncio.sleep(random.uniform(1, 2))
                        
                    except Exception as e:
                        print(f"   ⚠️ Error fetching details for {number}: {e}")
                        try:
                            await page.goto(OTP_SUMMARY_URL, timeout=20000, wait_until='domcontentloaded')
                            await asyncio.sleep(1)
                        except:
                            pass
            else:
                print(f"   ⚠️ No DETAILS link for {number}")
        
        if random.random() < 0.15:
            await save_cookies()
        
        return all_messages, "success"
        
    except PlaywrightTimeoutError:
        print("❌ Browser timeout")
        return None, "timeout"
    except Exception as e:
        print(f"❌ Fetch error: {e}")
        import traceback
        traceback.print_exc()
        return None, f"error_{str(e)[:50]}"

# ============================================================
# OTP FORMATTING
# ============================================================
def extract_otp_code(message):
    patterns = [
        r'(?:code|كود|رمز|código|код|验证码|verification|otp|pin|kode|passcode|confirmation|access|security|is your|your.*code|developer\))[\s\W:-]*(\d{3,8})',
        r'(\d{4,8})',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, message, re.IGNORECASE | re.UNICODE)
        if match:
            return re.sub(r'[- ]', '', match.group(1))
    return "????"

def detect_country_flag(phone):
    clean = phone.lstrip('+')
    codes = {
        '1':'🇺🇸','7':'🇷🇺','20':'🇪🇬','27':'🇿🇦','30':'🇬🇷','31':'🇳🇱','32':'🇧🇪','33':'🇫🇷',
        '34':'🇪🇸','36':'🇭🇺','39':'🇮🇹','40':'🇷🇴','41':'🇨🇭','43':'🇦🇹','44':'🇬🇧','45':'🇩🇰',
        '46':'🇸🇪','47':'🇳🇴','48':'🇵🇱','49':'🇩🇪','51':'🇵🇪','52':'🇲🇽','54':'🇦🇷','55':'🇧🇷',
        '56':'🇨🇱','57':'🇨🇴','58':'🇻🇪','60':'🇲🇾','61':'🇦🇺','62':'🇮🇩','63':'🇵🇭','64':'🇳🇿',
        '65':'🇸🇬','66':'🇹🇭','81':'🇯🇵','82':'🇰🇷','84':'🇻🇳','86':'🇨🇳','91':'🇮🇳','92':'🇵🇰',
        '93':'🇦🇫','94':'🇱🇰','95':'🇲🇲','98':'🇮🇷','212':'🇲🇦','213':'🇩🇿','216':'🇹🇳','218':'🇱🇾',
        '234':'🇳🇬','254':'🇰🇪','351':'🇵🇹','353':'🇮🇪','380':'🇺🇦','420':'🇨🇿','852':'🇭🇰',
        '880':'🇧🇩','966':'🇸🇦','971':'🇦🇪','972':'🇮🇱','994':'🇦🇿','995':'🇬🇪','998':'🇺🇿',
        '591':'🇧🇴','593':'🇪🇨','595':'🇵🇾','598':'🇺🇾','673':'🇧🇳','679':'🇫🇯','855':'🇰🇭',
        '856':'🇱🇦','886':'🇹🇼','960':'🇲🇻','961':'🇱🇧','962':'🇯🇴','964':'🇮🇶','965':'🇰🇼',
        '967':'🇾🇪','968':'🇴🇲','973':'🇧🇭','974':'🇶🇦','975':'🇧🇹','976':'🇲🇳','977':'🇳🇵',
        '992':'🇹🇯','993':'🇹🇲','996':'🇰🇬',
    }
    
    for code in sorted(codes.keys(), key=len, reverse=True):
        if clean.startswith(code):
            return codes[code]
    return '🌍'

def format_telegram_message(msg_data):
    flag = detect_country_flag(msg_data['phone'])
    otp_code = extract_otp_code(msg_data['message'])
    
    return (
        f"{flag} *NEW OTP RECEIVED*\n\n"
        f"📱 *Phone:* `{msg_data['phone']}`\n"
        f"✉️ *Sender:* `{msg_data['sender']}`\n"
        f"🔢 *OTP Code:* `{otp_code}`\n"
        f"⏰ *Time:* `{msg_data['datetime']}`\n\n"
        f"📝 *Full Message:*\n`{msg_data['message'][:250]}`"
    ), otp_code

# ============================================================
# TELEGRAM SEND
# ============================================================
async def send_to_channel(bot, msg_data):
    message, otp_code = format_telegram_message(msg_data)
    
    try:
        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=message,
            parse_mode="Markdown"
        )
        print(f"✅ SENT TO CHANNEL: {msg_data['phone']} | OTP: {otp_code}")
        return True
    except Exception as e:
        print(f"❌ Send error: {e}")
        return False

# ============================================================
# MAIN POLLING LOOP
# ============================================================
async def poll_loop(bot):
    global seen_messages
    
    print("\n🔄 ========================================")
    print("🔄 STARTING OTP POLLING LOOP")
    print(f"⏱️  Interval: ~{POLL_INTERVAL} seconds")
    print("🔄 ========================================\n")
    
    messages, status = await get_all_messages()
    
    if messages:
        for msg in messages:
            key = f"{msg['phone']}:{msg['datetime']}:{msg['message'][:40]}"
            seen_messages.add(key)
        print(f"📌 Initial load: {len(messages)} messages marked as seen\n")
    else:
        print(f"⚠️ Initial fetch: {status}\n")
    
    error_count = 0
    
    while True:
        try:
            messages, status = await get_all_messages()
            
            if messages is None:
                error_count += 1
                print(f"\n⚠️ Fetch error #{error_count}: {status}")
                
                if error_count >= 5:
                    try:
                        await bot.send_message(
                            chat_id=ADMIN_ID,
                            text=f"⚠️ *ALERT*\n\nOTP fetch failing repeatedly!\nStatus: `{status}`\nErrors: {error_count}\n\nCheck: /status\nForce login: /relogin",
                            parse_mode="Markdown"
                        )
                    except:
                        pass
                    error_count = 0
            else:
                error_count = 0
                new_count = 0
                
                for msg in messages:
                    key = f"{msg['phone']}:{msg['datetime']}:{msg['message'][:40]}"
                    
                    if key not in seen_messages:
                        seen_messages.add(key)
                        await send_to_channel(bot, msg)
                        new_count += 1
                        await asyncio.sleep(0.5)
                
                if new_count > 0:
                    print(f"\n🔔 ===== {new_count} NEW OTPs FORWARDED! =====\n")
                else:
                    print(f"ℹ️ Checked — no new messages ({len(messages)} total)")
            
            if len(seen_messages) > 5000:
                seen_messages = set(list(seen_messages)[-2500:])
            
            sleep_time = POLL_INTERVAL + random.randint(-5, 8)
            sleep_time = max(15, sleep_time)
            print(f"😴 Sleeping {sleep_time}s...\n")
            await asyncio.sleep(sleep_time)
            
        except Exception as e:
            print(f"\n💥 Poll loop crash: {e}")
            import traceback
            traceback.print_exc()
            await asyncio.sleep(10)

# ============================================================
# BOT COMMANDS
# ============================================================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    name = update.effective_user.first_name
    
    if uid == ADMIN_ID:
        await update.message.reply_text(
            f"👋 Welcome *{name}* (Admin)!\n\n"
            f"🤖 *MySMS Portal OTP Forwarder*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"⚙️ *Admin Commands:*\n"
            f"/status - Check bot & login status\n"
            f"/testfetch - Test fetch OTPs from panel\n"
            f"/relogin - Force re-login to panel\n"
            f"/screenshot - Get browser screenshot\n"
            f"/clearseen - Clear seen messages cache\n\n"
            f"📢 Channel: `{CHANNEL_ID}`\n"
            f"⏱️ Poll: ~{POLL_INTERVAL}s\n"
            f"🔐 Panel: mysmsportal.com",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "🤖 *MySMS OTP Bot*\n\nOTPs from the panel are automatically forwarded to the channel.\n\nContact admin for access."
        )

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return
    
    msg = await update.message.reply_text("⏳ Checking status...")
    
    messages, status = await get_all_messages()
    
    login_status = "✅ Logged in & working" if messages is not None else f"❌ Issue: {status}"
    
    reply = (
        f"📊 *Bot Status*\n\n"
        f"Browser: ✅ Playwright Stealth\n"
        f"Panel: 🔗 mysmsportal.com\n"
        f"Login: {login_status}\n"
        f"Fetch Status: `{status}`\n"
        f"Total messages: {len(messages) if messages else 0}\n"
        f"Seen cache: {len(seen_messages)}\n"
        f"Credentials: ✅ Set"
    )
    
    if messages and len(messages) > 0:
        latest = messages[-1]
        code = extract_otp_code(latest['message'])
        reply += f"\n\n*Latest Message:*\n📱 `{latest['phone']}`\n🔢 `{code}`\n⏰ `{latest['datetime']}`"
    
    await msg.edit_text(reply, parse_mode="Markdown")

async def testfetch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return
    
    msg = await update.message.reply_text("⏳ Fetching from mysmsportal.com via browser...")
    
    messages, status = await get_all_messages()
    
    if messages is None:
        await msg.edit_text(f"❌ Fetch failed!\nStatus: `{status}`", parse_mode="Markdown")
        return
    
    if len(messages) == 0:
        await msg.edit_text(f"✅ Browser OK!\nStatus: `{status}`\nNo messages found right now.", parse_mode="Markdown")
        return
    
    reply = f"✅ *SUCCESS!*\nFetched {len(messages)} messages\n\n*Latest 3:*\n\n"
    
    for i, msg_data in enumerate(messages[-3:]):
        code = extract_otp_code(msg_data['message'])
        reply += f"--- {i+1} ---\n📱 `{msg_data['phone']}`\n✉️ `{msg_data['sender']}`\n🔢 `{code}`\n⏰ `{msg_data['datetime']}`\n\n"
    
    await msg.edit_text(reply, parse_mode="Markdown")

async def relogin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return
    
    msg = await update.message.reply_text("🔐 Forcing re-login to mysmsportal.com...")
    
    try:
        os.remove(cookies_file)
    except:
        pass
    
    await context.clear_cookies()
    
    success = await do_login()
    
    if success:
        await msg.edit_text("✅ *Re-login successful!*", parse_mode="Markdown")
    else:
        await msg.edit_text("❌ *Re-login failed!*\nCheck credentials or use /screenshot to debug.", parse_mode="Markdown")

async def screenshot_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return
    
    try:
        path = f"screenshot_{int(datetime.now().timestamp())}.png"
        await page.screenshot(path=path, full_page=True)
        
        with open(path, 'rb') as f:
            await update.message.reply_photo(photo=f, caption="📸 Current browser view")
        
        os.remove(path)
    except Exception as e:
        await update.message.reply_text(f"❌ Screenshot error: {e}")

async def clearseen_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global seen_messages
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return
    
    count = len(seen_messages)
    seen_messages = set()
    await update.message.reply_text(f"✅ Cleared {count} seen messages from cache!")

# ============================================================
# MAIN
# ============================================================
async def main():
    print("=" * 60)
    print("🤖 MySMS PORTAL OTP FORWARDER BOT")
    print("🎯 Target: mysmsportal.com")
    print("🛡️ Mode: Playwright Stealth (Anti-Bot Detection)")
    print("=" * 60)
    
    if not BOT_TOKEN:
        print("❌ ERROR: BOT_TOKEN not set in environment variables!")
        return
    
    print(f"\n📢 Telegram Channel: {CHANNEL_ID}")
    print(f"👑 Admin Telegram ID: {ADMIN_ID}")
    print(f"🔐 Panel User: {PANEL_USER}")
    print(f"⏱️ Poll Interval: ~{POLL_INTERVAL} seconds")
    print()
    
    print("🌐 Launching browser in stealth mode...")
    await setup_browser()
    print("✅ Browser ready!")
    
    print("\n🔍 Checking login status...")
    logged_in = await is_logged_in()
    
    if not logged_in:
        print("⚠️ Not logged in — attempting auto-login...")
        await do_login()
    else:
        print("✅ Already logged in! (cookies loaded)")
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("testfetch", testfetch_cmd))
    app.add_handler(CommandHandler("relogin", relogin_cmd))
    app.add_handler(CommandHandler("screenshot", screenshot_cmd))
    app.add_handler(CommandHandler("clearseen", clearseen_cmd))
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    
    print("\n✅ Telegram bot is running!")
    print("=" * 60)
    
    await poll_loop(app.bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped by user")
    except Exception as e:
        print(f"\n💥 FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
