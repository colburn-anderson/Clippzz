"""Clips cut from several parts of one stream: finding the parts in the
captions, laying them out, keeping beats inside one part, and rendering each
shot from the right file. No network, no API key, no ffmpeg run."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("CLIPPZZ_DATA", tempfile.mkdtemp())
os.environ.setdefault("CLIPPZZ_WORK", tempfile.mkdtemp())

from clippzz import clipper, config, parts, planner, render, web  # noqa: E402

LINES = [
    [5180.0, ">> Oh, there's bread."],
    [5184.0, "What the hell can I do with bread?"],
    [5188.0, "Just eat it."],
    [5404.0, ">> Yo, bread sucks, bro."],
    [5408.0, ">> Chad, bread sucks."],
    [5501.0, ">> Put the bread in the furnace."],
    [5532.0, "a potato, not bread."],
    [6153.0, ">> You didn't know you can bake a potato?"],
    [21911.0, "You got some bread?"],
    [22000.0, "the gingerbread man"],
]


class SearchTest(unittest.TestCase):
    def test_hits_group_up_and_skip_the_moment(self):
        hits = parts.search(LINES, ["bread"], avoid=(5463, 5576))
        self.assertEqual([(h["start"], h["end"]) for h in hits],
                         [(5180.0, 5184.0), (5404.0, 5408.0), (21911.0, 21911.0)])
        self.assertIn("Just eat it.", hits[0]["text"])     # the caption text around the hit
        self.assertEqual(parts.search(LINES, ["  "]), [])

    def test_a_word_inside_another_word_does_not_count(self):
        self.assertNotIn(22000.0, [h["start"] for h in parts.search(LINES, ["bread"])])

    def test_nearest_hits_come_back_in_stream_order(self):
        hits = parts.search(LINES, ["bread", "potato"])
        self.assertEqual([h["start"] for h in parts.nearest(hits, 5483, 3)], [5404.0, 5501.0, 5532.0])


class CleanTest(unittest.TestCase):
    def test_lengths_overlaps_and_bad_answers(self):
        picked = [
            {"start_s": 5178, "end_s": 5190, "why": "finds the bread"},
            {"start_s": 5500, "end_s": 5510, "why": "inside the moment's own part"},
            {"start_s": 6160, "end_s": 6150, "why": "backwards"},
            {"start_s": 5402, "end_s": 5403, "why": "too short"},
            {"start_s": 7000, "end_s": 7100, "why": "too long"},
            {"start_s": "x", "end_s": 1, "why": "broken"},
        ]
        self.assertEqual(parts.clean(picked, (5463.0, 5576.0), vod_duration=42900),
                         [(5178.0, 5190.0), (5402.0, 5406.0), (6150.0, 6160.0), (7000.0, 7020.0)])

    def test_total_length_is_capped_keeping_the_nearest(self):
        picked = [{"start_s": 1300 + 100 * i, "end_s": 1315 + 100 * i, "why": ""} for i in range(5)]
        self.assertEqual(parts.clean(picked, (1000.0, 1200.0)), [(1300.0, 1315.0), (1400.0, 1415.0)])


class LayoutTest(unittest.TestCase):
    def test_parts_merge_when_close_and_start_on_keyframes(self):
        out = parts.layout(5483.0, 5536.0, [(5177.0, 5190.0), (5401.0, 5413.0), (5455.0, 5460.0),
                                            (6151.0, 6163.0)], 42900)
        self.assertEqual([(p["start"], p["end"], p["main"]) for p in out],
                         [(5176, 5190.0, False), (5400, 5413.0, False), (5454, 5576.0, True),
                          (6150, 6163.0, False)])

    def test_footage_is_the_stored_parts_or_the_moment(self):
        m = {"start_s": 100.0, "end_s": 130.0}
        self.assertEqual(clipper.footage_layout({"parts": None, "section_start_s": None}, m, False),
                         [{"start": 54, "end": 130.0 + config.CLIP_PAD_AFTER_S, "main": True}])
        stored = [{"start": 5176, "end": 5190, "main": False, "at": 0.0, "dur": 14.0}]
        self.assertEqual(clipper.footage_layout({"parts": json.dumps(stored), "section_start_s": None}, m, True),
                         stored)
        again = clipper.footage_layout({"parts": "[]", "section_start_s": 60.0}, m, True)
        self.assertEqual(again[0]["start"], 60.0)          # an older clip renders from its own section


class TimelineTest(unittest.TestCase):
    def test_beats_never_cross_a_join(self):
        plan = planner.tidy({"beats": [
            {"start_s": 8.0, "end_s": 13.0, "focus_x": 0.3, "zoom": 1.8},
            {"start_s": 13.0, "end_s": 20.1, "focus_x": 0.6},
        ]}, [], duration=40.0, bounds=[12.0, 20.0])
        self.assertEqual([(b["start_s"], b["end_s"]) for b in plan["beats"]],
                         [(8.0, 12.0), (12.0, 13.0), (13.0, 20.0)])   # 20.0-20.1 is too short to keep
        self.assertEqual(plan["beats"][1]["zoom"], 1.8)

    def test_each_shot_reads_its_own_part(self):
        src = [(Path("/w/part0.mp4"), 0.0, 14.0), (Path("/w/part1.mp4"), 14.0, 100.0)]
        self.assertEqual(render.source_at(5.0, src), (Path("/w/part0.mp4"), 5.0))
        self.assertEqual(render.source_at(20.5, src), (Path("/w/part1.mp4"), 6.5))
        self.assertEqual(render.source_at(3.0, Path("/w/src.mp4")), (Path("/w/src.mp4"), 3.0))
        beats = render.build_timeline([{"start_s": 2.0, "end_s": 4.0}, {"start_s": 20.0, "end_s": 21.0}])
        cmd = render.ffmpeg_command(src, beats, [], [], {}, Path("/w/c.ass"), Path("/w/o.mp4"), 1920, 1080)
        inputs = [(cmd[i - 3], cmd[i + 1]) for i, a in enumerate(cmd) if a == "-i"]
        self.assertEqual(inputs, [("2.000", "/w/part0.mp4"), ("6.000", "/w/part1.mp4")])

    def test_frames_are_kept_in_every_extra_part(self):
        frames = [(i / 2, b"") for i in range(40)]           # 0.0 to 19.5 s, two a second
        kept = [t for t, _ in clipper.thin_frames(frames, [(2.0, 3.0), (10.0, 12.0)])]
        self.assertIn(2.5, kept)
        self.assertIn(10.5, kept)
        self.assertNotIn(6.5, kept)
        self.assertIn(6.0, kept)

    def test_the_planner_hears_about_the_parts(self):
        pieces = [{"start": 5176, "at": 0.0, "dur": 14.0, "main": False},
                  {"start": 5454, "at": 14.0, "dur": 122.0, "main": True}]
        note = clipper.parts_note(pieces)
        self.assertIn("part 1: 0.0 to 14.0 s, from 1:26:16 in the stream", note)
        self.assertIn("part 2 (the moment): 14.0 to 136.0 s, from 1:30:54 in the stream", note)
        self.assertEqual(clipper.parts_note(pieces[1:]), "")

    def test_finding_parts_shows_as_the_download_step(self):
        self.assertEqual(web.clip_step("working", "Finding the other times in the stream"), 1)
        self.assertEqual(web.clip_step("working", "Downloading part 2 of 4 in HD"), 1)
        self.assertEqual(web.clip_step("working", "Writing captions from the audio"), 2)



class FetchTest(unittest.TestCase):
    def test_a_dropped_download_is_tried_again(self):
        wd, calls = Path(tempfile.mkdtemp()), []

        def flaky(cmd, timeout):
            calls.append(cmd)
            if len(calls) == 1:
                (wd / "part0.mp4.part").write_bytes(b"half")
                return subprocess.CompletedProcess(cmd, 1, b"", b"ERROR: ffmpeg exited with code 8")
            (wd / "part0.mp4").write_bytes(b"video")
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        with mock.patch.object(clipper.sources, "_run", flaky), mock.patch.object(clipper.time, "sleep"):
            self.assertEqual(clipper.fetch_section("abc", 1, 2, wd, name="part0"), wd / "part0.mp4")
        self.assertEqual(len(calls), 2)
        self.assertFalse((wd / "part0.mp4.part").exists())   # the half download was cleared away

    def test_it_gives_up_after_three_tries(self):
        wd = Path(tempfile.mkdtemp())
        failing = mock.Mock(return_value=subprocess.CompletedProcess([], 1, b"", b"ERROR: code 8"))
        with mock.patch.object(clipper.sources, "_run", failing), mock.patch.object(clipper.time, "sleep"):
            with self.assertRaises(clipper.sources.SourceError):
                clipper.fetch_section("abc", 1, 2, wd)
        self.assertEqual(failing.call_count, 3)


if __name__ == "__main__":
    unittest.main()
