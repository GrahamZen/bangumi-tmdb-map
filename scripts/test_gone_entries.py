"""TMDB 上已经没了的背景图条目: logos.py 记 gone、对应表 logo 列写成各语言都没有、prepare.py 排进重新匹配. python3 -m unittest discover -s scripts"""
import datetime
import os
import tempfile
import unittest

import common
import logos
import prepare


class GoneEntriesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def path(self, name):
        return os.path.join(self.dir.name, name)

    def test_gone_round_trips_through_logos_file(self):
        data = {
            "tv/326788": {"checked": "2026-10-13", "original": "", "logos": {}, "gone": True},
            "tv/105248": {"checked": "2026-10-13", "original": "ja", "logos": {"ja": {"none": True}}},
        }
        common.save_logos(self.path("logos.tsv"), data)
        loaded = common.load_logos(self.path("logos.tsv"))
        self.assertTrue(loaded["tv/326788"]["gone"])
        self.assertFalse(loaded["tv/105248"].get("gone"))
        self.assertEqual({"tv/326788": "2026-10-13"}, common.gone_refs(loaded))

    def test_gone_entry_is_written_as_no_logo_in_every_language(self):
        gone = {"checked": "2026-10-13", "original": "", "logos": {}, "gone": True}
        self.assertEqual("o=ja ja=- zh=- en=-", common.logo_cell("tv/326788", None, gone))
        # 没查过的照旧是空
        self.assertEqual("", common.logo_cell("tv/326788", None, None))

    def test_detail_404_is_reported_as_gone(self):
        self.assertIsNone(logos.check("tv/326788", lambda path, params=None: None))

    def test_gone_entry_is_checked_again_after_two_weeks(self):
        entry = {"checked": "2026-10-01", "original": "", "logos": {}, "gone": True}
        self.assertFalse(logos.due(entry, datetime.date(2026, 10, 13)))
        self.assertTrue(logos.due(entry, datetime.date(2026, 10, 15)))

    def test_empty_result_saved_before_gone_existed_is_checked_again(self):
        legacy = {"checked": "2026-10-13", "original": "", "logos": {}}
        self.assertTrue(logos.due(legacy, datetime.date(2026, 10, 13)))

    def test_subjects_on_a_gone_backdrop_are_matched_again_once(self):
        with open(self.path("map.tsv"), "w", encoding="utf-8") as f:
            f.write("# header\n")
            f.write("513878\ttv/326788\t/a.jpg\ttv/326788\tauto\t\t\t\t\n")
            f.write("1\ttv/1\t/b.jpg\ttv/1\tauto\t\t\t\t\n")
        found = {"tv/326788": {"checked": "2026-10-13", "original": "", "logos": {}, "gone": True}}
        matched_before = {513878: {"checked": "2026-09-25", "status": "hit", "hash": "x", "fails": 0},
                          1: {"checked": "2026-09-25", "status": "hit", "hash": "y", "fails": 0}}
        self.assertEqual({513878}, prepare.gone_backdrop_subjects(self.path("map.tsv"), found, matched_before))
        # 发现之后已经重新匹配过 (仍落在同一个条目): 不再天天重跑
        matched_after = {**matched_before, 513878: {"checked": "2026-10-14", "status": "hit", "hash": "x", "fails": 0}}
        self.assertEqual(set(), prepare.gone_backdrop_subjects(self.path("map.tsv"), found, matched_after))


if __name__ == "__main__":
    unittest.main()
