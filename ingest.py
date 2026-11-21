"""
Download full-episode transcripts (YouTube captions) for the podcasts listed in podcasts.toml.

Usage:
    python ingest.py                   # every podcast in podcasts.toml
    python ingest.py doac lex          # selected podcasts (their keys in podcasts.toml)
    python ingest.py doac --max-new 3  # quick test: the 3 newest missing episodes
    python ingest.py --status          # downloaded vs. available, per podcast

Slow on purpose, so YouTube does not flag your connection: 3 requests 30-60 s apart, then a
12 minute pause (about 13 transcripts an hour; change with --batch, --delay, --pause). The run
stops at the first sign of a block (or 5 failures in a row): wait at least a day before running
again. Run it from a home connection; YouTube blocks most cloud servers and VPNs (or set
TRANSCRIPT_PROXY).

Each run checks the newest videos of every channel first and downloads only what is missing, so
running it weekly keeps everything current. The first run lists the whole channel once and caches
the list in data/episodes/<key>.json. Playlists are listed in full every run (they grow at the end).

A video that fails for a permanent reason (captions off, age-restricted, no captions in the
podcast's languages) on two different days is skipped from then on. Those failures are logged in
data/unavailable.tsv; delete a video's lines there to try it again.
"""
import argparse
import datetime
import json
import os
import random
import re
import time
import tomllib
from collections import Counter
from pathlib import Path

import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import GenericProxyConfig

CONFIG = Path("podcasts.toml")
DATA = Path("data/transcripts")
EPISODES = Path("data/episodes")
UNAVAILABLE = Path("data/unavailable.tsv")
NEWEST = 30  # videos checked on every run; older ones come from the cached channel list
LANGUAGES = ["en", "en-US", "en-GB"]
PERMANENT = {"TranscriptsDisabled", "NoTranscriptFound", "VideoUnavailable", "AgeRestricted", "InvalidVideoId"}
# VideoUnplayable is left out on purpose: the library also raises it for "try again later" rate limits.
BLOCKED = {"IpBlocked", "RequestBlocked", "PoTokenRequired", "FailedToCreateConsentCookie"}
MAX_FAILS_IN_A_ROW = 5  # stop: probably a block that looks like ordinary errors


def load_config(path: Path | None = None) -> dict:
    path = path or CONFIG
    if not path.exists():
        raise SystemExit(f"{path} not found: run from the repo root, or create it (see README)")
    try:
        shows = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise SystemExit(f"{path}: {e} (write regular expressions in 'single quotes')")
    for key, show in shows.items():
        def bad(msg):
            raise SystemExit(f"{path}: [{key}] {msg}")
        if not re.fullmatch(r"[a-z0-9_-]+", key):
            bad("keys must be lowercase letters, digits, '-' or '_'")
        if not show.get("channel"):
            bad("needs a channel URL")
        if not isinstance(show.get("min_minutes", 0), (int, float)):
            bad("min_minutes must be a number")
        if not isinstance(show.get("languages", []), list):
            bad("languages must be a list, e.g. [\"en\", \"de\"]")
        for field in ("title_include", "title_exclude"):
            try:
                re.compile(show.get(field, ""))
            except re.error as e:
                bad(f"{field} is not a valid regular expression: {e}")
    return shows


def is_playlist(url: str) -> bool:
    return "list=" in url


def videos_url(channel: str) -> str:
    """A channel's videos tab (share-link parameters removed); playlists and explicit tabs stay as they are."""
    if is_playlist(channel):
        return channel
    channel = re.split(r"[?#]", channel)[0].rstrip("/")
    if re.search(r"youtube\.com/(@[^/]+|channel/[^/]+|c/[^/]+|user/[^/]+)$", channel):
        return channel + "/videos"
    return channel


def list_videos(url: str, newest: int | None = None) -> list[dict]:
    """Videos in listing order: id, title, duration in seconds (0 if unknown)."""
    opts = {"extract_flat": "in_playlist", "quiet": True, "skip_download": True}
    if newest:
        opts["playlistend"] = newest  # one page instead of paging through the whole channel
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return [{"id": e["id"], "title": e.get("title") or "", "duration": int(e.get("duration") or 0)}
            for e in info.get("entries") or [] if e and e.get("id")]


def merge(first: list[dict], rest: list[dict]) -> list[dict]:
    ids = {e["id"] for e in first}
    return first + [e for e in rest if e["id"] not in ids]


def episodes(key: str, show: dict) -> list[dict]:
    """The show's episodes. For a channel: the whole channel on the first run, then only the newest
    page plus the cached list (a full relist again if the newest page has nothing in common with the
    cache, i.e. more than NEWEST videos came out since the last run). Playlists: always in full."""
    url, cache = videos_url(show["channel"]), EPISODES / f"{key}.json"
    old = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else []
    new = list_videos(url, newest=NEWEST) if old and not is_playlist(url) else []
    if new and {e["id"] for e in new} & {e["id"] for e in old}:
        eps = merge(new, old)
    else:
        if not old:
            print(f"[{key}] listing the whole channel (first run only, can take a few minutes)", flush=True)
        eps = merge(list_videos(url), old)  # never lose cached episodes to a partial listing
    if eps:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(eps, ensure_ascii=False), encoding="utf-8")
    keep = [e for e in eps if is_episode(show, e)]
    if eps and not keep:
        print(f"[{key}] {len(eps)} videos listed but none passed the filters: check channel, min_minutes and titles in {CONFIG}")
    return keep


def is_episode(show: dict, ep: dict) -> bool:
    return (ep["duration"] >= show.get("min_minutes", 20) * 60
            and re.search(show.get("title_include", ""), ep["title"]) is not None
            and not (show.get("title_exclude") and re.search(show["title_exclude"], ep["title"])))


def to_timestamped_text(snippets: list[dict], every: int = 60) -> str:
    """Caption snippets as paragraphs, each starting with an [hh:mm:ss] marker, about every 60 s."""
    out, buf, next_mark = [], [], 0
    for s in snippets:
        if s["start"] >= next_mark and buf:
            out.append(" ".join(buf))
            buf = []
        if not buf:
            t = int(s["start"])
            buf.append(f"[{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}]")
            next_mark = s["start"] + every
        buf.append(s["text"].replace("\n", " "))
    if buf:
        out.append(" ".join(buf))
    return "\n".join(out)


def make_client() -> YouTubeTranscriptApi:
    proxy = os.getenv("TRANSCRIPT_PROXY")
    if proxy:
        return YouTubeTranscriptApi(proxy_config=GenericProxyConfig(http_url=proxy, https_url=proxy))
    return YouTubeTranscriptApi()


class Pacer:
    """Waits before every YouTube transcript request but the first: delay to 2x delay seconds, and a
    long pause after every `batch` requests. One pacer for the whole run, so moving on to the next
    podcast never resets it. Also counts failures in a row across podcasts."""

    def __init__(self, batch: int = 3, delay: float = 30, pause_min: float = 12):
        self.batch, self.delay, self.pause_min, self.calls, self.fails_in_a_row = batch, delay, pause_min, 0, 0

    def wait(self):
        if self.calls:
            if self.calls % self.batch == 0:
                print(f"  pausing {self.pause_min:g} min after {self.batch} YouTube requests", flush=True)
                time.sleep(self.pause_min * 60)
            else:
                time.sleep(random.uniform(self.delay, 2 * self.delay))
        self.calls += 1


def today() -> str:
    return datetime.date.today().isoformat()


def dead_videos() -> set[str]:
    """Videos that failed permanently on two different days (one line per failure in UNAVAILABLE:
    podcast, video id, error, date, title). Two days, because fresh uploads lack captions for hours."""
    lines = UNAVAILABLE.read_text(encoding="utf-8").splitlines() if UNAVAILABLE.exists() else []
    days = {(cols[1], cols[3]) for cols in (line.split("\t") for line in lines) if len(cols) > 3}
    return {vid for vid, n in Counter(vid for vid, _ in days).items() if n >= 2}


def ingest_show(key: str, show: dict, pacer: Pacer, api, max_new: int | None = None) -> bool:
    """Download the show's missing transcripts. Returns True when the whole run should stop (blocked)."""
    outdir = DATA / key
    outdir.mkdir(parents=True, exist_ok=True)
    try:
        eps = episodes(key, show)
    except yt_dlp.utils.DownloadError as e:
        print(f"[{key}] could not list {show['channel']}: {e}", flush=True)
        return "not a bot" in str(e)  # YouTube's bot check: stop; anything else: next podcast
    print(f"[{key}] {len(eps)} episodes", flush=True)
    dead = dead_videos()
    ok = cached = skipped = failed = 0
    for i, ep in enumerate(eps, 1):
        path = outdir / f"{ep['id']}.json"
        if path.exists():
            cached += 1
            continue
        if ep["id"] in dead:
            skipped += 1
            continue
        pacer.wait()
        try:
            fetched = api.fetch(ep["id"], languages=show.get("languages", LANGUAGES))
        except Exception as e:  # captions off, age-restricted, dropped connection, blocked IP ...
            failed += 1
            pacer.fails_in_a_row += 1
            name, reason = type(e).__name__, str(getattr(e, "reason", "") or "")[:80]
            print(f"  {i}/{len(eps)} FAIL {name}: {ep['title'][:60]}" + (f" ({reason})" if reason else ""), flush=True)
            if name in PERMANENT:
                with UNAVAILABLE.open("a", encoding="utf-8") as f:
                    f.write(f"{key}\t{ep['id']}\t{name}\t{today()}\t{ep['title'].replace(chr(9), ' ')}\n")
            if name in BLOCKED or "Block" in name or pacer.fails_in_a_row >= MAX_FAILS_IN_A_ROW:
                print(f"  Stopping: YouTube is probably blocking this connection ({pacer.fails_in_a_row} failure(s) in a row)."
                      " Wait at least a day before the next run.", flush=True)
                return True
            continue
        pacer.fails_in_a_row = 0
        record = {"show": key, "video_id": ep["id"], "title": ep["title"], "duration_sec": ep["duration"],
                  "url": f"https://www.youtube.com/watch?v={ep['id']}", "source": "youtube",
                  "auto_generated": fetched.is_generated, "transcript": to_timestamped_text(fetched.to_raw_data())}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)  # never leave a half-written transcript behind
        ok += 1
        print(f"  {i}/{len(eps)} ok  {ep['title'][:70]}", flush=True)
        if max_new is not None and ok >= max_new:
            break
    print(f"[{key}] {ok} new, {cached} already downloaded, {skipped} skipped (no captions), {failed} failed", flush=True)
    return False


def status(shows: dict):
    dead = dead_videos()
    for key, show in shows.items():
        have = {p.stem for p in (DATA / key).glob("*.json")}
        cache = EPISODES / f"{key}.json"
        if not cache.exists():
            print(f"{key:12} {len(have):5} downloaded   (channel not listed yet; run: ingest.py {key})")
            continue
        ids = {e["id"] for e in json.loads(cache.read_text(encoding="utf-8")) if is_episode(show, e)}
        done, gone = len(ids & have), len((ids & dead) - have)
        print(f"{key:12} {done:5} of {len(ids)} downloaded, {gone} without captions, "
              f"{len(ids) - done - gone} to go   {show.get('name', '')}")


def positive_int(value: str) -> int:
    if not value.isdigit() or int(value) < 1:
        raise argparse.ArgumentTypeError("must be a whole number of 1 or more")
    return int(value)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Download podcast transcripts from YouTube.")
    p.add_argument("shows", nargs="*", help="keys from podcasts.toml (default: all)")
    p.add_argument("--max-new", type=positive_int, help="stop after N new transcripts per podcast, newest first")
    p.add_argument("--status", action="store_true", help="show progress per podcast and exit")
    p.add_argument("--batch", type=positive_int, default=3, help="YouTube requests before each long pause")
    p.add_argument("--delay", type=float, default=30, help="seconds between requests (random, up to double)")
    p.add_argument("--pause", type=float, default=12, help="minutes to pause after each batch")
    a = p.parse_args(argv)
    shows = load_config()
    unknown = [k for k in a.shows if k not in shows]
    if unknown:
        p.error(f"not in {CONFIG}: {', '.join(unknown)} (available: {', '.join(shows)})")
    selected = {k: shows[k] for k in (a.shows or shows)}
    if a.status:
        status(selected)
        return 0
    pacer, api = Pacer(a.batch, a.delay, a.pause), make_client()
    for key, show in selected.items():
        if ingest_show(key, show, pacer, api, a.max_new):
            return 1  # do not touch YouTube again for the other podcasts
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
