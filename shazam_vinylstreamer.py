#!/usr/bin/env python3
"""
shazam_vinylstreamer.py
-----------------------
Captures audio from the Icecast stream on Vinylstreamer, identifies the
currently playing song using ShazamIO, downloads the cover art locally,
and updates the Icecast-KH stream metadata (StreamTitle + StreamUrl).

Requirements:
    pip install shazamio

System dependencies:
    sudo apt install ffmpeg

Configuration:
    Edit the CONFIG section below before running.

Run manually:
    python3 shazam_vinylstreamer.py

Run as a systemd service:
    See shazam_vinylstreamer.service
"""

import asyncio
import base64
import json
import logging
import os
import subprocess
import tempfile
import urllib.parse
import urllib.request

from shazamio import Shazam

# ---------------------------------------------------------------------------
# CONFIG — edit these values
# ---------------------------------------------------------------------------

ICECAST_URL      = "http://localhost:8000/stream.mp3"      # Icecast stream URL
ICECAST_STATS    = "http://localhost:8000/status-json.xsl" # Icecast stats endpoint
ICECAST_MOUNT    = "/stream.mp3"                           # Mount point name
ICECAST_ADMIN    = "admin"                                 # Icecast admin username
ICECAST_ADMIN_PW = "hackme"                                # Icecast admin password (from icecast.xml)
COVER_LOCAL_PATH = "/usr/share/icecast2/web/cover.jpg"     # Where to save downloaded cover art
COVER_PUBLIC_URL = "http://localhost:8000/cover.jpg"  # Public URL for the saved cover art

# How many seconds of audio to capture for Shazam recognition
CAPTURE_SECONDS  = 8

# How long to wait between recognition attempts (seconds)
POLL_INTERVAL    = 30

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Icecast listener check
# ---------------------------------------------------------------------------

def get_listener_count() -> int:
    """Return the current number of listeners from the Icecast stats endpoint."""
    try:
        with urllib.request.urlopen(ICECAST_STATS, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            source = data.get("icestats", {}).get("source", {})
            if isinstance(source, list):
                return sum(s.get("listeners", 0) for s in source)
            return source.get("listeners", 0)
    except Exception as e:
        log.warning(f"Could not fetch Icecast stats: {e}")
        return 0

# ---------------------------------------------------------------------------
# Icecast metadata update
# ---------------------------------------------------------------------------

def update_icecast_metadata(artist: str, title: str, cover_url: str = ""):
    """Push song info into the Icecast stream's StreamTitle and StreamUrl tags."""
    song = f"{artist} - {title}" if title else artist
    params = {
        "mount": ICECAST_MOUNT,
        "mode":  "updinfo",
        "song":  song,
    }
    if cover_url:
        params["url"] = cover_url
    url = f"http://localhost:8000/admin/metadata?{urllib.parse.urlencode(params)}"
    credentials = base64.b64encode(f"{ICECAST_ADMIN}:{ICECAST_ADMIN_PW}".encode()).decode()
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {credentials}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                log.info(f"Icecast metadata updated: {song}" + (f" [cover: {cover_url}]" if cover_url else " [no cover]"))
            else:
                log.warning(f"Icecast metadata update returned HTTP {resp.status}")
    except Exception as e:
        log.warning(f"Failed to update Icecast metadata: {e}")

# ---------------------------------------------------------------------------
# Cover art download
# ---------------------------------------------------------------------------

def download_cover(url: str) -> bool:
    """Download cover art from Shazam URL and save locally for Icecast to serve.
    Writes to a temp file first then renames atomically to avoid partial reads."""
    if not url:
        return False
    tmp_path = COVER_LOCAL_PATH + ".tmp"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
        with open(tmp_path, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, COVER_LOCAL_PATH)
        log.info(f"Cover art downloaded ({len(data)} bytes)")
        return True
    except Exception as e:
        log.warning(f"Failed to download cover art: {e}")
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return False

# ---------------------------------------------------------------------------
# Audio capture
# ---------------------------------------------------------------------------

def capture_audio(output_path: str) -> bool:
    """Capture CAPTURE_SECONDS of audio from the Icecast stream."""
    cmd = [
        "ffmpeg",
        "-y",
        "-i", ICECAST_URL,
        "-t", str(CAPTURE_SECONDS),
        "-ar", "44100",
        "-ac", "2",
        "-f", "wav",
        output_path,
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=CAPTURE_SECONDS + 10,
        )
        return result.returncode == 0 and os.path.getsize(output_path) > 0
    except subprocess.TimeoutExpired:
        log.warning("ffmpeg timed out capturing audio")
        return False
    except Exception as e:
        log.error(f"ffmpeg error: {e}")
        return False

# ---------------------------------------------------------------------------
# Shazam recognition
# ---------------------------------------------------------------------------

async def recognize(audio_path: str) -> dict:
    shazam = Shazam()
    try:
        result = await shazam.recognize(audio_path)
        track = result.get("track", {})
        if not track:
            return {}

        # Extract album from metadata sections
        album = "Unknown"
        for section in track.get("sections", []):
            for meta in section.get("metadata", []):
                if meta.get("title", "").lower() == "album":
                    album = meta.get("text", "Unknown")
                    break

        # Extract cover art URL
        images = track.get("images", {})
        cover = images.get("coverarthq") or images.get("coverart") or ""

        return {
            "title":  track.get("title", "Unknown"),
            "artist": track.get("subtitle", "Unknown"),
            "album":  album,
            "cover":  cover,
        }
    except Exception as e:
        log.error(f"Shazam error: {e}")
        return {}

# ---------------------------------------------------------------------------
# Clear cover art
# ---------------------------------------------------------------------------

def clear_cover():
    """Remove the local cover art file so the page shows the placeholder."""
    try:
        if os.path.exists(COVER_LOCAL_PATH):
            os.remove(COVER_LOCAL_PATH)
            log.info("Cover art cleared")
    except Exception as e:
        log.warning(f"Failed to clear cover art: {e}")

# ---------------------------------------------------------------------------
# Handle a recognized track — download cover and update Icecast
# ---------------------------------------------------------------------------

def handle_track(track: dict):
    """Download cover art and push metadata to Icecast."""
    cover_url = ""
    if track.get("cover") and download_cover(track["cover"]):
        cover_url = COVER_PUBLIC_URL
    update_icecast_metadata(track["artist"], track["title"], cover_url)

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def main():
    log.info("Starting Shazam Vinylstreamer service")

    last_track   = None
    prev_listeners = 0

    while True:
        # Poll until at least one listener is active
        listeners = get_listener_count()
        if listeners < 1:
            log.info("No active listeners — waiting...")
            if prev_listeners >= 1:
                update_icecast_metadata("Paused", "", "")
            prev_listeners = 0
            await asyncio.sleep(15)
            continue

        # First listener just tuned in — identify current track immediately
        if prev_listeners == 0 and listeners >= 1:
            log.info("First listener detected — running immediate recognition...")
            update_icecast_metadata("Detecting...", "", "")
            await asyncio.sleep(5)  # Let stream buffer settle before capturing
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp_path = tmp.name
            try:
                if capture_audio(tmp_path):
                    track = await recognize(tmp_path)
                    if track and track != last_track:
                        log.info(f"Now playing: {track['artist']} — {track['title']}")
                        last_track = track
                        handle_track(track)
                    elif not track:
                        log.info("No match on immediate recognition")
                        last_track = None
                        clear_cover()
                        update_icecast_metadata("Unknown", "", "")
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        prev_listeners = listeners

        # Regular poll
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            log.info(f"Capturing {CAPTURE_SECONDS}s from Icecast stream...")
            if not capture_audio(tmp_path):
                log.warning("Audio capture failed — is Icecast running?")
            else:
                log.info("Sending to Shazam...")
                track = await recognize(tmp_path)

                if track:
                    if track != last_track:
                        log.info(f"New track: {track['artist']} — {track['title']}")
                        last_track = track
                        handle_track(track)
                    else:
                        log.info("Track unchanged, skipping metadata update")
                else:
                    log.info(f"No match found — will try again in {POLL_INTERVAL}s")
                    last_track = None
                    clear_cover()
                    update_icecast_metadata("Unknown", "", "")
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

        log.info(f"Waiting {POLL_INTERVAL}s before next attempt...")
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
