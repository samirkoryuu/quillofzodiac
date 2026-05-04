import requests
from bs4 import BeautifulSoup
import re
import json

# Configuration
HUB_URL = "https://hiveslave-scraper.onrender.com"
API_KEY = "hiveslave_secret_key_20262025202420232022202120100000"
PROFILE_URL = "https://www.webnovel.com/profile/4503456957"

BOOK_HREF_RE = re.compile(r"/book/([^/?#]+?)_(\d+)")

def parse_profile(html):
    """Extracts books from the Webnovel profile. Tries JSON first, then HTML."""
    soup = BeautifulSoup(html, "lxml")
    
    # Method 1: Parse __NEXT_DATA__ JSON (Most reliable for Next.js sites)
    next_data_script = soup.find("script", id="__NEXT_DATA__")
    if next_data_script:
        try:
            data = json.loads(next_data_script.string)
            # Path: props -> initialState -> entities -> authorPageInfo -> [profile_id] -> bookListItems
            # Or: props -> pageProps -> initialState -> ...
            
            # Search for bookListItems in the nested dictionary
            def find_book_list(obj):
                if isinstance(obj, dict):
                    if "bookListItems" in obj:
                        return obj["bookListItems"]
                    for v in obj.values():
                        res = find_book_list(v)
                        if res: return res
                elif isinstance(obj, list):
                    for item in obj:
                        res = find_book_list(item)
                        if res: return res
                return None

            book_items = find_book_list(data)
            if book_items:
                books = []
                for item in book_items:
                    books.append({
                        "id": str(item.get("bookId")),
                        "title": item.get("bookName"),
                        "url": f"https://www.webnovel.com/book/{item.get('bookId')}"
                    })
                return books
        except Exception as e:
            print(f"JSON parsing failed, falling back to HTML: {e}")

    # Method 2: HTML Parsing (Fallback)
    works_pane = soup.select_one("#tabWorks")
    if not works_pane:
        # Check if it's the mobile version (it often is on these nodes)
        # Mobile version uses different classes
        works_pane = soup.select_one(".styles_profile_tab_content__JpjTP")
        
    if not works_pane:
        print("Could not find a works container in HTML.")
        return []

    books = []
    # Search for all links that look like books
    links = soup.find_all("a", href=BOOK_HREF_RE)
    seen_ids = set()
    
    for a in links:
        href = a.get("href", "")
        m = BOOK_HREF_RE.search(href)
        if not m: continue
        
        book_id = m.group(2)
        if book_id in seen_ids: continue
        seen_ids.add(book_id)
        
        # Try to find a title in the text or nearby
        title = a.get("title") or a.get_text(strip=True)
        if not title or len(title) < 2:
            # Look for a heading inside or near
            h = a.find(["h1", "h2", "h3", "h4", "p"])
            if h: title = h.get_text(strip=True)
            
        books.append({
            "id": book_id,
            "title": title or f"Book {book_id}",
            "url": "https://www.webnovel.com" + href if not href.startswith("http") else href
        })
    
    return books

def parse_book_details(html):
    """Extracts chapter count, views, and genre from a book detail page."""
    details = {
        "chapters": 0,
        "views": "0",
        "genre": "Unknown"
    }
    
    # 1. Chapter Count (Check JSON data first as it's most reliable)
    soup = BeautifulSoup(html, "lxml")
    next_data = soup.find("script", id="__NEXT_DATA__")
    if next_data:
        try:
            data = json.loads(next_data.string)
            # Find book detail in JSON
            def find_val(obj, key):
                if isinstance(obj, dict):
                    if key in obj: return obj[key]
                    for v in obj.values():
                        res = find_val(v, key)
                        if res: return res
                elif isinstance(obj, list):
                    for item in obj:
                        res = find_val(item, key)
                        if res: return res
                return None
            
            details["chapters"] = find_val(data, "totalChapterNum") or find_val(data, "chapterNum") or 0
            details["genre"] = find_val(data, "categoryName") or "Unknown"
        except:
            pass

    # 2. Fallback to Regex/Soup for Views and Chapters
    if not details["chapters"]:
        m = re.search(r'>([\d,]+)\s+Chapters?<', html)
        if m: details["chapters"] = int(m.group(1).replace(",", ""))

    # Views (Usually looks like "1.2M Views" or "500 Views")
    mv = re.search(r'>([\d.,KMB]+)\s+Views?<', html)
    if mv:
        details["views"] = mv.group(1)
        
    # Genre Fallback
    if details["genre"] == "Unknown":
        det = soup.select_one(".det-hd-detail")
        if det:
            txt = det.get_text(" ", strip=True)
            mg = re.match(r"([A-Za-z][A-Za-z &/]+?)\s+\d", txt)
            if mg: details["genre"] = mg.group(1).strip()

    return details

def fetch_via_hub():
    print(f"Requesting mobile profile scrape for: {PROFILE_URL}")
    headers = {"X-API-Key": API_KEY}
    payload = {
        "url": PROFILE_URL,
        "wait_selector": ".styles_profile_tab_content__JpjTP"
    }
    
    try:
        response = requests.post(f"{HUB_URL}/scrape", json=payload, headers=headers, timeout=60)
        if response.status_code != 200:
            print(f"Profile Scrape Failed: {response.status_code}")
            return
            
        html = response.json().get("content", "")
        books = parse_profile(html)
        
        if not books:
            print("No books found on profile.")
            return

        print(f"Found {len(books)} books. Starting Deep Scrape for details...")
        print("-" * 60)
        
        enriched_books = []
        for i, book in enumerate(books, 1):
            print(f"[{i}/{len(books)}] Fetching details for: {book['title']}...")
            
            # Request individual book page
            book_payload = {"url": book["url"], "wait_selector": "h1"}
            try:
                book_resp = requests.post(f"{HUB_URL}/scrape", json=book_payload, headers=headers, timeout=60)
                if book_resp.status_code == 200:
                    book_html = book_resp.json().get("content", "")
                    details = parse_book_details(book_html)
                    book.update(details)
                else:
                    print(f"   ! Failed to fetch book page (Status {book_resp.status_code})")
            except Exception as e:
                print(f"   ! Error fetching book details: {e}")
            
            enriched_books.append(book)

        # Final Report
        print("\n" + "="*60)
        print(f"FINAL REPORT: {PROFILE_URL}")
        print("="*60)
        for i, b in enumerate(enriched_books, 1):
            try:
                print(f"{i}. {b['title']}")
            except:
                print(f"{i}. (Title Encoding Error)")
            print(f"   ID: {b['id']} | Genre: {b['genre']}")
            print(f"   Stats: {b.get('chapters', 0)} Chapters | {b.get('views', '0')} Views")
            print(f"   Link: {b['url']}")
            print("-" * 30)

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    fetch_via_hub()
