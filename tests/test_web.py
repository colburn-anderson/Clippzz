"""The dashboard's review, approve, progress and posted flow, on a scratch database."""
import os
import tempfile
import time
import unittest

os.environ.setdefault("CLIPPZZ_DATA", tempfile.mkdtemp())
os.environ.setdefault("CLIPPZZ_WORK", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402

from clippzz import db, web  # noqa: E402


class ReviewFlowTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(web.app)
        with db.session() as conn:
            vid = f"v{int(time.time() * 1e6) % 10**10:010d}"
            db.add_vod(conn, vid, f"https://www.youtube.com/watch?v={vid}")
            db.set_state(conn, vid, "done", "ok", title="Test video", channel="IShowSpeed")
            cur = conn.execute(
                "INSERT INTO moments (vod_id, start_s, end_s, judged, judge_score, verdict, title, joke, flags)"
                " VALUES (?, 100, 140, 1, 68, 'maybe', 'Speed gets humbled', 'He celebrates, then loses.', '[]')",
                (vid,))
            self.mid = cur.lastrowid
            cur = conn.execute(
                "INSERT INTO moments (vod_id, start_s, end_s, judged, judge_score, verdict, title, joke, flags)"
                " VALUES (?, 200, 230, 1, 61, 'maybe', 'Skip me', 'Meh.', '[]')", (vid,))
            self.other = cur.lastrowid
            self.vid = vid

    def test_review_approve_progress_and_skip(self):
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn("Speed gets humbled", home.text)
        self.assertIn(f'action="/moments/{self.mid}/approve"', home.text)

        r = self.client.post(f"/moments/{self.mid}/approve", data={"note": "end on his face"},
                             follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.client.post(f"/moments/{self.mid}/approve", data={"note": ""})  # a double click queues nothing more
        with db.session() as conn:
            clips = conn.execute("SELECT * FROM clips WHERE moment_id=?", (self.mid,)).fetchall()
            m = conn.execute("SELECT rating, note FROM moments WHERE id=?", (self.mid,)).fetchone()
        self.assertEqual(len(clips), 1)
        self.assertEqual((m["rating"], m["note"]), ("good", "end on his face"))

        home = self.client.get("/")
        self.assertNotIn(f'action="/moments/{self.mid}/approve"', home.text)  # left the review list
        self.assertIn("in line", home.text)                                   # shows in progress
        status = self.client.get("/status.json").json()
        self.assertIn(clips[0]["id"], [c["id"] for c in status["clips"]])

        self.client.post(f"/moments/{self.other}/skip")
        self.assertNotIn(f'action="/moments/{self.other}/approve"', self.client.get("/").text)

    def test_posted_moves_a_clip_and_can_be_undone(self):
        with db.session() as conn:
            cid = db.add_clip(conn, self.mid)
            db.set_clip(conn, cid, "done", "20.0 s", title="CLIP!!! #ishowspeed", path="/nowhere.mp4")
        self.client.post(f"/clips/{cid}/posted")
        with db.session() as conn:
            self.assertIsNotNone(conn.execute("SELECT posted_at FROM clips WHERE id=?", (cid,)).fetchone()[0])
        self.client.post(f"/clips/{cid}/posted", data={"undo": "1"})
        with db.session() as conn:
            self.assertIsNone(conn.execute("SELECT posted_at FROM clips WHERE id=?", (cid,)).fetchone()[0])

    def test_clip_steps(self):
        self.assertEqual(web.clip_step("queued", None), 0)
        self.assertEqual(web.clip_step("working", "Downloading the moment in HD"), 1)
        self.assertEqual(web.clip_step("working", "Planning the edit from 190 frames"), 4)
        self.assertEqual(web.clip_step("done", "x"), 6)


if __name__ == "__main__":
    unittest.main()
