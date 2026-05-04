from fastapi import FastAPI, HTTPException, Depends, Header, Request
from pydantic import BaseModel
import uuid
import asyncio
import time
from typing import Dict, Optional

app = FastAPI(title="HiveSlave Scraper Hub")

# Configuration
API_KEY = "hiveslave_secret_key_20262025202420232022202120100000"

# State: Stores connected phone clients
# { client_id: { "last_seen": timestamp, "queue": asyncio.Queue } }
clients: Dict[str, Dict] = {}

class ScrapeRequest(BaseModel):
    url: str
    wait_selector: Optional[str] = "body"

async def verify_api_key(x_api_key: str = Header(None)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API Key")

@app.get("/")
async def root():
    return {
        "status": "Hub is running",
        "active_clients": len([c for c in clients.values() if time.time() - c['last_seen'] < 60]),
        "total_registered": len(clients)
    }

@app.get("/latest-client")
async def get_latest_client():
    """Serves the latest mobile_client.py code for OTA updates."""
    try:
        with open("mobile_client.py", "r") as f:
            return {"code": f.read()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- ENDPOINTS FOR THE BOT ---

@app.post("/scrape")
async def hub_scrape(req: ScrapeRequest, _=Depends(verify_api_key)):
    """The bot calls this. The Hub forwards the request to an available phone."""
    
    # Find an active client (seen in the last 60 seconds)
    active_clients = [cid for cid, data in clients.items() if time.time() - data['last_seen'] < 60]
    
    if not active_clients:
        raise HTTPException(status_code=503, detail="No mobile scraper clients online")
    
    # Pick the first available client (can be improved to load balance)
    target_client = active_clients[0]
    request_id = str(uuid.uuid4())
    
    # Create a future to wait for the response
    response_future = asyncio.get_event_loop().create_future()
    clients[target_client]['pending_requests'][request_id] = response_future
    
    # Put the request in the client's queue
    await clients[target_client]['queue'].put({
        "id": request_id,
        "url": req.url,
        "wait_selector": req.wait_selector
    })
    
    try:
        # Wait up to 45 seconds for the phone to respond
        result = await asyncio.wait_for(response_future, timeout=45)
        return result
    except asyncio.TimeoutError:
        # Cleanup
        if request_id in clients[target_client]['pending_requests']:
            del clients[target_client]['pending_requests'][request_id]
        raise HTTPException(status_code=504, detail="Mobile client timed out")

# --- ENDPOINTS FOR THE PHONE ---

@app.get("/register/{client_id}")
async def register_client(client_id: str):
    """Phones call this to stay 'Online'."""
    if client_id not in clients:
        clients[client_id] = {
            "queue": asyncio.Queue(),
            "pending_requests": {},
            "last_seen": 0
        }
    
    clients[client_id]['last_seen'] = time.time()
    
    # Long polling: if there's a task, return it immediately.
    # Otherwise, wait up to 20 seconds for a task.
    try:
        task = await asyncio.wait_for(clients[client_id]['queue'].get(), timeout=20)
        return {"task": task}
    except asyncio.TimeoutError:
        return {"task": None}

@app.post("/respond/{client_id}/{request_id}")
async def client_respond(client_id: str, request_id: str, response: dict):
    """Phones call this to send back the scraped HTML."""
    if client_id in clients and request_id in clients[client_id]['pending_requests']:
        future = clients[client_id]['pending_requests'].pop(request_id)
        if not future.done():
            future.set_result(response)
        return {"status": "ok"}
    return {"status": "request_not_found"}

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
