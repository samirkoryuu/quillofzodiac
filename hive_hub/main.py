from fastapi import FastAPI, HTTPException, Depends, Header, Request
from pydantic import BaseModel
import uuid
import asyncio
import time
from typing import Dict, Optional

app = FastAPI(title="HiveSlave Scraper Hub")

# Configuration
API_KEY = "hiveslave_secret_key_20262025202420232022202120100000"

async def verify_api_key(x_api_key: str = Header(None)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API Key")

# Global State
clients: Dict[str, Dict] = {}
task_queue = asyncio.Queue()  
task_status: Dict[str, Dict] = {} # task_id -> {status, result, created_at, user_id}
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

# --- ENDPOINTS FOR THE PHONE ---

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
        task_status[request_id]["result"] = response
        return {"status": "ok"}
    return {"status": "expired_or_not_found"}

if __name__ == "__main__":
    import uvicorn
    import os
    uvicorn.run(app, host="0.0.0.0", port=9999)
