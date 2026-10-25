"""Public application surface for the three independent assignment protocols."""
import json
import os
import re
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from . import incident, invoice, mailroom
from .common import canonical, digest, require

app = FastAPI(title="TDS assignment agents", redirect_slashes=False, docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "HEAD", "OPTIONS"], allow_headers=["*"])
MAX_REQUEST = 4 * 1024 * 1024


@app.middleware("http")
async def bounds(request, call_next):
    try:
        if int(request.headers.get("content-length", "0")) > MAX_REQUEST:
            return JSONResponse({"error": "Request too large"}, 413)
    except ValueError:
        return JSONResponse({"error": "Invalid Content-Length"}, 400)
    if request.method == "POST":
        size = 0
        chunks = []
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_REQUEST:
                return JSONResponse({"error": "Request too large"}, 413)
            chunks.append(chunk)
        request._body = b"".join(chunks)
        if request._body:
            try:
                canonical(json.loads(request._body))
            except (ValueError, TypeError, RecursionError):
                return JSONResponse({"error": "Invalid JSON"}, 400)
    result = await call_next(request)
    return result


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def schema_error(request, exc):
    # FastAPI's default validation error may echo credentials or confidential input.
    return JSONResponse({"error": "Invalid request schema"}, status_code=422)


@app.exception_handler(Exception)
async def unexpected_error(request, exc):
    return JSONResponse({"error": "Internal error"}, status_code=500)


def principal(request: Request):
    auth = request.headers.get("authorization", "")
    require(bool(re.fullmatch(r"Bearer [^\s]+", auth)), 401, "Authentication required")
    require(request.headers.get("a2a-version") == "1.0", 400, "Unsupported A2A version")
    if request.method == "POST":
        require(request.headers.get("content-type", "").split(";", 1)[0].lower() == "application/a2a+json", 415, "Unsupported media type")
    # Store a one-way principal identifier, never a bearer credential.
    return digest(auth)


@app.get("/healthz")
def health():
    return {"status": "ok"}


@app.get("/.well-known/agent-card.json")
def agent_card(request: Request):
    root = os.getenv("PUBLIC_BASE_URL", str(request.base_url))
    if os.getenv("PUBLIC_BASE_URL_FILE"):
        location = Path(os.environ["PUBLIC_BASE_URL_FILE"])
        if location.is_file():
            root = location.read_text().strip()
    base = root.rstrip("/") + "/a2a/"
    return invoice.card(base)


@app.post("/a2a/message:send")
def invoice_send(body: dict, owner=Depends(principal)):
    return invoice.send(owner, body)


@app.get("/a2a/tasks")
def invoice_list(owner=Depends(principal)):
    return invoice.listing(owner)


@app.get("/a2a/tasks/{tid}")
def invoice_read(tid: str, owner=Depends(principal)):
    return invoice.read(owner, tid)


@app.post("/a2a/tasks/{tid}:cancel")
def invoice_cancel(tid: str, owner=Depends(principal)):
    return invoice.cancel(owner, tid)


@app.post("/v1/mailroom/actions")
def mailroom_post(body: dict):
    return mailroom.handle(body)


@app.post("/v2/incidents")
def incident_post(body: dict, request: Request):
    return incident.create(body, request.headers)


@app.post("/v2/incidents/{rid}/receipts")
def incident_receipts(rid: str, body: dict):
    return incident.receipts(rid, body)


@app.get("/v2/incidents/{rid}")
def incident_read(rid: str):
    return incident.read(rid)


@app.api_route("/media/{name}", methods=["GET", "HEAD"])
def media(name: str):
    require(name in {"nixon.glb", "morph.mp4"}, 404, "Not found")
    path = Path(os.getenv("MEDIA_DIR", "output")) / name
    require(path.is_file(), 404, "Not found")
    return FileResponse(path, media_type="model/gltf-binary" if name.endswith(".glb") else "video/mp4", headers={"Access-Control-Allow-Origin": "*"})
