from fastapi import FastAPI, HTTPException, Depends, Header, Request
from pydantic import BaseModel
import uuid
import asyncio
import time
import os
import json
import re
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import Dict, Optional

app = FastAPI(title="HiveSlave Scraper Hub")

# Configuration
API_KEY = "hiveslave_secret_key_20262025202420232022202120100000"

async def verify_api_key(x_api_key: str = Header(None)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API Key")

# Global State
TASKS_FILE = "tasks_db.json"
clients: Dict[str, Dict] = {}
task_queue = asyncio.Queue()  
task_status: Dict[str, Dict] = {}

def load_tasks():
    global task_status
    if os.path.exists(TASKS_FILE):
        try:
            with open(TASKS_FILE, "r") as f:
                task_status = json.load(f)
        except:
            task_status = {}

def save_tasks():
    try:
        with open(TASKS_FILE, "w") as f:
            json.dump(task_status, f)
    except:
        pass

load_tasks()
response_futures: Dict[str, asyncio.Future] = {} 

class ScrapeRequest(BaseModel):
    url: str
    wait_selector: Optional[str] = "body"
    user_id: Optional[int] = None # To track who to notify

@app.get("/")
async def root():
    active_count = len([c for c in clients.values() if time.time() - c['last_seen'] < 60])
    return {
        "status": "Hub is running",
        "active_clients": active_count,
        "queue_depth": task_queue.qsize(),
        "tasks_tracked": len(task_status)
    }

@app.get("/latest-client")
async def get_latest_client():
    """Serves the latest mobile_client.py code for OTA updates."""
    try:
        # Assuming mobile_client.py is in the same directory as main.py
        with open("mobile_client.py", "r") as f:
            return {"code": f.read()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- ENDPOINTS FOR THE BOT ---

@app.post("/scrape")
async def hub_scrape(req: ScrapeRequest, _=Depends(verify_api_key)):
    """The bot calls this to queue a task."""
    task_id = str(uuid.uuid4())
    now = time.time()
    
    task_info = {
        "id": task_id,
        "url": req.url,
        "wait_selector": req.wait_selector,
        "created_at": now,
        "status": "pending",
        "user_id": req.user_id,
        "result": None
    }
    
    task_status[task_id] = task_info
    await task_queue.put(task_info)
    save_tasks()
    
    return {"status": "queued", "task_id": task_id}

@app.get("/task-status/{task_id}")
async def check_task(task_id: str, _=Depends(verify_api_key)):
    """Bot polls this to check if work is done."""
    if task_id not in task_status:
        raise HTTPException(status_code=404, detail="Task not found")
        
    task = task_status[task_id]
    
    # Check for 23-hour expiration (23 * 3600 = 82800 seconds)
    if task["status"] == "pending" and (time.time() - task["created_at"]) > 82800:
        task["status"] = "expired"
        
    return task

# --- DATABASE SETUP ---
DB_URL = os.environ.get("COCKROACH_DATABASE_URL")

def get_db_conn():
    try:
        return psycopg2.connect(DB_URL)
    except Exception as e:
        print(f"DB Connection Error: {e}")
        return None

def parse_and_save_data(html, profile_url):
    """Parses HTML and hardening it to CockroachDB."""
    # Extract profile ID from URL
    profile_id = profile_url.split("/")[-1].split("?")[0]
    
    # Advanced Parsing
    match = re.search(r'id=\"__NEXT_DATA__\".*?>(.*?)</script>', html)
    if not match:
        match = re.search(r'g_data.profile\s*=\s*({.*?});', html)
        
    if not match: return False

    try:
        data = json.loads(match.group(1))
        def find_books(obj):
            if isinstance(obj, dict):
                if 'bookListItems' in obj: return obj['bookListItems']
                for v in obj.values():
                    res = find_books(v)
                    if res: return res
            elif isinstance(obj, list):
                for item in obj:
                    res = find_books(item)
                    if res: return res
            return None
        
        books = find_books(data)
        if not books: return False

        # Save to DB
        conn = get_db_conn()
        if not conn: return False
        cur = conn.cursor()
        
        now = int(time.time())
        
        # 1. Update Writer Timestamp
        cur.execute(
            "UPDATE wn_writers SET last_discovered_at = %s WHERE profile_id = %s",
            (now, profile_id)
        )
        
        # 2. Upsert Books
        for b in books:
            book_id = str(b.get('bookId'))
            cur.execute("""
                INSERT INTO wn_books (book_id, profile_id, title, chapter_count, genre, last_change_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (book_id, profile_id) DO UPDATE SET
                title = EXCLUDED.title,
                chapter_count = EXCLUDED.chapter_count,
                genre = EXCLUDED.genre,
                last_change_at = CASE 
                    WHEN wn_books.chapter_count <> EXCLUDED.chapter_count THEN EXCLUDED.last_change_at 
                    ELSE wn_books.last_change_at 
                END
            """, (book_id, profile_id, b.get('bookName'), b.get('chapterNum'), b.get('categoryName'), now))
            
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"Parsing/DB Error: {e}")
        return False

# --- ENDPOINTS FOR THE PHONE ---

@app.get("/register")
async def register_legacy():
    """Allows older mobile apps to connect without a Client ID."""
    return await register_client("legacy_phone_node")

@app.get("/register/{client_id}")
async def register_client(client_id: str):
    """Phones pick up work from the global pool."""
    if client_id not in clients:
        clients[client_id] = {"last_seen": 0}
    clients[client_id]['last_seen'] = time.time()
    
    if not task_queue.empty():
        try:
            task = await task_queue.get()
            # If task already expired while in queue, skip it
            if (time.time() - task["created_at"]) > 82800:
                task_status[task["id"]]["status"] = "expired"
                return {"task": None}
            return {"task": task}
        except:
            pass
    return {"task": None}

@app.post("/respond/{client_id}/{request_id}")
async def client_respond(client_id: str, request_id: str, response: dict):
    """Phones return results here."""
    if request_id in task_status:
        task_status[request_id]["status"] = "completed"
        task_status[request_id]["completed_at"] = time.time()
        task_status[request_id]["result"] = response
        
        # HARDENING TO DB
        html = response.get("content", "")
        url = task_status[request_id].get("url", "")
        if html and url:
            task_status[request_id]["saved_to_db"] = parse_and_save_data(html, url)
            
        save_tasks()
        return {"status": "ok", "ping": {
            "requested_at": task_status[request_id]["created_at"],
            "completed_at": task_status[request_id]["completed_at"]
        }}
    return {"status": "expired_or_not_found"}

if __name__ == "__main__":
    import uvicorn
    import os
    uvicorn.run(app, host="0.0.0.0", port=9991)
