#!/usr/bin/env python3
"""
HakunaMatata Multi-Mode Downloader
====================================
Three modes: Movies | TV Shows | Anime
Unified Rich terminal UI throughout.

Usage:
    python hakuna_downloader.py
"""

from __future__ import annotations

import os
import re
import sys
import time
import subprocess
from pathlib import Path
from urllib.parse import urlparse, unquote, parse_qs

import requests

# -- curl_cffi for AnimePahe Cloudflare bypass --
from curl_cffi import requests as cffi_requests

# -- Rich --
from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

# -- Hakuna / GZMovie --
GZMOVIE_API_BASE = "https://gzmovieboxapi.septorch.tech"
HAKUNA_CDN_VIDEO = "https://bcdnxw.hakunaymatata.com"
HAKUNA_CDN_SUBTITLE = "https://cacdn.hakunaymatata.com"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

DOWNLOAD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:137.0) Gecko/20100101 Firefox/137.0",
    "Accept": "*/*",
    "Referer": "https://videodownloader.site/",
    "Origin": "https://videodownloader.site/",
}

QUALITY_ORDER = [1080, 720, 480, 360]

# -- AnimePahe / Kwik --
ANIME_PAHE_DOMAINS = ["https://animepahe.com", "https://animepahe.ru", "https://animepahe.org"]
KWIK_BASE = "https://kwik.si"
KWIK_DOMAINS = ["kwink.ai", "kwik.si", "kwik.cx", "kwik.link"]

ANIME_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://animepahe.com/",
}

KWIK_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://animepahe.com/",
}

# Persistent session for all AnimePahe requests
_anime_session = None
_active_anime_domain = None


def _init_anime_session():
    """Initialize and warmup the persistent AnimePahe session using curl_cffi (bypasses Cloudflare)."""
    global _anime_session, _active_anime_domain
    if _anime_session is not None:
        return _anime_session, _active_anime_domain
    for domain in ANIME_PAHE_DOMAINS:
        session = cffi_requests.Session(impersonate="chrome120")
        try:
            session.get(domain, timeout=30)
            time.sleep(1)
            _anime_session = session
            _active_anime_domain = domain
            return session, domain
        except Exception:
            continue
    # Fallback: return session with primary domain (errors will surface later)
    _anime_session = cffi_requests.Session(impersonate="chrome120")
    _active_anime_domain = ANIME_PAHE_DOMAINS[0]
    return _anime_session, _active_anime_domain

console = Console()
# ═══════════════════════════════════════════════════════════════════════════════
# SHARED UI HELPERS
# ═══════════════════════════════════════════════════════════════════════════════


def banner():
    """Launch banner."""
    art = Text()
    lines = [
        "██╗  ██╗ █████╗ ██╗  ██╗██╗   ██╗███╗   ██╗ █████╗ ",
        "██║  ██║██╔══██╗██║  ██║██║   ██║████╗  ██║██╔══██╗",
        "███████║███████║███████║██║   ██║██╔██╗ ██║███████║",
        "██╔══██║██╔══██║██╔══██║██║   ██║██║╚██╗██║██╔══██║",
        "██║  ██║██║  ██║██║  ██║╚██████╔╝██║ ╚████║██║  ██║",
        "╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝╚═╝  ╚═╝",
    ]
    for line in lines:
        art.append(line + "\n", style="bold bright_cyan")
    grp = Group(
        Align.center(art),
        Align.center(Text("MovieBox Downloader", style="bold bright_magenta")),
        Align.center(Text("Movies • TV Shows • Anime", style="dim italic")),
    )
    console.print(
        Panel(
            grp,
            border_style="bright_cyan",
            padding=(1, 2),
            title="[bold bright_green]✨ Welcome[/bold bright_green]",
            subtitle="[dim]v3.1 - Multi-Mode[/dim]",
        )
    )
    console.print()


def spinner(text: str):
    return console.status(f"[bold bright_yellow]{text}[/bold bright_yellow]", spinner="dots")


def fmt_size(size_bytes: int) -> str:
    if size_bytes == 0:
        return "N/A"
    mb = size_bytes / (1024 * 1024)
    gb = mb / 1024
    return f"{gb:.2f} GB" if gb >= 1 else f"{mb:.1f} MB"


def safe_name(name: str) -> str:
    name = re.sub(r"[^\w\s-]", "", name)
    return re.sub(r"\s+", "_", name.strip())


def divider(title: str, color: str = "bright_cyan"):
    console.print(Rule(f"[bold {color}]{title}[/bold {color}]", style=color))
    console.print()
# ═══════════════════════════════════════════════════════════════════════════════
# HAKUNA API FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════


def hakuna_search(query: str, page: int = 1, per_page: int = 24):
    resp = requests.get(
        f"{GZMOVIE_API_BASE}/api/search",
        headers=DEFAULT_HEADERS,
        params={"query": query, "page": page, "per_page": per_page},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "success":
        raise RuntimeError(data.get("error", "Search failed"))
    return data.get("data", {}).get("items", []), data.get("data", {}).get("pager", {})


def hakuna_media(subject_id: str, detail_path: str, season: int = 0, episode: int = 0):
    params = {"subjectId": subject_id, "detailPath": detail_path}
    if season:
        params["season"] = season
    if episode:
        params["episode"] = episode
    resp = requests.get(
        f"{GZMOVIE_API_BASE}/api/media",
        headers=DEFAULT_HEADERS,
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "success":
        raise RuntimeError(data.get("error", "Media fetch failed"))
    downloads_data = data.get("data", {}).get("downloads", {}).get("data", {})
    return downloads_data.get("downloads", []), downloads_data.get("captions", [])


def hakuna_details(subject_id: str):
    resp = requests.get(
        f"{GZMOVIE_API_BASE}/api/item-details",
        headers=DEFAULT_HEADERS,
        params={"subjectId": subject_id},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("data", {})
# ═══════════════════════════════════════════════════════════════════════════════
# ANIME API FUNCTIONS  (AnimePahe + Kwik — adapted from Zentrix)
# ═══════════════════════════════════════════════════════════════════════════════


def decode_packer(script: str) -> str:
    """
    Decode Dean Edwards "eval(function(p,a,c,k,e,d){...})" packed JavaScript.
    Returns the decoded JavaScript source as a string.
    """
    packer_match = re.search(
        r"(?:eval\s*\()?\s*function\s*\(\s*p\s*,\s*a\s*,\s*c\s*,\s*k\s*,\s*e\s*,\s*d\s*\)\s*\{.*?\}\s*\(([^)]+)\)\s*\)?",
        script,
        re.DOTALL,
    )
    if not packer_match:
        raise ValueError("Could not find packer pattern in script")

    args_str = packer_match.group(1)

    # Extract packed string (first argument) - can be single or double quoted
    # Handle escaped quotes inside
    p_match = re.match(r"'((?:[^'\\]|\\.)*)'", args_str) or re.match(
        r'"((?:[^"\\]|\\.)*)"', args_str
    )
    if not p_match:
        raise ValueError("Could not extract packed string")

    p = p_match.group(1).encode().decode("unicode_escape")
    remaining = args_str[p_match.end():].strip()
    if remaining.startswith(","):
        remaining = remaining[1:].strip()

    # Extract radix (a)
    a_match = re.match(r"(\d+)", remaining)
    if not a_match:
        raise ValueError("Could not extract radix")
    radix = int(a_match.group(1))
    remaining = remaining[a_match.end():].strip()
    if remaining.startswith(","):
        remaining = remaining[1:].strip()

    # Extract count (c)
    c_match = re.match(r"(\d+)", remaining)
    if not c_match:
        raise ValueError("Could not extract count")
    count = int(c_match.group(1))
    remaining = remaining[c_match.end():].strip()
    if remaining.startswith(","):
        remaining = remaining[1:].strip()

    # Extract dictionary string: 'word1|word2|word3'.split('|')
    dict_match = None
    for pattern in [
        r"['\"](.*?)['\"]\.split\(['\"]\\\|['\"]\)",
        r"['\"](.*?)['\"]\.split\(['\"]\|['\"]\)",
    ]:
        dict_match = re.match(pattern, remaining)
        if dict_match:
            break

    if not dict_match:
        raise ValueError("Could not extract dictionary string")

    dictionary = dict_match.group(1).split("|")

    # Ensure dictionary has at least 'count' entries
    while len(dictionary) < count:
        dictionary.append("")

    # ── Decode the packed string ──
    def decode_token(match: re.Match) -> str:
        token = match.group(0)
        try:
            idx = int(token, radix)
        except ValueError:
            return token
        if 0 <= idx < len(dictionary) and dictionary[idx]:
            return dictionary[idx]
        return token

    decoded = re.sub(r"\b\w+\b", decode_token, p)
    return decoded


def extract_m3u8_from_decoded_js(js_code: str) -> str:
    """Extract .m3u8 URL from decoded JavaScript source code."""
    patterns = [
        r"(https?://[^\s'\"\\<>]+\.m3u8[^\s'\"\\,<>]*)",
        "src\\s*:\\s*['\"](https?://[^\\s'\"]+)['\"]",
        "url\\s*:\\s*['\"](https?://[^\\s'\"]+)['\"]",
        "['\"](https?://[^\\s'\"]*vault[^\\s'\"]*)['\"]",
        "['\"](https?://[^\\s'\"]*owocdn[^\\s'\"]*)['\"]",
    ]
    for pattern in patterns:
        match = re.search(pattern, js_code)
        if match:
            url = match.group(1) if match.lastindex else match.group(0)
            url = url.rstrip("'\"")
            if ".m3u8" in url or "owocdn" in url:
                return url
    return ""


def search_anime(query: str) -> list:
    """Search AnimePahe. Returns list of result dicts."""
    session, domain = _init_anime_session()
    resp = session.get(
        f"{domain}/api",
        params={"m": "search", "q": query},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", [])


def get_episodes(anime_id) -> list:
    """Fetch ALL episodes across all pages. Returns list of episode dicts."""
    session, domain = _init_anime_session()
    all_episodes = []
    page = 1
    total_pages = 1
    while page <= total_pages:
        resp = session.get(
            f"{domain}/api",
            params={
                "m": "release",
                "id": str(anime_id),
                "sort": "episode_asc",
                "page": str(page),
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        episodes = data.get("data", [])
        if not episodes and page == 1:
            break
        all_episodes.extend(episodes)
        total_pages = data.get("last_page", 1)
        page += 1
    all_episodes.sort(key=lambda e: float(e.get("episode", 0)))
    return all_episodes


def get_kwik_sources(anime_session: str, episode_session: str) -> list:
    """Visit the play page and extract Kwik stream URLs.
    Returns list of dicts: {url, quality, fansub, audio}
    """
    session, domain = _init_anime_session()
    play_url = f"{domain}/play/{anime_session}/{episode_session}"
    resp = session.get(play_url, timeout=30)
    resp.raise_for_status()
    html = resp.text

    # Method 1: Extract from download buttons on the play page
    sources = []
    button_pattern = re.findall(
        r'<button[^>]+data-src="([^"]+)"[^>]*'
        r'data-fansub="([^"]*)"[^>]*'
        r'data-resolution="([^"]*)"[^>]*'
        r'data-audio="([^"]*)"[^>]*>',
        html,
        re.IGNORECASE,
    )

    for src, fansub, resolution, audio in button_pattern:
        if any(src.startswith(f"https://{d}") for d in KWIK_DOMAINS):
            sources.append({
                "url": src,
                "quality": f"{resolution}p" if resolution else "?",
                "fansub": fansub or "Unknown",
                "audio": audio or "jpn",
            })

    if sources:
        def quality_key(s):
            try:
                return int(s["quality"].replace("p", "").replace("?", "0"))
            except (ValueError, AttributeError):
                return 0
        sources.sort(key=quality_key, reverse=True)
        return sources

    # Method 2: Fallback - find Kwik embed URLs in the page
    kwik_pattern = re.compile(
        r"https://(?:kwink\.ai|kwik\.(?:si|cx|link))/e/\w+",
        re.IGNORECASE,
    )
    kwik_links = list(set(kwik_pattern.findall(html)))
    if kwik_links:
        return [{"url": link, "quality": "?", "fansub": "", "audio": ""} for link in kwik_links]
    return []


def resolve_kwik_m3u8(kwik_url: str) -> str:
    """Resolve a Kwik page to extract the direct .m3u8 stream URL.
    Handles the obfuscated eval() packed JavaScript decoder in pure Python.
    Returns the m3u8 URL string, or raises RuntimeError on failure.
    """
    session, domain = _init_anime_session()
    kwik_headers = {
        "Referer": f"{domain}/play/",
        "Origin": domain,
    }

    resp = session.get(kwik_url, headers=kwik_headers, timeout=30)
    resp.raise_for_status()
    html = resp.text

    # Try direct m3u8 extraction first
    direct_m3u8 = re.search(
        r'https?://[^\s\'"<>]+\.m3u8[^\s\'",<>]*', html
    )
    if direct_m3u8:
        return direct_m3u8.group(0)

    # Extract and decode obfuscated scripts
    script_blocks = re.findall(
        r"<script[^>]*>([\s\S]*?)</script>", html, re.IGNORECASE
    )

    candidate_scripts = []
    for script in script_blocks:
        if "eval(" not in script:
            continue
        score = 0
        if "function(p,a,c,k,e,d)" in script:
            score += 10
        if ".m3u8" in script or "Plyr" in script or "source" in script:
            score += 5
        if len(script) > 500:
            score += 2
        candidate_scripts.append((score, script))

    candidate_scripts.sort(key=lambda x: x[0], reverse=True)
    if not candidate_scripts:
        raise RuntimeError("No obfuscated JavaScript found on Kwik page")

    last_error = ""
    for score, script in candidate_scripts:
        try:
            decoded_js = decode_packer(script)
        except Exception as e:
            last_error = str(e)
            continue
        if not decoded_js:
            continue

        m3u8_url = extract_m3u8_from_decoded_js(decoded_js)
        if m3u8_url:
            return m3u8_url

        # Check for nested packer (double obfuscation)
        if "eval(" in decoded_js:
            try:
                double_decoded = decode_packer(decoded_js)
                m3u8_url = extract_m3u8_from_decoded_js(double_decoded)
                if m3u8_url:
                    return m3u8_url
            except Exception:
                pass

    # Fallback: Try extracting from iframe
    iframe_match = re.search(
        "<iframe[^>]+src=['\"](https?://[^'\"]+)['\"]", html, re.IGNORECASE
    )
    if iframe_match:
        iframe_url = iframe_match.group(1)
        try:
            resp = session.get(iframe_url, headers=kwik_headers, timeout=30)
            m3u8_url = extract_m3u8_from_decoded_js(resp.text)
            if m3u8_url:
                return m3u8_url
        except Exception:
            pass

    raise RuntimeError(
        f"Could not extract m3u8 URL from Kwik page. "
        f"(Last decode error: {last_error})"
    )
# ═══════════════════════════════════════════════════════════════════════════════
# DOWNLOAD ENGINE
# ═══════════════════════════════════════════════════════════════════════════════


def download_with_progress(url: str, output_path: str, desc: str | None = None, headers: dict | None = None):
    output_path = Path(output_path)
    headers = headers or DOWNLOAD_HEADERS.copy()
    desc = desc or output_path.name

    downloaded = 0
    if output_path.exists():
        downloaded = output_path.stat().st_size
        headers["Range"] = f"bytes={downloaded}-"

    resp = requests.get(url, headers=headers, stream=True, timeout=60)

    if resp.status_code == 416:
        console.print(f"  [bold bright_green]✓[/bold bright_green] {desc} [dim]already complete[/dim]")
        return True
    if resp.status_code not in (200, 206):
        console.print(f"  [bold red]✗[/bold red] Failed: HTTP {resp.status_code}")
        return False

    total = int(resp.headers.get("Content-Length", 0))
    if resp.status_code == 206:
        total += downloaded
    else:
        downloaded = 0

    mode = "ab" if downloaded and resp.status_code == 206 else "wb"

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold bright_cyan]{task.description}[/bold bright_cyan]"),
        BarColumn(bar_width=40, complete_style="bright_cyan", finished_style="bright_green"),
        "[progress.percentage]{task.percentage:>3.0f}%",
        "•",
        DownloadColumn(binary_units=True),
        "•",
        TransferSpeedColumn(),
        "•",
        TimeRemainingColumn(),
        console=console,
    )
    task_id = progress.add_task(desc[:40], total=total, completed=downloaded)

    with progress:
        with open(output_path, mode) as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    progress.update(task_id, advance=len(chunk))
    return True


def pick_quality(downloads: list, preferred: str | None = None):
    if not downloads:
        return None
    qmap = {d["resolution"]: d for d in downloads}
    if preferred:
        m = re.match(r"(\d+)", preferred)
        if m:
            qn = int(m.group(1))
            if qn in qmap:
                return qmap[qn]
            avail = sorted(qmap.keys(), reverse=True)
            for q in avail:
                if q <= qn:
                    return qmap[q]
            return qmap[avail[-1]] if avail else None
    for q in QUALITY_ORDER:
        if q in qmap:
            return qmap[q]
    return downloads[0]
# ═══════════════════════════════════════════════════════════════════════════════
# SHARED INTERACTIVE SCREENS
# ═══════════════════════════════════════════════════════════════════════════════


def pick_mode():
    """Mode selection screen. Returns 1 (Movies), 2 (TV Shows), 3 (Anime), or None (exit)."""
    table = Table(
        box=box.ROUNDED,
        border_style="bright_cyan",
        show_header=False,
        padding=(0, 2),
    )
    table.add_column("#", style="bold bright_yellow", justify="center", width=4)
    table.add_column("Mode", style="bold bright_white", min_width=15)
    table.add_column("Description", style="dim", min_width=35)

    table.add_row("[1]", "[bold bright_green]Movies[/bold bright_green]", "Direct download from Hakuna CDN")
    table.add_row("[2]", "[bold bright_blue]TV Shows[/bold bright_blue]", "Browse seasons & episodes, then download")
    table.add_row("[3]", "[bold bright_magenta]Anime[/bold bright_magenta]", "Search animepahe, extract HLS streams from Kwik")

    console.print(table)
    console.print()

    choice = IntPrompt.ask(
        "[bold bright_yellow]Select mode[/bold bright_yellow] [dim][1-3, or 0 to exit][/dim]",
        default=0,
    )
    if choice in (1, 2, 3):
        return choice
    return None


def prompt_search(label: str = "What do you want to watch?"):
    console.print(f"[bold bright_yellow]🎬 {label}[/bold bright_yellow]", end=" ")
    return Prompt.ask("").strip()


def show_hakuna_results(items, title="Search Results"):
    """Display Hakuna search results. Returns selected item or None."""
    if not items:
        console.print(
            Panel(
                "[bold red]No results found.[/bold red]\n[dim]Try a different query.[/dim]",
                title="[bold red]⚠ {title}[/bold red]",
                border_style="red",
            )
        )
        return None

    table = Table(
        title=f"[bold bright_cyan]Found {len(items)} result(s)[/bold bright_cyan]",
        box=box.ROUNDED,
        border_style="bright_cyan",
        header_style="bold bright_white on bright_cyan",
        row_styles=["", "dim"],
        show_lines=True,
        padding=(0, 1),
    )
    table.add_column("#", style="bold bright_yellow", justify="center", width=4)
    table.add_column("Title", style="bold bright_white", min_width=30)
    table.add_column("Year", style="bright_green", justify="center", width=6)
    table.add_column("Rating", style="bright_yellow", justify="center", width=8)
    table.add_column("Available", justify="center", width=10)

    for i, item in enumerate(items[:20], 1):
        t = item.get("title", "Unknown")
        yr = item.get("releaseDate", "N/A")[:4] if item.get("releaseDate") else "N/A"
        rating = item.get("imdbRatingValue", "N/A")
        if rating and rating != "N/A":
            try:
                rating = f"⭐ {float(rating):.1f}"
            except (ValueError, TypeError):
                rating = "N/A"
        avail = "[bold bright_green]✓[/bold bright_green]" if item.get("hasResource") else "[bold red]✗[/bold red]"
        table.add_row(str(i), t, yr, rating, avail)

    console.print()
    console.print(table)
    console.print()

    max_c = min(len(items), 20)
    while True:
        c = IntPrompt.ask(
            f"[bold bright_yellow]Pick your title[/bold bright_yellow] [dim][1-{max_c}, 0=back][/dim]",
            default=0,
        )
        if c == 0:
            return None
        if 1 <= c <= max_c:
            return items[c - 1]
        console.print("[bold red]Invalid selection.[/bold red]")


def show_anime_results(results):
    """Display animepahe search results. Returns selected anime or None."""
    if not results:
        console.print(
            Panel(
                "[bold red]No anime found.[/bold red]\n[dim]Try a different search.[/dim]",
                title="[bold red]⚠ No Results[/bold red]",
                border_style="red",
            )
        )
        return None

    table = Table(
        title=f"[bold bright_magenta]Found {len(results)} anime[/bold bright_magenta]",
        box=box.ROUNDED,
        border_style="bright_magenta",
        header_style="bold bright_white on bright_magenta",
        row_styles=["", "dim"],
        show_lines=True,
        padding=(0, 1),
    )
    table.add_column("#", style="bold bright_yellow", justify="center", width=4)
    table.add_column("Title", style="bold bright_white", min_width=30)
    table.add_column("Year", style="bright_green", justify="center", width=6)
    table.add_column("Type", style="bright_cyan", justify="center", width=8)
    table.add_column("Status", style="bright_yellow", justify="center", width=10)
    table.add_column("Episodes", style="bright_cyan", justify="center", width=8)

    for i, a in enumerate(results[:20], 1):
        title = a.get("title", "Unknown")
        year = str(a.get("year", "N/A"))
        atype = a.get("type", "?")
        status = a.get("status", "?")
        eps = str(a.get("episodes", "?"))
        table.add_row(str(i), title, year, atype, status, eps)

    console.print()
    console.print(table)
    console.print()

    max_c = min(len(results), 20)
    while True:
        c = IntPrompt.ask(
            f"[bold bright_yellow]Pick anime[/bold bright_yellow] [dim][1-{max_c}, 0=back][/dim]",
            default=0,
        )
        if c == 0:
            return None
        if 1 <= c <= max_c:
            return results[c - 1]
        console.print("[bold red]Invalid selection.[/bold red]")


def show_seasons(seasons_data):
    """{season_num: [episodes, ...], ...} -> user picks season. Returns (season_num, episodes) or None."""
    if not seasons_data:
        console.print("[bold red]No season data.[/bold red]")
        return None

    divider("📺 Season Selection", "bright_blue")

    table = Table(
        box=box.ROUNDED,
        border_style="bright_blue",
        header_style="bold bright_white on bright_blue",
        show_lines=True,
        padding=(0, 1),
    )
    table.add_column("#", style="bold bright_yellow", justify="center", width=4)
    table.add_column("Season", style="bold bright_white", min_width=20)
    table.add_column("Episodes", style="bright_cyan", justify="center", width=10)

    season_list = []
    for i, (sn, eps) in enumerate(sorted(seasons_data.items()), 1):
        table.add_row(str(i), f"Season {sn}", str(len(eps)))
        season_list.append((sn, eps))

    console.print(table)
    console.print()

    while True:
        c = IntPrompt.ask(
            f"[bold bright_yellow]Pick season[/bold bright_yellow] [dim][1-{len(season_list)}, 0=back][/dim]",
            default=0,
        )
        if c == 0:
            return None
        if 1 <= c <= len(season_list):
            return season_list[c - 1]
        console.print("[bold red]Invalid selection.[/bold red]")


def show_episodes_hakuna(episodes, season_num):
    """Display Hakuna episodes. Returns selected episode or None."""
    if not episodes:
        console.print("[bold red]No episodes.[/bold red]")
        return None

    divider(f"📺 Season {season_num} - Episodes", "bright_blue")

    table = Table(
        box=box.ROUNDED,
        border_style="bright_blue",
        header_style="bold bright_white on bright_blue",
        show_lines=True,
        padding=(0, 1),
    )
    table.add_column("#", style="bold bright_yellow", justify="center", width=4)
    table.add_column("Episode", style="bold bright_white", min_width=40)

    for i, ep in enumerate(episodes, 1):
        ep_title = ep.get("title", "")
        ep_num = ep.get("episode", i)
        display = f"EP{ep_num}: {ep_title}" if ep_title else f"Episode {ep_num}"
        table.add_row(str(i), display)

    console.print(table)
    console.print()

    while True:
        c = IntPrompt.ask(
            f"[bold bright_yellow]Pick episode[/bold bright_yellow] [dim][1-{len(episodes)}, 0=back][/dim]",
            default=0,
        )
        if c == 0:
            return None
        if 1 <= c <= len(episodes):
            return episodes[c - 1]
        console.print("[bold red]Invalid selection.[/bold red]")


def show_episodes_anime(episodes):
    """Display animepahe episodes. Returns selected episode or None."""
    if not episodes:
        console.print("[bold red]No episodes.[/bold red]")
        return None

    divider("📺 Episodes", "bright_magenta")

    table = Table(
        box=box.ROUNDED,
        border_style="bright_magenta",
        header_style="bold bright_white on bright_magenta",
        show_lines=True,
        padding=(0, 1),
    )
    table.add_column("#", style="bold bright_yellow", justify="center", width=5)
    table.add_column("Episode", style="bold bright_white", min_width=40)
    table.add_column("Duration", style="bright_cyan", justify="center", width=10)
    table.add_column("Date", style="dim", justify="center", width=12)

    for i, ep in enumerate(episodes, 1):
        ep_num = ep.get("episode", i)
        ep_title = ep.get("title", "")
        duration = ep.get("duration", "?")
        aired = ep.get("created_at", "")[:10] if ep.get("created_at") else ""
        display = f"EP{ep_num}: {ep_title}" if ep_title else f"Episode {ep_num}"
        table.add_row(str(i), display, str(duration), aired)

    console.print(table)
    console.print()

    while True:
        c = IntPrompt.ask(
            f"[bold bright_yellow]Pick episode[/bold bright_yellow] [dim][1-{len(episodes)}, 0=back][/dim]",
            default=0,
        )
        if c == 0:
            return None
        if 1 <= c <= len(episodes):
            return episodes[c - 1]
        console.print("[bold red]Invalid selection.[/bold red]")


def quality_picker(downloads, mode_name=""):
    """Pick quality from downloads list. Returns selected download dict or None."""
    if not downloads:
        console.print("[bold red]No quality options.[/bold red]")
        return None

    color = "bright_magenta" if mode_name == "anime" else "bright_magenta"
    divider("🎬 Quality Selection", color)

    sorted_dl = sorted(downloads, key=lambda d: d.get("resolution", 0), reverse=True)

    table = Table(
        box=box.ROUNDED,
        border_style=color,
        header_style=f"bold bright_white on {color}",
        show_lines=True,
        padding=(0, 1),
    )
    table.add_column("#", style="bold bright_yellow", justify="center", width=4)
    table.add_column("Quality", style="bold bright_white", justify="center", width=10)
    table.add_column("Size", style="bright_cyan", justify="center", width=12)
    table.add_column("Status", justify="center", width=12)

    for i, d in enumerate(sorted_dl, 1):
        res = d.get("resolution", "?")
        res_str = f"{res}p" if res else "Source"
        size_str = fmt_size(int(d.get("size", 0)))
        status = "[bold bright_green]★ Best[/bold bright_green]" if i == 1 else ""
        table.add_row(f"[{i}]", res_str, size_str, status)

    console.print(table)
    console.print()

    choice = IntPrompt.ask(
        f"[bold bright_yellow]Pick quality[/bold bright_yellow] [dim][1-{len(sorted_dl)}, Enter=best][/dim]",
        default=1,
    )
    if 1 <= choice <= len(sorted_dl):
        return sorted_dl[choice - 1]
    return sorted_dl[0]


def action_menu():
    """Returns 1 (Download), 2 (Stream), 3 (Auto-launch)."""
    divider("⚡ Choose Action", "bright_green")

    actions = [
        ("[bold bright_green][1] Download[/bold bright_green]", "Save to your device via ffmpeg"),
        ("[bold bright_yellow][2] Stream[/bold bright_yellow]", "Copy m3u8 URL for VLC/mpv"),
        ("[bold bright_blue][3] Auto-launch[/bold bright_blue]", "Open directly in mpv"),
    ]
    for label, desc in actions:
        console.print(f"  {label} - [dim]{desc}[/dim]")
    console.print()

    while True:
        c = IntPrompt.ask(
            "[bold bright_yellow]Your choice[/bold bright_yellow] [dim][1-3, Enter=download][/dim]",
            default=1,
        )
        if 1 <= c <= 3:
            return c
        console.print("[bold red]Invalid choice.[/bold red]")


def end_session_menu():
    """Returns 'again', 'switch', or 'exit'."""
    console.print()
    table = Table(
        box=box.ROUNDED,
        border_style="bright_cyan",
        show_header=False,
        padding=(0, 2),
    )
    table.add_column("#", style="bold bright_yellow", justify="center", width=4)
    table.add_column("Action", style="bold bright_white", min_width=25)
    table.add_row("[1]", "[bold bright_green]Search again[/bold bright_green] (same mode)")
    table.add_row("[2]", "[bold bright_yellow]Switch mode[/bold bright_yellow]")
    table.add_row("[3]", "[bold red]Exit[/bold red]")
    console.print(table)
    console.print()

    while True:
        c = IntPrompt.ask(
            "[bold bright_yellow]What next?[/bold bright_yellow] [dim][1-3, Enter=search again][/dim]",
            default=1,
        )
        if c == 1:
            return "again"
        if c == 2:
            return "switch"
        if c == 3:
            return "exit"
        console.print("[bold red]Invalid choice.[/bold red]")


def handle_stream(source_url: str, title: str):
    console.print(
        Panel(
            f"[bold bright_yellow]🔗 Stream URL:[/bold bright_yellow]\n\n"
            f"[bold bright_white]{title}[/bold bright_white]\n"
            f"[underline bright_cyan]{source_url}[/underline bright_cyan]\n\n"
            "[bright_yellow]Use this m3u8 URL in VLC, mpv, or any HLS player.[/bright_yellow]",
            title="[bold bright_yellow]📋 Stream Ready[/bold bright_yellow]",
            border_style="bright_yellow",
        )
    )


def handle_autolaunch(source_url: str, title: str):
    with spinner("Launching mpv..."):
        try:
            subprocess.Popen(
                ["mpv", source_url, f"--force-media-title={title}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1)
        except FileNotFoundError:
            console.print("[bold red]✗ mpv not found.[/bold red] [dim]Install mpv or choose Stream.[/dim]")
            return False
    console.print(
        Panel(
            f"[bold bright_green]▶ mpv launched![/bold bright_green]\n\n"
            f"[bold bright_white]{title}[/bold bright_white]\n"
            "[dim]Enjoy! ☕[/dim]",
            title="[bold bright_green]🎬 Now Playing[/bold bright_green]",
            border_style="bright_green",
        )
    )
    return True


def download_summary(output_path: str, elapsed: float):
    output_path = Path(output_path)
    if not output_path.exists():
        return
    sz = output_path.stat().st_size
    mb = sz / (1024 * 1024)
    spd = mb / elapsed if elapsed > 0 else 0

    t = Table(box=box.ROUNDED, border_style="bright_green", show_header=False, padding=(0, 1))
    t.add_column("Key", style="bold bright_cyan", width=12)
    t.add_column("Value", style="bright_white")
    t.add_row("File", str(output_path))
    t.add_row("Size", f"{mb:.1f} MB")
    t.add_row("Time", f"{elapsed:.1f}s")
    t.add_row("Speed", f"{spd:.1f} MB/s")
    console.print(
        Panel(t, title="[bold bright_green]✅ Download Complete[/bold bright_green]", border_style="bright_green")
    )
# ═══════════════════════════════════════════════════════════════════════════════
# MODE FLOW FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════


def extract_seasons(details: dict):
    """Extract {season_num: [episodes]} from Hakuna item-details."""
    seasons = {}
    for ep in details.get("episodes", []):
        sn = ep.get("season", 1)
        seasons.setdefault(sn, []).append(ep)
    for sn in seasons:
        seasons[sn].sort(key=lambda e: e.get("episode", 0))
    return seasons


def run_download_stream(source_url: str, title: str, filename: str, resolution: int, captions: list | None = None):
    """Shared download/stream/auto-launch handler for Movies & TV Shows (MP4 direct download)."""
    action = action_menu()
    output_dir = Path(".")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / filename
    safe = safe_name(title)

    if action == 2:  # Stream
        handle_stream(source_url, title)
        return True
    elif action == 3:  # Auto-launch
        return handle_autolaunch(source_url, title)
    else:  # Download
        console.print()
        console.print(f"[bold bright_cyan]⬇ Downloading to:[/bold bright_cyan] [dim]{output_path}[/dim]")
        console.print()
        start = time.time()
        try:
            ok = download_with_progress(source_url, output_path, desc=f"{resolution}p video")
        except KeyboardInterrupt:
            console.print("\n[bold yellow]⚠ Cancelled.[/bold yellow]")
            return False

        if ok:
            download_summary(output_path, time.time() - start)
            # Subtitles
            if captions:
                console.print()
                console.print("[bold bright_cyan]📜 Subtitles[/bold bright_cyan] [dim]checking...[/dim]")
                for cap in captions:
                    sub_url = cap.get("url", "")
                    if not sub_url:
                        continue
                    sub_lang = cap.get("lan", "unknown")
                    sub_name = cap.get("lanName", sub_lang)
                    sub_ext = sub_url.split("?")[0].split(".")[-1] or "srt"
                    sub_fn = f"{safe}_{resolution}p_{sub_lang}.{sub_ext}"
                    sub_path = output_dir / sub_fn
                    try:
                        download_with_progress(sub_url, sub_path, desc=f"Sub [{sub_name}]")
                    except Exception as e:
                        console.print(f"  [dim]Sub [{sub_name}] failed: {e}[/dim]")
        else:
            console.print(
                Panel(
                    "[bold red]Download failed.[/bold red]\n"
                    "[dim]CDN link may have expired or resource unavailable.[/dim]",
                    border_style="red",
                )
            )
        return ok


def movies_mode():
    """Movies flow: search  ->  pick (subjectType==1)  ->  quality  ->  download/stream."""
    while True:
        query = prompt_search()
        if not query:
            continue

        try:
            with spinner(f"Searching movies for '[bold]{query}[/bold]'..."):
                items, _ = hakuna_search(query)
        except Exception as e:
            console.print(f"[bold red]✗ Search failed:[/bold red] {e}")
            continue

        # Filter movies only
        movies = [it for it in items if it.get("subjectType") == 1]
        if not movies:
            console.print("[bold yellow]⚠ No movies found.[/bold yellow] [dim]Try a different query.[/dim]")
            action = end_session_menu()
            if action == "exit":
                return "exit"
            if action == "switch":
                return "switch"
            continue

        selected = show_hakuna_results(movies, "Movies")
        if selected is None:
            action = end_session_menu()
            if action == "exit":
                return "exit"
            if action == "switch":
                return "switch"
            continue

        title = selected["title"]
        sid = selected["subjectId"]
        dpath = selected["detailPath"]

        # Info
        info = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
        info.add_column("Key", style="bold bright_cyan", width=10)
        info.add_column("Value", style="bright_white")
        info.add_row("Title", title)
        info.add_row("Type", "Movie")
        console.print(Panel(info, border_style="dim", padding=(0, 1)))

        # Fetch media
        try:
            with spinner("Fetching download links..."):
                downloads, captions = hakuna_media(sid, dpath)
        except Exception as e:
            console.print(f"[bold red]✗ Failed:[/bold red] {e}")
            action = end_session_menu()
            if action == "exit":
                return "exit"
            if action == "switch":
                return "switch"
            continue

        if not downloads:
            console.print(
                Panel(
                    "[bold red]No download links available.[/bold red]",
                    title="[bold red]⚠ Unavailable[/bold red]",
                    border_style="red",
                )
            )
            action = end_session_menu()
            if action == "exit":
                return "exit"
            if action == "switch":
                return "switch"
            continue

        q = quality_picker(downloads)
        if q is None:
            action = end_session_menu()
            if action == "exit":
                return "exit"
            if action == "switch":
                return "switch"
            continue

        res = q["resolution"]
        url = q["sourceUrl"]
        fn = f"{safe_name(title)}_{res}p.mp4"
        run_download_stream(url, title, fn, res, captions)

        action = end_session_menu()
        if action == "exit":
            return "exit"
        if action == "switch":
            return "switch"
        # 'again' -> loop


def tvshows_mode():
    """TV Shows flow: search  ->  pick (subjectType==2)  ->  seasons  ->  episodes  ->  quality  ->  download/stream."""
    while True:
        query = prompt_search()
        if not query:
            continue

        try:
            with spinner(f"Searching TV shows for '[bold]{query}[/bold]'..."):
                items, _ = hakuna_search(query)
        except Exception as e:
            console.print(f"[bold red]✗ Search failed:[/bold red] {e}")
            continue

        # Filter TV only
        shows = [it for it in items if it.get("subjectType") == 2]
        if not shows:
            console.print("[bold yellow]⚠ No TV shows found.[/bold yellow] [dim]Try a different query.[/dim]")
            action = end_session_menu()
            if action == "exit":
                return "exit"
            if action == "switch":
                return "switch"
            continue

        selected = show_hakuna_results(shows, "TV Shows")
        if selected is None:
            action = end_session_menu()
            if action == "exit":
                return "exit"
            if action == "switch":
                return "switch"
            continue

        title = selected["title"]
        sid = selected["subjectId"]
        dpath = selected["detailPath"]

        info = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
        info.add_column("Key", style="bold bright_cyan", width=10)
        info.add_column("Value", style="bright_white")
        info.add_row("Title", title)
        info.add_row("Type", "TV Series")
        console.print(Panel(info, border_style="dim", padding=(0, 1)))

        # Fetch details for seasons/episodes
        try:
            with spinner("Fetching seasons & episodes..."):
                details = hakuna_details(sid)
        except Exception as e:
            console.print(f"[bold yellow]⚠ Details fetch failed:[/bold yellow] {e}")
            details = {}

        seasons_data = extract_seasons(details)

        season = 0
        episode = 0
        season_str = ""

        if seasons_data:
            result = show_seasons(seasons_data)
            if result is None:
                action = end_session_menu()
                if action == "exit":
                    return "exit"
                if action == "switch":
                    return "switch"
                continue
            season, episodes = result
            ep_result = show_episodes_hakuna(episodes, season)
            if ep_result is None:
                action = end_session_menu()
                if action == "exit":
                    return "exit"
                if action == "switch":
                    return "switch"
                continue
            episode = ep_result.get("episode", 1)
        else:
            console.print("[dim]No structured data. Enter manually.[/dim]")
            season = IntPrompt.ask("[bold bright_blue]Season[/bold bright_blue]", default=1)
            episode = IntPrompt.ask("[bold bright_blue]Episode[/bold bright_blue]", default=1)

        season_str = f"_S{season:02d}E{episode:02d}"

        # Fetch media links
        try:
            with spinner("Fetching download links..."):
                downloads, captions = hakuna_media(sid, dpath, season, episode)
        except Exception as e:
            console.print(f"[bold red]✗ Failed:[/bold red] {e}")
            action = end_session_menu()
            if action == "exit":
                return "exit"
            if action == "switch":
                return "switch"
            continue

        if not downloads:
            console.print(
                Panel(
                    "[bold red]No download links available.[/bold red]",
                    title="[bold red]⚠ Unavailable[/bold red]",
                    border_style="red",
                )
            )
            action = end_session_menu()
            if action == "exit":
                return "exit"
            if action == "switch":
                return "switch"
            continue

        q = quality_picker(downloads)
        if q is None:
            action = end_session_menu()
            if action == "exit":
                return "exit"
            if action == "switch":
                return "switch"
            continue

        res = q["resolution"]
        url = q["sourceUrl"]
        fn = f"{safe_name(title)}{season_str}_{res}p.mp4"
        run_download_stream(url, title, fn, res, captions)

        action = end_session_menu()
        if action == "exit":
            return "exit"
        if action == "switch":
            return "switch"


def anime_mode():
    """Anime flow: search septorch (subjectType=2) -> pick -> season/episode -> quality -> download/stream."""
    while True:
        query = prompt_search("What anime do you want to watch?")
        if not query:
            continue

        try:
            with spinner(f"Searching for '[bold]{query}[/bold]'..."):
                items, _ = hakuna_search(query)
        except Exception as e:
            console.print(f"[bold red]✗ Search failed:[/bold red] {e}")
            continue

        # Filter anime (subjectType == 2)
        anime_items = [it for it in items if it.get("subjectType") == 2]

        if not anime_items:
            console.print("[bold yellow]⚠ No anime found.[/bold yellow] [dim]Try a different query.[/dim]")
            action = end_session_menu()
            if action == "exit":
                return "exit"
            if action == "switch":
                return "switch"
            continue

        selected = show_hakuna_results(anime_items)
        if selected is None:
            action = end_session_menu()
            if action == "exit":
                return "exit"
            if action == "switch":
                return "switch"
            continue

        anime_title = selected.get("title", "Unknown")
        subject_id = selected.get("subjectId", "")
        detail_path = selected.get("detailPath", "")

        # Ask for season and episode
        console.print()
        try:
            season = IntPrompt.ask("[bold bright_cyan]Season[/bold bright_cyan]", default=1)
            episode = IntPrompt.ask("[bold bright_cyan]Episode[/bold bright_cyan]", default=1)
        except Exception:
            continue

        display_title = f"{anime_title} S{season:02d}E{episode:02d}"

        # Fetch media sources
        try:
            with spinner(f"Fetching sources for [bold]{display_title}[/bold]..."):
                downloads, captions = hakuna_media(subject_id, detail_path, season, episode)
        except Exception as e:
            console.print(f"[bold red]✗ Failed to fetch sources:[/bold red] {e}")
            action = end_session_menu()
            if action == "exit":
                return "exit"
            if action == "switch":
                return "switch"
            continue

        if not downloads:
            console.print(Panel(
                "[bold red]No video sources found.[/bold red]\n[dim]Try a different episode or season.[/dim]",
                border_style="red",
            ))
            action = end_session_menu()
            if action == "exit":
                return "exit"
            if action == "switch":
                return "switch"
            continue

        q = quality_picker(downloads, mode_name="anime")
        if q is None:
            action = end_session_menu()
            if action == "exit":
                return "exit"
            if action == "switch":
                return "switch"
            continue

        res = q.get("resolution", 0)
        url = q.get("sourceUrl") or q.get("downloadUrl") or q.get("streamUrl")
        res_str = f"{res}p" if res else "src"
        fn = f"{safe_name(anime_title)}_S{season:02d}E{episode:02d}_{res_str}.mp4"

        action_choice = action_menu()
        output_dir = Path(".")
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / fn

        if action_choice == 2:
            handle_stream(url, display_title)
        elif action_choice == 3:
            handle_autolaunch(url, display_title)
        else:
            console.print()
            console.print(f"[bold bright_cyan]⬇ Downloading to:[/bold bright_cyan] [dim]{output_path}[/dim]")
            console.print()
            start = time.time()
            try:
                ok = download_with_progress(url, output_path, desc=f"{res_str}", headers=DOWNLOAD_HEADERS)
            except KeyboardInterrupt:
                console.print("\n[bold yellow]⚠ Cancelled.[/bold yellow]")
                ok = False
            if ok:
                download_summary(output_path, time.time() - start)
            else:
                console.print(Panel("[bold red]Download failed.[/bold red]", border_style="red"))

        action = end_session_menu()
        if action == "exit":
            return "exit"
        if action == "switch":
            return "switch"

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════


def main():
    console.clear()
    banner()

    while True:
        mode = pick_mode()
        if mode is None:
            break

        console.clear()
        banner()

        if mode == 1:
            console.print("[bold bright_green]🎬 Movies Mode[/bold bright_green]")
            console.print()
            result = movies_mode()
        elif mode == 2:
            console.print("[bold bright_blue]📺 TV Shows Mode[/bold bright_blue]")
            console.print()
            result = tvshows_mode()
        elif mode == 3:
            console.print("[bold bright_magenta]🍥 Anime Mode[/bold bright_magenta]")
            console.print()
            result = anime_mode()
        else:
            break

        if result == "exit":
            break
        # "switch" -> loop back to mode picker
        console.clear()
        banner()

    console.print()
    console.print(
        Panel(
            "[bold bright_cyan]Thanks for using Hakuna Downloader![/bold bright_cyan]\n"
            "[dim]See you next time 👋[/dim]",
            border_style="bright_cyan",
        )
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print()
        console.print("\n[bold yellow]👋 Goodbye![/bold yellow]")
        sys.exit(0)
    except EOFError:
        console.print()
        sys.exit(0)
