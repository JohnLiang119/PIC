import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto("https://book.yunzhan365.com/wxfu/rmxs/mobile/index.html")
        await asyncio.sleep(5)
        
        # Method 1: Check inputs
        inputs = await page.evaluate('''() => {
            return Array.from(document.querySelectorAll('input')).map(i => i.value);
        }''')
        print(f"Inputs: {inputs}")
        
        # Method 2: Check all text for slash pattern
        text = await page.evaluate('() => document.body.innerText')
        import re
        matches = re.findall(r'(\d+(?:-\d+)?)\s*/\s*(\d+)', text)
        print(f"Matches in innerText: {matches}")
        
        # Method 3: specific classes
        html = await page.content()
        with open("scratch.html", "w", encoding="utf-8") as f:
            f.write(html)
            
        await browser.close()

asyncio.run(main())
