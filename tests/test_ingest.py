"""Download logic without network: channel listing, filters, pacing, blocks and skipped videos.
Run from the repo root: python -m unittest discover -s tests"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ingest


class IpBlocked(Exception):  # same class name as youtube_transcript_api's
    pass


class TranscriptsDisabled(Exception):  # permanent: captions are off
    pass


class ConnectionError(Exception):  # transient: Wi-Fi dropped
    pass


class FakeApi:
    def __init__(self):
        self.asked, self.block_on, self.fail = [], None, {}

    def fetch(self, video_id, languages):
        self.asked.append(video_id)
        if len(self.asked) == self.block_on:
            raise IpBlocked()
        if video_id in self.fail:
            raise self.fail[video_id]()
        return mock.Mock(is_generated=True, to_raw_data=lambda: [{"start": 0.0, "text": video_id}])


def eps(*ids, minutes=60, title="ep #{}"):
    return [{"id": i, "title": title.format(i), "duration": minutes * 60} for i in ids]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.channels = {"https://www.youtube.com/@a/videos": eps("a1", "a2"),
                         "https://www.youtube.com/@b/videos": eps("b1", "b2")}
        self.listed = []
        self.sleeps = []
        self.api = FakeApi()
        config = root / "podcasts.toml"
        config.write_text('[a]\nchannel = "https://www.youtube.com/@a"\n[b]\nchannel = "https://www.youtube.com/@b/"\n')

        def fake_list(url, newest=None):
            self.listed.append((url, newest))
            return self.channels[url][:newest]

        for p in (mock.patch.object(ingest, "DATA", root / "transcripts"),
                  mock.patch.object(ingest, "EPISODES", root / "episodes"),
                  mock.patch.object(ingest, "UNAVAILABLE", root / "unavailable.tsv"),
                  mock.patch.object(ingest, "CONFIG", config),
                  mock.patch.object(ingest, "list_videos", fake_list),
                  mock.patch.object(ingest, "make_client", lambda: self.api),
                  mock.patch.object(ingest.time, "sleep", self.sleeps.append)):
            self.enterContext(p)
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.addCleanup(self.tmp.cleanup)

    def run_show(self, key="a", show=None, pacer=None, **kw):
        return ingest.ingest_show(key, show or {"channel": f"https://www.youtube.com/@{key}"},
                                  pacer or ingest.Pacer(), self.api, **kw)


class Pacing(Base):
    def test_pauses_after_every_batch_of_three(self):
        self.channels["https://www.youtube.com/@a/videos"] = eps(*[f"v{i}" for i in range(7)])
        self.assertFalse(self.run_show(pacer=ingest.Pacer(batch=3, delay=30, pause_min=12)))
        self.assertEqual(len(self.api.asked), 7)
        self.assertEqual([i for i, s in enumerate(self.sleeps) if s == 12 * 60], [2, 5])  # d d P d d P
        self.assertTrue(all(30 <= s <= 60 for s in self.sleeps if s != 12 * 60))

    def test_pacing_carries_over_to_the_next_podcast(self):
        self.assertEqual(ingest.main([]), 0)
        self.assertEqual(self.api.asked, ["a1", "a2", "b1", "b2"])
        self.assertEqual(self.sleeps[-1], 12 * 60)  # 4th request overall pauses, not a fresh batch for b

    def test_block_stops_the_run_and_skips_remaining_podcasts(self):
        self.api.block_on = 2
        self.assertEqual(ingest.main([]), 1)
        self.assertEqual(self.api.asked, ["a1", "a2"])  # b never asked
        self.assertEqual([p.stem for p in (ingest.DATA / "a").glob("*.json")], ["a1"])

    def test_max_new_stops_early(self):
        self.run_show(max_new=1)
        self.assertEqual(self.api.asked, ["a1"])


class SkippedVideos(Base):
    def test_permanent_failure_on_two_different_days_is_never_asked_again(self):
        self.api.fail = {"a1": TranscriptsDisabled, "a2": ConnectionError}
        with mock.patch.object(ingest, "today", lambda: "2026-01-01"):
            self.run_show()
            self.run_show()  # same day again: still one strike (fresh uploads lack captions for hours)
        self.assertEqual(self.api.asked, ["a1", "a2", "a1", "a2"])
        with mock.patch.object(ingest, "today", lambda: "2026-01-02"):
            self.run_show()  # second day: second strike
        self.api.asked.clear()
        self.run_show()
        self.assertEqual(self.api.asked, ["a2"])  # skipped now; dropped connections are always retried

    def test_five_failures_in_a_row_stop_the_run(self):
        self.channels["https://www.youtube.com/@a/videos"] = eps(*[f"v{i}" for i in range(9)])
        self.api.fail = {f"v{i}": ConnectionError for i in range(9)}
        self.assertEqual(ingest.main(["a", "b"]), 1)
        self.assertEqual(len(self.api.asked), ingest.MAX_FAILS_IN_A_ROW)

    def test_a_success_resets_the_failure_streak(self):
        self.channels["https://www.youtube.com/@a/videos"] = eps(*[f"v{i}" for i in range(12)])
        self.api.fail = {f"v{i}": ConnectionError for i in range(12) if i != 4}  # 4 fail, 1 ok, then 5 fail
        self.assertEqual(ingest.main(["a"]), 1)
        self.assertEqual(len(self.api.asked), 4 + 1 + ingest.MAX_FAILS_IN_A_ROW)

    def test_a_channel_that_cannot_be_listed_is_skipped(self):
        def broken(url, newest=None):
            if "@a" in url:
                raise ingest.yt_dlp.utils.DownloadError("This channel does not exist")
            return self.channels[url]
        with mock.patch.object(ingest, "list_videos", broken):
            self.assertEqual(ingest.main([]), 0)
        self.assertEqual(self.api.asked, ["b1", "b2"])

    def test_downloaded_episodes_are_not_fetched_again(self):
        self.run_show()
        self.run_show()
        self.assertEqual(self.api.asked, ["a1", "a2"])
        record = json.loads((ingest.DATA / "a" / "a1.json").read_text())
        self.assertEqual((record["show"], record["url"], record["transcript"]),
                         ("a", "https://www.youtube.com/watch?v=a1", "[00:00:00] a1"))


class Listing(Base):
    def test_first_run_lists_whole_channel_then_only_the_newest_page(self):
        url = "https://www.youtube.com/@a/videos"
        self.channels[url] = eps("old1", "old2")
        self.run_show()
        self.channels[url] = eps("new1", "old1", "old2")
        self.run_show()
        self.assertEqual(self.listed, [(url, None), (url, ingest.NEWEST)])
        self.assertEqual(self.api.asked, ["old1", "old2", "new1"])

    def test_relists_everything_when_the_newest_page_has_nothing_known(self):
        url = "https://www.youtube.com/@a/videos"
        self.channels[url] = eps("old1")
        self.run_show()
        with mock.patch.object(ingest, "NEWEST", 1):
            self.channels[url] = eps("new2", "new1", "old1")  # 2 new videos, newest page holds only 1
            self.run_show()
        self.assertEqual(self.listed[-1], (url, None))
        self.assertEqual(self.api.asked, ["old1", "new2", "new1"])

    def test_playlists_are_listed_in_full_every_run(self):
        url = "https://www.youtube.com/playlist?list=PL1"
        self.channels[url] = eps("p1", "p2")
        show = {"channel": url}
        self.run_show("p", show)
        self.channels[url] = eps("p1", "p2", "p3")  # playlists grow at the end
        self.run_show("p", show)
        self.assertEqual(self.listed, [(url, None), (url, None)])
        self.assertEqual(self.api.asked, ["p1", "p2", "p3"])

    def test_status_counts_only_current_episodes(self):
        self.run_show()
        (ingest.DATA / "a" / "old-clip.json").write_text("{}")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ingest.status({"a": {"channel": "https://www.youtube.com/@a"}})
        self.assertIn("2 of 2 downloaded, 0 without captions, 0 to go", out.getvalue())

    def test_filters(self):
        show = {"channel": "x", "min_minutes": 30, "title_include": r"#\d+", "title_exclude": "(?i)clip"}
        self.assertTrue(ingest.is_episode(show, {"title": "Guest | Podcast #12", "duration": 3600}))
        self.assertFalse(ingest.is_episode(show, {"title": "Guest | Podcast #12", "duration": 600}))
        self.assertFalse(ingest.is_episode(show, {"title": "Guest | Podcast", "duration": 3600}))
        self.assertFalse(ingest.is_episode(show, {"title": "CLIP: Guest #12", "duration": 3600}))
        self.assertTrue(ingest.is_episode({"channel": "x"}, {"title": "anything", "duration": 20 * 60}))

    def test_channel_urls(self):
        self.assertEqual(ingest.videos_url("https://www.youtube.com/@lexfridman/"),
                         "https://www.youtube.com/@lexfridman/videos")
        self.assertEqual(ingest.videos_url("https://www.youtube.com/channel/UC123"),
                         "https://www.youtube.com/channel/UC123/videos")
        self.assertEqual(ingest.videos_url("https://youtube.com/@hubermanlab?si=AbC123"),
                         "https://youtube.com/@hubermanlab/videos")
        for unchanged in ("https://www.youtube.com/@x/videos", "https://www.youtube.com/@x/streams",
                          "https://www.youtube.com/playlist?list=PL123"):
            self.assertEqual(ingest.videos_url(unchanged), unchanged)


class Config(unittest.TestCase):
    def check(self, text):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "podcasts.toml")
            path.write_text(text)
            return ingest.load_config(path)

    def test_valid(self):
        self.assertEqual(list(self.check('[my-show]\nchannel = "https://www.youtube.com/@x"\n')), ["my-show"])

    def test_rejects_bad_settings_with_a_message(self):
        for text in ('[x]\nname = "X"\n',                                   # no channel
                     '["My Show"]\nchannel = "u"\n',                       # bad key
                     "[x]\nchannel = 'u'\ntitle_include = '#(\\d+'\n",     # broken regex
                     "[x]\nchannel = 'u'\nmin_minutes = 'forty'\n",        # not a number
                     '[x]\nchannel = "u"\ntitle_include = "#\\d+"\n'):     # backslash in double quotes
            with self.assertRaises(SystemExit):
                self.check(text)

    def test_shipped_config_is_valid(self):
        self.assertTrue(ingest.load_config(Path(__file__).parent.parent / "podcasts.toml"))

    def test_counts_must_be_positive(self):
        for args in (["--max-new", "0"], ["--batch", "0"]):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                ingest.main(args)


class Timestamps(unittest.TestCase):
    def test_paragraph_every_minute_with_marker(self):
        text = ingest.to_timestamped_text([{"start": 0, "text": "a"}, {"start": 30, "text": "b"},
                                           {"start": 61, "text": "c\nd"}, {"start": 3725, "text": "e"}])
        self.assertEqual(text, "[00:00:00] a b\n[00:01:01] c d\n[01:02:05] e")


if __name__ == "__main__":
    unittest.main()
