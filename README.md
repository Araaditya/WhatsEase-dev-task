# WhatsEase Chat Application

**WhatsEase** is a real-time chat application built with **FastAPI**, **Socket.IO**, and a **Supabase** backend. It features JWT authentication, persistent message history, and an integrated AI chatbot powered by the **Google Gemini API**.

---

## Features

* **Real-time Messaging**: Instant communication between users in different rooms.
* **User Authentication**: Secure login system using JWT.
* **Room Management**: Users can create new chat rooms and join existing ones by their unique ID.
* **Persistent Chat History**: All messages are saved to a PostgreSQL database (Supabase) and loaded when a user joins a room.
* **AI Chatbot**: Integrated Gemini-powered bot that responds to user messages in a designated room.

---

## Database Schema

The application uses a **PostgreSQL** database managed by **Supabase**.
The schema is designed to handle users, rooms, message history, and their relationships.

---

## Tech Stack

* **Backend**: Python, FastAPI, Uvicorn, Python-SocketIO
* **Database**: Supabase (PostgreSQL)
* **AI**: Google Gemini API
* **Authentication**: JWT (PyJWT)
* **Frontend**: HTML, CSS, JavaScript (no framework)
* **Environment Management**: python-dotenv

---

## Setup and Installation

1. **Clone the repository:**

   ```bash
   git clone <your-repository-url>
   cd <your-repository-folder>
   ```

2. **Create and activate a virtual environment:**

   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
   ```

3. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables:**

   Create a `.env` file in the project root:

   ```env
   GEMINI_API_KEY=your_gemini_api_key_here
   SUPABASE_URL=your_supabase_project_url_here
   SUPABASE_KEY=your_supabase_service_role_key_here
   BOT_ROOM_ID=the_uuid_of_your_bot_room_here
   ```

---

## How to Run

Make sure your virtual environment is activated, then run the Uvicorn server:

```bash
uvicorn main:socket_app --reload
```

Your server should now be running at `http://127.0.0.1:8000/`.
