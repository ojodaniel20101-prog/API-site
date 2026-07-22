#!/usr/bin/env python3
"""
AnimeHeaven Scraper Module
==========================
Web API adapter for animeheaven.me.
No Selenium, no CLI, no disk downloads — pure HTTP scraping for Railway.
"""

import re
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

# ---------------------------------------------------------------------------
BASE_URL = "https://animeheaven.me"
SEARCH_URL = f"{BASE_URL}/search.php"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

REQUEST_TIMEOUT = 30
RETRY_ATTEMPTS = 3
RETRY_DELAY = 5
VIDEO_SUBDOMAINS = ["rk", "fi", "cc", "la", "ny"]


class AnimeHeavenError(Exception):
    """Base exception for AnimeHeaven scraper."""
    pass


class SearchNotFoundError(AnimeHeavenError):
    pass


class EpisodeNotFoundError(AnimeHeavenError):
    pass


class VideoSourceNotFoundError(AnimeHeavenError):
    pass


def _get_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def search_anime(query: str) -> list[dict]:
    """
    Search for anime on AnimeHeaven.
    Returns list of dicts: {title, url, id}
    """
    session = _get_session()
    last_error = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            resp = session.get(SEARCH_URL, params={"s": query}, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            last_error = e
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_DELAY)
            else:
                raise AnimeHeavenError(f"Search failed after {RETRY_ATTEMPTS} attempts: {last_error}")

    soup = BeautifulSoup(resp.content, "lxml")
    results = []
    for link in soup.find_all("a", href=re.compile(r"anime\.php\?")):
        title = link.get_text(strip=True)
        href = link.get("href", "")
        if not title or not href:
            continue
        full_url = urljoin(BASE_URL, href)
        anime_id = href.split("?")[-1] if "?" in href else ""
        results.append({"title": title, "url": full_url, "id": anime_id})

    # Deduplicate by ID
    seen = set()
    unique = []
    for r in results:
        if r["id"] and r["id"] not in seen:
            seen.add(r["id"])
            unique.append(r)

    if not unique:
        raise SearchNotFoundError(f'No results found for "{query}"')
    return unique


def get_episode_list(anime_url: str, anime_id: str) -> list[dict]:
    """
    Extract episode list from anime page.
    Returns list of dicts: {number, title, ep_id, watch_url}
    """
    session = _get_session()
    resp = session.get(anime_url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()

    html = resp.text
    soup = BeautifulSoup(resp.content, "lxml")
    episodes = []

    # Method 1: Extract from <a> tags with gate.php and id attributes
    for link in soup.find_all("a", href="gate.php"):
        ep_id = link.get("id", "")
        if not ep_id:
            continue
        divs = link.find_all("div")
        for div in divs:
            text = div.get_text(strip=True)
            if re.match(r"^[0-9]+(?:\.[0-9]+)?$", text):
                ep_num = text
                episodes.append({
                    "number": ep_num,
                    "title": f"Episode {ep_num}",
                    "ep_id": ep_id,
                    "watch_url": f"{BASE_URL}/watch.php?{anime_id}&e={ep_num}"
                })
                break

    # Method 2: Try to find maxep and construct range
    if not episodes:
        maxep_match = re.search(r"var\s+maxep\s*=\s*([0-9]+)", html)
        if maxep_match:
            max_ep = int(maxep_match.group(1))
            for i in range(1, max_ep + 1):
                ep_num = str(i)
                episodes.append({
                    "number": ep_num,
                    "title": f"Episode {ep_num}",
                    "ep_id": "",
                    "watch_url": f"{BASE_URL}/watch.php?{anime_id}&e={ep_num}"
                })

    # Deduplicate and sort
    seen = set()
    unique_eps = []
    for ep in episodes:
        if ep["number"] not in seen:
            seen.add(ep["number"])
            unique_eps.append(ep)

    def sort_key(ep):
        try:
            return float(ep["number"])
        except ValueError:
            return 0

    unique_eps.sort(key=sort_key)
    return unique_eps


def extract_video_direct(ep_id: str, subdomains: list[str] | None = None) -> str | None:
    """
    Attempt to construct and verify direct video URL from episode ID.
    Returns video URL if a working CDN is found, else None.
    """
    if not ep_id:
        return None

    subdomains = subdomains or VIDEO_SUBDOMAINS
    test_session = requests.Session()
    test_session.headers.update({
        "User-Agent": HEADERS["User-Agent"],
        "Referer": f"{BASE_URL}/",
    })

    for sub in subdomains:
        url = f"https://{sub}.animeheaven.me/video.mp4?{ep_id}&d"
        try:
            resp = test_session.head(url, timeout=10, allow_redirects=True)
            if resp.status_code == 200:
                ct = resp.headers.get("Content-Type", "")
                cl = resp.headers.get("Content-Length", "0")
                if "video" in ct or (cl and int(cl) > 100000):
                    return url
        except Exception:
            continue

    # Also try without &d flag
    for sub in subdomains:
        url = f"https://{sub}.animeheaven.me/video.mp4?{ep_id}"
        try:
            resp = test_session.head(url, timeout=10, allow_redirects=True)
            if resp.status_code == 200:
                ct = resp.headers.get("Content-Type", "")
                if "video" in ct:
                    return url
        except Exception:
            continue

    return None


def extract_video_via_gate(anime_id: str, ep_number: str, ep_id: str = "") -> str:
    """
    Try gate.php cookie method to extract video URL.
    Returns video URL or raises VideoSourceNotFoundError.
    """
    gate_session = requests.Session()
    gate_session.headers.update(HEADERS)
    if ep_id:
        gate_session.cookies.set("key", ep_id, domain="animeheaven.me")
    try:
        resp = gate_session.get(
            f"{BASE_URL}/gate.php", timeout=20, allow_redirects=True
        )
        if resp.status_code == 200:
            match = re.search(
                r'(https?://[a-z0-9]+\.animeheaven\.me/video\.mp4\?[^"\'\s<>]+)',
                resp.text
            )
            if match:
                return match.group(1)
    except Exception:
        pass

    raise VideoSourceNotFoundError(
        "Could not extract video source via gate.php."
    )


def extract_video_source(anime_id: str, ep_number: str, ep_id: str = "") -> tuple[str, str]:
    """
    Extract video source URL for an episode.
    Returns (video_url, source_type) where source_type is 'mp4' or 'm3u8'.
    """
    if ep_id:
        direct_url = extract_video_direct(ep_id)
        if direct_url:
            return direct_url, "mp4"

    # Fallback: gate.php cookie method
    gate_url = extract_video_via_gate(anime_id, ep_number, ep_id)
    if gate_url:
        return gate_url, "mp4" if ".m3u8" not in gate_url else "m3u8"

    raise VideoSourceNotFoundError(
        "Could not extract video source. Try a different episode or check if the site layout changed."
    )
