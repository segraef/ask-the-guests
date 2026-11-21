"""
Keyword search across the downloaded transcripts, so Claude Code can answer questions locally.

Usage:
    python search.py "weight loss" "fat loss" ozempic              # all podcasts
    python search.py --show doac --top 30 "weight loss" fasting    # one podcast (its key)
    python search.py --episode <video_id>                          # print one full transcript

Terms are case-insensitive and match the start of a word ("fast" finds "fasting").
Episodes whose title matches come first, then by how many ~60s passages mention a term;
each passage prints with a YouTube link that opens at that moment.

Searches go through data/index.db, a SQLite full-text index (one file, built into Python,
nothing to install or run). It builds itself on first use (about a minute for 1,000+ episodes)
and picks up new, changed or removed transcripts on every search. Delete it to rebuild.
"""
import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

DATA = Path("data/transcripts")
INDEX = Path("data/index.db")
STAMP = re.compile(r"^\[(\d+):(\d\d):(\d\d)\]")
# SQLite 3.43+ can keep only the index (about half the size); older versions also store the text.
FTS_OPTIONS = ", content='', contentless_delete=1" if sqlite3.sqlite_version_info >= (3, 43) else ""


def link(url: str, passage: str) -> str:
    m = STAMP.match(passage)
    return f"{url}&t={int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])}s" if m else url


def open_index() -> sqlite3.Connection:
    """Open the index and bring it up to date with the transcript files (by modification time and
    folder). Passage text is read back from the JSON files. Safe to run several searches at once."""
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(INDEX, timeout=300, isolation_level=None)
    db.executescript(f"""
        CREATE TABLE IF NOT EXISTS episodes (video_id TEXT PRIMARY KEY, show TEXT, title TEXT, mtime REAL);
        CREATE TABLE IF NOT EXISTS lines (id INTEGER PRIMARY KEY, video_id TEXT, n INTEGER);
        CREATE INDEX IF NOT EXISTS lines_video ON lines (video_id);
        CREATE VIRTUAL TABLE IF NOT EXISTS passages USING fts5(text{FTS_OPTIONS});
    """)
    db.execute("BEGIN IMMEDIATE")  # one updater at a time; a second search waits, then sees the result
    known = {vid: (show, mtime) for vid, show, mtime in db.execute("SELECT video_id, show, mtime FROM episodes")}
    files = {p.stem: p for p in DATA.glob("*/*.json")}
    changed = [p for vid, p in files.items() if known.get(vid) != (p.parent.name, p.stat().st_mtime)]
    for vid in [vid for vid in known if vid not in files] + [p.stem for p in changed if p.stem in known]:
        db.execute("DELETE FROM passages WHERE rowid IN (SELECT id FROM lines WHERE video_id = ?)", (vid,))
        db.execute("DELETE FROM lines WHERE video_id = ?", (vid,))
        db.execute("DELETE FROM episodes WHERE video_id = ?", (vid,))
    for p in changed:
        try:
            ep = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:  # half-written or broken file: skip it, retry next search
            print(f"skipping {p}: {e}", file=sys.stderr)
            continue
        db.execute("INSERT INTO episodes VALUES (?, ?, ?, ?)", (p.stem, p.parent.name, ep["title"], p.stat().st_mtime))
        for n, line in enumerate(ep["transcript"].split("\n")):
            rowid = db.execute("INSERT INTO lines (video_id, n) VALUES (?, ?)", (p.stem, n)).lastrowid
            db.execute("INSERT INTO passages (rowid, text) VALUES (?, ?)", (rowid, line))
    db.execute("COMMIT")
    return db


def search(terms: list[str], show: str | None = None) -> list[tuple[dict, list[str]]]:
    words = [re.findall(r"\w+", t) for t in terms]
    words = [w for w in words if w]
    if not words:
        return []
    # each term becomes a prefix phrase: "weight loss"* matches "weight loss", "weight-losses", ...
    query = " OR ".join('"' + " ".join(w) + '"*' for w in words)
    db = open_index()
    rows = db.execute(
        "SELECT e.show, l.video_id, l.n FROM passages p JOIN lines l ON l.id = p.rowid JOIN episodes e USING (video_id)"
        " WHERE passages MATCH ? AND (? IS NULL OR e.show = ?)", (query, show, show)).fetchall()
    db.close()
    found = {}
    for sh, vid, n in rows:
        found.setdefault((sh, vid), set()).add(n)
    hits = []
    for (sh, vid), ns in found.items():
        ep = json.loads((DATA / sh / f"{vid}.json").read_text(encoding="utf-8"))
        lines = ep.pop("transcript").split("\n")
        ep["show"] = sh  # the folder is the podcast key, even if the file says otherwise
        hits.append((ep, [lines[n] for n in sorted(ns) if n < len(lines)]))
    title_pat = re.compile("|".join(r"\b" + r"\W+".join(map(re.escape, w)) for w in words), re.I)
    return sorted(hits, key=lambda h: (not title_pat.search(h[0]["title"]), -len(h[1])))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Search the downloaded podcast transcripts.")
    p.add_argument("terms", nargs="*")
    p.add_argument("--show", help="only this podcast (its key in podcasts.toml)")
    p.add_argument("--top", type=int, default=20, help="episodes to print passages for")
    p.add_argument("--passages", type=int, default=8, help="passages per episode")
    p.add_argument("--episode", help="print the full transcript of one video id")
    a = p.parse_args()

    if not next(DATA.glob("*/*.json"), None):
        raise SystemExit("No transcripts yet. Download some first: .venv/bin/python ingest.py <podcast> --max-new 3")
    if a.episode:
        path = next(DATA.glob(f"*/{a.episode}.json"), None)
        if not path:
            raise SystemExit(f"No downloaded transcript for video id {a.episode}")
        ep = json.loads(path.read_text(encoding="utf-8"))
        print(f"# {ep['title']}\n{ep['url']}\n\n{ep['transcript']}")
        raise SystemExit
    if not a.terms:
        p.error("give search terms or --episode")

    hits = search(a.terms, a.show)
    total = len(list(DATA.glob(f"{a.show or '*'}/*.json")))
    print(f"{len(hits)} of {total} episodes mention {a.terms}\n")
    for ep, passages in hits[:max(a.top, 50)]:
        print(f"{len(passages):4d}  {ep['show']}  {ep['video_id']}  {ep['title'][:90]}")
    if len(hits) > max(a.top, 50):
        print(f"   ... {len(hits) - max(a.top, 50)} more")
    for ep, passages in hits[:a.top]:
        print(f"\n## {ep['title']}\n{ep['url']}  ({len(passages)} passages, showing {min(len(passages), a.passages)})")
        for passage in passages[:a.passages]:
            print(f"- {link(ep['url'], passage)}\n  {passage}")
