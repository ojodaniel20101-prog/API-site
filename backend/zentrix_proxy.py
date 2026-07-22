#!/usr/bin/env python3
"""
Zentrix Proxy - Direct HakunaYMatata/GZMoviebox Streaming API Module
========================================================================
Reverse engineered from the HakunaYMatata (MovieBox) streaming platform.
Connects directly to h5-api.aoneroom.com without any middleman.

No API key required - uses automatic Bearer token authentication.

Usage:
    from zentrix_proxy import zentrix_search, zentrix_media, zentrix_details

    # Search for movies/TV shows
    results = zentrix_search("inception")

    # Get stream URLs and subtitles  
    downloads, captions = zentrix_media(subject_id, detail_path)

    # Get full movie/show details
    details = zentrix_details(detail_path)

Stream URLs require Referer: https://videodownloader.site/ header
"""

__version__ = "1.0.0"
__author__ = "Zentrix Tech"

import re
import json
from typing import Optional, Tuple, List, Dict, Any
from dataclasses import dataclass

# Use requests library as specified
try:
    import requests
except ImportError:
    raise ImportError("requests is required. Install with: pip install requests")


# =============================================================================
# Configuration
# =============================================================================

API_HOST = "https://h5-api.aoneroom.com"
API_BASE = f"{API_HOST}"
REFERER = "https://videodownloader.site/"

# API Endpoints
ENDPOINT_SEARCH = "/wefeed-h5api-bff/subject/search"
ENDPOINT_SEARCH_SUGGEST = "/wefeed-h5api-bff/subject/search-suggest"
ENDPOINT_DETAIL = "/wefeed-h5api-bff/detail"
ENDPOINT_DOWNLOAD = "/wefeed-h5api-bff/subject/download"
ENDPOINT_STREAM = "/wefeed-h5-bff/web/subject/play"
ENDPOINT_APP_INFO = "/wefeed-h5-bff/app/get-latest-app-pkgs"
ENDPOINT_HOME = "/wefeed-h5api-bff/home"

# Valid detail path pattern
VALID_DETAIL_PATH_PATTERN = re.compile(r"^[\w-]+-\w{9,13}$")

# Subject type mapping
SUBJECT_TYPES = {
    "movies": "Movies",
    "tv_series": "TV Series", 
    "anime": "Anime",
    "music": "Music",
    "education": "Education"
}


# =============================================================================
# Data Models
# =============================================================================

@dataclass
class UserInfo:
    """User authentication info extracted from x-user header"""
    token: str
    userId: str
    userType: int
    appType: int


@dataclass 
class MediaDownload:
    """Downloadable/streamable media file"""
    id: str
    url: str
    resolution: int
    size: int
    definition: str = ""  # e.g. "360P", "720P"
    format: str = ""       # e.g. "mp4"

    def __repr__(self):
        return f"MediaDownload({self.resolution}P - {self.definition})"

    def get_stream_headers(self) -> Dict[str, str]:
        """Get headers needed to stream this URL without 403 errors"""
        return {"Referer": REFERER}


@dataclass
class MediaCaption:
    """Subtitle/caption file"""
    id: str
    lan: str          # Language code, e.g. "en", "es"
    lanName: str      # Full language name, e.g. "English", "Spanish"  
    url: str
    size: int
    delay: int = 0

    def __repr__(self):
        return f"MediaCaption({self.lanName})"

    def get_download_headers(self) -> Dict[str, str]:
        """Get headers needed to download this subtitle without 403 errors"""
        return {"Referer": REFERER}


@dataclass
class SearchResultItem:
    """A single search result item"""
    subjectId: str
    subjectType: str      # "movies", "tv_series", "anime", "music", "education"
    title: str
    description: str
    releaseDate: str
    duration: int
    genre: list
    cover: str
    imdbRatingValue: float
    detailPath: str
    hasResource: bool
    subtitles: list

    @property
    def is_movie(self) -> bool:
        return self.subjectType == "movies"

    @property
    def is_tv_series(self) -> bool:
        return self.subjectType == "tv_series"

    @property
    def page_url(self) -> str:
        return f"{API_HOST}/movies/{self.detailPath}?id={self.subjectId}"


# =============================================================================
# Internal Session Management
# =============================================================================

class _ZentrixSession:
    """
    Internal session manager that handles:
    - Authentication token acquisition via search-suggest endpoint
    - Bearer token management in Authorization header  
    - Cookie management (token, account cookies)
    - Request signing with proper headers
    """

    def __init__(self):
        self.session = requests.Session()
        self.user_info: Optional[UserInfo] = None
        self._auth_initialized = False

        # Set default headers matching the mobile app
        self.session.headers.update({
            "X-Client-Info": json.dumps({"timezone": "Africa/Nairobi"}),
            "Accept-Language": "en-US,en;q=0.5",
            "Accept": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64; rv:137.0) "
                "Gecko/20100101 Firefox/137.0"
            ),
            "Referer": REFERER,
        })

    def _ensure_auth(self):
        """Ensure authentication is established before making API calls"""
        if not self._auth_initialized:
            self._authenticate()
            self._auth_initialized = True

    def _authenticate(self):
        """
        Authenticate with the HakunaYMatata API.

        Auth Flow:
        1. POST to /wefeed-h5api-bff/subject/search-suggest with dummy query
        2. Extract x-user response header containing JSON user info with token
        3. Set Authorization: Bearer {token} for all subsequent requests
        4. The response also sets token/account cookies automatically
        """
        try:
            # Step 1: Fetch user info via search-suggest endpoint
            resp = self.session.post(
                f"{API_BASE}{ENDPOINT_SEARCH_SUGGEST}",
                json={"keyword": "avatar", "perPage": 0},
                headers={"Referer": REFERER},
                timeout=30
            )
            resp.raise_for_status()

            # Extract user info from x-user header
            x_user = resp.headers.get("x-user")
            if x_user:
                user_data = json.loads(x_user)
                self.user_info = UserInfo(
                    token=user_data["token"],
                    userId=user_data["userId"],
                    userType=user_data.get("userType", 0),
                    appType=user_data.get("appType", 0)
                )

                # Set authorization header for all subsequent requests
                self.session.headers.update({
                    "Authorization": f"Bearer {self.user_info.token}"
                })

            # Step 2: Fetch app info to set essential cookies
            self._fetch_app_info()

        except Exception as e:
            # Auth failure is non-critical for some endpoints
            pass

    def _fetch_app_info(self):
        """Fetch app info which sets essential cookies for downloads"""
        try:
            self.session.get(
                f"{API_BASE}{ENDPOINT_APP_INFO}",
                params={"app_name": "moviebox"},
                timeout=30
            )
        except Exception:
            pass  # Non-critical

    def request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """Make an authenticated API request and return the data field"""
        self._ensure_auth()

        url = f"{API_BASE}{endpoint}"
        kwargs.setdefault("timeout", 30)

        resp = self.session.request(method, url, **kwargs)
        resp.raise_for_status()

        # Validate JSON response
        content_type = resp.headers.get("content-type", "")
        if "application/json" not in content_type:
            raise RuntimeError(f"Unexpected content type: {content_type}")

        data = resp.json()

        # API uses {code: 0, message: "ok", data: ...} envelope format
        if data.get("code") == 0 and data.get("message") == "ok":
            return data.get("data", {})
        else:
            raise RuntimeError(
                f"API Error: {data.get('message', 'Unknown error')} "
                f"(code: {data.get('code', 'unknown')})"
            )

    def get(self, endpoint: str, params: dict = None) -> Dict[str, Any]:
        """Make a GET request"""
        return self.request("GET", endpoint, params=params)

    def post(self, endpoint: str, json_data: dict = None) -> Dict[str, Any]:
        """Make a POST request"""
        return self.request("POST", endpoint, json=json_data)


# Global session instance (lazy-initialized on first use)
_session = None

def _get_session() -> _ZentrixSession:
    """Get or create the global session"""
    global _session
    if _session is None:
        _session = _ZentrixSession()
    return _session


# =============================================================================
# Public API Functions
# =============================================================================

def zentrix_search(query: str, page: int = 1, subject_type: str = "ALL", 
                   per_page: int = 24) -> Dict[str, Any]:
    """
    Search for movies, TV shows, anime, music, and educational content.

    Args:
        query: Search query string (movie/series title)
        page: Page number (default: 1)
        subject_type: Filter by type - "ALL", "MOVIE", "TV", "ANIME", 
                      "MUSIC", "EDUCATION" (default: "ALL")
        per_page: Results per page, max ~24 (default: 24)

    Returns:
        Dict with:
        - items: List of SearchResultItem objects with metadata
        - pager: Pagination info {hasMore, nextPage, page, perPage, totalCount}
        - raw: Full API response for advanced use

    Example:
        results = zentrix_search("Avengers", page=1)
        for item in results["items"]:
            print(f"{item.title} ({item.imdbRatingValue}★) - {item.subjectType}")
            print(f"  ID: {item.subjectId} | Path: {item.detailPath}")
    """
    subject_type_map = {
        "ALL": None,
        "MOVIE": 1,
        "TV": 2,
        "ANIME": 3,
        "MUSIC": 4,
        "EDUCATION": 5,
    }

    st = subject_type_map.get(subject_type.upper())

    # Search uses POST with "keyword" parameter
    payload = {
        "keyword": query,
        "page": page,
        "pageSize": per_page,
    }
    if st:
        payload["subjectType"] = st

    data = _get_session().post(ENDPOINT_SEARCH, json_data=payload)

    # Parse items into SearchResultItem objects
    items = []
    for item_data in data.get("items", []):
        try:
            # Handle genre as string or list
            genre = item_data.get("genre", [])
            if isinstance(genre, str):
                genre = [g.strip() for g in genre.split(",") if g.strip()]

            # Handle subtitles as string or list  
            subtitles = item_data.get("subtitles", [])
            if isinstance(subtitles, str):
                subtitles = [s.strip() for s in subtitles.split(",") if s.strip()]

            # Get cover URL
            cover = ""
            cover_data = item_data.get("cover")
            if isinstance(cover_data, dict):
                cover = cover_data.get("url", "")
            elif isinstance(cover_data, str):
                cover = cover_data

            # Map subjectType numeric values
            stype = item_data.get("subjectType", "")
            if isinstance(stype, int):
                type_map = {1: "movies", 2: "tv_series", 3: "anime", 4: "music", 5: "education"}
                stype = type_map.get(stype, str(stype))

            item = SearchResultItem(
                subjectId=str(item_data.get("subjectId", "")),
                subjectType=stype,
                title=item_data.get("title", ""),
                description=item_data.get("description", ""),
                releaseDate=str(item_data.get("releaseDate", "")),
                duration=item_data.get("duration", 0),
                genre=genre,
                cover=cover,
                imdbRatingValue=item_data.get("imdbRatingValue", 0.0),
                detailPath=item_data.get("detailPath", ""),
                hasResource=item_data.get("hasResource", False),
                subtitles=subtitles,
            )
            items.append(item)
        except Exception:
            continue

    # Parse pager
    pager_data = data.get("pager", {})
    pager = {
        "hasMore": pager_data.get("hasMore", False),
        "nextPage": int(pager_data.get("nextPage", page)) if pager_data.get("nextPage") else page,
        "page": int(pager_data.get("page", page)) if pager_data.get("page") else page,
        "perPage": int(pager_data.get("perPage", per_page)) if pager_data.get("perPage") else per_page,
        "totalCount": int(pager_data.get("totalCount", 0)) if pager_data.get("totalCount") else 0,
    }

    return {
        "items": items,
        "pager": pager,
        "raw": data
    }


def zentrix_media(subject_id: str, detail_path: str, 
                  season: int = 0, episode: int = 0) -> Tuple[List[MediaDownload], List[MediaCaption]]:
    """
    Get stream URLs and subtitles for a specific movie or TV episode.

    IMPORTANT: Stream URLs require Referer: https://videodownloader.site/ 
    header to avoid 403 errors. Use MediaDownload.get_stream_headers() 
    to get the correct headers.

    Args:
        subject_id: The unique subject ID from search results
        detail_path: The detail path from search results (e.g. "titanic-kGoZgiDdff")
        season: Season number for TV series (default: 0 for movies/single items)
        episode: Episode number for TV series (default: 0 for movies/single items)

    Returns:
        Tuple of (downloads_list, captions_list)
        - downloads_list: List of MediaDownload objects sorted by resolution (best first)
        - captions_list: List of MediaCaption objects with subtitle URLs

    Example:
        downloads, captions = zentrix_media("12345678901234567", "titanic-kGoZgiDdff")

        # Get best quality stream URL
        best = downloads[0]  # Already sorted by resolution (highest first)
        print(f"Best stream: {best.url}")

        # Stream with correct headers (avoids 403)
        headers = best.get_stream_headers()
        resp = requests.get(best.url, headers=headers, stream=True)

        # Get English subtitle
        en_sub = next((c for c in captions if c.lan == "en"), None)
        if en_sub:
            print(f"Subtitle: {en_sub.url}")
    """
    params = {
        "subjectId": subject_id,
        "se": season,
        "ep": episode,
        "detailPath": detail_path,
    }

    data = _get_session().get(ENDPOINT_DOWNLOAD, params=params)

    # Parse downloads (video files)
    downloads = []
    for dl_data in data.get("downloads", []):
        try:
            url = str(dl_data.get("url", ""))
            # Extract file extension from URL
            ext = "mp4"
            if "." in url.split("?")[0]:
                ext = url.split("?")[0].split(".")[-1]

            dl = MediaDownload(
                id=dl_data.get("id", ""),
                url=url,
                resolution=dl_data.get("resolution", 0),
                size=dl_data.get("size", 0),
                definition=dl_data.get("definition", f"{dl_data.get('resolution', 0)}P"),
                format=ext,
            )
            downloads.append(dl)
        except Exception:
            continue

    # Sort by resolution (highest first)
    downloads.sort(key=lambda x: x.resolution, reverse=True)

    # Parse captions/subtitles
    captions = []
    for cap_data in data.get("captions", []):
        try:
            cap = MediaCaption(
                id=cap_data.get("id", ""),
                lan=cap_data.get("lan", ""),
                lanName=cap_data.get("lanName", ""),
                url=str(cap_data.get("url", "")),
                size=cap_data.get("size", 0),
                delay=cap_data.get("delay", 0),
            )
            captions.append(cap)
        except Exception:
            continue

    return downloads, captions


def zentrix_details(detail_path: str) -> Dict[str, Any]:
    """
    Get full details about a movie or TV show.

    Args:
        detail_path: The detail path (e.g. "titanic-kGoZgiDdff")

    Returns:
        Dict containing full movie/show details:
        - title, description, releaseDate, duration
        - genre, countryName, imdbRatingValue
        - cover image URL, trailer info
        - episode list for TV series
        - stafflist (cast/crew info)
        - raw: Full API response

    Example:
        details = zentrix_details("titanic-kGoZgiDdff")
        print(f"Title: {details['title']}")
        print(f"Rating: {details.get('imdbRatingValue', 'N/A')}")
        print(f"Genres: {', '.join(details.get('genre', []))}")

        # Get episode list for TV series
        if details.get("episodeList"):
            for ep in details["episodeList"]:
                print(f"S{ep['season']}E{ep['episode']}: {ep.get('title', '')}")
    """
    params = {
        "detailPath": detail_path,
    }

    data = _get_session().get(ENDPOINT_DETAIL, params=params)

    # Normalize subject type
    stype = data.get("subjectType", "")
    if isinstance(stype, int):
        type_map = {1: "movies", 2: "tv_series", 3: "anime", 4: "music", 5: "education"}
        stype = type_map.get(stype, str(stype))

    # Process genre
    genre = data.get("genre", [])
    if isinstance(genre, str):
        genre = [g.strip() for g in genre.split(",") if g.strip()]

    # Process subtitles
    subtitles = data.get("subtitles", [])
    if isinstance(subtitles, str):
        subtitles = [s.strip() for s in subtitles.split(",") if s.strip()]

    # Get cover URL
    cover = ""
    cover_data = data.get("cover")
    if isinstance(cover_data, dict):
        cover = cover_data.get("url", "")
    elif isinstance(cover_data, str):
        cover = cover_data

    result = {
        "subjectId": str(data.get("subjectId", "")),
        "subjectType": stype,
        "title": data.get("title", ""),
        "description": data.get("description", ""),
        "releaseDate": str(data.get("releaseDate", "")),
        "duration": data.get("duration", 0),
        "genre": genre,
        "countryName": data.get("countryName", ""),
        "imdbRatingValue": data.get("imdbRatingValue", 0.0),
        "cover": cover,
        "detailPath": data.get("detailPath", detail_path),
        "stafflist": data.get("stafflist", []),
        "hasResource": data.get("hasResource", False),
        "subtitles": subtitles,
        "episodeList": data.get("episodeList", []),
        "season": data.get("season", 0),
        "totalSeasons": data.get("totalSeasons", 0),
        "raw": data,
    }

    # Process trailer info
    trailer = data.get("trailer")
    if trailer and isinstance(trailer, dict):
        video_url = ""
        video_data = trailer.get("videoAddress")
        if isinstance(video_data, dict):
            video_url = video_data.get("url", "")

        trailer_cover = ""
        cover_data = trailer.get("cover")
        if isinstance(cover_data, dict):
            trailer_cover = cover_data.get("url", "")

        result["trailer"] = {
            "videoUrl": video_url,
            "coverUrl": trailer_cover,
        }

    return result


# =============================================================================
# Stream Helper Functions
# =============================================================================

def zentrix_stream_headers() -> Dict[str, str]:
    """
    Get the headers required to stream/download media without 403 errors.

    The HakunaYMatata CDN (hakunaymatata.com) checks the Referer header
    and blocks requests without the proper referrer.

    Returns:
        Dict with required headers for streaming

    Example:
        downloads, captions = zentrix_media(subject_id, detail_path)
        headers = zentrix_stream_headers()

        # Stream the video
        resp = requests.get(downloads[0].url, headers=headers, stream=True)
        for chunk in resp.iter_content(8192):
            # Process chunk...
            pass

        # Download subtitle
        sub_resp = requests.get(captions[0].url, headers=headers)
        srt_content = sub_resp.text
    """
    return {"Referer": REFERER}


def zentrix_stream(subject_id: str, detail_path: str,
                   season: int = 0, episode: int = 0) -> List[Dict[str, Any]]:
    """
    Get streaming-optimized URLs via the web play endpoint.

    Uses the /wefeed-h5-bff/web/subject/play endpoint which returns
    optimized stream formats with proper auth signing.

    Args:
        subject_id: The unique subject ID
        detail_path: The detail path
        season: Season number (default: 0)
        episode: Episode number (default: 0)

    Returns:
        List of stream objects with url, format, resolutions, codecName
    """
    params = {
        "subjectId": subject_id,
        "se": season,
        "ep": episode,
    }

    # This endpoint requires a specific referer
    headers = {"Referer": f"{API_HOST}/movies/{detail_path}"}

    session = _get_session()
    session._ensure_auth()

    url = f"{API_BASE}{ENDPOINT_STREAM}"
    resp = session.session.get(url, params=params, headers=headers, timeout=30)
    resp.raise_for_status()

    data = resp.json()
    if data.get("code") == 0 and data.get("message") == "ok":
        return data.get("data", {}).get("streams", [])

    return []


def zentrix_home() -> Dict[str, Any]:
    """
    Get homepage content including trending, featured movies, and TV shows.

    Returns:
        Dict with homepage content including operatingList categories
    """
    params = {"host": "moviebox.ph"}
    return _get_session().get(ENDPOINT_HOME, params=params)


def zentrix_trending(page: int = 1, per_page: int = 24) -> Dict[str, Any]:
    """
    Get trending movies and TV shows.

    Args:
        page: Page number (default: 1)
        per_page: Results per page (default: 24)

    Returns:
        Dict with trending items and pager info
    """
    params = {"page": page, "perPage": per_page}
    return _get_session().get("/wefeed-h5api-bff/subject/trending", params=params)


def zentrix_popular_searches() -> List[str]:
    """
    Get popular/search-trending keywords.

    Returns:
        List of popular search terms
    """
    try:
        data = _get_session().get("/wefeed-h5api-bff/subject/popular-searches")
        if isinstance(data, list):
            return data
        elif isinstance(data, dict):
            return data.get("items", [])
    except Exception:
        pass
    return []


# =============================================================================
# Session Management
# =============================================================================

def zentrix_reset_session():
    """Reset the API session. Call this if you encounter auth errors."""
    global _session
    _session = _ZentrixSession()


# =============================================================================
# Module Test
# =============================================================================

if __name__ == "__main__":
    print("Zentrix Proxy v1.0.0 - HakunaYMatata/GZMoviebox Direct API")
    print("=" * 60)

    # Test search
    print("\n[1] Testing search for 'inception'...")
    results = zentrix_search("inception", page=1, per_page=5)
    print(f"Found {len(results['items'])} items (total: {results['pager']['totalCount']})")

    if results["items"]:
        item = results["items"][0]
        print(f"\nFirst result:")
        print(f"  Title: {item.title}")
        print(f"  Type: {item.subjectType}")
        print(f"  ID: {item.subjectId}")
        print(f"  Path: {item.detailPath}")
        print(f"  Rating: {item.imdbRatingValue}")

        # Test details
        print(f"\n[2] Testing details...")
        details = zentrix_details(item.detailPath)
        print(f"  Title: {details.get('title', 'N/A')}")
        print(f"  Duration: {details.get('duration', 0)} mins")
        print(f"  Genres: {details.get('genre', [])}")

        # Test media
        print(f"\n[3] Testing media (downloads + captions)...")
        downloads, captions = zentrix_media(item.subjectId, item.detailPath)
        print(f"  Downloads: {len(downloads)}")
        for dl in downloads[:3]:
            print(f"    - {dl.resolution}P ({dl.definition}): {dl.url[:50]}...")
        print(f"  Captions: {len(captions)}")
        for cap in captions[:3]:
            print(f"    - {cap.lanName} ({cap.lan}): {cap.url[:50]}...")

        # Verify stream URL works
        if downloads:
            print(f"\n[4] Verifying stream URL accessibility...")
            headers = downloads[0].get_stream_headers()
            test_resp = requests.head(downloads[0].url, headers=headers, timeout=15, allow_redirects=True)
            print(f"  HEAD Status: {test_resp.status_code}")
            if test_resp.status_code == 200:
                print(f"  ✓ Stream URL is accessible!")
            else:
                print(f"  ⚠ Stream URL returned {test_resp.status_code}")

    print("\n" + "=" * 60)
    print("✓ Zentrix Proxy test complete!")
    print("=" * 60)
