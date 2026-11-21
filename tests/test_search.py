"""Search rules and the self-updating index. Run from the repo root: python -m unittest discover -s tests"""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

import search


class Search(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "pod").mkdir()
        for vid, text in {
            "one": "[00:00:00] intro\n[00:01:05] weight loss starts in the kitchen\n[01:00:00] fasting helps weight loss",
            "two": "[00:00:00] my father ran\n[00:02:00] Weight Loss drugs",
            "three": "[00:00:00] nothing relevant",
        }.items():
            (root / "pod" / f"{vid}.json").write_text(json.dumps(
                {"show": "pod", "video_id": vid, "title": vid, "url": f"https://www.youtube.com/watch?v={vid}", "transcript": text}))
        self.old = search.DATA, search.INDEX
        search.DATA, search.INDEX = root, root / "index.db"

    def tearDown(self):
        search.DATA, search.INDEX = self.old
        self.tmp.cleanup()

    def test_ranks_by_matching_passages(self):
        hits = search.search(["weight loss"])
        self.assertEqual([(ep["video_id"], len(p)) for ep, p in hits], [("one", 2), ("two", 1)])
        self.assertTrue(hits[0][1][0].startswith("[00:01:05]"))  # passages in transcript order

    def test_terms_match_word_starts_only(self):
        self.assertEqual([ep["video_id"] for ep, _ in search.search(["fast"])], ["one"])
        self.assertEqual(search.search(["ather"]), [])  # "father" must not match

    def test_title_match_ranks_first(self):
        path = search.DATA / "pod" / "two.json"
        path.write_text(json.dumps({**json.loads(path.read_text()), "title": "The weight loss doctor"}))
        self.assertEqual([ep["video_id"] for ep, _ in search.search(["weight loss"])], ["two", "one"])

    def test_show_filter(self):
        (search.DATA / "other").mkdir()
        (search.DATA / "other" / "x.json").write_text(json.dumps(
            {"show": "other", "video_id": "x", "title": "x", "url": "u", "transcript": "[00:00:00] weight loss"}))
        self.assertEqual(sorted(ep["video_id"] for ep, _ in search.search(["weight loss"], "other")), ["x"])

    def test_index_follows_added_changed_and_removed_transcripts(self):
        self.assertEqual(search.search(["kitchen"])[0][0]["video_id"], "one")
        new = {"show": "pod", "video_id": "four", "title": "four", "url": "u", "transcript": "[00:00:00] kitchen"}
        (search.DATA / "pod" / "four.json").write_text(json.dumps(new))
        self.assertEqual(sorted(ep["video_id"] for ep, _ in search.search(["kitchen"])), ["four", "one"])
        (search.DATA / "pod" / "one.json").unlink()
        os.utime(search.DATA / "pod" / "four.json", (1, 1))  # a changed file is re-read
        self.assertEqual([ep["video_id"] for ep, _ in search.search(["kitchen"])], ["four"])
        self.assertEqual(search.search(["intro"]), [])  # removed episode's passages are gone

    def test_broken_file_is_skipped_not_fatal(self):
        (search.DATA / "pod" / "bad.json").write_text('{"show": "pod", "video_id": "bad", "transcr')
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual([ep["video_id"] for ep, _ in search.search(["kitchen"])], ["one"])

    def test_moved_folder_is_reindexed_under_its_new_name(self):
        search.search(["kitchen"])
        (search.DATA / "pod").rename(search.DATA / "renamed")  # keeps file modification times
        self.assertEqual([ep["show"] for ep, _ in search.search(["kitchen"])], ["renamed"])

    def test_punctuation_in_terms(self):
        path = search.DATA / "pod" / "two.json"
        path.write_text(json.dumps({**json.loads(path.read_text()), "title": "Weight-loss #100"}))
        self.assertEqual([ep["video_id"] for ep, _ in search.search(['"weight loss"', "?"])], ["two", "one"])

    def test_link_opens_at_passage_time(self):
        self.assertEqual(search.link("https://www.youtube.com/watch?v=x", "[01:00:05] text"),
                         "https://www.youtube.com/watch?v=x&t=3605s")


if __name__ == "__main__":
    unittest.main()
