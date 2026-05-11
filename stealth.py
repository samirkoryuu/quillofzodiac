import random
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from curl_cffi import requests as curl_requests

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/119.0.0.0 Safari/537.36",
]

async def get_stealth_page(browser):
    """Creates a new page with stealth settings."""
    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={'width': 1920, 'height': 1080},
        device_scale_factor=1,
    )
    page = await context.new_page()
    await Stealth().apply_stealth_async(page)
    
    # Extra stealth: mask webdriver and add random delays
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
    """)
    
    return page, context

async def scrape_with_stealth(url: str, wait_selector: str = "body"):
    """Main entry point for stealth scraping. Tries curl_cffi (TLS impersonation) first, then Playwright."""
    
    # 1. Try curl_cffi (extremely fast and good for Cloudflare)
    try:
        print(f"Trying curl_cffi for {url}...")
        # Use impersonate to mimic a real browser TLS fingerprint
        r = curl_requests.get(url, impersonate="chrome120", timeout=30)
        if r.status_code == 200 and "Just a moment..." not in r.text:
            print("✅ curl_cffi success!")
            return r.text
        print(f"curl_cffi blocked or failed (Status {r.status_code}). Falling back to Playwright...")
    except Exception as e:
        print(f"curl_cffi error: {e}. Falling back to Playwright...")

    # 2. Fallback to Playwright (Headless Browser)
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
        )
        page, context = await get_stealth_page(browser)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            
            # Wait for potential Cloudflare Turnstile
            if "Just a moment..." in await page.title():
                await asyncio.sleep(5) 
            
            if wait_selector:
                await page.wait_for_selector(wait_selector, timeout=30000)
            
            content = await page.content()
            return content
        finally:
            await browser.close()

import asyncio # Needed for sleep in the function
