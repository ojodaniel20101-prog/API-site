#!/usr/bin/env python3
"""
Zentrix API v1.7.0
==================
FastAPI REST wrapper powered by zentrix_proxy.py (direct HakunaYMatata API).
No middleman — connects directly to h5-api.aoneroom.com.

Endpoints:
  GET  /health                       -> Health check
  GET  /api/search                   -> Search movies/TV/anime
  GET  /api/sources                  -> Get stream URLs + subtitles
  GET  /api/details                  -> Get full movie/show details
  GET  /api/trailer                  -> Get YouTube trailer
  GET  /api/anime/search             -> Search anime
  GET  /api/anime/episodes           -> Get episode list
  GET  /api/anime/stream             -> Get anime stream URL
  GET  /api/anime/download           -> Get anime download URL
  GET  /api/image/generate           -> Text-to-image generation
  GET  /api/image/edit               -> Edit image with AI prompt
  GET  /api/image/transform          -> Transform image (img2img)
  GET  /api/image/transform/result   -> Poll transform result by task_id
  GET  /api/image/blend              -> Blend up to 4 images
  GET  /zentrix/stream               -> Proxy video stream
  GET  /zentrix/download             -> Proxy video download
  GET  /zentrix/encode               -> Encode CDN URL to token

Auth:  x-api-key header or ?apikey= query param
"""

from __future__ import annotations

import os
import base64
import logging
import httpx
import urllib.parse
from zentrix_proxy import _get_session as _zx_session
from contextlib import asynccontextmanager
from typing import Optional

logger = logging.getLogger("zentrix")

from fastapi import FastAPI, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse
from pydantic import BaseModel

# -- Import from zentrix_proxy (direct API, no septorch) -------------------------
from zentrix_proxy import (
    zentrix_media,
    zentrix_details,
    zentrix_reset_session,
)

# -- Import hakuna search (uses gzmovieboxapi.septorch.tech) ----------------------
from hakuna_v5 import hakuna_search as _hakuna_search

# -- Import AnimeHeaven scraper -------------------------------------------------
from animeheaven import (
    search_anime as ah_search,
    get_episode_list as ah_episodes,
    extract_video_source as ah_extract,
    AnimeHeavenError,
)

def zentrix_search(query: str, page: int = 1, subject_type: str = "ALL", per_page: int = 24):
    """Wrapper around hakuna_search that returns zentrix_proxy-compatible format."""
    import requests as _req
    type_map = {"ALL": None, "MOVIE": "movie", "TV": "tv", "ANIME": "anime"}
    items_raw, pager = _hakuna_search(query=query, page=page, per_page=per_page)
    items = []
    for r in items_raw:
        cover = r.get("cover", {})
        items.append({
            "subjectId": r.get("subjectId") or r.get("id", ""),
            "title": r.get("title", ""),
            "subjectType": r.get("subjectType", ""),
            "detailPath": r.get("detailPath", ""),
            "releaseDate": r.get("releaseDate", ""),
            "imdbRatingValue": r.get("imdbRatingValue", 0),
            "hasResource": r.get("hasResource", False),
            "cover": cover.get("url", "") if isinstance(cover, dict) else cover,
            "genre": r.get("genre", []),
            "subtitles": r.get("subtitles", []),
        })
    has_more = pager.get("hasMore", False) if pager else False
    return {"items": items, "pager": {"page": page, "hasMore": has_more}}

# ================================================================================
# CONFIGURATION
# ================================================================================

# Support both API_KEY (single) and ZENTRIX_API_KEYS (comma-separated, legacy)
_API_KEY_SINGLE = os.getenv("API_KEY", "")
_API_KEY_RAW = os.getenv("ZENTRIX_API_KEYS", "")
_API_KEY_LIST = [k.strip() for k in _API_KEY_RAW.split(",") if k.strip()]
API_KEY = _API_KEY_SINGLE or (_API_KEY_LIST[0] if _API_KEY_LIST else "ZENTRIX")
_VALID_KEYS = {_API_KEY_SINGLE} | set(_API_KEY_LIST)
_VALID_KEYS.discard("")
APP_VERSION = "1.8.0"
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")

CDN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:137.0) Gecko/20100101 Firefox/137.0",
    "Accept": "*/*",
    "Referer": "https://www.hakunaymatata.com/",
    "Origin": "https://www.hakunaymatata.com/",
}


# ================================================================================
# URL HELPER
# ================================================================================

def build_url(path: str, request: Request | None = None) -> str:
    base = BASE_URL
    if not base and request is not None:
        base = str(request.base_url).rstrip("/")
    return f"{base}{path}" if base else path


# ================================================================================
# TOKEN HELPERS
# ================================================================================

def make_token(url: str) -> str:
    return base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")


def decode_token(token: str) -> str | None:
    try:
        return base64.urlsafe_b64decode(token + "==").decode("utf-8")
    except Exception:
        return None


# ================================================================================
# PYDANTIC MODELS
# ================================================================================

class StreamItem(BaseModel):
    resolution: int
    quality: str
    url: str
    size: Optional[int] = None


class SubtitleItem(BaseModel):
    language: str
    languageName: str
    url: str


class HealthResponse(BaseModel):
    status: str
    version: str
    creator: str


# ================================================================================
# FASTAPI APP
# ================================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title="Zentrix API",
    description="Direct streaming API for movies, TV shows, and anime via HakunaYMatata.",
    version=APP_VERSION,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

# -- CORS ------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Length", "Content-Range", "Accept-Ranges"],
)

# Request tracking
import time as _time
from collections import deque
_request_log = deque(maxlen=500)

@app.middleware("http")
async def track_requests(request: Request, call_next):
    start = _time.time()
    response = await call_next(request)
    ms = round((_time.time() - start) * 1000)
    path = request.url.path
    if not path.startswith("/docs") and path != "/openapi.json":
        _request_log.appendleft({
            "path": path,
            "method": request.method,
            "status": response.status_code,
            "ms": ms,
            "ip": request.headers.get("x-real-ip", str(request.client.host) if request.client else "unknown"),
            "time": int(_time.time() * 1000),
        })
    return response


# -- Auth middleware -------------------------------------------------------------
_AUTH_REQUIRED_PREFIXES = ("/api/",)
_AUTH_REQUIRED_EXACT = {"/zentrix/encode"}
_OPEN_PATHS = {"/health"}


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)
    path = request.url.path
    needs_auth = (
        path not in _OPEN_PATHS
        and (
            any(path.startswith(p) for p in _AUTH_REQUIRED_PREFIXES)
            or path in _AUTH_REQUIRED_EXACT
        )
    )
    if needs_auth:
        api_key = (
            request.headers.get("x-api-key")
            or request.query_params.get("apikey")
            or ""
        )
        if api_key not in _VALID_KEYS:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "success": False,
                    "error": "Invalid or missing API key. Use x-api-key header or ?apikey= parameter.",
                },
            )
    return await call_next(request)


# -- Global exception handler ---------------------------------------------------
@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"success": False, "error": "An internal server error occurred."},
    )


# ================================================================================
# ENDPOINTS
# ================================================================================

@app.get("/health", response_model=HealthResponse)
async def health_check():
    return {"status": "ok", "version": APP_VERSION, "creator": "ZENTRIX TECH"}


@app.get("/api/search")
async def search(
    q: str = Query(..., description="Search query"),
    type: str = Query("all", description="Filter: movie, tv, anime, all"),
    page: int = Query(1, ge=1, description="Page number"),
):
    """Search for movies, TV shows, or anime."""
    # Map type to zentrix_proxy subject_type
    type_map = {
        "movie": "MOVIE",
        "tv": "TV",
        "anime": "ANIME",
        "all": "ALL",
    }
    subject_type = type_map.get(type.lower().strip(), "ALL")

    try:
        result = zentrix_search(query=q, page=page, subject_type=subject_type, per_page=24)
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "error": f"Search failed: {str(e)}"},
        )

    items = result.get("items", [])
    pager = result.get("pager", {})

    # Serialize search result dicts
    serialized = []
    for item in items:
        serialized.append({
            "subjectId": item.get("subjectId", ""),
            "title": item.get("title", ""),
            "subjectType": item.get("subjectType", ""),
            "detailPath": item.get("detailPath", ""),
            "releaseDate": item.get("releaseDate", ""),
            "imdbRatingValue": item.get("imdbRatingValue", 0),
            "hasResource": item.get("hasResource", False),
            "poster": item.get("cover", ""),
            "genre": item.get("genre", []),
            "duration": item.get("duration", 0),
            "description": item.get("description", ""),
        })

    return {
        "success": True,
        "creator": "ZENTRIX TECH",
        "results": serialized,
        "page": pager.get("page", page),
        "hasMore": pager.get("hasMore", False),
        "totalCount": pager.get("totalCount", 0),
    }


@app.get("/api/sources")
async def get_sources(
    request: Request,
    q: str = Query(..., description="Movie or show title"),
    type: str = Query("all", description="Filter: movie, tv, anime, all"),
    season: int = Query(0, ge=0, description="Season number (0 for movies)"),
    episode: int = Query(0, ge=0, description="Episode number (0 for movies)"),
):
    """Get stream URLs and subtitles by title. Internally searches and resolves IDs."""
    # Step 1: Search for title
    type_map = {"movie": "MOVIE", "tv": "TV", "anime": "ANIME", "all": "ALL"}
    subject_type = type_map.get(type.lower().strip(), "ALL")
    try:
        result = zentrix_search(query=q, page=1, subject_type=subject_type, per_page=5)
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": f"Search failed: {str(e)}"})
    items = result.get("items", [])
    if not items:
        return JSONResponse(status_code=404, content={"success": False, "error": f"No results found for '{q}'"})
    item = items[0]
    subject_id = item.get("subjectId", "")
    detail_path = item.get("detailPath", "")

    # Step 2: Get streams
    try:
        downloads, captions = zentrix_media(
            subject_id=subject_id,
            detail_path=detail_path,
            season=season,
            episode=episode,
        )
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "error": f"Failed to fetch sources: {str(e)}"},
        )

    # Build streams list
    streams = []
    for dl in downloads:
        url = dl.url
        # Wrap through septorch proxy to bypass CDN 403
        proxied_url = f"https://gzmovieboxapi.septorch.tech/api/proxy?url={urllib.parse.quote(url, safe='')}&apikey=Godszeal"
        token = make_token(proxied_url)
        streams.append({
            "resolution": dl.resolution,
            "quality": f"{dl.resolution}p",
            "stream_url": build_url(f"/zentrix/stream?token={token}", request),
            "download_url": build_url(
                f"/zentrix/download?token={token}&filename=video_{dl.resolution}p.mp4", request
            ),
        })

    # Build subtitles list
    subtitles = []
    for cap in captions:
        subtitles.append({
            "language": cap.lan,
            "languageName": cap.lanName,
            "url": cap.url,
        })

    return {
        "success": True,
        "creator": "ZENTRIX TECH",
        "streams": streams,
        "subtitles": subtitles,
    }


@app.get("/api/details")
async def get_details(
    request: Request,
    q: str = Query(..., description="Movie or show title"),
    type: str = Query("all", description="Filter: movie, tv, anime, all"),
):
    """Get full details by title. Internally searches and resolves IDs."""
    type_map = {"movie": "MOVIE", "tv": "TV", "anime": "ANIME", "all": "ALL"}
    subject_type = type_map.get(type.lower().strip(), "ALL")
    try:
        result = zentrix_search(query=q, page=1, subject_type=subject_type, per_page=5)
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": f"Search failed: {str(e)}"})
    items = result.get("items", [])
    if not items:
        return JSONResponse(status_code=404, content={"success": False, "error": f"No results found for '{q}'"})
    detail_path = items[0].get("detailPath", "")
    try:
        data = zentrix_details(detail_path=detail_path)
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "error": f"Failed to fetch details: {str(e)}"},
        )

    raw = data.get("raw", {})
    subject = raw.get("subject", {})
    raw_trailer = subject.get("trailer", {})
    trailer_url = raw_trailer.get("videoAddress", {}).get("url") if raw_trailer else None
    trailer_cover = raw_trailer.get("cover", {}).get("url") if isinstance(raw_trailer.get("cover"), dict) else raw_trailer.get("cover") if raw_trailer else None

    # Prefer raw.subject for fields, fallback to top-level
    def g(key, fallback=None):
        return subject.get(key) or data.get(key) or fallback

    return {
        "success": True,
        "creator": "ZENTRIX TECH",
        "id": g("subjectId", ""),
        "title": g("title", ""),
        "description": g("description", ""),
        "releaseDate": g("releaseDate", ""),
        "genre": g("genre", []),
        "rating": g("imdbRatingValue", 0),
        "ratingCount": g("imdbRatingCount", 0),
        "country": g("countryName", ""),
        "subjectType": g("subjectType", ""),
        "detailPath": g("detailPath", ""),
        "hasResource": g("hasResource", False),
        "poster": g("cover", {}).get("url", "") if isinstance(g("cover"), dict) else g("cover", ""),
        "backdrop": subject.get("stills", {}).get("url", "") if isinstance(subject.get("stills"), dict) else "",
        "duration": g("duration", 0),
        "totalSeasons": data.get("totalSeasons", 0),
        "episodeList": data.get("episodeList", []),
        "trailer": {
            "url": build_url(f"/zentrix/stream?token={make_token(trailer_url)}", request) if trailer_url else None,
            "raw_url": trailer_url,
            "cover": trailer_cover,
        } if trailer_url else None,
        "cast": [
            {
                "name": s.get("name"),
                "character": s.get("character"),
                "avatar": s.get("avatarUrl") or s.get("avatar"),
            }
            for s in data.get("stafflist", [])[:20]
        ],
    }


# ================================================================================
# PROXY ENDPOINTS
# ================================================================================

@app.get("/zentrix/stream")
async def proxy_stream(
    request: Request,
    token: str = Query(..., description="Base64-encoded CDN URL"),
):
    """Proxy video stream. Supports Range requests for seeking."""
    cdn_url = decode_token(token)
    if cdn_url is None:
        return JSONResponse(status_code=400, content={"success": False, "error": "Invalid token"})

    cdn_headers = dict(CDN_HEADERS)

    range_header = request.headers.get("range")
    if range_header:
        cdn_headers["Range"] = range_header

    # Set correct referer based on CDN domain
    if "aoneroom.com" in cdn_url:
        cdn_headers.update({"Referer": "https://aoneroom.com/", "Origin": "https://aoneroom.com/"})
    elif "hakunaymatata.com" in cdn_url or "hakunamatata.com" in cdn_url:
        cdn_headers.update({"Referer": "https://www.hakunaymatata.com/", "Origin": "https://www.hakunaymatata.com/"})

    import asyncio
    loop = asyncio.get_event_loop()
    zx = _zx_session()
    zx._ensure_auth()
    # Strip expired params and re-fetch fresh URL if needed
    resp_sync = await loop.run_in_executor(
        None, 
        lambda: zx.session.get(cdn_url, headers=cdn_headers, stream=True, timeout=60, allow_redirects=True)
    )
    # If 403, try resetting session and retry once
    if resp_sync.status_code == 403:
        from zentrix_proxy import zentrix_reset_session
        zentrix_reset_session()
        zx = _zx_session()
        zx._ensure_auth()
        resp_sync = await loop.run_in_executor(
            None,
            lambda: zx.session.get(cdn_url, headers=cdn_headers, stream=True, timeout=60, allow_redirects=True)
        )
    resp = resp_sync
    client = None

    response_headers = {
        "Accept-Ranges": "bytes",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges",
        "Content-Type": resp.headers.get("content-type", "video/mp4"),
    }

    for h in ("content-length", "content-range", "accept-ranges", "etag", "last-modified", "cache-control"):
        if h in resp.headers:
            response_headers[h] = resp.headers[h]

    async def byte_generator():
        try:
            for chunk in resp.iter_content(chunk_size=65536):
                yield chunk
        finally:
            resp.close()

    return StreamingResponse(byte_generator(), status_code=resp.status_code, headers=response_headers)


@app.get("/zentrix/download")
async def proxy_download(
    token: str = Query(..., description="Base64-encoded CDN URL"),
    filename: str = Query("video.mp4", description="Download filename"),
):
    """Proxy video download. Forces browser download."""
    cdn_url = decode_token(token)
    if cdn_url is None:
        return JSONResponse(status_code=400, content={"success": False, "error": "Invalid token"})

    client = httpx.AsyncClient(timeout=120, follow_redirects=True)
    req = client.build_request("GET", cdn_url, headers=CDN_HEADERS)
    resp = await client.send(req, stream=True)

    response_headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Access-Control-Allow-Origin": "*",
        "Content-Type": resp.headers.get("content-type", "video/mp4"),
    }
    if "content-length" in resp.headers:
        response_headers["Content-Length"] = resp.headers["content-length"]

    async def byte_generator():
        try:
            for chunk in resp.iter_content(chunk_size=65536):
                yield chunk
        finally:
            resp.close()

    return StreamingResponse(byte_generator(), status_code=resp.status_code, headers=response_headers)


@app.get("/zentrix/encode")
async def encode_url(
    request: Request,
    url: str = Query(..., description="CDN URL to encode"),
):
    """Encode a CDN URL to a proxy token."""
    token = make_token(url)
    return {
        "success": True,
        "creator": "ZENTRIX TECH",
        "token": token,
        "stream_url": build_url(f"/zentrix/stream?token={token}", request),
        "download_url": build_url(f"/zentrix/download?token={token}", request),
    }


# ================================================================================
# ENTRY POINT
# ================================================================================

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8001"))
    host = os.getenv("HOST", "0.0.0.0")
    uvicorn.run("main:app", host=host, port=port, reload=True)

# ================================================================================
# ANIME ENDPOINTS
# ================================================================================

@app.get("/api/anime/search")
async def anime_search(
    q: str = Query(..., description="Anime title to search"),
):
    """Search anime on AnimeHeaven."""
    try:
        results = ah_search(q)
    except AnimeHeavenError as e:
        return JSONResponse(status_code=404, content={"success": False, "error": str(e)})
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": f"Search failed: {str(e)}"})

    return {
        "success": True,
        "creator": "ZENTRIX TECH",
        "results": results,
    }


@app.get("/api/anime/episodes")
async def anime_episodes(
    id: str = Query(..., description="Anime ID from /api/anime/search"),
    url: str = Query(..., description="Anime URL from /api/anime/search"),
):
    """Get episode list for an anime."""
    try:
        episodes = ah_episodes(url, id)
    except AnimeHeavenError as e:
        return JSONResponse(status_code=404, content={"success": False, "error": str(e)})
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": f"Failed to fetch episodes: {str(e)}"})

    return {
        "success": True,
        "creator": "ZENTRIX TECH",
        "episodes": episodes,
    }


def _build_anime_stream_response(
    request: Request,
    anime_id: str,
    ep_number: str,
    ep_id: str,
    title: str = "",
) -> dict:
    """Shared extraction logic for stream and download endpoints."""
    video_url, source_type = ah_extract(anime_id, ep_number, ep_id)
    token = make_token(video_url)
    filename = f"{title}_Episode_{ep_number}.mp4" if title else f"Episode_{ep_number}.mp4"
    return {
        "success": True,
        "creator": "ZENTRIX TECH",
        "title": title,
        "episode": ep_number,
        "source_type": source_type,
        "stream_url": build_url(f"/zentrix/stream?token={token}", request),
        "download_url": build_url(f"/zentrix/download?token={token}&filename={filename}", request),
    }


@app.get("/api/anime/stream")
async def anime_stream(
    request: Request,
    id: str = Query(..., description="Anime ID"),
    ep_number: str = Query(..., description="Episode number"),
    ep_id: str = Query("", description="Episode hash ID (optional, speeds up extraction)"),
    title: str = Query("", description="Anime title (optional, for filename)"),
):
    """Get stream URL for an anime episode (token-proxied for in-browser playback)."""
    try:
        return _build_anime_stream_response(request, id, ep_number, ep_id, title)
    except AnimeHeavenError as e:
        return JSONResponse(status_code=404, content={"success": False, "error": str(e)})
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": f"Stream extraction failed: {str(e)}"})


@app.get("/api/anime/download")
async def anime_download(
    request: Request,
    id: str = Query(..., description="Anime ID"),
    ep_number: str = Query(..., description="Episode number"),
    ep_id: str = Query("", description="Episode hash ID (optional, speeds up extraction)"),
    title: str = Query("", description="Anime title (optional, for filename)"),
):
    """Get download URL for an anime episode (token-proxied)."""
    try:
        return _build_anime_stream_response(request, id, ep_number, ep_id, title)
    except AnimeHeavenError as e:
        return JSONResponse(status_code=404, content={"success": False, "error": str(e)})
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": f"Download extraction failed: {str(e)}"})





async def get_trailer(
    title: str = Query(..., description="Movie or show title"),
    year: str = Query("", description="Release year (optional)"),
):
    """Get YouTube trailer URL for a movie or show."""
    import httpx, os
    yt_key = os.getenv("YOUTUBE_API_KEY", "")
    if not yt_key:
        return JSONResponse(status_code=500, content={"success": False, "error": "YouTube API key not configured"})
    query = f"{title} {year} official trailer".strip()
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={"part": "snippet", "q": query, "type": "video", "maxResults": 1, "videoEmbeddable": "true", "key": yt_key}
        )
    data = resp.json()
    items = data.get("items", [])
    if not items:
        return {"success": False, "error": "No trailer found"}
    video = items[0]
    video_id = video["id"]["videoId"]
    return {
        "success": True,
        "creator": "ZENTRIX TECH",
        "title": video["snippet"]["title"],
        "youtube_id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "embed_url": f"https://www.youtube.com/embed/{video_id}",
        "thumbnail": video["snippet"]["thumbnails"]["high"]["url"],
    }


# ================================================================================
# IMAGE GENERATION ENDPOINTS (Nano Banana)
# ================================================================================

_OMEGA_BASE = "https://omegatech-api.dixonomega.tech"

def _clean_omega(data: dict) -> dict:
    """Strip OmegaTech branding from response and re-brand as Zentrix."""
    remove_keys = {
        "poweredby", "powered_by", "provider", "api", "source",
        "credit", "credits", "author", "by", "attribution",
        "statuscode", "model",
    }
    return {k: v for k, v in data.items() if k.lower() not in remove_keys}


_ZENTRIX_UPLOAD = "https://upload.zentrixtech.name.ng/upload"


async def _rehost_image(image_url: str) -> str:
    """Download image from upstream and re-upload to Zentrix CDN. Returns new URL."""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            dl = await client.get(image_url, follow_redirects=True)
            dl.raise_for_status()
            content_type = dl.headers.get("content-type", "image/webp").split(";")[0].strip()
            ext_map = {
                "image/webp": ".webp", "image/jpeg": ".jpg", "image/jpg": ".jpg",
                "image/png": ".png", "image/gif": ".gif",
            }
            ext = ext_map.get(content_type, ".webp")
            filename = f"zentrix_img{ext}"
            upload = await client.post(
                _ZENTRIX_UPLOAD,
                files={"file": (filename, dl.content, content_type)},
            )
            upload.raise_for_status()
            result = upload.json()
            return result.get("raw_url") or result.get("url") or image_url
    except Exception:
        return image_url  # fallback to original if rehost fails


@app.get("/api/image/generate")
async def image_generate(
    prompt: str = Query(..., description="Text description of the image to generate"),
):
    """Generate an AI image from a text prompt."""
    async with httpx.AsyncClient(timeout=60) as client:
        try:
            resp = await client.get(
                f"{_OMEGA_BASE}/api/ai/nano-banana-pro",
                params={"prompt": prompt},
            )
            data = resp.json()
        except Exception as e:
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    if not resp.is_success:
        return JSONResponse(status_code=resp.status_code, content={"success": False, "error": data.get("error", "Image generation failed")})

    cleaned = _clean_omega(data)
    if "image" in cleaned:
        cleaned["image"] = await _rehost_image(cleaned["image"])
    cleaned["success"] = True
    cleaned["creator"] = "ZENTRIX TECH"
    return cleaned


@app.get("/api/image/edit")
async def image_edit(
    url: str = Query(..., description="URL of the image to edit"),
    prompt: str = Query(..., description="Edit instruction (e.g. 'Add a hat')"),
):
    """Edit an existing image using an AI prompt."""
    async with httpx.AsyncClient(timeout=90) as client:
        # Try nano-banana first, fall back to nano-banana2
        try:
            resp = await client.get(
                f"{_OMEGA_BASE}/api/ai/nano-banana",
                params={"url": url, "prompt": prompt},
            )
            data = resp.json()
        except Exception as e:
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

        if not resp.is_success or data.get("statusCode", 200) >= 400:
            # Fallback to nano-banana2
            try:
                resp = await client.get(
                    f"{_OMEGA_BASE}/api/ai/nano-banana2",
                    params={"image": url, "prompt": prompt},
                )
                data = resp.json()
            except Exception as e:
                return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    if not resp.is_success:
        return JSONResponse(status_code=resp.status_code, content={"success": False, "error": data.get("error", "Image edit failed")})

    cleaned = _clean_omega(data)
    for key in ("image", "image_url"):
        if key in cleaned:
            cleaned[key] = await _rehost_image(cleaned[key])
    cleaned["success"] = True
    cleaned["creator"] = "ZENTRIX TECH"
    return cleaned


@app.get("/api/image/transform")
async def image_transform(
    image: str = Query(..., description="Source image URL"),
    prompt: str = Query("Change the background, keep everything else the same", description="Transformation instruction"),
):
    """Transform an image using AI (img2img). Returns a task_id to poll for result."""
    async with httpx.AsyncClient(timeout=60) as client:
        try:
            resp = await client.get(
                f"{_OMEGA_BASE}/api/ai/nano-banana2",
                params={"image": image, "prompt": prompt},
            )
            data = resp.json()
        except Exception as e:
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    if not resp.is_success:
        return JSONResponse(status_code=resp.status_code, content={"success": False, "error": data.get("error", "Image transform failed")})

    cleaned = _clean_omega(data)
    cleaned["success"] = True
    cleaned["creator"] = "ZENTRIX TECH"
    return cleaned


@app.get("/api/image/transform/result")
async def image_transform_result(
    task_id: str = Query(..., description="Task ID from /api/image/transform"),
    fp: str = Query("", description="Fingerprint from /api/image/transform (optional)"),
):
    """Poll for the result of an image transform job."""
    params = {"task_id": task_id}
    if fp:
        params["fp"] = fp
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(
                f"{_OMEGA_BASE}/api/ai/nano-banana2-result",
                params=params,
            )
            data = resp.json()
        except Exception as e:
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    if not resp.is_success:
        return JSONResponse(status_code=resp.status_code, content={"success": False, "error": data.get("error", "Failed to fetch result")})

    cleaned = _clean_omega(data)
    # Result endpoint uses image_url instead of image
    for key in ("image", "image_url"):
        if key in cleaned:
            cleaned[key] = await _rehost_image(cleaned[key])
    cleaned["success"] = True
    cleaned["creator"] = "ZENTRIX TECH"
    return cleaned


@app.get("/api/image/blend")
async def image_blend(
    image1: str = Query(..., description="First image URL (required)"),
    image2: str = Query("", description="Second image URL (optional)"),
    image3: str = Query("", description="Third image URL (optional)"),
    image4: str = Query("", description="Fourth image URL (optional)"),
    prompt: str = Query("", description="Blending instruction (optional)"),
):
    """Blend up to 4 images together using AI."""
    params: dict = {"image1": image1}
    if image2: params["image2"] = image2
    if image3: params["image3"] = image3
    if image4: params["image4"] = image4
    if prompt: params["prompt"] = prompt

    async with httpx.AsyncClient(timeout=90) as client:
        try:
            resp = await client.get(
                f"{_OMEGA_BASE}/api/ai/nanobana-pro-v3",
                params=params,
            )
            data = resp.json()
        except Exception as e:
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    if not resp.is_success:
        return JSONResponse(status_code=resp.status_code, content={"success": False, "error": data.get("error", "Image blend failed")})

    cleaned = _clean_omega(data)
    if "image" in cleaned:
        cleaned["image"] = await _rehost_image(cleaned["image"])
    cleaned["success"] = True
    cleaned["creator"] = "ZENTRIX TECH"
    return cleaned


@app.get("/admin/stats")
async def admin_stats(request: Request, adminkey: str = Query(...)):
    if adminkey != "ZENTRIX_ADMIN_2024":
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    logs = list(_request_log)
    total = len(logs)
    success = sum(1 for r in logs if r["status"] < 400)
    errors = sum(1 for r in logs if r["status"] >= 400)
    rate = round(success / total * 100) if total else 100
    # Top endpoints
    from collections import Counter
    top = Counter(r["path"] for r in logs).most_common(10)
    # Unique visitors (by IP)
    unique_ips = len(set(r["ip"] for r in logs))
    # Requests by hour
    from datetime import datetime
    hourly = {}
    for r in logs:
        hour = datetime.fromtimestamp(r["time"]/1000).strftime("%H:00")
        hourly[hour] = hourly.get(hour, 0) + 1

    return {
        "total_requests": total,
        "success": success,
        "errors": errors,
        "success_rate": rate,
        "unique_visitors": unique_ips,
        "top_endpoints": [{"path": p, "count": c} for p, c in top],
        "hourly": [{"hour": h, "count": c} for h, c in sorted(hourly.items())],
        "recent": logs[:50],
        "uptime": "running",
        "version": APP_VERSION,
    }
