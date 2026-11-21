<p align="center">
  <img src=".github/logo.svg" width="96" alt="ask-the-guests logo">
</p>

<h1 align="center">ask-the-guests</h1>

<p align="center">Ask what hundreds of podcast guests said about anything, with a timestamp link for every point.</p>

---

A [Claude Code](https://claude.com/claude-code) skill. Download the transcripts of the podcasts you like
once, then ask questions. Claude reads the relevant episodes and tells you what the guests agree on,
where they disagree, and links to the exact moment they said it. Everything stays on your machine.

## Setup

Needs Python 3.11+.

```bash
git clone https://github.com/segraef/ask-the-guests && cd ask-the-guests
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Pick your podcasts

Edit [`podcasts.toml`](podcasts.toml). Any podcast on YouTube works:

```toml
[huberman]
channel = "https://www.youtube.com/@hubermanlab"
```

## Download

```bash
.venv/bin/python ingest.py huberman --max-new 3   # try it with 3 episodes
.venv/bin/python ingest.py                        # everything
```

Downloads are slow on purpose (about 13 an hour) so YouTube doesn't block you. Run it from home and
leave it running; run it again any time to fetch new episodes.

## Ask

Open Claude Code in this folder:

```
/ask-the-guests what do guests recommend for weight loss?
```

Transcripts are for personal use only; don't share them.
