"""YouTube discovery and timestamped English captions for Learn."""

from __future__ import annotations

import asyncio
import html
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
CUE_TIME = re.compile(r"(?P<start>(?:\d+:)?\d{2}:\d{2}\.\d{3})\s+-->\s+(?P<end>(?:\d+:)?\d{2}:\d{2}\.\d{3})")
TAGS = re.compile(r"<[^>]*>")
_slots = asyncio.Semaphore(3)


class YouTubeError(Exception):
    pass


def parse_video_id(value: str) -> str:
    value = value.strip()
    if VIDEO_ID.fullmatch(value):
        return value
    try:
        url = urlsplit(value)
        if url.scheme not in ("http", "https"):
            raise ValueError
        if url.hostname == "youtu.be":
            value = url.path.strip("/")
        elif url.hostname in ("youtube.com", "www.youtube.com", "m.youtube.com"):
            if url.path == "/watch":
                value = parse_qs(url.query).get("v", [""])[0]
            else:
                path = url.path.strip("/").split("/")
                value = path[1] if len(path) == 2 and path[0] in ("embed", "shorts", "live") else ""
        else:
            raise ValueError
        if VIDEO_ID.fullmatch(value):
            return value
    except ValueError:
        pass
    raise YouTubeError("Enter a YouTube video URL or its 11-character ID.")


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def _invoke(executable: str, args: list[str], target: str, cwd: str | None) -> str:
    binary = shutil.which(executable)
    if not binary:
        raise YouTubeError("Install yt-dlp and make it available on your PATH to find videos and captions.")
    command = [
        binary, "--ignore-config", "--no-update", "--no-playlist", "--no-cache-dir",
        "--socket-timeout", "12", "--retries", "0", "--extractor-retries", "0",
        "--skip-download", "--no-progress", "--encoding", "utf-8",
    ]
    if shutil.which("node"):
        command.extend(["--js-runtimes", "node"])
    try:
        response = subprocess.run(
            [*command, *args, "--", target], cwd=cwd, capture_output=True,
            encoding="utf-8", errors="replace", timeout=45, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise YouTubeError("YouTube did not respond in time. Try again shortly.") from exc
    if response.returncode:
        if "429" in response.stderr:
            raise YouTubeError("YouTube is limiting requests. Wait a moment and try again.")
        if "resolve" in response.stderr.lower():
            raise YouTubeError("Could not reach YouTube. Check the app's network access.")
        raise YouTubeError("YouTube could not provide this video or caption track.")
    return response.stdout


async def run(executable: str, args: list[str], target: str, cwd: str | None = None) -> str:
    async with _slots:
        return await asyncio.to_thread(_invoke, executable, args, target, cwd)


def _json(output: str) -> dict:
    try:
        result = json.loads(output)
        if isinstance(result, dict):
            return result
    except (ValueError, TypeError):
        pass
    raise YouTubeError("YouTube returned incomplete video information.")


async def search(executable: str, query: str, size: int = 4) -> list[dict]:
    result = _json(await run(executable, ["--flat-playlist", "--dump-single-json"], f"ytsearch{size}:{query}"))
    return [item for item in result.get("entries", []) if isinstance(item, dict) and VIDEO_ID.fullmatch(item.get("id") or "")]


async def describe(executable: str, video_id: str, comments: bool) -> dict:
    options = ["--dump-single-json"]
    if comments:
        options += ["--get-comments", "--extractor-args",
                    "youtube:comment_sort=top;max_comments=12,12,0,0,1;skip=hls,dash"]
    else:
        options += ["--extractor-args", "youtube:skip=hls,dash"]
    return _json(await run(executable, options, watch_url(video_id)))


def comment_sample(metadata: dict) -> list[str]:
    comments = []
    seen = set()
    for row in metadata.get("comments") or []:
        if not isinstance(row, dict) or not isinstance(row.get("text"), str):
            continue
        clean = " ".join(row["text"].split())[:500]
        key = clean.casefold()
        if len(clean.split()) >= 4 and key not in seen:
            seen.add(key)
            comments.append(clean)
    return comments[:12]


def caption_track(metadata: dict) -> tuple[str, str]:
    for property_, source in (("subtitles", "creator"), ("automatic_captions", "automatic")):
        tracks = metadata.get(property_) or {}
        order = ("en", "en-US", "en-GB", "en-orig") if source == "creator" else ("en-orig", "en-US", "en-GB", "en")
        for code in order:
            if any(r.get("ext") == "vtt" for r in tracks.get(code, [])):
                return code, source
    raise YouTubeError("This video has no usable English captions. Choose another video.")


def _seconds(value: str) -> float:
    result = 0.0
    for part in value.split(":"):
        result = result * 60 + float(part)
    return result


def parse_vtt(source: str) -> list[dict]:
    """Unroll live-style caption repeats and combine cues into cited segments."""
    pieces: list[dict] = []
    recent: list[str] = []
    last_end = -10.0
    for block in re.split(r"\n\s*\n", source.replace("\r\n", "\n").lstrip("\ufeff")):
        lines = block.splitlines()
        for index, line in enumerate(lines):
            match = CUE_TIME.match(line)
            if match:
                break
        else:
            continue
        start, end = (_seconds(match[name]) for name in ("start", "end"))
        if end <= start:
            continue
        if start > last_end + 1:
            recent = []
        last_end = max(end, last_end)
        additions: list[str] = []
        for caption in lines[index + 1:]:
            cleaned = html.unescape(TAGS.sub("", caption)).replace("\u200b", "").strip()
            if not cleaned or (cleaned.startswith("[") and cleaned.endswith("]")):
                continue
            words = cleaned.split()
            size = min(len(recent), len(words))
            while size and [w.casefold() for w in recent[-size:]] != [w.casefold() for w in words[:size]]:
                size -= 1
            new_words = words[size:]
            additions.extend(new_words)
            recent = (recent + new_words)[-48:]
        text = " ".join(additions)
        if not text:
            continue
        if pieces and end - pieces[-1]["start"] <= 25 and len(pieces[-1]["text"]) + len(text) < 550:
            pieces[-1]["end"] = max(end, pieces[-1]["end"])
            pieces[-1]["text"] += " " + text
        else:
            pieces.append({"start": start, "end": end, "text": text[:750]})
    return [{"id": f"S{i}", **piece} for i, piece in enumerate(pieces, 1)]


async def get_captions(executable: str, video_id: str, language: str, source: str) -> list[dict]:
    args = [
        "--no-simulate", "--write-subs" if source == "creator" else "--write-auto-subs",
        "--sub-langs", re.escape(language), "--sub-format", "vtt", "-o", "%(id)s.%(ext)s",
    ]
    with tempfile.TemporaryDirectory(prefix="mrfinancer-captions-") as scratch:
        await run(executable, args, watch_url(video_id), scratch)
        path = Path(scratch) / f"{video_id}.{language}.vtt"
        if not path.is_file():
            raise YouTubeError("YouTube did not return the requested caption file. Try another video.")
        segments = parse_vtt(path.read_text(encoding="utf-8-sig"))
    if sum(len(row["text"].split()) for row in segments) < 120:
        raise YouTubeError("The English transcript is too short to generate ten useful questions.")
    return segments
