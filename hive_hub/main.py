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

# Dual-DB Routing
try:
    from sequencer import get_integer_id, which_db
    from router import get_db_conn as route_db_conn, get_both_connections
    from bulk_push import bulk_push_worker
    DUAL_DB_ENABLED = True
    print("✅ Dual-DB routing modules loaded.")
except ImportError as e:
    DUAL_DB_ENABLED = False
    print(f"⚠️ Dual-DB modules not found, single-DB mode: {e}")

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
    """Hourly: Moves AppSuba recent_findings → correct CockroachDB via router."""
    while True:
        try:
            if not supabase:
                print("[SWEEPER] Skipping: Supabase client not initialized.")
                await asyncio.sleep(600)
                continue

            print("[SWEEPER] Starting hourly archive cycle...")
            response = supabase.table("recent_findings").select("*").execute()
            findings = response.data or []

            if not findings:
                print("[SWEEPER] No new findings to archive.")
            else:
                # Group by DB based on writer's integer_id parity
                db1_findings = []
                db2_findings = []

                for f in findings:
                    pid = f.get('profile_id', 'unknown')
                    int_id = get_integer_id(pid, 'writer') if DUAL_DB_ENABLED else 1
                    if int_id % 2 == 1:
                        db1_findings.append(f)
                    else:
                        db2_findings.append(f)

                async def _archive_batch(batch, integer_id_sample, db_label):
                    if not batch: return 0
                    conn = get_db_conn(integer_id_sample)
                    if not conn: return 0
                    cur = conn.cursor()
                    written = 0
                    for f in batch:
                        try:
                            cur.execute("""
                                INSERT INTO intel_books
                                    (book_id, profile_id, title, chapter_count,
                                     genre, collections, views, power_ranking, last_change_at)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                                ON CONFLICT (book_id, profile_id) DO UPDATE SET
                                    chapter_count  = EXCLUDED.chapter_count,
                                    collections    = EXCLUDED.collections,
                                    views          = EXCLUDED.views,
                                    power_ranking  = EXCLUDED.power_ranking,
                                    last_change_at = EXCLUDED.last_change_at
                            """, (
                                f['book_id'], f['profile_id'], f['book'],
                                f['chapters'], f['genre'], f['collections'],
                                f['views'], f['power_ranking'], int(time.time())
                            ))
                            written += 1
                        except Exception as e:
                            print(f"[SWEEPER] Row error: {e}")
                    conn.commit()
                    cur.close()
                    conn.close()
                    print(f"[SWEEPER] ✅ {db_label}: {written} records archived.")
                    return written

                await _archive_batch(db1_findings, 1, "MainCock (DB1)")
                await _archive_batch(db2_findings, 2, "MainButt (DB2)")

                # Wait 5 minutes then verify + delete
                print(f"[SWEEPER] Waiting 5 minutes for verification...")
                await asyncio.sleep(300)

                # Delete confirmed records from AppSuba
                ids = [f['id'] for f in findings]
                supabase.table("recent_findings").delete().in_("id", ids).execute()
                print(f"[SWEEPER] 🗑️ Cleared {len(ids)} records from AppSuba. Hehe, delete!")

        except Exception as e:
            print(f"[SWEEPER] ❌ Cycle error: {e}")

        await asyncio.sleep(3600)  # Run every 1 hour

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(archive_sweeper())
    if DUAL_DB_ENABLED:
        asyncio.create_task(bulk_push_worker())
        print("[STARTUP] ✅ Bulk Push Worker started.")
    print("[STARTUP] ✅ Archive Sweeper started.")

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
# MainCock = DB1 (odd IDs) | MainButt = DB2 (even IDs)
DB_URL  = os.environ.get("COCKROACH_DATABASE_URL",  "")   # MainCock
DB_URL2 = os.environ.get("COCKROACH_DATABASE_URL_2", "")   # MainButt

def get_db_conn(integer_id: int = 1):
    """Returns DB connection routed by integer_id parity."""
    if DUAL_DB_ENABLED:
        return route_db_conn(integer_id)
    # Fallback: single DB
    try:
        return psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    except Exception as e:
        print(f"DB Connection Error: {e}")
        return None

def parse_and_save_data(html, profile_url, task_id, created_at, user_id=None):
    """Parses HTML, relays to Supabase, and routes to correct CockroachDB."""
    match = re.search(r'id=\"__NEXT_DATA__\".*?>(.*?)</script>', html, re.DOTALL)
    if not match: return False

    try:
        data = json.loads(match.group(1))
        user_info  = data.get('props', {}).get('pageProps', {}).get('data', {}).get('userInfo', {})
        books_data = data.get('props', {}).get('pageProps', {}).get('data', {}).get('bookList', [])

        penname    = user_info.get('userName', 'Unknown')
        profile_id = str(user_info.get('userId', 'Unknown'))
        country    = user_info.get('areaName', 'Unknown')

        # Get this writer's integer_id for DB routing
        integer_id = get_integer_id(profile_id, 'writer') if DUAL_DB_ENABLED else 1
        target_db  = which_db(profile_id, 'writer') if DUAL_DB_ENABLED else 'MainCock (DB1)'
        print(f"[HUB] Writer '{penname}' (ID:{profile_id}) → {target_db}")

        findings = []
        for b in books_data:
            power_rank    = b.get('powerRank', 999)
            filtered_rank = power_rank if power_rank <= 200 else None

            finding = {
                "penname":       penname,
                "country":       country,
                "book":          b.get('bookName', 'Untitled'),
                "book_id":       str(b.get('bookId', '')),
                "profile_id":    profile_id,
                "genre":         b.get('categoryName', 'Unknown'),
                "collections":   b.get('collectNum', 0),
                "chapters":      b.get('chapterNum', 0),
                "views":         str(b.get('visitNum', '0')),
                "power_ranking": filtered_rank,
                "task_id":       task_id
            }
            findings.append(finding)

            # 1. Fast relay to AppSuba (recent_findings)
            if supabase:
                supabase.table("recent_findings").insert(finding).execute()

        # 2. Award 10 Cloud Marks via AppSuba
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
