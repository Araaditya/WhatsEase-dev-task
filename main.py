import os
import sys
import asyncio
import logging
from typing import Optional, Dict, Any, Set
from collections import defaultdict
from dotenv import load_dotenv
import google.generativeai as genai
import httpx
import socketio
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from auth import create_access_token, decode_access_token

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not all([GEMINI_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    logger.critical("FATAL: Missing environment variables (GEMINI_API_KEY, SUPABASE_URL, SUPABASE_KEY)")
    sys.exit(1)
genai.configure(api_key=GEMINI_API_KEY)
SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

BOT_USER_EMAIL = "gemini-bot@example.com"
BOT_ROOM_ID = 2

class AppState:
    """A simple class to hold shared application state."""
    def __init__(self):
        self.http_client: Optional[httpx.AsyncClient] = None
        self.room_members: Dict[int, Set[str]] = defaultdict(set)

state = AppState()

app = FastAPI(title="Chat App", description="A real-time chat application with an AI bot.")
sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
socket_app = socketio.ASGIApp(sio, app)
templates = Jinja2Templates(directory="templates")

@app.on_event("startup")
async def startup_event():
    """Initializes the HTTP client on application startup."""
    state.http_client = httpx.AsyncClient(timeout=10.0)

@app.on_event("shutdown")
async def shutdown_event():
    """Closes the HTTP client on application shutdown."""
    if state.http_client:
        await state.http_client.aclose()

class LoginRequest(BaseModel):
    email: str
    password: str

async def get_user_db(user_email: str) -> Optional[Dict[str, Any]]:
    """Fetches a single user from the database by email."""
    url = f"{SUPABASE_URL}/rest/v1/users?select=*&email=eq.{user_email}&limit=1"
    try:
        resp = await state.http_client.get(url, headers=SUPABASE_HEADERS)
        resp.raise_for_status()
        data = resp.json()
        return data[0] if data else None
    except httpx.HTTPStatusError:
        logger.exception("get_user_db failed")
        return None

async def find_room_by_id_db(room_id: int) -> Optional[Dict[str, Any]]:
    """Finds a single room in the database by its ID."""
    url = f"{SUPABASE_URL}/rest/v1/rooms?select=*&id=eq.{room_id}&limit=1"
    try:
        resp = await state.http_client.get(url, headers=SUPABASE_HEADERS)
        resp.raise_for_status()
        data = resp.json()
        return data[0] if data else None
    except httpx.HTTPStatusError:
        logger.warning("find_room_by_id_db failed for room: %s", room_id)
        return None

async def create_room_db(name: str) -> Optional[Dict[str, Any]]:
    """Creates a new room in the database."""
    payload = {"name": name}
    url = f"{SUPABASE_URL}/rest/v1/rooms"
    headers = {**SUPABASE_HEADERS, "Prefer": "return=representation"}
    try:
        resp = await state.http_client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data[0] if data else None
    except httpx.HTTPStatusError:
        logger.warning("create_room_db failed: %s", name)
        return None

async def add_participant_db(room_id: int, user_email: str) -> None:
    """Adds a user to a room's participant list in the database."""
    payload = {"room_id": room_id, "user_email": user_email}
    url = f"{SUPABASE_URL}/rest/v1/room_participants"
    try:
        await state.http_client.post(url, headers=SUPABASE_HEADERS, json=payload)
    except httpx.HTTPStatusError:
        logger.info("Participant may already be in room (this is okay).")

async def save_message_db(room_id: int, user_email: str, content: str, is_bot: bool = False) -> Optional[Dict[str, Any]]:
    """Saves a chat message to the database."""
    payload = {"room_id": room_id, "user_email": user_email, "content": content, "is_bot_response": is_bot}
    url = f"{SUPABASE_URL}/rest/v1/messages?select=*,users(name)"
    headers = {**SUPABASE_HEADERS, "Prefer": "return=representation"}
    try:
        resp = await state.http_client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data[0] if data else None
    except httpx.HTTPStatusError:
        logger.warning("save_message_db failed: %s", resp.text)
        return None

async def get_messages_for_room_db(room_id: int) -> Optional[list]:
    """Fetches the message history for a given room."""
    url = f"{SUPABASE_URL}/rest/v1/messages?select=*,users(name)&room_id=eq.{room_id}&order=timestamp.asc"
    try:
        resp = await state.http_client.get(url, headers=SUPABASE_HEADERS)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError:
        logger.warning("get_messages_for_room_db failed")
        return None

async def get_gemini_response(prompt_text: str) -> str:
    """Gets an intelligent response from the Gemini API."""
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = f"You are a helpful assistant. Keep answers concise. User's message: {prompt_text}"
        response = await model.generate_content_async(prompt)
        return response.text
    except Exception as e:
        logger.error(f"Gemini API call failed: {e}")
        return "Sorry, I am unable to respond right now."

async def _handle_bot_reply(room_id: int, user_message: str):
    """Orchestrates the bot's response to a user message."""
    logger.info(f"Getting Gemini response for: '{user_message}'")
    reply_text = await get_gemini_response(user_message)
    bot_message = await save_message_db(
        room_id=room_id,
        user_email=BOT_USER_EMAIL,
        content=reply_text,
        is_bot=True
    )
    if bot_message:
        logger.info(f"Broadcasting BOT message to {len(state.room_members.get(room_id, []))} members in room {room_id}")
        for member_sid in state.room_members.get(room_id, set()):
            await sio.emit("new_message", bot_message, to=member_sid)

async def _get_validated_session(sid) -> Optional[Dict[str, Any]]:
    """Helper to get and validate a user's session data."""
    session = await sio.get_session(sid)
    if not session or "user_email" not in session:
        await sio.emit("error", {"msg": "Authentication error. Please reconnect."}, to=sid)
        return None
    return session

@app.post("/api/login", tags=["Authentication"])
async def login(login_data: LoginRequest):
    """Handles user login and returns a JWT."""
    user = await get_user_db(login_data.email)
    if not user: # In production, you would also verify the password hash here.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")
    
    access_token = create_access_token(data={"sub": user["email"]})
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def get_index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def get_login(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@sio.event
async def connect(sid, environ, auth):
    token = (auth or {}).get("token")
    if not token: return False
    payload = decode_access_token(token)
    if not payload or "sub" not in payload: return False
    
    user_email = payload["sub"]
    await sio.save_session(sid, {"user_email": user_email})
    logger.info("Connected: sid=%s, email=%s", sid, user_email)
    return True

@sio.event
async def disconnect(sid):
    for room_id in list(state.room_members):
        if sid in state.room_members[room_id]:
            state.room_members[room_id].remove(sid)
            if not state.room_members[room_id]:
                del state.room_members[room_id]
    
    session = await sio.get_session(sid)
    user_email = session.get("user_email", "unknown") if session else "unknown"
    logger.info("Disconnected: sid=%s, email=%s", sid, user_email)

@sio.event
async def create_room(sid, data):
    session = await _get_validated_session(sid)
    if not session: return
    
    room_name = (data or {}).get("room_name", "").strip()
    if not room_name:
        return await sio.emit("error", {"msg": "Room name cannot be empty"}, to=sid)
    
    new_room = await create_room_db(room_name)
    if new_room:
        await sio.emit("room_created", new_room, to=sid)
    else:
        await sio.emit("error", {"msg": "Could not create room"}, to=sid)

@sio.event
async def join_room(sid, data):
    session = await _get_validated_session(sid)
    if not session: return

    room_id_raw = (data or {}).get("room_id")
    if not room_id_raw or not str(room_id_raw).isdigit():
        return await sio.emit("error", {"msg": "A valid Room ID is required"}, to=sid)

    room_id = int(room_id_raw)
    room = await find_room_by_id_db(room_id)
    if not room:
        return await sio.emit("error", {"msg": f"Room with ID '{room_id}' not found."}, to=sid)
    
    state.room_members[room_id].add(sid)
    asyncio.create_task(add_participant_db(room_id, session["user_email"]))
    logger.info("'%s' joined room %d. Total members: %s", session["user_email"], room_id, len(state.room_members[room_id]))
    await sio.emit("room_joined", {"room_id": room_id}, to=sid)

@sio.event
async def send_message(sid, data):
    session = await _get_validated_session(sid)
    if not session: return
    
    room_id_raw = (data or {}).get("room_id")
    content = (data or {}).get("content", "").strip()
    if not room_id_raw or not str(room_id_raw).isdigit() or not content:
        return
    
    room_id = int(room_id_raw)
    saved_message = await save_message_db(room_id, session["user_email"], content, is_bot=False)
    
    if saved_message:
        logger.info(f"Broadcasting USER message to {len(state.room_members.get(room_id, []))} members in room {room_id}")
        for member_sid in state.room_members.get(room_id, set()):
            await sio.emit("new_message", saved_message, to=member_sid)

        if room_id == BOT_ROOM_ID:
            asyncio.create_task(_handle_bot_reply(room_id, content))

@sio.event
async def request_past_messages(sid, data):
    session = await _get_validated_session(sid)
    if not session: return
    
    room_id_raw = (data or {}).get("room_id")
    if not room_id_raw or not str(room_id_raw).isdigit():
        return await sio.emit("error", {"msg": "Valid room_id is required"}, to=sid)
    messages = await get_messages_for_room_db(int(room_id_raw))
    if messages is not None:
        await sio.emit("past_messages", messages, to=sid)