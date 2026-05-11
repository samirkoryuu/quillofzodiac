from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
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
    from bulk_push import bulk_push_worker, perform_sync
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
client_waiters: Dict[str, asyncio.Event] = {} # Events to wake up long-polling phones

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

                # USER REQUEST: Wait exactly 1 hour after successful push before deleting
                print(f"[SWEEPER] Push successful. Waiting 1 hour for safety before purging staging area...")
                await asyncio.sleep(3600) 

                # Delete confirmed records from AppSuba
                ids = [f['id'] for f in findings]
                supabase.table("recent_findings").delete().in_("id", ids).execute()
                print(f"[SWEEPER] 🗑️ Purge complete. Cleared {len(ids)} records from staging.")

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
    
    # 1. Save to Supabase (Source of Truth)
    if supabase:
        try:
            supabase.table("task_queue").insert({
                "id": task_id,
                "url": req.url,
                "status": "pending",
                "assigned_to": None  # Global pool
            }).execute()
        except Exception as e:
            print(f"[HUB] Supabase Task Save Error: {e}")

    # 2. Local fallback/tracking
    task_info = {
        "id": task_id,
        "url": req.url,
        "wait_selector": req.wait_selector,
        "created_at": time.time(),
        "status": "pending",
        "user_id": req.user_id,
        "result": None
    }
    task_status[task_id] = task_info
    await task_queue.put(task_info)
    save_tasks()
    
    # WAKE UP ALL WAITING CLIENTS
    for event in client_waiters.values():
        event.set()
    
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

@app.post("/set-main-book")
async def set_main_book(req: Dict, _=Depends(verify_api_key)):
    """Manually sets the 'Main' book for a writer."""
    user_id = req.get("user_id")
    book_id = req.get("book_id")
    if not user_id or not book_id:
        raise HTTPException(status_code=400, detail="Missing user_id or book_id")
    
    if supabase:
        try:
            supabase.table("writer_stats").upsert({
                "user_id": user_id,
                "main_book_id": str(book_id)
            }).execute()
            return {"status": "success", "message": f"Main book set to {book_id}"}
        except Exception as e:
            return {"status": "error", "detail": str(e)}
    return {"status": "error", "detail": "Supabase not connected"}

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

        # --- CATEGORIZATION LOGIC ---
        total_chapters = 0
        total_books = len(books_data)
        
        # 1. Fetch Previous Stats to detect "Latest" (Chapter Change)
        prev_chapters = {}
        if supabase:
            try:
                # Look at the most recent successful findings for this writer
                prev_res = supabase.table("recent_findings").select("book_id, chapters").eq("profile_id", profile_id).order("id", { "ascending": False }).limit(20).execute()
                for r in prev_res.data or []:
                    if r['book_id'] not in prev_chapters:
                        prev_chapters[r['book_id']] = r['chapters']
            except: pass

        # 2. Fetch Manual "Main" Book Setting
        manual_main_id = None
        if user_id and supabase:
            try:
                stats_res = supabase.table("writer_stats").select("main_book_id").eq("user_id", user_id).execute()
                if stats_res.data: manual_main_id = stats_res.data[0].get("main_book_id")
            except: pass

        highest_book_id = None
        latest_book_id  = None
        max_chapters = -1
        
        # Pass 1: Identify Highest and Latest
        for b in books_data:
            bid = str(b.get('bookId', ''))
            chaps = b.get('chapterNum', 0)
            total_chapters += chaps
            
            # Highest: Most Chapters
            if chaps > max_chapters:
                max_chapters = chaps
                highest_book_id = bid
            
            # Latest: Chapter count increased since last scrape
            if bid in prev_chapters and chaps > prev_chapters[bid]:
                latest_book_id = bid

        # Pass 2: Save with Tags
        findings = []
        for b in books_data:
            bid = str(b.get('bookId', ''))
            book_name = b.get('bookName', 'Untitled')
            
            categories = []
            if bid == manual_main_id: categories.append("Main")
            if bid == highest_book_id: categories.append("Highest")
            if bid == latest_book_id:  categories.append("Latest")

            finding = {
                "penname":       penname,
                "country":       country,
                "book":          book_name,
                "book_id":       bid,
                "profile_id":    profile_id,
                "genre":         b.get('categoryName', 'Unknown'),
                "collections":   b.get('collectNum', 0),
                "chapters":      b.get('chapterNum', 0),
                "views":         str(b.get('visitNum', '0')),
                "power_ranking": b.get('powerRank'),
                "task_id":       task_id,
                "category_tags": categories
            }
            findings.append(finding)

            if supabase:
                try:
                    supabase.table("recent_findings").insert(finding).execute()
                except: pass

        # 2. Update Global Writer Stats/Profile in AppSuba
        if user_id and supabase:
            try:
                # Get the highest and latest book names for profile mapping
                highest_name = next((b['book'] for b in findings if "Highest" in b['category_tags']), "N/A")
                latest_name = next((b['book'] for b in findings if "Latest" in b['category_tags']), highest_name)

                # Update writer_stats (The Core Dashboard)
                # We fill every field we found: books, chapters, and words
                supabase.table("writer_stats").upsert({
                    "user_id": user_id,
                    "books_count": total_books,
                    "latest_chapter_count": total_chapters,
                    "last_updated": "now()",
                    "latest_book_name": latest_name, # Map to specific columns requested
                    "highest_book_name": highest_name
                }).execute()
                
                # Update profiles table (Public Identity)
                supabase.table("profiles").update({
                    "writer_title": f"Master of {highest_name}" if highest_name != "N/A" else "Active Author",
                    "official_role": "Elite Writer" if total_chapters > 100 else "Writer"
                }).eq("id", user_id).execute()
                
                # Award 10 Cloud Marks for successful sync
                supabase.rpc("award_cloud_marks", {"u_id": user_id, "amount": 10}).execute()
            except Exception as e:
                print(f"[HUB] Profile Mapping Error: {e}")

        # 3. Success Notification to Bots (Mission Accomplished signal)
        if supabase:
            supabase.table("server_updates").insert({
                "content": f"🏆 MISSION ACCOMPLISHED: {penname} data grid updated. {total_books} books, {total_chapters} chapters synced."
            }).execute()

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
    """Phones pick up work. Uses Long Polling for instant assignment."""
    if client_id not in clients:
        clients[client_id] = {"last_seen": 0}
        print(f"[HUB] New Node registered: {client_id}")
            
    clients[client_id]['last_seen'] = time.time()
    
    # Create or reset wait event for this client
    if client_id not in client_waiters:
        client_waiters[client_id] = asyncio.Event()
    client_waiters[client_id].clear()

    # LONG POLLING LOOP (Wait up to 25s for a task)
    for _ in range(25): # 25 seconds max
        # 1. Check local memory queue (Fastest)
        if not task_queue.empty():
            try:
                task = task_queue.get_nowait()
                # Mark as active in Supabase immediately
                if supabase:
                    supabase.table("task_queue").update({"status": "active"}).eq("id", task['id']).execute()
                return {"task": task}
            except asyncio.QueueEmpty:
                pass
                
        # 2. Check Supabase (Persistent pool)
        if supabase:
            try:
                res = supabase.table("task_queue").select("*").eq("status", "pending").limit(1).execute()
                if res.data:
                    db_task = res.data[0]
                    # ATOMIC CLAIM: Only return if we successfully marked it active
                    claim = supabase.table("task_queue").update({"status": "active"}).eq("id", db_task['id']).eq("status", "pending").execute()
                    if claim.data:
                        return {"task": db_task}
            except Exception as e:
                print(f"[HUB] DB Pull Error: {e}")

        # No task? Wait for the next one to be pushed
        try:
            await asyncio.wait_for(client_waiters[client_id].wait(), timeout=1.0)
            client_waiters[client_id].clear() # Reset for next check in loop
        except asyncio.TimeoutError:
            continue # Just loop and check again

    return {"task": None}

@app.post("/respond/{client_id}/{request_id}")
async def client_respond(client_id: str, request_id: str, response: dict):
    """Phones return results here. Handles rotation on failure and persistent data relay."""
    
    # 1. Handle Rotation on Failure
    if "error" in response:
        print(f"[HUB] Node {client_id} failed task {request_id}: {response['error']}")
        
        # Check Supabase for the task if not in memory
        task_info = task_status.get(request_id)
        if not task_info and supabase:
            try:
                res = supabase.table("task_queue").select("*").eq("id", request_id).execute()
                if res.data:
                    task_info = res.data[0]
                    # Convert created_at string to timestamp if needed
                    from datetime import datetime
                    dt = datetime.fromisoformat(task_info['created_at'].replace('Z', '+00:00'))
                    task_info['created_at'] = dt.timestamp()
            except: pass

        if task_info:
            created_at = task_info.get("created_at", 0)
            if (time.time() - created_at) < 82800:
                print(f"[HUB] Re-queuing task {request_id} for another node.")
                if supabase:
                    supabase.table("task_queue").update({"status": "pending"}).eq("id", request_id).execute()
                # Update memory if possible
                if request_id in task_status:
                    task_status[request_id]["status"] = "pending"
                await task_queue.put(task_info)
                return {"status": "rotated"}
            else:
                if supabase:
                    supabase.table("task_queue").update({"status": "failed"}).eq("id", request_id).execute()
                    supabase.table("server_updates").insert({"content": f"❌ Mission Failed: Task {request_id} expired."}).execute()
                return {"status": "failed_expired"}

    # 2. Success Flow - Database Persistence
    if supabase:
        try:
            supabase.table("task_queue").update({
                "status": "completed",
                "result": response
            }).eq("id", request_id).execute()
        except Exception as e:
            print(f"[HUB] Supabase Result Update Error: {e}")

    # 3. Data Relay (The "Why it wasn't updating" Fix)
    task_info = task_status.get(request_id)
    
    # CRITICAL FIX: If not in memory, pull details from Supabase to proceed with parsing
    if not task_info and supabase:
        try:
            db_res = supabase.table("task_queue").select("*").eq("id", request_id).execute()
            if db_res.data:
                task_info = db_res.data[0]
                # Map DB fields to what parse_and_save_data expects
                task_info["url"] = task_info.get("url", "")
                task_info["user_id"] = task_info.get("assigned_to") # Approximate
                from datetime import datetime
                dt = datetime.fromisoformat(task_info['created_at'].replace('Z', '+00:00'))
                task_info['created_at'] = dt.timestamp()
        except: pass

    if task_info:
        # Update memory tracking for the current session
        if request_id not in task_status:
            task_status[request_id] = task_info
            
        task_status[request_id]["status"] = "completed"
        task_status[request_id]["completed_at"] = time.time()
        task_status[request_id]["result"] = response
        
        # RELAY TO SUPABASE & MASTER DB
        html = response.get("content", "")
        url = task_info.get("url", "")
        created_at = task_info.get("created_at", 0)
        user_id = task_info.get("user_id")
        
        if html and url:
            print(f"[HUB] Relaying results for task {request_id}...")
            task_status[request_id]["saved_to_relay"] = parse_and_save_data(html, url, request_id, created_at, user_id)
            
        save_tasks()
        return {"status": "ok"}
    
    return {"status": "not_found"}

if __name__ == "__main__":
    import uvicorn
    import os
    uvicorn.run(app, host="0.0.0.0", port=9991)
