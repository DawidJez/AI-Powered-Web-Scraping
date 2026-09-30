"""
Everything around the model call: which fragment of the page it gets to see, what is done with
a reply that is not what was asked for, and the budgets that keep the free tier from cutting us off.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import ai_extractor
from ai_extractor import AIExtractor


def extractor():
    """the real __init__ builds a Gemini client - none of this talks to it"""
    instance = object.__new__(AIExtractor)
    instance.debug = False
    instance.tokens_usage = []
    instance._minute_log = []
    return instance


PAGE = """
<div class="cmp-facet"><h3>Product number</h3><input type="checkbox" id="f-1"></div>
<table><tr><td>Pin count</td></tr></table>
"""


class WindowScore(unittest.TestCase):
    """a fragment with real controls beats a fragment that only contains the words"""

    def test_controls_score_higher_than_plain_text(self):
        with_controls = extractor()._window_score('<div class="facet"><input type="checkbox"></div>')
        plain = extractor()._window_score("<td>Product number</td>")
        self.assertGreater(with_controls, plain)


class HtmlWindows(unittest.TestCase):
    """the field name is looked up literally in the page source"""

    def test_name_is_found_when_it_matches_the_page(self):
        self.assertTrue(extractor()._extract_html_windows(PAGE, ["Product number"]))

    def test_lookup_is_case_sensitive(self):
        """documented behaviour, not an accident - the user guide tells people to copy the label"""
        self.assertFalse(extractor()._extract_html_windows(PAGE, ["product number"]))

    def test_name_that_is_not_on_the_page_gives_nothing(self):
        self.assertFalse(extractor()._extract_html_windows(PAGE, ["Nie ma takiego pola"]))


class JsonObject(unittest.TestCase):
    """one shape for every reply: a dict, or the fallback"""

    def test_plain_object(self):
        self.assertEqual(extractor()._json_object('{"a": 1}', None), {"a": 1})

    def test_object_wrapped_in_an_array(self):
        self.assertEqual(extractor()._json_object('[{"a": 1}]', None), {"a": 1})

    def test_unusable_reply_gives_the_fallback(self):
        self.assertIsNone(extractor()._json_object("nie jestem jsonem", None))


class Backoff(unittest.TestCase):
    """the free tier rejects in bursts, so each wait has to outlast the burst"""

    def test_wait_grows_with_every_attempt(self):
        waits = [extractor()._backoff(n) for n in (1, 2, 3, 4)]
        self.assertEqual(waits, sorted(waits))
        self.assertGreater(waits[-1], waits[0] * 3)


class MinuteWindow(unittest.TestCase):
    """waiting a few seconds on purpose is cheaper than a 429, which costs a fixed minute"""

    class Clock:
        def __init__(self):
            self.now, self.slept = 1000.0, []

        def time(self):
            return self.now

        def sleep(self, seconds):
            self.slept.append(seconds)
            self.now += seconds

    def setUp(self):
        self.clock = self.Clock()
        self.real_time = ai_extractor.time
        ai_extractor.time = self.clock

    def tearDown(self):
        ai_extractor.time = self.real_time

    def test_requests_under_the_limit_go_straight_through(self):
        instance = extractor()
        for _ in range(ai_extractor.REQUESTS_PER_MINUTE - 1):
            instance._respect_limits("x" * 400)
            instance._minute_log.append((self.clock.time(), 100))
        self.assertEqual(self.clock.slept, [])

    def test_the_request_over_the_limit_waits_out_the_window(self):
        instance = extractor()
        for _ in range(ai_extractor.REQUESTS_PER_MINUTE + 1):
            instance._respect_limits("x" * 400)
            instance._minute_log.append((self.clock.time(), 100))
        self.assertEqual(len(self.clock.slept), 1)
        self.assertGreater(self.clock.slept[0], 60)

    def test_token_ceiling_trips_before_the_request_count(self):
        instance = extractor()
        instance._minute_log = [(self.clock.time(), ai_extractor.TOKENS_PER_MINUTE - 10_000)]
        instance._respect_limits("y" * 80_000)
        self.assertEqual(len(self.clock.slept), 1)

    def test_one_request_bigger_than_the_whole_budget_does_not_spin(self):
        instance = extractor()
        instance._minute_log = []
        instance._respect_limits("z" * 4_000_000)
        self.assertEqual(self.clock.slept, [])

    def test_entries_older_than_a_minute_leave_the_window(self):
        instance = extractor()
        instance._minute_log = [(self.clock.time() - 61, 200_000)
                                for _ in range(ai_extractor.REQUESTS_PER_MINUTE)]
        instance._respect_limits("x" * 400)
        self.assertEqual(self.clock.slept, [])


class DailyCounter(unittest.TestCase):
    """every run is a new process, so the daily count has to live in a file"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.real_path = ai_extractor.DAILY_USAGE_PATH
        ai_extractor.DAILY_USAGE_PATH = Path(self.tmp.name) / "usage.json"

    def tearDown(self):
        ai_extractor.DAILY_USAGE_PATH = self.real_path
        self.tmp.cleanup()

    def test_count_survives_a_new_instance(self):
        extractor()._count_daily_request()
        extractor()._count_daily_request()
        stored = json.loads(ai_extractor.DAILY_USAGE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(stored["requests"], 2)

    def test_each_share_is_announced_once_a_day_not_once_a_run(self):
        instance = extractor()
        crossings = []
        for share in ai_extractor.DAILY_WARN_SHARES:
            crossings.append(int(ai_extractor.REQUESTS_PER_DAY * share))

        printed = []
        real_print = ai_extractor.print if hasattr(ai_extractor, "print") else print
        import builtins
        builtins.print = lambda *a, **k: printed.append(" ".join(str(x) for x in a))
        try:
            for _ in range(max(crossings) + 5):
                instance._count_daily_request()
        finally:
            builtins.print = real_print

        warnings = [p for p in printed if "daily Gemini request budget" in p]
        self.assertEqual(len(warnings), len(ai_extractor.DAILY_WARN_SHARES))


if __name__ == "__main__":
    unittest.main()
