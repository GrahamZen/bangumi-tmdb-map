"""标题 logo: logo_correction.py 与 common / merge 里 logo 列的单测: python3 -m unittest discover -s scripts"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

import common
import correction
import logo_correction

HERE = os.path.dirname(os.path.abspath(__file__))


def form(bgm_id="135275", tmdb="", language="ja", logo="/base.png", reason="自动挑的是第三季的"):
    def value(v):
        return v if v else "_No response_"
    return (f"### Bangumi id\n\n{value(bgm_id)}\n\n### TMDB 条目\n\n{value(tmdb)}\n\n### 语言\n\n{value(language)}\n\n"
            f"### 标题 logo\n\n{value(logo)}\n\n### 说明\n\n{value(reason)}\n")


class Repo:
    """最小的仓库: このすば第一季 (自动对应 tv/65844) 与一个没有 TMDB 条目的; logos.tsv 里自动挑的是第三季的 logo."""

    def __init__(self, logo_override=None):
        self.dir = tempfile.TemporaryDirectory()
        root = self.root = self.dir.name
        os.makedirs(os.path.join(root, "docs", "data", "s"))
        os.makedirs(os.path.join(root, "state"))
        os.makedirs(os.path.join(root, "logo-overrides"))
        with open(os.path.join(root, "docs", "data", "index.tsv"), "w", encoding="utf-8") as f:
            f.write("# id\tname\tcn\tdate\tplatform\tpop\tstatus\tsource\tref\ttitle\tchecked\n")
            f.write("135275\tこの素晴らしい世界に祝福を！\t为美好的世界献上祝福！\t2016-01-13\tTV\t9\thit\tauto\ttv/65844\tKonoSuba\t2026-09-25\n")
            f.write("554346\tLEGO\t乐高\t2014-01-29\tTV\t1\tmiss\tauto\t\t\t2026-09-25\n")
        records = {
            "135275": {"auto": {"status": "hit", "backdrop": "tv/65844", "backdropPath": "/k.jpg", "stillsSource": "tv/65844",
                                "episodes": "S1E1", "tmdb": {"name": "KonoSuba"}}},
            "554346": {"auto": {"status": "miss"}},
        }
        for shard in (67, 277):
            part = {k: v for k, v in records.items() if int(k) // 2000 == shard}
            with open(os.path.join(root, "docs", "data", "s", f"{shard}.json"), "w", encoding="utf-8") as f:
                json.dump(part, f, ensure_ascii=False)
        common.save_logos(os.path.join(root, "state", "logos.tsv"), {
            "tv/65844": {"checked": "2026-10-01", "original": "ja",
                         "logos": {"ja": {"logo": "/s3.png", "aspect": 2.383}, "zh": {"none": True}, "en": {"none": True}}},
        })
        if logo_override is not None:
            with open(os.path.join(root, "logo-overrides", "135275.json"), "w", encoding="utf-8") as f:
                json.dump(logo_override, f)

    def propose(self, **kw):
        return logo_correction.propose(logo_correction.parse_form(form(**kw)), self.root, 21, "2026-10-03")


def fake_fetch(fail=False):
    images = {"logos": [
        {"file_path": "/s3.png", "iso_639_1": "ja", "aspect_ratio": 2.383, "vote_average": 3.3, "vote_count": 1},
        {"file_path": "/base.png", "iso_639_1": "ja", "aspect_ratio": 2.112, "vote_average": 0, "vote_count": 0},
        {"file_path": "/en.png", "iso_639_1": "en", "aspect_ratio": 4.1},
    ]}
    detail = {"seasons": [{"season_number": 1, "name": "シーズン1", "air_date": "2016-01-14"}]}

    def fetch(path, params=None):
        if fail:
            raise TimeoutError()
        if path == "/tv/65844/images":
            return 200, images
        if path == "/tv/65844":
            return 200, detail
        return 404, None
    return fetch


def run(repo, image_size=lambda path: None, **kw):
    p = repo.propose(**kw)
    if not p.errors and not p.noop:
        logo_correction.verify(p, fake_fetch(), image_size=image_size)
    if not p.errors and not p.noop:
        logo_correction.finish(p)
    return p


class ParseTest(unittest.TestCase):
    def test_logo_form_is_not_an_entry_correction(self):
        self.assertIsNone(correction.parse_form(form()))
        self.assertIsNotNone(logo_correction.parse_form(form()))

    def test_entry_correction_form_is_not_a_logo_correction(self):
        body = "### Bangumi id\n\n265\n\n### TMDB 条目\n\ntv/890\n\n### 背景图\n\n_No response_\n"
        self.assertIsNone(logo_correction.parse_form(body))
        self.assertIsNotNone(correction.parse_form(body))

    def test_language_words_and_image_urls(self):
        self.assertEqual("ja", logo_correction.parse_lang("日文"))
        self.assertEqual("zh", logo_correction.parse_lang(" ZH "))
        self.assertIsNone(logo_correction.parse_lang("japanese"))
        self.assertEqual("/abc.png", logo_correction.parse_logo("https://image.tmdb.org/t/p/w500/abc.png"))
        self.assertEqual("/abc.png", logo_correction.parse_logo("https://image.tmdb.org/t/p/original/abc.svg"))
        self.assertIsNone(logo_correction.parse_logo("/abc.jpg"))


class ProposeTest(unittest.TestCase):
    def test_set_a_logo(self):
        p = run(Repo())
        self.assertEqual([], p.errors)
        self.assertEqual("tv/65844", p.override["tmdb"])
        self.assertEqual({"logo": "/base.png", "aspect": 2.112, "auto_was": "/s3.png:2.383"}, p.override["logos"]["ja"])
        self.assertEqual([(1, "シーズン1", "2016-01-14")], p.seasons)
        self.assertEqual(["/s3.png", "/base.png", "/en.png"], [x["file_path"] for x in p.candidates])
        pr = logo_correction.render_pr(p, 21, "someone")
        self.assertIn("/base.png</code> 日文 ✅", pr)
        self.assertIn("第 1 季「シーズン1」", pr)
        self.assertIn("Closes #21", pr)

    def test_text_instead_of_a_logo(self):
        p = run(Repo(), language="zh", logo="无")
        self.assertEqual([], p.errors)
        self.assertEqual({"none": True, "auto_was": "-"}, p.override["logos"]["zh"])
        self.assertTrue(any("和现在自动挑的一样" in w for w in p.warnings))

    def test_other_languages_of_an_existing_override_are_kept(self):
        existing = {"tmdb": "tv/65844", "logos": {"en": {"logo": "/en.png", "aspect": 4.1}}}
        p = run(Repo(existing))
        self.assertEqual({"en", "ja"}, set(p.override["logos"]))

    def test_revert_the_last_language_deletes_the_file(self):
        existing = {"tmdb": "tv/65844", "logos": {"ja": {"logo": "/base.png", "aspect": 2.112}}}
        p = run(Repo(existing), logo="撤销")
        self.assertEqual([], p.errors)
        self.assertIsNone(p.override)

    def test_revert_without_an_override(self):
        p = run(Repo(), logo="撤销")
        self.assertTrue(any("不用撤销" in e for e in p.errors))

    def test_same_as_the_existing_override_is_a_noop(self):
        existing = {"tmdb": "tv/65844", "logos": {"ja": {"logo": "/base.png", "aspect": 2.112}}}
        p = Repo(existing).propose()
        self.assertTrue(p.noop)

    def test_override_for_another_entry_is_dropped(self):
        existing = {"tmdb": "tv/1", "logos": {"en": {"logo": "/old.png", "aspect": 3}}}
        p = run(Repo(existing))
        self.assertEqual({"ja"}, set(p.override["logos"]))
        self.assertTrue(any("原来的修正一并清掉" in w for w in p.warnings))

    def test_logo_must_come_from_the_current_entry(self):
        p = run(Repo(), tmdb="tv/1429")
        self.assertTrue(any("只能从对应表里这个条目现在的 TMDB 条目 tv/65844" in e for e in p.errors))

    def test_logo_not_on_tmdb(self):
        p = run(Repo(), logo="/nope.png")
        self.assertTrue(any("不在 tv/65844 的 logo 里" in e for e in p.errors))

    def test_svg_logo_missing_from_the_api_is_checked_on_the_image_host(self):
        p = run(Repo(), logo="https://image.tmdb.org/t/p/original/svg-only.svg", image_size=lambda path: (500, 250))
        self.assertEqual([], p.errors)
        self.assertEqual({"logo": "/svg-only.png", "aspect": 2.0, "auto_was": "/s3.png:2.383"}, p.override["logos"]["ja"])
        self.assertTrue(any("接口的 logo 列表里没有这张" in w for w in p.warnings))
        self.assertIn("图床上有它的 PNG 版", logo_correction.render_pr(p, 21, "someone"))

    def test_logo_of_another_language_is_allowed_with_a_warning(self):
        p = run(Repo(), language="zh", logo="/en.png")
        self.assertEqual([], p.errors)
        self.assertTrue(any("标的语言是 en" in w for w in p.warnings))

    def test_entry_without_tmdb(self):
        p = run(Repo(), bgm_id="554346")
        self.assertTrue(any("没有 TMDB 条目" in e for e in p.errors))


class LogoColumnTest(unittest.TestCase):
    def test_round_trip(self):
        text = "o=ja ja=/a.png:2.38 zh=- en=/b.png:10"
        self.assertEqual(text, common.format_logos(*common.parse_logos(text)))

    def test_override_on_top_of_auto(self):
        auto = {"original": "ja", "logos": {"ja": {"logo": "/s3.png", "aspect": 2.383}, "zh": {"none": True}}}
        override = {"tmdb": "tv/65844", "logos": {"ja": {"logo": "/base.png", "aspect": 2.112}}}
        self.assertEqual("o=ja ja=/base.png:2.112 zh=-", common.logo_cell("tv/65844", override, auto))
        stale = dict(override, tmdb="tv/1")
        self.assertEqual("o=ja ja=/s3.png:2.383 zh=-", common.logo_cell("tv/65844", stale, auto))
        self.assertEqual("", common.logo_cell("tv/65844", None, None))
        self.assertEqual("", common.logo_cell(None, override, auto))

    def test_bad_override_files(self):
        for raw in ({"logos": {"ja": {"none": True}}},
                    {"tmdb": "tv/1", "logos": {}},
                    {"tmdb": "tv/1", "logos": {"japanese": {"none": True}}},
                    {"tmdb": "tv/1", "logos": {"ja": {"logo": "/a.jpg", "aspect": 2}}},
                    {"tmdb": "tv/1", "logos": {"ja": {"logo": "/a.png"}}},
                    {"tmdb": "tv/1", "logos": {"ja": {"none": True, "logo": "/a.png"}}}):
            with self.assertRaises(ValueError, msg=raw):
                common.normalize_logo_override(raw)

    def test_merge_writes_the_logo_column(self):
        repo = Repo({"tmdb": "tv/65844", "logos": {"ja": {"logo": "/base.png", "aspect": 2.112}}})
        root = repo.root
        map_path = os.path.join(root, "map", "bgm-tmdb.tsv")
        subprocess.run([sys.executable, os.path.join(HERE, "merge.py"), "--apply-only",
                        "--state", os.path.join(root, "state", "state.tsv"), "--map", map_path,
                        "--meta", os.path.join(root, "map", "meta.json"), "--site", os.path.join(root, "docs"),
                        "--overrides", os.path.join(root, "overrides"),
                        "--logo-overrides", os.path.join(root, "logo-overrides"),
                        "--logos", os.path.join(root, "state", "logos.tsv")],
                       check=True, capture_output=True)
        with open(map_path, encoding="utf-8") as f:
            rows = [line.rstrip("\n").split("\t") for line in f if not line.startswith("#")]
        self.assertEqual([["135275", "tv/65844", "/k.jpg", "tv/65844", "auto", "S1E1", "o=ja ja=/base.png:2.112 zh=- en=-"]],
                         rows)


if __name__ == "__main__":
    unittest.main()
