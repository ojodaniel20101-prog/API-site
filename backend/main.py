"""
ZENTRIX API — Backend
FastAPI wrapper around the movie/series source API.
Deploy on Render as a Web Service.
"""

import os
import time
import math
import secrets
import hashlib
from typing import Optional

import requests
from fastapi import FastAPI, HTTPException, Security, Request
from fastapi.security.api_key import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# ─── CONFIG ───────────────────────────────────────────────────────────────────

SOURCE_BASE = os.environ.get("SOURCE_BASE", "https://gzmovieboxapi.septorch.tech")
SOURCE_KEY  = os.environ.get("SOURCE_KEY", "Godszeal")

# Comma-separated list of valid API keys stored in env var
# e.g. ZENTRIX_API_KEYS=key1,key2,key3
_raw_keys = os.environ.get("ZENTRIX_API_KEYS", "")
VALID_API_KEYS: set[str] = set(k.strip() for k in _raw_keys.split(",") if k.strip())

# If no keys configured, generate one at startup and print it (dev mode)
if not VALID_API_KEYS:
    _dev_key = "dev-" + secrets.token_hex(16)
    VALID_API_KEYS.add(_dev_key)
    print(f"\n[ZENTRIX] No ZENTRIX_API_KEYS set. Dev key: {_dev_key}\n")

TIMEOUT = 30

# ─── APP SETUP ────────────────────────────────────────────────────────────────

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="Zentrix API",
    description="Movie & series download API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten this to your frontend domain in production
    allow_methods=["GET"],
    allow_headers=["*"],
)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# ─── SESSION ──────────────────────────────────────────────────────────────────

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
})

# ─── AUTH ─────────────────────────────────────────────────────────────────────

def require_api_key(key: Optional[str] = Security(api_key_header)):
    if not key or key not in VALID_API_KEYS:
        raise HTTPException(
            status_code=401,
            detail={
                "status": "error",
                "code": 401,
                "message": "Invalid or missing API key. Include your key as X-API-Key header.",
            },
        )
    return key

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def format_bytes(size_bytes: int) -> str:
    if size_bytes <= 0:
        return "Unknown"
    names = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(max(size_bytes, 1), 1024)))
    i = min(i, len(names) - 1)
    p = math.pow(1024, i)
    return f"{round(size_bytes / p, 2)} {names[i]}"


def source_search(query: str, page: int = 1) -> dict:
    resp = SESSION.get(
        f"{SOURCE_BASE}/api/search",
        params={"query": query, "page": page, "perPage": 24, "apikey": SOURCE_KEY},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "success":
        raise HTTPException(502, detail={"status": "error", "code": 502, "message": "Upstream search failed."})
    return data["data"]


def source_media(subject_id: str, detail_path: str, season: int = 0, episode: int = 0) -> dict:
    for attempt in range(3):
        resp = SESSION.get(
            f"{SOURCE_BASE}/api/media",
            params={
                "subjectId": subject_id,
                "detailPath": detail_path,
                "season": season,
                "episode": episode,
                "apikey": SOURCE_KEY,
            },
            timeout=TIMEOUT,
        )
        if resp.status_code == 429:
            if attempt < 2:
                time.sleep(8)
                continue
            raise HTTPException(429, detail={"status": "error", "code": 429, "message": "Upstream rate limited. Try again in a moment."})
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "success":
            raise HTTPException(502, detail={"status": "error", "code": 502, "message": "Upstream media fetch failed."})
        return data["data"]
    raise HTTPException(503, detail={"status": "error", "code": 503, "message": "Service temporarily unavailable."})


def pick_quality(downloads: list, preferred: Optional[str]) -> Optional[dict]:
    if not downloads:
        return None
    if not preferred:
        # Return highest resolution
        return max(downloads, key=lambda d: int(d.get("resolution", 0) or 0))
    # Find exact match, fallback to closest
    pref = int(preferred)
    exact = [d for d in downloads if int(d.get("resolution", 0) or 0) == pref]
    if exact:
        return exact[0]
    # Closest available
    return min(downloads, key=lambda d: abs(int(d.get("resolution", 0) or 0) - pref))

# ─── ROUTES ───────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def root():
    return {"name": "Zentrix API", "version": "1.0.0", "status": "ok", "docs": "/docs"}


@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok", "timestamp": int(time.time())}


@app.get("/v1/api/download")
@limiter.limit("100/minute")
def download(
    request: Request,
    query: str,
    type: Optional[str] = None,
    quality: Optional[str] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    _key: str = Security(require_api_key),
):
    """
    Search for a movie or series and return download links.

    - **query**: Movie or series name (required)
    - **type**: `movie` or `series` (optional, auto-detected)
    - **quality**: `360`, `480`, `720`, or `1080` (optional, defaults to best)
    - **season**: Season number — required when type is `series`
    - **episode**: Episode number — required when type is `series`
    """

    # Validate series params
    if type == "series" and (season is None or episode is None):
        raise HTTPException(
            400,
            detail={
                "status": "error",
                "code": 400,
                "message": "season and episode are required when type is 'series'.",
            },
        )

    # Search
    try:
        results = source_search(query)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, detail={"status": "error", "code": 502, "message": f"Search failed: {e}"})

    items = results.get("items", [])
    if not items:
        raise HTTPException(
            404,
            detail={"status": "error", "code": 404, "message": "No results found for the given query.", "query": query},
        )

    # Pick best match
    # Filter by type if specified
    if type == "movie":
        candidates = [i for i in items if i.get("subjectType") != 2]
    elif type == "series":
        candidates = [i for i in items if i.get("subjectType") == 2]
    else:
        candidates = items

    if not candidates:
        candidates = items  # fallback to all if filter returns nothing

    # Prefer items with available resources
    with_resource = [i for i in candidates if i.get("hasResource")]
    selected = (with_resource or candidates)[0]

    is_series = selected.get("subjectType") == 2
    subject_id = selected["subjectId"]
    detail_path = selected["detailPath"]
    title = selected.get("title", "Unknown")
    year = str(selected.get("releaseDate", ""))[:4] or None
    rating = selected.get("imdbRatingValue")
    genre = selected.get("genre", "")

    # Fetch media sources
    s = season or 0
    e = episode or 0

    try:
        media = source_media(subject_id, detail_path, s, e)
    except HTTPException:
        raise
    except Exception as ex:
        raise HTTPException(502, detail={"status": "error", "code": 502, "message": f"Media fetch failed: {ex}"})

    downloads = media.get("downloads", {}).get("data", {}).get("downloads", [])
    subtitles = media.get("subtitles", []) or []

    if not downloads:
        raise HTTPException(
            404,
            detail={
                "status": "error",
                "code": 404,
                "message": "No download sources available for this title.",
                "title": title,
            },
        )

    chosen = pick_quality(downloads, quality)
    if not chosen:
        raise HTTPException(404, detail={"status": "error", "code": 404, "message": "Requested quality not available."})

    download_url = chosen.get("downloadUrl") or chosen.get("streamUrl")
    resolution = chosen.get("resolution")
    file_size = format_bytes(int(chosen.get("size", 0) or 0))
    fmt = chosen.get("format", "mp4")

    # Pick English subtitle if available, else first one
    subtitle_url = None
    if subtitles:
        en_subs = [s for s in subtitles if "en" in str(s.get("language", "")).lower()]
        sub = (en_subs or subtitles)[0]
        subtitle_url = sub.get("url")

    # All available qualities
    available_qualities = sorted(
        [{"resolution": f"{d.get('resolution')}p", "size": format_bytes(int(d.get('size', 0) or 0))} for d in downloads],
        key=lambda x: int(x["resolution"].replace("p", "") or 0),
        reverse=True,
    )

    response_data = {
        "status": "success",
        "data": {
            "title": title,
            "year": year,
            "type": "series" if is_series else "movie",
            "genre": genre,
            "imdb_rating": rating,
            "quality": f"{resolution}p" if resolution else None,
            "format": fmt,
            "file_size": file_size,
            "download_url": download_url,
            "subtitle_url": subtitle_url,
            "available_qualities": available_qualities,
        },
    }

    if is_series and s and e:
        response_data["data"]["season"] = s
        response_data["data"]["episode"] = e

    return JSONResponse(content=response_data)


# ─── ERROR HANDLERS ───────────────────────────────────────────────────────────

@app.exception_handler(404)
def not_found(request, exc):
    return JSONResponse(status_code=404, content={"status": "error", "code": 404, "message": "Endpoint not found."})


@app.exception_handler(500)
def server_error(request, exc):
    return JSONResponse(status_code=500, content={"status": "error", "code": 500, "message": "Internal server error."})
