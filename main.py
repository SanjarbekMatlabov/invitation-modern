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
import databases
import sqlalchemy
from sqlalchemy import Column, String, MetaData, Table, create_engine, select, desc, delete

# PostgreSQL Database URL
DATABASE_URL = "postgresql://wishes:HHGqpNh9JAukOfMbi9qPutwWRw9uB7Pg@dpg-d04ig2i4d50c73a7lj9g-a/wishes_pxer"

# Create FastAPI app
app = FastAPI(title="Wedding Invitation API")

# Set up database
database = databases.Database(DATABASE_URL)
metadata = MetaData()

# Define wishes table
wishes = Table(
    "wishes",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String),
    Column("message", String),
    Column("password_hash", String),
    Column("date", String),
)

# Create tables
engine = create_engine(DATABASE_URL)
metadata.create_all(engine)

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
def get_formatted_date():
    now = datetime.now()
    return now.strftime("%d/%m/%Y, %H:%M")


def hash_password(password):
    # Simple password hashing
    return hashlib.sha256(password.encode()).hexdigest()


# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")


# Startup and shutdown events
@app.on_event("startup")
async def startup():
    await database.connect()


@app.on_event("shutdown")
async def shutdown():
    await database.disconnect()


# API Routes
@app.get("/api/wishes")
async def get_wishes():
    try:
        # Query all wishes and order by date (newest first)
        query = wishes.select().order_by(desc(wishes.c.date))
        result = await database.fetch_all(query)
        
        # Return only necessary information (no passwords)
        sanitized_wishes = [
            {
                "id": row["id"],
                "name": row["name"],
                "message": row["message"],
                "date": row["date"]
            }
            for row in result
        ]
        return sanitized_wishes
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Server error: {str(e)}")


@app.post("/api/wishes", status_code=201)
async def add_wish(wish: WishCreate):
    try:
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
        
        # Insert into database
        query = wishes.insert().values(**new_wish)
        await database.execute(query)
        
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
        # Find wish by ID
        query = wishes.select().where(wishes.c.id == wish_id)
        wish = await database.fetch_one(query)
        
        if not wish:
            raise HTTPException(status_code=404, detail="Wish not found")
        
        # Check password
        if hash_password(wish_delete.password) != wish["password_hash"]:
            raise HTTPException(status_code=401, detail="Invalid password")
        
        # Remove wish
        delete_query = wishes.delete().where(wishes.c.id == wish_id)
        await database.execute(delete_query)
        
        # Broadcast to all connected clients
        await manager.broadcast({
            "action": "delete_wish",
            "id": wish_id
        })
        
        return {"message": "Wish deleted successfully"}
    
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