"""Fast checks for the parts that need no network and no API key.
Run with:  python -m unittest discover tests
"""
import os
import tempfile
import unittest

os.environ.setdefault("CLIPPZZ_DATA", tempfile.mkdtemp())
os.environ.setdefault("CLIPPZZ_WORK", tempfile.mkdtemp())

from clippzz import db, ranker, sources  # noqa: E402


class SourcesTest(unittest.TestCase):
    def test_video_id(self):
        for url in ("https://www.youtube.com/watch?v=o0UeucVxYCA&t=10s",
                    "https://youtu.be/o0UeucVxYCA", "https://youtube.com/shorts/o0UeucVxYCA",
                    "https://www.youtube.com/live/o0UeucVxYCA?si=x", "o0UeucVxYCA"):
            self.assertEqual(sources.video_id(url), "o0UeucVxYCA")
        self.assertIsNone(sources.video_id("https://example.com/"))

    def test_parse_json3(self):
        data = {"events": [
            {"tStartMs": 1000, "segs": [{"utf8": ">> How"}, {"utf8": " do", "tOffsetMs": 80}]},
            {"tStartMs": 2000, "segs": [{"utf8": "\n"}]},
            {"tStartMs": 3000},
            {"tStartMs": 3500, "segs": [{"utf8": "[screaming]"}]},
        ]}
        t = sources.parse_json3(data)
        self.assertEqual(t["lines"], [[1.0, ">> How do"], [3.5, "[screaming]"]])
        self.assertEqual(t["words"], [[1.0, ">> How"], [1.08, "do"], [3.5, "[screaming]"]])


class RankerTest(unittest.TestCase):
    def test_chunks_cover_everything_with_overlap(self):
        lines = [[float(t), f"line {t}"] for t in range(0, 1500, 10)]
        chunks = list(ranker.chunk_lines(lines, 1500, size=600, overlap=45))
        self.assertEqual([c[0] for c in chunks], [0.0, 600.0, 1200.0])
        seen = {l[0] for c in chunks for l in c[2]}
        self.assertEqual(seen, {l[0] for l in lines})
        self.assertIn([610.0, "line 610"], chunks[0][2])  # overlap into the next window

    def test_loud_lines_are_marked(self):
        levels = [-40.0] * 100
        levels[12] = -5.0
        text = ranker.format_lines([[10.0, "quiet"], [12.0, "LOUD"], [15.0, "quiet"]],
                                   levels, ranker.loud_threshold(levels, 0.02))
        self.assertEqual(text, "[10] quiet\n[12] LOUD !!\n[15] quiet")

    def test_dedupe_keeps_best_of_overlapping(self):
        ms = [{"start_s": 100, "end_s": 140, "score": 60}, {"start_s": 110, "end_s": 150, "score": 80},
              {"start_s": 300, "end_s": 330, "score": 50}]
        kept = ranker.dedupe(ms)
        self.assertEqual([(m["start_s"], m["score"]) for m in kept], [(110, 80), (300, 50)])


class DbTest(unittest.TestCase):
    def test_vod_lifecycle_and_spend(self):
        conn = db.connect()
        self.assertTrue(db.add_vod(conn, "abcdefghijk", "https://www.youtube.com/watch?v=abcdefghijk"))
        self.assertFalse(db.add_vod(conn, "abcdefghijk", "x"))
        self.assertEqual(db.next_vod(conn)["id"], "abcdefghijk")
        db.set_state(conn, "abcdefghijk", "done", "ok")
        self.assertIsNone(db.next_vod(conn))
        cost = db.record_usage(conn, "abcdefghijk", "judge", "claude-opus-5-5", 1_000_000, 100_000)
        self.assertAlmostEqual(cost, 6.0)
        self.assertAlmostEqual(db.month_spend(conn), 6.0)


if __name__ == "__main__":
    unittest.main()
