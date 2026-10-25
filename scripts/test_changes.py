"""更新记录: changes.py 比较对应表前后两版、merge.py 每次写表时记进 docs/data/changes/. python3 -m unittest discover -s scripts"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

import changes
import common

HERE = os.path.dirname(os.path.abspath(__file__))


def row(ref, path="", stills="", source="auto", episodes=""):
    return (ref, path, stills, source, episodes)


class DiffTest(unittest.TestCase):
    def test_entity_change_and_image_only_change_are_told_apart(self):
        old = {1: row("tv/1", "/a.jpg"), 2: row("tv/2", "/b.jpg"), 3: row("tv/3"), 4: row("tv/4", episodes="S1E1")}
        new = {1: row("tv/9", "/z.jpg"), 2: row("tv/2", "/c.jpg"), 4: row("tv/4", episodes="S2E1"), 5: row("movie/5")}
        self.assertEqual(([1, 3, 4, 5], [2]), changes.diff(old, new))

    def test_manual_none_row_differs_from_auto_row(self):
        self.assertEqual(([7], []), changes.diff({7: row("tv/7")}, {7: row("", source="manual")}))

    def test_no_change_gives_no_run(self):
        rows = {1: row("tv/1", "/a.jpg")}
        self.assertIsNone(changes.make_run("2026-10-25", "update", "", rows, dict(rows), lambda s: None, lambda s: None))

    def test_run_carries_names_titles_and_manual_note(self):
        before = {"bgm": {"name": "ミニドラ", "nameCN": "迷你龙小剧场"}, "auto": {"tmdb": {"name": "残念生物事典"}}}
        after = dict(before, manual={"title": "小林家的龙女仆", "note": "修正请求 #71"})
        run = changes.make_run("2026-10-25", "apply", "", {332570: row("tv/111072", "/x.jpg", "tv/111072")},
                               {332570: row("tv/69291", "", "tv/69291/season/0", "manual")},
                               {332570: before}.get, {332570: after}.get)
        self.assertEqual({
            "date": "2026-10-25", "kind": "apply", "images": [],
            "changes": [{
                "id": 332570, "name": "ミニドラ", "cn": "迷你龙小剧场",
                "old": {"ref": "tv/111072", "stills": "tv/111072", "source": "auto", "title": "残念生物事典"},
                "new": {"ref": "tv/69291", "stills": "tv/69291/season/0", "source": "manual", "title": "小林家的龙女仆",
                        "note": "修正请求 #71"},
            }],
        }, run)

    def test_episodes_are_recorded_only_when_they_changed(self):
        run = changes.make_run("2026-10-25", "update", "abc", {1: row("tv/1", episodes="S1E1")},
                               {1: row("tv/1", episodes="S2E1")}, lambda s: None, lambda s: None)
        self.assertEqual("S1E1", run["changes"][0]["old"]["episodes"])
        self.assertEqual("S2E1", run["changes"][0]["new"]["episodes"])
        self.assertEqual("abc", run["matcher"])
        run = changes.make_run("2026-10-25", "update", "", {1: row("tv/1", episodes="S1E1")},
                               {1: row("tv/2", episodes="S1E3")}, lambda s: None, lambda s: None)
        self.assertNotIn("episodes", run["changes"][0]["old"])
        run = changes.make_run("2026-10-25", "update", "", {1: row("tv/1")}, {}, lambda s: None, lambda s: None)
        self.assertNotIn("episodes", run["changes"][0]["old"])
        self.assertIsNone(run["changes"][0]["new"])

    def test_runs_are_kept_per_month_newest_first(self):
        with tempfile.TemporaryDirectory() as site:
            changes.record(site, {"date": "2026-09-30", "kind": "update", "changes": [{"id": 1}], "images": []})
            changes.record(site, {"date": "2026-10-24", "kind": "update", "changes": [], "images": [1]})
            changes.record(site, {"date": "2026-10-25", "kind": "logos", "changes": [{"id": 2}, {"id": 3}], "images": [2]})
            self.assertEqual(["2026-10-25", "2026-10-24"], [r["date"] for r in changes.load_month(site, "2026-10")])
            with open(os.path.join(site, "data", "changes", "index.json"), encoding="utf-8") as f:
                self.assertEqual([{"month": "2026-10", "runs": 2, "changes": 2}, {"month": "2026-09", "runs": 1, "changes": 1}],
                                 json.load(f))


class MergeRecordsChangesTest(unittest.TestCase):
    """merge.py --apply-only: 人工修正换掉的条目与 logos 换掉的背景图都记进更新记录."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        p = self.path
        os.makedirs(p("docs/data/s"))
        os.makedirs(p("overrides"))
        os.makedirs(p("map"))
        records = {
            "1": {"bgm": {"name": "ガイ"}, "auto": {"status": "hit", "backdrop": "tv/10", "backdropPath": "/a.jpg",
                                                  "stillsSource": "tv/10", "tmdb": {"name": "強殖装甲ガイバー"}}},
            "2": {"bgm": {"name": "B"}, "auto": {"status": "hit", "backdrop": "tv/20", "backdropPath": "/old.jpg",
                                               "stillsSource": "tv/20", "tmdb": {"name": "B 剧"}}},
        }
        with open(p("docs/data/s/0.json"), "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False)
        with open(p("docs/data/index.tsv"), "w", encoding="utf-8") as f:
            f.write("# id\tname\tcn\tdate\tplatform\tpop\tstatus\tsource\tref\ttitle\tchecked\n")
            f.write("1\tガイ\t\t1988-12-06\tOVA\t1\thit\tauto\ttv/10\t強殖装甲ガイバー\t2026-10-01\n")
            f.write("2\tB\t\t2020-01-01\tTV\t1\thit\tauto\ttv/20\tB 剧\t2026-10-01\n")
        with open(p("map/bgm-tmdb.tsv"), "w", encoding="utf-8") as f:
            f.write("# header\n1\ttv/10\t/a.jpg\ttv/10\tauto\t\t\t\t\n2\ttv/20\t/old.jpg\ttv/20\tauto\t\t\t\t\n")
        with open(p("overrides/1.json"), "w", encoding="utf-8") as f:
            json.dump({"none": True, "note": "维护者修正"}, f)
        common.save_backdrops(p("backdrops.tsv"), {"tv/20": {"checked": "2026-10-25", "path": "/new.jpg"}})

    def path(self, name):
        return os.path.join(self.dir.name, name)

    def merge(self, *extra):
        p = self.path
        subprocess.run([sys.executable, os.path.join(HERE, "merge.py"), "--apply-only", "--state", p("state.tsv"),
                        "--map", p("map/bgm-tmdb.tsv"), "--meta", p("map/meta.json"), "--site", p("docs"),
                        "--overrides", p("overrides"), "--logo-overrides", p("logo-overrides"), "--logos", p("logos.tsv"),
                        "--anidb", p("anidb.tsv"), "--names", p("names.tsv"), "--backdrops", p("backdrops.tsv"),
                        "--today", "2026-10-25", *extra], check=True, capture_output=True)
        return changes.load_month(p("docs"), "2026-10")

    def test_override_and_new_backdrop_are_recorded(self):
        runs = self.merge("--kind", "logos")
        self.assertEqual(1, len(runs))
        run = runs[0]
        self.assertEqual(("2026-10-25", "logos", [2]), (run["date"], run["kind"], run["images"]))
        self.assertEqual([1], [c["id"] for c in run["changes"]])
        change = run["changes"][0]
        self.assertEqual("ガイ", change["name"])
        self.assertEqual({"ref": "tv/10", "stills": "tv/10", "source": "auto", "title": "強殖装甲ガイバー"}, change["old"])
        self.assertEqual({"ref": "", "stills": "", "source": "manual", "title": "", "note": "维护者修正"}, change["new"])

    def test_rewriting_the_same_table_adds_nothing(self):
        self.merge()
        self.assertEqual(1, len(self.merge()))


if __name__ == "__main__":
    unittest.main()
