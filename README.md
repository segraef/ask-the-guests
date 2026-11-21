<p align="center">
  <img src=".github/logo.svg" width="96" alt="ask-the-guests logo">
</p>

<h1 align="center">ask-the-guests</h1>

<p align="center">Ask what hundreds of podcast guests said about anything, with a timestamp link for every point.</p>

---

A [Claude Code](https://claude.com/claude-code) skill plus two small Python scripts. Download the full
transcripts of the podcasts you like once (YouTube captions), then ask questions in plain language.
Claude finds the relevant episodes, reads each one in full, and answers with what the guests agree on,
where they disagree, and a link to the exact moment they said it.

No AI API key, no database server, no vector store: just JSON files and a self-building SQLite index.
Transcripts stay on your machine.

## Setup

You need [Claude Code](https://claude.com/claude-code) and Python 3.11 or newer (`python3 --version`;
on a Mac without it: `brew install python`).

```bash
git clone https://github.com/segraef/ask-the-guests && cd ask-the-guests
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Pick your podcasts

Edit [`podcasts.toml`](podcasts.toml). It ships with Diary of a CEO, Joe Rogan and Lex Fridman; remove
them or add any podcast that is on YouTube:

```toml
[huberman]                                        # short key, used in commands
name = "Huberman Lab"
channel = "https://www.youtube.com/@hubermanlab"  # channel or playlist URL
min_minutes = 40                                  # skip clips and shorts (default 20)
title_include = '#\d+'                            # optional: only titles matching this regex
title_exclude = '(?i)clip|trailer'                # optional: drop titles matching this regex
languages = ["en"]                                # optional: caption languages, e.g. ["de"]
```

Write regular expressions in 'single quotes'. Every option is explained at the top of the file.

## Download

Run from a home connection; YouTube blocks most cloud servers and VPNs.

```bash
.venv/bin/python ingest.py doac --max-new 3   # try it: the 3 newest episodes of one podcast
.venv/bin/python ingest.py                    # everything in podcasts.toml (resumable)
.venv/bin/python ingest.py --status           # progress per podcast
```

The first run of a podcast lists its whole channel once, which can take a few minutes before the first
download starts.

Downloads are slow on purpose so YouTube doesn't flag your connection: about 13 transcripts an hour.
A big back catalogue takes days, so leave it running (`caffeinate -i` on a Mac keeps it awake). If
YouTube starts blocking you, the script stops by itself; wait a day before running it again.

Each run checks the newest videos first (playlists are re-listed in full) and skips what you already have, so running it weekly keeps
everything up to date.

## Ask

Open Claude Code in this folder and run the skill:

```
/ask-the-guests what do guests recommend for weight loss?
/ask-the-guests quick: how do I get a toddler to sleep?
/ask-the-guests doac: what do founders say about hiring?
```

`quick` reads 5 episodes instead of 12; a podcast key limits the search to that podcast. You get:

- **What most guests agree on**
- **Where they disagree**, and which side the cited evidence supports
- **A best-supported plan**, every point with a timestamp link
- **Caveats**: credentials, contested claims, gaps in coverage

You can also search directly: `.venv/bin/python search.py "weight loss" ozempic --show doac`.

## Personal use

Transcripts are for your own research. The `data/` folder is git-ignored; don't publish or redistribute
it. Answers summarise what podcast guests said; they are not medical, legal or financial advice.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests
```
