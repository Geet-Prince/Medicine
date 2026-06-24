from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from flask_cors import CORS
from curl_cffi import requests as cffi_requests
import subprocess
import json
import os
import sys
import urllib.parse
import time
import random
import re
import logging

# ─────────────────────────────────────────────────────────────
# App Setup
# ─────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder='static')
CORS(app)

# Suppress Flask default error logs polluting the console
log = logging.getLogger('werkzeug')
log.setLevel(logging.WARNING)

# ─────────────────────────────────────────────────────────────
# Browser User-Agent pool — rotated on every yt-dlp call.
# ─────────────────────────────────────────────────────────────
_UA_POOL = [
    # Chrome Windows (most common)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Chrome Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Firefox Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    # Firefox Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:126.0) Gecko/20100101 Firefox/126.0",
    # Safari Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    # Edge Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    # Smart TV — best with tv_embedded
    "Mozilla/5.0 (SMART-TV; Linux; Tizen 5.0) AppleWebKit/538.1 (KHTML, like Gecko) Version/5.0 TV Safari/538.1",
    "Mozilla/5.0 (SMART-TV; Linux; Tizen 6.0) AppleWebKit/538.1 (KHTML, like Gecko) Version/6.0 TV Safari/538.1",
]

# ─────────────────────────────────────────────────────────────
# Player client strategies — (client_name, format_selector)
# ios/android return m4a audio directly; web clients return webm/opus.
# ─────────────────────────────────────────────────────────────
_PLAYER_CLIENTS = [
    # (client,          format_selector)
    ("tv_embedded",    "bestaudio/best"),   # Most reliable across all IPs
    ("mweb",           "bestaudio/best"),   # Mobile web fallback
    ("ios",            "bestaudio"),        # iOS uses m4a, no opus streams
    ("android_music",  "bestaudio"),        # Android music client
    (None,             "bestaudio/best"),   # yt-dlp default (ANDROID_VR)
]

_ACCEPT_LANGS = [
    "en-US,en;q=0.9",
    "en-GB,en;q=0.9",
    "en-US,en;q=0.9,hi;q=0.8",
    "en-IN,en;q=0.9",
]


def _rand_ua() -> str:
    return random.choice(_UA_POOL)


# ─────────────────────────────────────────────────────────────
# Build yt-dlp command
# ─────────────────────────────────────────────────────────────
def _build_cmd(query: str, flat: bool = False, client: str = None):
    ua = _rand_ua()
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--user-agent",  ua,
        "--impersonate", "chrome",  # curl-cffi Chrome TLS fingerprint
        "--force-ipv4",             # Railway IPv6 is often blocked by YouTube
        "--geo-bypass",             # Bypass regional restrictions
        "--no-warnings",
        "--socket-timeout", "20",
        "--retries",        "3",
        "--no-playlist",
    ]

    if client:
        cmd += ["--extractor-args", f"youtube:player_client={client}"]

    if flat:
        cmd += ["--dump-json", "--flat-playlist"]
    else:
        cmd += ["--dump-json", "-f", fmt]

    cmd.append(query)
    return cmd, ua


# ─────────────────────────────────────────────────────────────
# yt-dlp — fetch full audio URL + metadata
# Tries ios → android_music → tv_embedded → mweb → default
# ─────────────────────────────────────────────────────────────
def fetch_song_data(query: str) -> dict:
    if query.startswith("http://") or query.startswith("https://"):
        search_query = query
    else:
        search_query = f"ytsearch1:{query}"

    last_error = "All player clients failed. YouTube may be rate-limiting."

    for client, fmt in _PLAYER_CLIENTS:
        cmd, ua = _build_cmd(search_query, flat=False, client=client, fmt=fmt)
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60
            )
        except subprocess.TimeoutExpired:
            last_error = "Search timed out (60s)."
            continue
        except Exception as e:
            last_error = f"Subprocess error: {e}"
            continue

        if result.returncode != 0 or not result.stdout.strip():
            err = result.stderr.strip() or "No output from yt-dlp."
            err = re.sub(r'\x1b\[[0-9;]*m', '', err)
            last_error = err[:400]
            continue

        # Parse JSON — yt-dlp may output multiple JSON lines
        data = None
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                break
            except json.JSONDecodeError:
                continue

        if data is None:
            last_error = "No valid JSON in yt-dlp output."
            continue

        # Extract best audio URL
        audio_url = (
            data.get("url") or
            (data.get("requested_formats") or [{}])[0].get("url")
        )

        if not audio_url and data.get("formats"):
            formats = data["formats"]
            audio_only = [
                f for f in formats
                if f.get("acodec") not in (None, "none")
                and f.get("vcodec") in (None, "none", "")
            ]
            best = sorted(audio_only or formats,
                          key=lambda f: f.get("abr") or 0, reverse=True)
            audio_url = best[0].get("url") if best else None

        if not audio_url:
            last_error = "No playable audio URL found in yt-dlp output."
            continue

        http_headers = data.get("http_headers", {})
        http_headers.setdefault("User-Agent", ua)

        info = {
            "title":        data.get("title", "Unknown Track"),
            "artist":       data.get("uploader", data.get("channel", "Unknown Artist")),
            "duration":     data.get("duration", 0),
            "thumbnail":    data.get("thumbnail", ""),
            "view_count":   data.get("view_count", 0),
            "webpage_url":  data.get("webpage_url", ""),
            "album":        data.get("album", ""),
            "http_headers": http_headers,
        }

        print(f"[yt-dlp] ✓ client={client or 'default'}  {info['title'][:50]}")
        return {"audio_url": audio_url, "info": info, "error": None}

    print(f"[yt-dlp] ✗ All clients failed: {last_error[:100]}")
    return {"audio_url": None, "info": None, "error": last_error}


# ─────────────────────────────────────────────────────────────
# yt-dlp — fast flat search (metadata only, no stream URL)
# ─────────────────────────────────────────────────────────────
def fetch_search_results(query: str, count: int = 10) -> list:
    search_query = f"ytsearch{count}:{query}"

    for client, fmt in _PLAYER_CLIENTS:
        cmd, _ = _build_cmd(search_query, flat=True, client=client, fmt=fmt)
        # flat-playlist doesn't need --no-playlist
        cmd = [x for x in cmd if x != "--no-playlist"]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30
            )
        except Exception as e:
            print(f"[search_list] error ({client}): {e}")
            continue

        if not result.stdout.strip():
            continue

        results = []
        for line in result.stdout.strip().splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                vid  = data.get("id")
                if not vid:
                    continue
                thumb = (data.get("thumbnail") or
                         f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg")
                results.append({
                    "id":        vid,
                    "title":     data.get("title", "Unknown Track"),
                    "artist":    data.get("uploader", data.get("channel", "Unknown Artist")),
                    "duration":  data.get("duration", 0),
                    "thumbnail": thumb,
                })
            except json.JSONDecodeError:
                continue

        if results:
            return results

    return []


# ─────────────────────────────────────────────────────────────
# Stream URL cache  —  keyed by query string
# ─────────────────────────────────────────────────────────────
_stream_cache: dict = {}
CACHE_TTL = 45 * 60   # 45 minutes


def cache_get(key: str):
    entry = _stream_cache.get(key)
    if not entry:
        return None
    if time.time() > entry.get("expires_at", 0):
        del _stream_cache[key]
        return None
    return entry


def cache_set(key: str, audio_url: str, http_headers: dict, info: dict):
    _stream_cache[key] = {
        "url":          audio_url,
        "http_headers": http_headers,
        "expires_at":   time.time() + CACHE_TTL,
        "title":        info.get("title", ""),
        "artist":       info.get("artist", ""),
        "duration":     info.get("duration", 0),
        "thumbnail":    info.get("thumbnail", ""),
    }


# ─────────────────────────────────────────────────────────────
# Global error handler — never return a raw Python exception
# ─────────────────────────────────────────────────────────────
@app.errorhandler(Exception)
def handle_exception(e):
    print(f"[Medicine] Unhandled exception: {e}")
    return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


# Suppress the 404 for favicon.ico
@app.route('/favicon.ico')
def favicon():
    return Response(status=204)   # 204 No Content — silent success


@app.route('/api/search_list', methods=['POST'])
def search_list():
    try:
        body  = request.get_json(silent=True) or {}
        query = body.get('query', '').strip()
        if not query:
            return jsonify({"error": "No query provided"}), 400
        results = fetch_search_results(query, count=10)
        return jsonify({"results": results})
    except Exception as e:
        print(f"[search_list] exception: {e}")
        return jsonify({"error": str(e), "results": []}), 200  # 200 so frontend doesn't hard-fail


@app.route('/api/search', methods=['POST'])
def search():
    try:
        body  = request.get_json(silent=True) or {}
        query = body.get('query', '').strip()
        if not query:
            return jsonify({"error": "No query provided"}), 400

        # Serve from cache if still fresh
        cached = cache_get(query)
        if cached:
            stream_path = "/api/stream?q=" + urllib.parse.quote(query)
            return jsonify({
                "stream_url": stream_path,
                "info": {
                    "title":     cached.get("title", query),
                    "artist":    cached.get("artist", ""),
                    "duration":  cached.get("duration", 0),
                    "thumbnail": cached.get("thumbnail", ""),
                },
                "cached": True
            })

        result = fetch_song_data(query)

        # If all clients failed, return a graceful 200 with error key
        # (returning 500 crashes the frontend preloader)
        if result["error"] or not result["audio_url"]:
            return jsonify({
                "error":      result["error"] or "No audio URL found.",
                "stream_url": None,
                "info":       None,
            }), 200

        http_headers = result["info"].pop("http_headers", {})
        cache_set(query, result["audio_url"], http_headers, result["info"])

        stream_path = "/api/stream?q=" + urllib.parse.quote(query)
        return jsonify({
            "stream_url": stream_path,
            "info":       result["info"],
        })

    except Exception as e:
        print(f"[search] unhandled: {e}")
        return jsonify({"error": str(e), "stream_url": None, "info": None}), 200


@app.route('/api/stream')
def stream_audio():
    """
    Proxy YouTube audio stream to browser.
    Supports Range requests for seeking.
    Auto-refreshes expired stream URLs.
    """
    try:
        query = request.args.get('q', '').strip()
        if not query:
            return jsonify({"error": "Missing query parameter"}), 400

        cached = cache_get(query)
        if not cached:
            result = fetch_song_data(query)
            if result["error"] or not result["audio_url"]:
                return jsonify({"error": "Could not resolve stream."}), 404
            http_headers = result["info"].pop("http_headers", {})
            cache_set(query, result["audio_url"], http_headers, result["info"])
            cached = cache_get(query)

        yt_url = cached["url"]

        # Build request headers — impersonate a real browser fetching audio
        req_headers = {
            "User-Agent":      cached.get("http_headers", {}).get("User-Agent", _rand_ua()),
            "Accept":          "*/*",
            "Accept-Language": random.choice(_ACCEPT_LANGS),
            "Accept-Encoding": "identity",
            "Referer":         "https://www.youtube.com/",
            "Origin":          "https://www.youtube.com",
            "Sec-Fetch-Mode":  "no-cors",
            "Sec-Fetch-Dest":  "audio",
            "Connection":      "keep-alive",
        }
        # Merge headers yt-dlp provided for this URL
        req_headers.update(cached.get("http_headers", {}))

        # Forward browser Range header for seeking
        if "Range" in request.headers:
            req_headers["Range"] = request.headers["Range"]

        yt_resp = cffi_requests.get(
            yt_url,
            headers=req_headers,
            timeout=20,
            impersonate="chrome",
            stream=True
        )

        # Handle expired stream — evict cache and signal frontend
        if yt_resp.status_code in (403, 410):
            _stream_cache.pop(query, None)
            return jsonify({"error": "Stream expired.", "expired": True}), 403

        if yt_resp.status_code not in (200, 206):
            return jsonify({"error": f"YouTube returned HTTP {yt_resp.status_code}"}), 502

        content_type   = yt_resp.headers.get("Content-Type",   "audio/webm")
        content_length = yt_resp.headers.get("Content-Length", None)
        content_range  = yt_resp.headers.get("Content-Range",  None)
        status_code    = yt_resp.status_code

        def generate():
            try:
                for chunk in yt_resp.iter_content(chunk_size=65536):
                    if chunk:
                        yield chunk
            finally:
                yt_resp.close()

        resp_headers = {
            "Content-Type":                content_type,
            "Accept-Ranges":               "bytes",
            "Access-Control-Allow-Origin": "*",
            "Cache-Control":               "no-cache",
        }
        if content_length:
            resp_headers["Content-Length"] = content_length
        if content_range:
            resp_headers["Content-Range"] = content_range

        return Response(
            stream_with_context(generate()),
            status=status_code,
            headers=resp_headers,
            direct_passthrough=True,
        )

    except Exception as e:
        print(f"[stream_audio] exception: {e}")
        return jsonify({"error": str(e)}), 502


@app.route('/api/health')
def health():
    return jsonify({
        "status":        "operational",
        "service":       "Medicine",
        "cache_entries": len(_stream_cache),
    })


@app.route('/api/debug')
def debug():
    """Shows what yt-dlp returns for a test query — useful for Railway debugging."""
    try:
        cmd, ua = _build_cmd("ytsearch1:hello", flat=False, client="ios")
        result  = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
        return jsonify({
            "returncode": result.returncode,
            "stdout_len": len(result.stdout),
            "stderr":     result.stderr[:500],
            "ua":         ua,
        })
    except Exception as e:
        return jsonify({"error": str(e)})


# ─────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────
if __name__ == '__main__':
    os.makedirs('static', exist_ok=True)
    port = int(os.environ.get('PORT', 5000))
    print("============================================")
    print("  Medicine -- Music is the Best Medicine   ")
    print(f"  http://localhost:{port}                    ")
    print("============================================")
    app.run(
        host='0.0.0.0',
        port=port,
        debug=True,
        use_reloader=True,
        threaded=True,
    )
