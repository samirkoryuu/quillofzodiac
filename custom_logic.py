import re
from bs4 import BeautifulSoup

def parse_webnovel_profile(html: str):
    """
    Parses a Webnovel user profile to find all 'Original Works'.
    Extracts: title, url, chapters, words, views, and genre.
    """
    soup = BeautifulSoup(html, 'html.parser')
    results = []
    
    # 1. Find the works list
    # Webnovel usually puts works in a list with specific classes
    work_items = (
        soup.select('ul.work-list li') or 
        soup.select('.det-tab-pane li') or 
        soup.select('._book-list li') or 
        soup.select('.m-stories > li') or 
        soup.select('.j_workWrap li') or
        soup.select('.g_book_item')
    )
    
    for item in work_items:
        try:
            # Try finding title in h2, h3, h4 or .w-title
            title_tag = item.find(["h2", "h3", "h4"]) or item.select_one('.w-title')
            link_tag = item.select_one('a')
            if not link_tag: continue
            
            title = title_tag.get_text(strip=True) if title_tag else link_tag.get_text(strip=True)
            href = link_tag.get('href', '')
            # Clean href: ensures it's a full URL
            if href.startswith('/'):
                href = f"https://www.webnovel.com{href}"
            
            # Extract stats (Chapters, Words, Views)
            stats_text = item.get_text(separator=' ', strip=True)
            
            # Use Regex to extract numbers
            chapters = 0
            words = 0
            views = "0"
            
            ch_match = re.search(r'(\d+)\s*Chapters', stats_text, re.I)
            if ch_match: chapters = int(ch_match.group(1))
            
            wd_match = re.search(r'([\d\.]+k?)\s*Words', stats_text, re.I)
            if wd_match: words = _parse_stat_number(wd_match.group(1))
            
            vw_match = re.search(r'([\d\.]+k?|[\d\.]+m?)\s*Views', stats_text, re.I)
            if vw_match: views = vw_match.group(1)
            
            # Genre
            genre_tag = item.select_one('.w-genre') or item.select_one('.genre')
            genre = genre_tag.get_text(strip=True) if genre_tag else "Unknown"
            
            results.append({
                "title": title,
                "url": href,
                "chapters": chapters,
                "words": words,
                "views": views,
                "genre": genre
            })
        except Exception:
            continue
            
    return results

def _parse_stat_number(s: str) -> int:
    """Converts 12.5k -> 12500 etc."""
    s = s.lower().replace(',', '')
    if 'k' in s:
        return int(float(s.replace('k', '')) * 1000)
    if 'm' in s:
        return int(float(s.replace('m', '')) * 1000000)
    try:
        return int(float(s))
    except:
        return 0

async def process_custom_action(action: str, url: str, params: dict, html: str = None):
    """Routing for custom scraping logic."""
    if action == "parse_profile":
        if not html: return {"error": "HTML required"}
        return {"works": parse_webnovel_profile(html)}
    
    return {"error": f"Unknown action: {action}"}
