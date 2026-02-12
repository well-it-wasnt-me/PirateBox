"""PirateBox FastAPI app: local-only mischief with a straight face."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

try:
    from . import db
except ImportError:  # pragma: no cover - fallback for `python app/main.py`
    import db

APP_NAME = os.getenv("PIRATEBOX_NAME", "PirateBox")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Boot the database before the app starts pretending everything is fine."""
    db.init_db()
    yield


app = FastAPI(title=APP_NAME, lifespan=lifespan)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")


def _format_size(num_bytes: int) -> str:
    """Turn raw bytes into something a human can pretend to parse."""
    step = 1024.0
    size = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < step:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= step
    return f"{size:.1f} PB"


def _format_time(value: str) -> str:
    """Make timestamps look less like a ransom note from UTC."""
    return value.replace("T", " ").replace("+00:00", " UTC")


def _safe_next(raw: Optional[str]) -> str:
    """Keep redirects on a short leash so nobody gets cute."""
    if not raw:
        return "/"
    if raw.startswith("/") and not raw.startswith("//"):
        return raw
    return "/"


def _captive_acknowledged(request: Request) -> bool:
    """Check whether the captive portal has been waved away already."""
    return request.cookies.get("piratebox_captive_ack") == "1"


templates.env.filters["filesize"] = _format_size
templates.env.filters["prettytime"] = _format_time


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    """Serve the home page where the box smiles and lies about simplicity."""
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "app_name": APP_NAME,
        },
    )


@app.get("/captive", response_class=HTMLResponse)
def captive_portal(request: Request) -> HTMLResponse:
    """Serve the captive portal splash screen for the unwillingly trapped."""
    return templates.TemplateResponse(
        request,
        "captive.html",
        {
            "app_name": APP_NAME,
            "title": "Welcome",
        },
    )


@app.get("/captive/ack", response_class=HTMLResponse)
def captive_ack(request: Request, next: Optional[str] = None) -> HTMLResponse:
    """Set a cookie so the portal stops nagging and gets out of the way."""
    next_url = _safe_next(next)
    response = templates.TemplateResponse(
        request,
        "captive_ack.html",
        {
            "app_name": APP_NAME,
            "title": "Opening",
            "next_url": next_url,
        },
    )
    response.set_cookie("piratebox_captive_ack", "1", max_age=60 * 60 * 24 * 7, samesite="lax")
    return response


@app.get("/files", response_class=HTMLResponse)
def files_page(request: Request) -> HTMLResponse:
    """List stored files and politely invite another upload."""
    files = db.list_files()
    return templates.TemplateResponse(
        request,
        "files.html",
        {
            "app_name": APP_NAME,
            "files": files,
            "max_upload_mb": db.MAX_UPLOAD_MB,
        },
    )


@app.post("/files/upload")
def upload_file(request: Request, file: UploadFile = File(...)) -> RedirectResponse:
    """Accept an upload, scan it for size limits, and stash it on disk."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    original_name = file.filename.strip()
    if not original_name:
        raise HTTPException(status_code=400, detail="Invalid filename")

    try:
        db.store_upload(file.file, original_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return RedirectResponse(url="/files", status_code=303)


@app.get("/files/{file_id}/download")
def download_file(file_id: int) -> FileResponse:
    """Stream a stored file back to anyone with the link and zero shame."""
    record = db.get_file(file_id)
    if not record:
        raise HTTPException(status_code=404, detail="File not found")

    file_path = db.FILES_DIR / record.stored_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File missing on disk")

    return FileResponse(
        file_path,
        media_type="application/octet-stream",
        filename=record.original_name,
    )


@app.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request) -> HTMLResponse:
    """Render the chat UI with the latest messages and no pretense."""
    messages = db.list_chat_messages(after_id=0, limit=50)
    last_id = messages[-1].id if messages else 0
    return templates.TemplateResponse(
        request,
        "chat.html",
        {
            "app_name": APP_NAME,
            "messages": messages,
            "last_id": last_id,
        },
    )


@app.get("/api/chat/messages")
def chat_messages(after_id: int = 0) -> JSONResponse:
    """Return chat messages newer than the given id."""
    messages = db.list_chat_messages(after_id=after_id, limit=200)
    payload = [message.__dict__ for message in messages]
    return JSONResponse({"messages": payload})


@app.post("/api/chat/messages")
def post_chat_message(
    nickname: Optional[str] = Form(None),
    message: Optional[str] = Form(None),
) -> JSONResponse:
    """Accept a chat message, sanitize it, and return what stuck."""
    clean_nick = db.normalize_nickname(nickname)
    clean_message = db.normalize_message(message, max_len=db.MAX_MESSAGE_LEN)

    if not clean_message:
        raise HTTPException(status_code=400, detail="Message required")

    if len(clean_message) > db.MAX_MESSAGE_LEN:
        raise HTTPException(status_code=400, detail="Message too long")

    msg = db.insert_chat_message(clean_nick, clean_message)
    return JSONResponse({"message": msg.__dict__})


@app.get("/forum", response_class=HTMLResponse)
def forum_page(request: Request) -> HTMLResponse:
    """List forum threads for people who still enjoy long-form arguments."""
    threads = db.list_threads()
    return templates.TemplateResponse(
        request,
        "forum.html",
        {
            "app_name": APP_NAME,
            "threads": threads,
        },
    )


@app.post("/forum")
def create_forum_thread(
    title: Optional[str] = Form(None),
    nickname: Optional[str] = Form(None),
    message: Optional[str] = Form(None),
) -> RedirectResponse:
    """Create a forum thread and pretend the internet didn't ruin this format."""
    clean_title = db.normalize_message(title, max_len=db.MAX_THREAD_TITLE_LEN)
    clean_nick = db.normalize_nickname(nickname)
    clean_message = db.normalize_message(message, max_len=db.MAX_MESSAGE_LEN)

    if not clean_title or not clean_message:
        raise HTTPException(status_code=400, detail="Title and message required")

    thread_id = db.create_thread(clean_title, clean_nick, clean_message)
    return RedirectResponse(url=f"/forum/{thread_id}", status_code=303)


@app.get("/forum/{thread_id}", response_class=HTMLResponse)
def forum_thread(thread_id: int, request: Request) -> HTMLResponse:
    """Render a single forum thread with all the replies."""
    thread = db.get_thread(thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    posts = db.list_posts(thread_id)
    return templates.TemplateResponse(
        request,
        "thread.html",
        {
            "app_name": APP_NAME,
            "thread": thread,
            "posts": posts,
        },
    )


@app.post("/forum/{thread_id}/reply")
def reply_thread(
    thread_id: int,
    nickname: Optional[str] = Form(None),
    message: Optional[str] = Form(None),
) -> RedirectResponse:
    """Insert a reply into a forum thread and send folks back to the noise."""
    thread = db.get_thread(thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    clean_nick = db.normalize_nickname(nickname)
    clean_message = db.normalize_message(message, max_len=db.MAX_MESSAGE_LEN)

    if not clean_message:
        raise HTTPException(status_code=400, detail="Message required")

    db.insert_post(thread_id, clean_nick, clean_message)
    return RedirectResponse(url=f"/forum/{thread_id}", status_code=303)


CAPTIVE_PORTAL_PATHS = {
    "/generate_204",
    "/gen_204",
    "/hotspot-detect.html",
    "/library/test/success.html",
    "/success.txt",
    "/success.html",
    "/ncsi.txt",
    "/connecttest.txt",
    "/redirect",
}

def _captive_success_response(path: str) -> Response:
    """Return OS-specific connectivity success responses to shut the portal up."""
    if path in {"/generate_204", "/gen_204"}:
        return Response(status_code=204)
    if path == "/hotspot-detect.html":
        return HTMLResponse("<html><body>Success</body></html>")
    if path in {"/library/test/success.html", "/success.html", "/success.txt"}:
        return PlainTextResponse("Success")
    if path == "/ncsi.txt":
        return PlainTextResponse("Microsoft NCSI")
    if path == "/connecttest.txt":
        return PlainTextResponse("Microsoft Connect Test")
    if path == "/redirect":
        return RedirectResponse(url="/", status_code=302)
    return PlainTextResponse("Success")


@app.get("/{path:path}", response_class=PlainTextResponse, response_model=None)
def captive_fallback(path: str, request: Request) -> Response:
    """Catch-all for captive portal probes and lost souls."""
    full_path = f"/{path}"
    if full_path in CAPTIVE_PORTAL_PATHS:
        if _captive_acknowledged(request):
            return _captive_success_response(full_path)
        return RedirectResponse(url="/captive", status_code=302)
    return PlainTextResponse("Not found", status_code=404)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "80")))
