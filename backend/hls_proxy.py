#!/usr/bin/env python3
"""
HLS Stream Proxy for AISports/MovieBox
=======================================
Bypasses referer-based access restrictions on live-pull.aisports.mobi
by proxying M3U8 playlists and TS segments with the required headers.

Usage:
    python hls_proxy.py
    
Then open in any browser/VLC:
    http://localhost:5000/proxy?url=<your_m3u8_url>

Termux Install:
    pkg update && pkg install python -y
    pip install flask requests
    python hls_proxy.py
"""

import os
import sys
import re
import logging
import urllib.parse
import requests
from flask import Flask, request, Response, redirect, jsonify

# Silence Flask startup banner
cli = sys.modules['flask.cli']
cli.show_server_banner = lambda *x: None

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger('hls_proxy')

# The referer that unlocks the stream (discovered via testing)
REQUIRED_REFERER = "https://aisports.mobi/"

# Headers sent to the upstream HLS server
UPSTREAM_HEADERS = {
    "Referer": REQUIRED_REFERER,
    "User-Agent": "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "identity",
}

# CORS headers to allow any player (VLC, browser, etc.)
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Expose-Headers": "*",
}


def apply_cors(response):
    """Add CORS headers to a response."""
    for k, v in CORS_HEADERS.items():
        response.headers[k] = v
    return response


def get_base_url(url):
    """Extract the base directory URL from a full URL."""
    parsed = urllib.parse.urlparse(url)
    path = parsed.path
    if "/" in path:
        path = path.rsplit("/", 1)[0] + "/"
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def rewrite_m3u8(content, base_url, proxy_base):
    """
    Rewrite an M3U8 playlist so all media URLs route through this proxy.
    Handles relative URLs, absolute URLs, and URI attributes in tags.
    """
    lines = content.splitlines()
    rewritten = []
    
    for line in lines:
        stripped = line.strip()
        
        # Handle tags with URI="..." attributes (e.g., #EXT-X-KEY)
        if stripped.startswith("#") and 'URI="' in stripped:
            def replace_uri(match):
                original = match.group(1)
                absolute = urllib.parse.urljoin(base_url, original)
                encoded = urllib.parse.quote(absolute, safe="")
                return f'URI="{proxy_base}/segment?url={encoded}"'
            line = re.sub(r'URI="([^"]*)"', replace_uri, stripped)
            rewritten.append(line)
            continue
        
        # Pass through all other tags unchanged
        if not stripped or stripped.startswith("#"):
            rewritten.append(line)
            continue
        
        # Convert relative segment URLs to absolute, then proxy them
        absolute_url = urllib.parse.urljoin(base_url, stripped)
        encoded = urllib.parse.quote(absolute_url, safe="")
        proxied = f"{proxy_base}/segment?url={encoded}"
        rewritten.append(proxied)
    
    return "\n".join(rewritten)


@app.route("/")
def index():
    """Show usage instructions."""
    return """<!DOCTYPE html>
<html>
<head><title>HLS Stream Proxy</title></head>
<body>
<h2>AISports HLS Proxy</h2>
<p><b>Status:</b> Running</p>
<p>Add your M3U8 URL as a <code>?url=</code> parameter to <code>/proxy</code></p>
<p><b>Example:</b></p>
<pre>http://localhost:5000/proxy?url=https%3A%2F%2Flive-pull.aisports.mobi%2Fmoviebox%2Fdevice02%2Fplaylist.m3u8%3Fsign%3D...%26t%3D...</pre>
</body>
</html>""", 200


@app.route("/proxy")
def proxy_playlist():
    """Fetch an M3U8 playlist and rewrite all segment URLs to use the proxy."""
    m3u8_url = request.args.get("url", "")
    if not m3u8_url:
        return jsonify({"error": "Missing ?url= parameter"}), 400
    
    m3u8_url = urllib.parse.unquote(m3u8_url)
    logger.info(f"Proxying playlist: {m3u8_url[:120]}...")
    
    try:
        resp = requests.get(m3u8_url, headers=UPSTREAM_HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch M3U8: {e}")
        return jsonify({"error": f"Upstream error: {str(e)}"}), 502
    
    content_type = resp.headers.get("Content-Type", "application/vnd.apple.mpegurl")
    
    proxy_base = request.host_url.rstrip("/")
    base_url = get_base_url(m3u8_url)
    
    rewritten = rewrite_m3u8(resp.text, base_url, proxy_base)
    
    response = Response(rewritten, status=200, content_type=content_type)
    return apply_cors(response)


@app.route("/segment")
def proxy_segment():
    """Proxy a .ts segment (or sub-playlist) with the required referer header."""
    segment_url = request.args.get("url", "")
    if not segment_url:
        return jsonify({"error": "Missing ?url= parameter"}), 400
    
    segment_url = urllib.parse.unquote(segment_url)
    
    try:
        resp = requests.get(segment_url, headers=UPSTREAM_HEADERS, timeout=30, stream=True)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch segment: {e}")
        return jsonify({"error": f"Upstream error: {str(e)}"}), 502
    
    content_type = resp.headers.get("Content-Type", "video/mp2t")
    
    def generate():
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            if chunk:
                yield chunk
    
    response = Response(generate(), status=200, content_type=content_type)
    response.headers["Accept-Ranges"] = "bytes"
    return apply_cors(response)


@app.route("/direct")
def proxy_direct():
    """
    Alternative endpoint: proxy any URL without M3U8 rewriting.
    Useful for testing or for players that handle relative URLs themselves.
    """
    target_url = request.args.get("url", "")
    if not target_url:
        return jsonify({"error": "Missing ?url= parameter"}), 400
    
    target_url = urllib.parse.unquote(target_url)
    
    try:
        resp = requests.get(target_url, headers=UPSTREAM_HEADERS, timeout=30, stream=True)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        return jsonify({"error": str(e)}), 502
    
    content_type = resp.headers.get("Content-Type", "application/octet-stream")
    
    def generate():
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            if chunk:
                yield chunk
    
    response = Response(generate(), status=200, content_type=content_type)
    response.headers["Accept-Ranges"] = "bytes"
    return apply_cors(response)


@app.route("/health")
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok", "service": "hls_proxy"})


# Handle OPTIONS preflight for all routes
@app.after_request
def after_request(response):
    """Ensure CORS headers are present on all responses."""
    return apply_cors(response)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "0.0.0.0")
    
    print(f"=" * 50)
    print(f"  HLS Stream Proxy - AISports/MovieBox")
    print(f"=" * 50)
    print(f"  URL: http://localhost:{port}/proxy?url=<m3u8_url>")
    print(f"  Bind: {host}:{port}")
    print(f"  Press Ctrl+C to stop")
    print(f"=" * 50)
    
    app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)
