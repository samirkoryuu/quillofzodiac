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
from typing import Dict, Optional, List
from supabase import create_client, Client

# Supabase Configuration (Fail-Safe Initialization)
SUPABASE_URL = "https://fnmjjvzzdiipqtqkvaki.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY") or "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..." # Fallback
supabase: Optional[Client] = None

try:
    if SUPABASE_URL and SUPABASE_KEY and "..." not in SUPABASE_KEY:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("✅ Supabase Client Initialized.")
    else:
        print("⚠️ Supabase Client skipped: Missing or placeholder key.")
except Exception as e:
    print(f"❌ Supabase Init Error: {e}")

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

async def archive_sweeper():
    """Background task: Moves Supabase findings to CockroachDB every hour."""
    while True:
        try:
            # 1. Fetch from Supabase
            if not supabase:
                print("[SWEEPER] Skipping: Supabase client not initialized.")
                await asyncio.sleep(600)
                continue
                
            response = supabase.table("recent_findings").select("*").execute()
            findings = response.data
            
            if findings:
                conn = get_db_conn()
                if conn:
                    cur = conn.cursor()
                    for f in findings:
                        # Archive to wn_books (Handling Composite Key: book_id + profile_id)
                        cur.execute("""
                            INSERT INTO wn_books (book_id, profile_id, title, chapter_count, genre, collections, views, power_ranking, last_change_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (book_id, profile_id) DO UPDATE SET
                            chapter_count = EXCLUDED.chapter_count,
                            collections = EXCLUDED.collections,
                            views = EXCLUDED.views,
                            power_ranking = EXCLUDED.power_ranking,
                            last_change_at = EXCLUDED.last_change_at
                        """, (
                            f['book_id'], f['profile_id'], f['book'], f['chapters'], f['genre'], 
                            f['collections'], f['views'], f['power_ranking'], 
                            int(time.time())
                        ))
                    conn.commit()
                    cur.close()
                    conn.close()
                    print(f"[SWEEPER] Successfully archived {len(findings)} records.")
                    
                    # 2. Wait 5 minutes before cleanup (as requested)
                    print("[SWEEPER] Waiting 5 minutes for verification...")
                    await asyncio.sleep(300)
                    
                    # 3. "Hehe, delete!" - Purge Supabase buffer
                    for f in findings:
                        supabase.table("recent_findings").delete().eq("id", f['id']).execute()
                    print("[SWEEPER] Cleanup complete.")
            else:
                print("[SWEEPER] No new findings to archive.")
                
        except Exception as e:
            print(f"[SWEEPER] Error during cycle: {e}")
            
        # Run every 1 hour
        await asyncio.sleep(3600)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(archive_sweeper())

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

def parse_and_save_data(html, profile_url, task_id, created_at, user_id=None):
    """Parses HTML and relays to Supabase recent_findings."""
    match = re.search(r'id=\"__NEXT_DATA__\".*?>(.*?)</script>', html)
    if not match: return False

    try:
        data = json.loads(match.group(1))
        # Deep extraction for Webnovel profile data
        user_info = data.get('props', {}).get('pageProps', {}).get('data', {}).get('userInfo', {})
        books_data = data.get('props', {}).get('pageProps', {}).get('data', {}).get('bookList', [])
        
        penname = user_info.get('userName', 'Unknown')
        profile_id = str(user_info.get('userId', 'Unknown'))
        country = user_info.get('areaName', 'Unknown')
        
        findings = []
        for b in books_data:
            power_rank = b.get('powerRank', 999)
            # POWER RANKING FILTER: Only track if in Top 200
            filtered_rank = power_rank if power_rank <= 200 else None
            
            finding = {
                "penname": penname,
                "country": country,
                "book": b.get('bookName', 'Untitled'),
                "book_id": str(b.get('bookId', '')),
                "profile_id": profile_id,
                "genre": b.get('categoryName', 'Unknown'),
                "collections": b.get('collectNum', 0),
                "chapters": b.get('chapterNum', 0),
                "views": str(b.get('visitNum', '0')),
                "power_ranking": filtered_rank,
                "task_id": task_id
            }
            findings.append(finding)
            
            # RELAY TO SUPABASE (Recent Findings)
            if supabase:
                supabase.table("recent_findings").insert(finding).execute()

        # REWARD: 10 Cloud Marks
        if user_id and supabase:
            supabase.rpc("award_cloud_marks", {"u_id": user_id, "amount": 10}).execute()
            
        return True
    except Exception as e:
        print(f"Relay Error: {e}")
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
        
        # RELAY TO SUPABASE
        html = response.get("content", "")
        url = task_status[request_id].get("url", "")
        created_at = task_status[request_id].get("created_at", 0)
        user_id = task_status[request_id].get("user_id") # Award to this user
        
        if html and url:
            task_status[request_id]["saved_to_relay"] = parse_and_save_data(html, url, request_id, created_at, user_id)
            
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
