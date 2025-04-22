from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Body
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Dict, Optional
import json
import os
from datetime import datetime
import asyncio
import uvicorn
import hashlib
import uuid

# Create FastAPI app
app = FastAPI(title="Wedding Invitation API")

# Ensure data directory exists
if not os.path.exists("data"):
    os.makedirs("data")

# Path to wishes.json
WISHES_PATH = "data/wishes.json"

# Create wishes.json if it doesn't exist
if not os.path.exists(WISHES_PATH):
    with open(WISHES_PATH, "w") as f:
        json.dump({"wishes": []}, f)


# Models
class WishBase(BaseModel):
    name: str
    message: str


class WishCreate(WishBase):
    password: str


class Wish(WishBase):
    id: str
    date: str


class WishDelete(BaseModel):
    password: str


# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: Dict):
        for connection in self.active_connections:
            await connection.send_json(message)


manager = ConnectionManager()


# Helper functions
def read_wishes():
    with open(WISHES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def write_wishes(wishes_data):
    with open(WISHES_PATH, "w", encoding="utf-8") as f:
        json.dump(wishes_data, f, ensure_ascii=False, indent=2)


def get_formatted_date():
    now = datetime.now()
    return now.strftime("%d/%m/%Y, %H:%M")


def hash_password(password):
    # Simple password hashing
    return hashlib.sha256(password.encode()).hexdigest()


# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")


# API Routes
@app.get("/api/wishes")
def get_wishes():
    try:
        wishes_data = read_wishes()
        # Return only necessary information (no passwords)
        sanitized_wishes = [
            {
                "id": w.get("id", str(i)),  # For backward compatibility
                "name": w["name"],
                "message": w["message"],
                "date": w["date"]
            }
            for i, w in enumerate(wishes_data["wishes"])
        ]
        return sanitized_wishes
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Server error: {str(e)}")


@app.post("/api/wishes", status_code=201)
async def add_wish(wish: WishCreate):
    try:
        # Read existing wishes
        wishes_data = read_wishes()
        
        # Generate unique ID and hash password
        wish_id = str(uuid.uuid4())
        hashed_password = hash_password(wish.password)
        
        # Create new wish
        new_wish = {
            "id": wish_id,
            "name": wish.name,
            "password_hash": hashed_password,
            "message": wish.message,
            "date": get_formatted_date()
        }
        
        # Add new wish to beginning of array
        wishes_data["wishes"].insert(0, new_wish)
        
        # Save updated wishes
        write_wishes(wishes_data)
        
        # Create sanitized response (without password)
        response = {
            "id": new_wish["id"],
            "name": new_wish["name"],
            "message": new_wish["message"],
            "date": new_wish["date"]
        }
        
        # Broadcast to all connected clients
        await manager.broadcast({
            "action": "new_wish",
            "wish": response
        })
        
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Server error: {str(e)}")


@app.delete("/api/wishes/{wish_id}")
async def delete_wish(wish_id: str, wish_delete: WishDelete):
    try:
        # Read existing wishes
        wishes_data = read_wishes()
        
        # Find wish by ID
        for i, wish in enumerate(wishes_data["wishes"]):
            stored_id = wish.get("id")
            
            # Handle case where wish has no ID (backward compatibility)
            if stored_id is None and wish_id.isdigit() and i == int(wish_id):
                stored_id = str(i)
            
            if stored_id == wish_id:
                # Check password - handle both new hashed passwords and legacy plain passwords
                if "password_hash" in wish:
                    # New system with hashed passwords
                    if hash_password(wish_delete.password) != wish["password_hash"]:
                        raise HTTPException(status_code=401, detail="Invalid password")
                elif "password" in wish:
                    # Legacy system with plaintext passwords
                    if wish_delete.password != wish["password"]:
                        raise HTTPException(status_code=401, detail="Invalid password")
                else:
                    raise HTTPException(status_code=401, detail="Password verification failed")
                
                # Remove wish
                wishes_data["wishes"].pop(i)
                
                # Save updated wishes
                write_wishes(wishes_data)
                
                # Broadcast to all connected clients
                await manager.broadcast({
                    "action": "delete_wish",
                    "id": wish_id
                })
                
                return {"message": "Wish deleted successfully"}
        
        # If we got here, wish wasn't found
        raise HTTPException(status_code=404, detail="Wish not found")
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Server error: {str(e)}")


# WebSocket endpoint
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # Keep connection alive with ping/pong
        while True:
            # Wait for a message (ping)
            await websocket.receive_text()
            # Send pong response
            await websocket.send_json({"action": "pong"})
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# Serve HTML file for all other routes
@app.get("/{full_path:path}")
def serve_html(full_path: str):
    return FileResponse("static/index.html")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)