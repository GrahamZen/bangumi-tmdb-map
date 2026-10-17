"""hero 背景图跟着 TMDB 的排序走: logos.py 顺带取、state/backdrops.tsv 存、merge.py 改写对应表的 backdrop_path 列. python3 -m unittest discover -s scripts"""
import datetime
import os
import tempfile
import unittest

import common
import logos


def images(*backdrops):
    return {"backdrops": [{"file_path": path, "iso_639_1": lang} for path, lang in backdrops], "logos": []}


class BackdropsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def test_hero_is_the_first_backdrop_without_language(self):
        self.assertEqual("/b.jpg", common.hero_backdrop(images(("/a.jpg", "en"), ("/b.jpg", None), ("/c.jpg", None))))
        self.assertEqual("/b.jpg", common.hero_backdrop(images(("/a.jpg", "ja"), ("/b.jpg", ""))))

    def test_no_backdrop_without_language_gives_none(self):
        self.assertIsNone(common.hero_backdrop(images(("/a.jpg", "en"))))
        self.assertIsNone(common.hero_backdrop({}))
        self.assertIsNone(common.hero_backdrop(None))

    def test_backdrops_round_trip_through_state_file(self):
        path = os.path.join(self.dir.name, "backdrops.tsv")
        data = {"tv/45790": {"checked": "2026-10-17", "path": "/l2gc2dPRUPe3bAfYCRTjc6uAMmB.jpg"},
                "collection/1": {"checked": "2026-10-16", "path": None}}
        common.save_backdrops(path, data)
        self.assertEqual(data, common.load_backdrops(path))

    def test_auto_rows_follow_the_current_hero(self):
        self.assertEqual("/new.jpg", common.map_backdrop_path("auto", "/old.jpg", "/new.jpg"))
        self.assertEqual("/old.jpg", common.map_backdrop_path("auto", "/old.jpg", None))

    def test_manual_image_is_kept_and_missing_one_is_filled(self):
        self.assertEqual("/chosen.jpg", common.map_backdrop_path("manual", "/chosen.jpg", "/new.jpg"))
        self.assertEqual("/new.jpg", common.map_backdrop_path("manual", None, "/new.jpg"))
        self.assertIsNone(common.map_backdrop_path("manual", None, None))

    def test_logo_check_takes_the_hero_from_the_same_images_request(self):
        requests = []

        def fetch(path, params=None):
            requests.append((path, params))
            if path == "/tv/45790":
                return {"original_language": "ja"}
            return images(("/en.jpg", "en"), ("/hero.jpg", None))

        original, _, _, backdrop = logos.check("tv/45790", fetch)
        self.assertEqual(("ja", "/hero.jpg"), (original, backdrop))
        self.assertEqual(2, len(requests))
        self.assertIn("null", requests[1][1]["include_image_language"].split(","))

    def test_backdrop_only_check(self):
        self.assertEqual((True, "/hero.jpg"), logos.check_backdrop("tv/1", lambda path, params=None: images(("/hero.jpg", None))))
        self.assertEqual((False, None), logos.check_backdrop("tv/1", lambda path, params=None: None))

    def test_backdrop_is_checked_again_after_two_weeks(self):
        today = datetime.date(2026, 10, 17)
        self.assertTrue(logos.backdrop_due(None, today))
        self.assertFalse(logos.backdrop_due({"checked": "2026-10-04", "path": "/a.jpg"}, today))
        self.assertTrue(logos.backdrop_due({"checked": "2026-10-03", "path": "/a.jpg"}, today))


if __name__ == "__main__":
    unittest.main()
