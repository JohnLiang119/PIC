import asyncio
import os
import sys
import argparse
from urllib.parse import urlparse
from playwright.async_api import async_playwright, Response

# 強制 stdout 使用 utf-8，避免 Windows 終端機編碼問題
sys.stdout.reconfigure(encoding='utf-8')


async def get_page_count(page):
    """嘗試從 config 或全域變數取得書本總頁數"""
    try:
        count = await page.evaluate('''() => {
            if (typeof htmlConfig !== "undefined" && htmlConfig.meta && htmlConfig.meta.pageCount) {
                return htmlConfig.meta.pageCount;
            }
            return 0;
        }''')
        return count
    except Exception:
        return 0


async def is_pdf_book(page):
    """偵測是否為 PDF 型態的書 (使用 pdf.js 渲染)"""
    try:
        result = await page.evaluate('''() => {
            return typeof pdfjsLib !== "undefined" || typeof pdfPages !== "undefined";
        }''')
        return result
    except Exception:
        return False


async def goto_page_js(page, page_index):
    """透過 JavaScript 呼叫書本的 gotoPage API 跳到指定頁"""
    try:
        await page.evaluate('''(idx) => {
            // 嘗試各種常見的翻頁 API
            if (typeof SlideForm !== "undefined" && window.phoneSlider) {
                window.phoneSlider.gotoPage(idx);
                return;
            }
            // 嘗試觸發自訂事件
            if (typeof BookEvent !== "undefined") {
                var evt = new CustomEvent("gotoPage", {detail: {page: idx}});
                document.dispatchEvent(evt);
            }
        }''', page_index)
    except Exception:
        pass


async def flip_next_via_touch(page):
    """點擊螢幕右側來翻到下一頁 (避免滑動太長導致一次跳太多頁)"""
    viewport = page.viewport_size
    if not viewport:
        return
    
    # 點擊螢幕最右側的垂直置中位置
    tap_x = viewport["width"] - 20
    tap_y = viewport["height"] / 2
    
    await page.mouse.click(tap_x, tap_y)


async def capture_page_screenshot(page, output_dir, page_num):
    """擷取當前頁面的截圖 (包含左右雙頁的所有畫布)"""
    filepath = os.path.join(output_dir, f"{page_num}.png")
    
    # 嘗試找出所有書頁 canvas 的聯集邊界框 (Bounding Box)
    try:
        clip_rect = await page.evaluate('''() => {
            const canvases = document.querySelectorAll("canvas");
            let minX = Infinity, minY = Infinity, maxX = 0, maxY = 0;
            let found = false;
            for (const c of canvases) {
                // 過濾掉太小的畫布 (例如 UI icon)
                if (c.width > 100 && c.height > 100) {
                    const rect = c.getBoundingClientRect();
                    // 確保元素可見
                    if (rect.width > 0 && rect.height > 0) {
                        minX = Math.min(minX, rect.left);
                        minY = Math.min(minY, rect.top);
                        maxX = Math.max(maxX, rect.right);
                        maxY = Math.max(maxY, rect.bottom);
                        found = true;
                    }
                }
            }
            if (found) {
                return { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
            }
            return null;
        }''')
        
        if clip_rect:
            # 針對計算出的總範圍進行裁切截圖
            await page.screenshot(path=filepath, clip=clip_rect)
            return True
    except Exception:
        pass
    
    # fallback: 截整個頁面
    await page.screenshot(path=filepath)
    return True


import shutil

async def download_book(url, output_dir, max_pages=0):
    # 下載前先清空目標資料夾
    if os.path.exists(output_dir):
        print(f"[{url}] 清空舊資料夾: {output_dir}")
        shutil.rmtree(output_dir)
        
    os.makedirs(output_dir, exist_ok=True)
    print(f"[{url}] 開始處理，儲存至: {output_dir}")
    
    # 用來記錄已下載的圖片，防止重複
    downloaded_urls = set()
    img_counter = {"count": 1}
    
    async def handle_response(response: Response):
        """攔截圖片型書本的頁面圖片"""
        if response.request.resource_type == "image":
            resp_url = response.url
            # 過濾: 只要 files/large 或 files/mobile 下的圖
            if "/files/" in resp_url and any(x in resp_url for x in ["large", "mobile"]):
                # 直接排除 WebP 格式的碎片圖
                if ".webp" in resp_url.lower():
                    return
                # 排除介面圖標和背景
                if "html5_templates" in resp_url or "common" in resp_url or "icon" in resp_url.lower():
                    return
                if "backGroundImgURL" in resp_url or "_brand" in resp_url:
                    return
                
                if resp_url not in downloaded_urls:
                    downloaded_urls.add(resp_url)
                    try:
                        body = await response.body()
                        if body:
                            ext = resp_url.split('.')[-1].split('?')[0]
                            if len(ext) > 4:
                                ext = "jpg"
                            
                            filename = f"{img_counter['count']}.{ext}"
                            filepath = os.path.join(output_dir, filename)
                            with open(filepath, "wb") as f:
                                f.write(body)
                            print(f"[{url}] 已下載圖片: {filename}")
                            img_counter['count'] += 1
                    except Exception as e:
                        print(f"[{url}] 無法讀取圖片: {e}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        # 啟用觸控支援，模擬手機裝置
        context = await browser.new_context(has_touch=True)
        page = await context.new_page()
        
        page.on("response", handle_response)
        
        print(f"[{url}] 載入網頁中...")
        await page.goto(url, wait_until="networkidle")
        
        # 處理全螢幕提示覆蓋層
        try:
            overlay_btn = await page.wait_for_selector('text="点击查看全屏"', timeout=8000)
            if overlay_btn:
                await overlay_btn.click()
                print(f"[{url}] 已點擊全螢幕提示")
                await asyncio.sleep(2)
        except Exception:
            print(f"[{url}] 未偵測到全螢幕提示，直接繼續。")
        
        # 取得書本資訊
        total_pages = await get_page_count(page)
        is_pdf = await is_pdf_book(page)
        print(f"[{url}] 書本資訊: 總頁數={total_pages}, PDF型態={is_pdf}")
        
        if is_pdf and total_pages > 0:
            # ===== PDF 型態: 使用截圖模式 =====
            print(f"[{url}] 偵測到 PDF 型態書本，切換為截圖模式...")
            
            # 等待第一頁渲染完成
            await asyncio.sleep(5)
            
            screenshot_num = 1
            last_file_size = 0
            consecutive_same = 0
            
            while consecutive_same < 3:
                # 翻頁後，先等待 1.5 秒讓翻頁動畫完全結束並渲染
                await asyncio.sleep(1.5)
                
                # 擷取當前畫面顯示的實際頁碼 (例如 "2-3")
                try:
                    current_page_str = await page.evaluate('''() => {
                        // 雲展網的手機版將頁數放在 <input> 標籤的 value 中，例如 "2-3/130"
                        const inputs = Array.from(document.querySelectorAll('input'));
                        for (const i of inputs) {
                            const match = (i.value || "").match(/(\\d+(-\\d+)?)\\s*\\/\\s*\\d+/);
                            if (match) return match[1];
                        }
                        
                        // 備用: 檢查純文字
                        const els = document.querySelectorAll('[class*="page"], [class*="slider"], [class*="index"], .txt');
                        for (const el of els) {
                            const text = el.textContent.trim();
                            const match = text.match(/(\\d+(-\\d+)?)\\s*\\/\\s*\\d+/);
                            if (match) return match[1];
                        }
                        return "";
                    }''')
                except Exception:
                    current_page_str = ""
                
                filename_base = current_page_str if current_page_str else str(screenshot_num)
                
                # 擷取截圖
                success = await capture_page_screenshot(page, output_dir, filename_base)
                if success:
                    # 用檔案大小來判斷是否跟上一張一樣（到底了）
                    filepath = os.path.join(output_dir, f"{filename_base}.png")
                    current_size = os.path.getsize(filepath)
                    
                    if current_size == last_file_size:
                        consecutive_same += 1
                        # 刪除重複的截圖
                        os.remove(filepath)
                        print(f"[{url}] 偵測到重複頁面 ({consecutive_same}/3)，跳過")
                    else:
                        consecutive_same = 0
                        last_file_size = current_size
                        print(f"[{url}] 已擷取第 {filename_base} 頁 (截圖序號: {screenshot_num})")
                        screenshot_num += 1
                        
                        if max_pages > 0 and screenshot_num > max_pages:
                            print(f"[{url}] 已達到最大截圖數限制 ({max_pages})，停止擷取。")
                            break
                            
                        # 如果當前頁碼包含最後一頁，則自動結束
                        if total_pages > 0 and str(total_pages) in filename_base.split('-'):
                            print(f"[{url}] 已抓取到最後一頁 ({total_pages})，自動停止。")
                            break
                
                # 截圖後再等待 1.5 秒 (總共停頓 3 秒)
                await asyncio.sleep(1.5)
                
                # 翻到下一頁 (僅使用滑鼠點擊，移除鍵盤避免二次翻頁)
                await flip_next_via_touch(page)
            
            print(f"[{url}] 截圖完成，共擷取 {screenshot_num - 1} 頁。")
        else:
            # ===== 圖片型態: 使用 network 攔截模式 =====
            print(f"[{url}] 使用圖片攔截模式，開始自動翻頁...")
            
            # 點擊空白處確保焦點
            await page.mouse.click(10, 10)
            await asyncio.sleep(1)
            
            consecutive_no_new = 0
            last_count = img_counter["count"]
            
            while consecutive_no_new < 3:
                await page.keyboard.press("ArrowRight")
                await asyncio.sleep(2)
                
                current_count = img_counter["count"]
                if current_count == last_count:
                    consecutive_no_new += 1
                else:
                    consecutive_no_new = 0
                    last_count = current_count
            
            print(f"[{url}] 翻頁結束，共下載 {img_counter['count'] - 1} 張圖片。")
        
        await browser.close()


async def main():
    parser = argparse.ArgumentParser(description="下載雲展網 (Yunzhan365) 電子書圖片")
    parser.add_argument("urls", nargs="+", help="雲展網電子書的 URL，可傳入多個")
    parser.add_argument("--outdir", default="downloads", help="下載的目標主資料夾")
    parser.add_argument("--max-pages", type=int, default=0, help="最多下載的頁數 (測試用，0 為無限制)")
    args = parser.parse_args()
    
    for url in args.urls:
        # 使用 URL 的一部分作為資料夾名稱
        parsed_url = urlparse(url)
        path_parts = [pt for pt in parsed_url.path.split('/') if pt and pt not in ('mobile', 'index.html')]
        book_id = "_".join(path_parts) if path_parts else "book"
        
        output_dir = os.path.join(args.outdir, book_id)
        await download_book(url, output_dir, args.max_pages)


if __name__ == "__main__":
    asyncio.run(main())
