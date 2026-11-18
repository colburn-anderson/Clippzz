"""Checks for the clip editor's pure parts: timeline maths, captions, crops,
plan tidying and the ffmpeg command. No network, no API key, no ffmpeg run.
"""
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("CLIPPZZ_DATA", tempfile.mkdtemp())
os.environ.setdefault("CLIPPZZ_WORK", tempfile.mkdtemp())

from clippzz import captions, clipper, config, emoji, planner, render, sounds  # noqa: E402

BEATS = [{"start_s": 10.0, "end_s": 12.0}, {"start_s": 20.0, "end_s": 21.5, "zoom": 1.3}]


class TimelineTest(unittest.TestCase):
    def test_beats_join_end_to_end(self):
        beats = render.build_timeline(BEATS)
        self.assertEqual([(b.out_s, round(b.dur, 3)) for b in beats], [(0.0, 2.0), (2.0, 1.5)])
        self.assertAlmostEqual(render.total(beats), 3.5)

    def test_section_time_to_output_time(self):
        beats = render.build_timeline(BEATS)
        self.assertAlmostEqual(render.to_out(11.0, beats), 1.0)
        self.assertAlmostEqual(render.to_out(20.5, beats), 2.5)
        self.assertIsNone(render.to_out(15.0, beats))              # in the cut
        self.assertAlmostEqual(render.to_out(15.0, beats, "next"), 2.0)
        self.assertAlmostEqual(render.to_out(15.0, beats, "prev"), 2.0)
        self.assertAlmostEqual(render.to_out(5.0, beats, "next"), 0.0)
        self.assertIsNone(render.to_out(5.0, beats, "prev"))

    def test_durations_are_whole_frames(self):
        beats = render.build_timeline([{"start_s": 0.0, "end_s": 1.017}])
        self.assertAlmostEqual(beats[0].dur * render.FPS, round(beats[0].dur * render.FPS), places=6)


class CaptionTest(unittest.TestCase):
    def test_words_pair_up_and_colours_change_by_phrase(self):
        beats = render.build_timeline([{"start_s": 0.0, "end_s": 10.0}])
        words = [[0.0, 0.2, "Look"], [0.25, 0.4, "at"], [0.45, 0.9, "my"], [0.95, 1.5, "stomach."],
                 [3.0, 3.3, "Chai,"], [3.35, 3.8, "fuck"], [3.85, 4.2, "you!"]]
        ev = render.speech_events(words, beats, {"Chai": "Kai"})
        self.assertEqual([e.text for e in ev], ["LOOK AT", "MY", "STOMACH", "KAI", "F*CK", "YOU!"])
        self.assertEqual(ev[0].colour, ev[2].colour)
        self.assertNotEqual(ev[2].colour, ev[3].colour)
        self.assertTrue(all(a.end <= b.start + 1e-9 for a, b in zip(ev, ev[1:])))

    def test_words_outside_beats_and_slurs_are_dropped(self):
        beats = render.build_timeline([{"start_s": 1.0, "end_s": 2.0}])
        words = [[0.2, 0.5, "before"], [1.2, 1.5, "niggas"], [1.6, 1.9, "inside"], [2.5, 2.8, "after"]]
        self.assertEqual([e.text for e in render.speech_events(words, beats)], ["INSIDE"])

    def test_phrase_fixes_replace_misheard_words(self):
        words = [[1.0, 1.2, "Bro,"], [1.3, 1.5, "you're"], [1.5, 1.7, "my"], [1.7, 1.9, "food"],
                 [1.9, 2.3, "bank!"], [3.0, 3.2, "Chai"]]
        fixed = render.apply_fixes(words, [("you're my food bank", "give me my food back"), ("chai", "Kai")])
        self.assertEqual([w[2] for w in fixed], ["Bro,", "give", "me", "my", "food", "back!", "Kai"])
        self.assertAlmostEqual(fixed[1][0], 1.3)
        self.assertAlmostEqual(fixed[5][1], 2.3)

    def test_fast_words_are_paired_so_they_stay_readable(self):
        beats = render.build_timeline([{"start_s": 0.0, "end_s": 5.0}])
        words = [[0.0, 0.1, "run"], [0.12, 0.3, "all"], [0.32, 0.45, "the"], [0.5, 1.2, "way"]]
        ev = render.speech_events(words, beats)
        self.assertEqual([e.text for e in ev], ["RUN ALL", "THE WAY"])

    def test_a_caption_never_lingers(self):
        beats = render.build_timeline([{"start_s": 0.0, "end_s": 5.0}])
        ev = render.speech_events([[0.5, 3.5, "Who?"]], beats)
        self.assertAlmostEqual(ev[0].end - ev[0].start, render.MAX_CAPTION_S)

    def test_loud_phrase_endings_get_an_exclamation(self):
        beats = render.build_timeline([{"start_s": 0.0, "end_s": 5.0}])
        words = [[0.0, 0.3, "give"], [0.35, 0.5, "me"], [0.55, 0.9, "food"]]
        ev = render.speech_events(words, beats, loud=lambda t: True)
        self.assertEqual(ev[-1].text, "FOOD!")

    def test_stage_direction_moves_up_over_speech(self):
        beats = render.build_timeline([{"start_s": 0.0, "end_s": 4.0}])
        speech = render.speech_events([[1.0, 1.4, "bro"]], beats)
        stage = render.stage_events([{"start_s": 0.8, "end_s": 2.0, "text": "steals plate", "color": "pink"},
                                     {"start_s": 3.0, "end_s": 3.9, "text": "*runs*", "color": "nope"}], beats)
        render.place(speech, stage)
        self.assertEqual([s.text for s in stage], ["*STEALS PLATE*", "*RUNS*"])
        self.assertEqual(stage[0].y, render.STAGE_Y_ABOVE)
        self.assertEqual(stage[1].y, render.CAPTION_Y)
        self.assertEqual(stage[1].colour, "white")

    def test_closing_stage_direction_stays_readable(self):
        beats = render.build_timeline([{"start_s": 0.0, "end_s": 3.0}])
        stage = render.stage_events([{"start_s": 2.8, "end_s": 3.5, "text": "plate secured", "color": "cyan"}], beats)
        self.assertAlmostEqual(stage[0].end, 3.0)
        self.assertAlmostEqual(stage[0].start, 2.4)

    def test_ass_document(self):
        doc = render.ass_document([render.Event(1.234, 2.5, "HEY {X}", "yellow")])
        self.assertIn("PlayResY: 1920", doc)
        self.assertIn("Dialogue: 0,0:00:01.23,0:00:02.50,Cap,,0,0,0,,", doc)
        self.assertIn("\\c&H1FE6FF&", doc)
        self.assertIn("HEY (X)", doc)


class CropTest(unittest.TestCase):
    def test_full_height_window_on_16_9(self):
        self.assertEqual(render.crop_box(1920, 1080, 0.5, 0.5, 1.0), (606, 1080, 657, 0))

    def test_window_stays_inside_the_frame(self):
        cw, ch, x, y = render.crop_box(1920, 1080, 0.0, 0.0, 1.5)
        self.assertEqual((x, y), (0, 0))
        cw, ch, x, y = render.crop_box(1920, 1080, 1.0, 1.0, 1.5)
        self.assertEqual((x + cw, y + ch), (1920, 1080))


class HitTest(unittest.TestCase):
    def test_flashes_are_spaced_and_mapped(self):
        beats = render.build_timeline([{"start_s": 0.0, "end_s": 5.0}])
        hits = render.hit_events([{"at_s": 1.0, "kind": "green", "length_s": 0.15},
                                  {"at_s": 1.2, "kind": "red", "length_s": 0.15},
                                  {"at_s": 2.0, "kind": "bw", "length_s": 9},
                                  {"at_s": 3.0, "kind": "purple", "length_s": 0.1}], beats)
        self.assertEqual([(h.kind, h.start, h.end) for h in hits], [("green", 1.0, 1.15), ("bw", 2.0, 4.5)])


class PlanTidyTest(unittest.TestCase):
    def test_edges_leave_words_and_beats_do_not_overlap(self):
        words = [[1.0, 1.6, "hello"], [5.0, 5.2, "yo"]]
        plan = planner.tidy({"beats": [
            {"start_s": 1.1, "end_s": 3.0, "focus_x": 2, "focus_y": 0.4, "zoom": 3},
            {"start_s": 2.5, "end_s": 5.15, "focus_x": 0.5, "focus_y": 0.4, "zoom": 1},
            {"start_s": 9.0, "end_s": 99.0, "focus_x": 0.5, "focus_y": 0.4, "zoom": 1},
        ]}, words, duration=12.0)
        b = plan["beats"]
        self.assertAlmostEqual(b[0]["start_s"], 0.97)  # took the whole word "hello"
        self.assertEqual((b[0]["focus_x"], b[0]["zoom"]), (1.0, 3.0))
        self.assertEqual(b[0]["layout"], "single")      # stack needs both boxes
        self.assertEqual(b[1]["start_s"], 3.0)          # no overlap with beat 1
        self.assertAlmostEqual(b[1]["end_s"], 5.25)     # finished the word "yo"
        self.assertEqual(b[2]["end_s"], 12.0)           # inside the section

    def test_stack_needs_two_boxes_and_ending_is_clamped(self):
        plan = planner.tidy({"beats": [
            {"start_s": 0, "end_s": 2, "layout": "stack", "top_box": [0, 0, 0.2, 0.3],
             "bottom_box": [0.8, 0, 1.0, 0.3]},
            {"start_s": 2, "end_s": 4, "layout": "stack", "top_box": [0, 0, 0.2, 0.3], "bottom_box": [1, 2]},
        ], "ending": {"freeze_s": 9, "tint": "purple"}}, [], duration=10.0)
        self.assertEqual([b["layout"] for b in plan["beats"]], ["stack", "single"])
        self.assertEqual(plan["ending"]["freeze_s"], 3.5)
        self.assertEqual(plan["ending"]["tint"], "none")

    def test_schema_lists_the_library(self):
        sch = planner.schema(["vine boom"], ["goofy"])
        props = sch["properties"]
        self.assertEqual(props["sounds"]["items"]["properties"]["name"]["enum"], ["vine boom"])
        self.assertEqual(props["music"]["enum"], ["none", "goofy"])
        self.assertIn("💀", props["stickers"]["items"]["properties"]["emoji"]["enum"])
        empty = planner.schema([], [])
        self.assertEqual(empty["properties"]["sounds"]["items"]["properties"]["name"]["enum"], ["none"])
        self.assertIn("none available yet", planner.system_prompt([], []))

    def test_format_ranges(self):
        self.assertEqual(planner.format_ranges([1.0, 1.5, 2.0, 7.5]), "1.0-2.5, 7.5-8.0")
        self.assertEqual(planner.format_ranges([]), "(none stand out)")


class CaptionCrossCheckTest(unittest.TestCase):
    def test_youtube_words_win_where_the_two_disagree(self):
        whisper = [[0.0, 0.3, "you're"], [0.3, 0.5, "my"], [0.5, 0.8, "food"], [0.8, 1.1, "bank,"],
                   [2.0, 2.4, "I'm"], [2.4, 2.6, "not"], [2.6, 3.0, "playin'."]]
        yt = [(0.0, "GIVE"), (0.3, "MY"), (0.5, "FOOD"), (0.8, "BACK."), (1.0, "[laughter]"),
              (2.1, "I'M"), (2.4, "NOT"), (2.7, "PLAYING,")]
        merged, diffs = captions.reconcile(whisper, captions.youtube_words(
            [(100 + t, w) for t, w in yt], 100.0, 10.0))
        self.assertEqual([w[2] for w in merged], ["GIVE", "my", "food", "BACK.", "I'm", "not", "PLAYING,"])
        self.assertEqual(merged[0][:2], [0.0, 0.3])  # whisper's timing kept
        self.assertEqual([(d[2], d[3]) for d in diffs], [("you're", "GIVE"), ("bank,", "BACK."),
                                                         ("playin'.", "PLAYING,")])

    def test_far_apart_words_are_not_swapped(self):
        merged, diffs = captions.reconcile([[0.0, 0.3, "hello"]], [(9.0, "goodbye")])
        self.assertEqual(merged, [[0.0, 0.3, "hello"]])
        self.assertEqual(diffs, [])

    def test_frames_thin_out_around_the_moment(self):
        frames = [(i / 2, b"") for i in range(20)]  # 0.0 to 9.5 s, two a second
        kept = [t for t, _ in clipper.thin_frames(frames, 3.0, 6.0)]
        self.assertIn(3.5, kept)                     # inside: every frame
        self.assertNotIn(1.5, kept)                  # outside: one a second
        self.assertIn(1.0, kept)


class LayoutTest(unittest.TestCase):
    def test_box_window_covers_the_box_at_the_panel_shape(self):
        w, h, x, y = render.box_window(1920, 1080, [0.0, 0.0, 0.12, 0.18], render.W / render.PANEL_H)
        self.assertAlmostEqual(w / h, 1080 / 960, places=2)
        self.assertLessEqual(x, 0.0 * 1920)
        self.assertGreaterEqual(x + w, 0.12 * 1920)

    def test_points_map_into_the_right_panel(self):
        beat = render.build_timeline([{"start_s": 0, "end_s": 2, "layout": "stack",
                                       "top_box": [0.0, 0.0, 0.2, 0.3], "bottom_box": [0.8, 0.0, 1.0, 0.3]}])[0]
        _, y_top = render.map_point(beat, 0.1, 0.15, 1920, 1080)
        _, y_bottom = render.map_point(beat, 0.9, 0.15, 1920, 1080)
        self.assertLess(y_top, render.PANEL_H)
        self.assertGreater(y_bottom, render.PANEL_H)

    def test_stickers_land_inside_the_frame(self):
        beats = render.build_timeline([{"start_s": 0, "end_s": 3, "focus_x": 0.5, "focus_y": 0.5, "zoom": 1}])
        st = render.sticker_events([{"start_s": 1, "end_s": 2, "emoji": "💀", "x": 0.99, "y": 0.99,
                                     "size": "large"}, {"start_s": 1, "end_s": 2, "emoji": "🦄",
                                                        "x": 0.5, "y": 0.5, "size": "small"}], beats, 1920, 1080)
        self.assertEqual(len(st), 1)                 # unknown emoji dropped
        self.assertTrue(0 <= st[0].x <= render.W - st[0].size and 0 <= st[0].y <= render.H - st[0].size)


class LibraryTest(unittest.TestCase):
    def test_library_names_and_descriptions(self):
        lib = Path(tempfile.mkdtemp())
        (lib / "sounds").mkdir()
        (lib / "sounds" / "vine boom.mp3").write_bytes(b"x")
        (lib / "sounds" / "vine boom.txt").write_text("Deep boom: a stare.")
        (lib / "sounds" / "._vine boom.mp3").write_bytes(b"x")  # Mac metadata file, ignored
        old = config.LIBRARY_DIR
        config.LIBRARY_DIR = lib
        try:
            self.assertEqual(sounds.choices(), ["vine boom"])
            self.assertEqual(sounds.label("vine boom"), "vine boom (Deep boom: a stare.)")
            self.assertEqual(sounds.music(), {})
        finally:
            config.LIBRARY_DIR = old

    @unittest.skipUnless(Path(config.EMOJI_FONT).exists(), "emoji font not installed here")
    def test_emoji_draws(self):
        path = emoji.png("💀", 120, Path(tempfile.mkdtemp()))
        self.assertTrue(path.exists() and path.stat().st_size > 500)


class CommandTest(unittest.TestCase):
    def test_ffmpeg_command(self):
        beats = render.build_timeline(BEATS)
        hits = [render.Hit(1.0, 1.15, "green")]
        cues = [render.Cue(2.0, "boom")]
        cmd = render.ffmpeg_command(Path("/w/src.mp4"), beats, hits, cues, {"boom": (Path("/w/boom.wav"), 0.8)},
                                    Path("/w/captions.ass"), Path("/w/out.mp4"), 1920, 1080)
        self.assertEqual(cmd.count("-i"), 3)
        graph = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("concat=n=2:v=1:a=1[vc][ac]", graph)
        self.assertIn("enable='between(t,1.000,1.150)'", graph)
        self.assertIn("ass=/w/captions.ass", graph)
        self.assertIn("adelay=delays=2000:all=1", graph)
        self.assertIn("atrim=duration=1.500", graph)  # sound cut where the clip ends
        self.assertIn("amix=inputs=2", graph)
        self.assertEqual(cmd[-1], "/w/out.mp4")

    def test_freeze_stickers_music_and_stack(self):
        beats = render.build_timeline([{"start_s": 0, "end_s": 2, "layout": "stack",
                                        "top_box": [0, 0, 0.2, 0.3], "bottom_box": [0.8, 0, 1, 0.3]},
                                       {"start_s": 5, "end_s": 7}])
        sticker = render.Sticker(4.0, 6.0, "💀", 420, 330, 610)
        cmd = render.ffmpeg_command(Path("/w/src.mp4"), beats, [], [], {}, Path("/w/c.ass"), Path("/w/o.mp4"),
                                    1920, 1080, [(sticker, Path("/w/e.png"))], freeze_s=2.0, tint="warm",
                                    music=(Path("/w/m.mp3"), 0.2))
        graph = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("vstack=inputs=2", graph)
        self.assertIn("tpad=stop_mode=clone:stop_duration=2.000", graph)
        self.assertIn("enable='gte(t,4.000)'", graph)          # tint over the freeze
        self.assertIn("overlay=x=330:y=610", graph)
        self.assertIn("apad=whole_dur=6.000", graph)
        self.assertIn("sidechaincompress", graph)
        self.assertIn("-stream_loop", cmd)
        self.assertEqual(cmd[cmd.index("-t", cmd.index("-filter_complex")) + 1], "6.000")


if __name__ == "__main__":
    unittest.main()
