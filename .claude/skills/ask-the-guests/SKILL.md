---
name: ask-the-guests
description: Answer a question from what podcast guests actually said, using the full episode transcripts downloaded into this repo (the podcasts listed in podcasts.toml), with episode titles and timestamp links. Use for advice questions ("what do guests recommend for weight loss / sleep / young kids"), cross-guest comparisons (consensus vs disagreement), single-episode summaries, or "who talked about X". Arguments are the question, optionally with a podcast key from podcasts.toml or "quick".
---

# Ask the guests

Answers come only from the transcripts in `data/transcripts/<podcast>/<video_id>.json`. Never answer
from general knowledge; if the transcripts don't cover it, say so.

Run everything from the repo root with `.venv/bin/python` (the virtualenv from the README; it needs
Python 3.11+). If `data/transcripts` has no files yet, tell the user to download first, e.g.
`.venv/bin/python ingest.py <podcast> --max-new 20` (keys are in `podcasts.toml`), and stop.

## 1. Find the episodes (under a second per search)

1. Turn the question into 10 to 25 search terms: synonyms, plural forms, brand and drug names, and the
   everyday words people say on air (e.g. weight loss: "weight loss" "fat loss" "lose weight" obesity
   ozempic wegovy semaglutide "calorie deficit" fasting).
   Terms match the START of a word, so a short stem catches unwanted words: "infant" also matches
   "infantry", "attachment" matches relationship attachment styles. Prefer specific phrases.
2. Rank episodes:
   `.venv/bin/python search.py --top 25 --passages 2 <terms>` (add `--show <key>` to search one podcast; keys are
   the section names in `podcasts.toml`).
   Episodes whose title matches a term are listed first, then by number of matching passages.
   Searches use a local index file (`data/index.db`) that builds and updates itself; nothing to start.
   Searching is instant, so try a second or third set of terms rather than guessing.
3. Pick the episodes to read from the list and the sample passages: dedicated episodes first, then the
   ones with many passages that are really about the topic. Skip noise (a war episode matching
   "infantry"). Default 12; "quick" means 5; a single-episode question means just that episode.
   Leave out guests whose main claims are medically contested, or flag them clearly if you include them.
   If fewer than about 5 episodes are really on topic, say so up front and read only those.

## 2. Read them in parallel (about 1 to 2 minutes)

Start ONE subagent PER EPISODE with the Agent tool, all in ONE message so they run in parallel; the
reading then takes as long as the longest single episode. Each agent reads its whole transcript, so no
context is lost. Give each agent this brief (fill in the question, the repo's absolute path and the id):

> Extract what this podcast guest says about: <question>.
> Working directory: <repo root>. Run `.venv/bin/python search.py --episode <id>` and read the whole
> transcript (if the output is truncated, Read the saved tool-result file).
> Transcripts have [hh:mm:ss] markers about every 60 s; the url is
> https://www.youtube.com/watch?v=<id>; build links as <url>&t=<seconds>s.
> Return: guest and credentials as stated in the episode; relevance (high / some /
> little); the guest's overall view in one sentence; up to 8 concrete recommendations with specifics
> (amounts, ages, timing, studies cited) and a timestamp link; warnings and caveats with links; one short
> verbatim quote with a link.
> Rules: skip sponsor and ad reads. Auto-captions have no speaker names (">>" marks a speaker change), so
> attribute a point to the guest only when the context makes it clear. Never invent numbers or studies
> that are not in the transcript.

## 3. Combine (about a minute)

Write the answer in plain language:

1. One line: how many episodes mention the topic, which ones were read, and how long it took.
2. **What most guests agree on**: points made by 3 or more guests, each with 1 or 2 timestamp links.
3. **Where they disagree**: who says what, with links. If the episodes cite controlled studies for one
   side, say which side the evidence they cite supports.
4. **Best-supported plan**: 4 to 7 concrete steps drawn from the above, with links.
5. Caveats: guests' credentials where it matters (naturopath vs medical doctor), contested claims,
   "summary of what podcast guests said, not medical advice" for health topics, and coverage gaps
   (`.venv/bin/python ingest.py --status` shows how much of each podcast is downloaded).

Every point needs an episode title or guest name and a timestamp link. No link, no claim.
