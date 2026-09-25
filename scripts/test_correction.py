"""correction.py 的单测: python3 -m unittest discover -s scripts"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

import correction

HERE = os.path.dirname(os.path.abspath(__file__))


def form(bgm_id="554346", tmdb="tv/38693", backdrop="", stills="", reason="备选里第一个就是这部"):
    def value(v):
        return v if v else "_No response_"
    return (f"### Bangumi id\n\n{value(bgm_id)}\n\n### TMDB 条目\n\n{value(tmdb)}\n\n### 背景图\n\n{value(backdrop)}\n\n"
            f"### 分集数据\n\n{value(stills)}\n\n### 说明\n\n{value(reason)}\n")


class Repo:
    """最小的仓库: 索引三条 (没对应 / 有对应 / 人工修正过), 条目记录与一个修正文件."""

    def __init__(self):
        self.dir = tempfile.TemporaryDirectory()
        root = self.root = self.dir.name
        os.makedirs(os.path.join(root, "docs", "data", "s"))
        os.makedirs(os.path.join(root, "overrides"))
        with open(os.path.join(root, "docs", "data", "index.tsv"), "w", encoding="utf-8") as f:
            f.write("# id\tname\tcn\tdate\tplatform\tpop\tstatus\tsource\tref\ttitle\tchecked\n")
            f.write("554346\tLEGO Ninjago：Masters of Spinjitzu Season 3\t乐高幻影忍者 第三季\t2014-01-29\tTV\t142\tmiss\tauto\t\t\t2026-09-25\n")
            f.write("265\tネオン・ジェネシス\t新世纪福音战士\t1995-10-04\tTV\t1\thit\tauto\ttv/890\tEVA\t2026-09-25\n")
            f.write("317\t狐仙大人\t狐仙大人\t2010-01-01\tTV\t1\thit\tmanual\ttv/1\tX\t2026-09-25\n")
        records = {
            "554346": {"auto": {"status": "miss", "checked": "2026-09-25", "candidates": []}},
            "265": {"auto": {"status": "hit", "backdrop": "tv/890", "backdropPath": "/eva.jpg", "stillsSource": "tv/890",
                             "tmdb": {"name": "新世纪福音战士"}}},
            "317": {"auto": {"status": "hit", "backdrop": "tv/9", "backdropPath": "/a.jpg", "stillsSource": "tv/9"},
                    "manual": {"none": False, "backdrop": "tv/1", "backdrop_path": "/m.jpg", "stills": [], "title": "X"}},
        }
        for shard in (0, 277):
            part = {k: v for k, v in records.items() if int(k) // 2000 == shard}
            with open(os.path.join(root, "docs", "data", "s", f"{shard}.json"), "w", encoding="utf-8") as f:
                json.dump(part, f, ensure_ascii=False)
        with open(os.path.join(root, "overrides", "317.json"), "w", encoding="utf-8") as f:
            json.dump({"backdrop": "tv/1", "backdrop_path": "/m.jpg", "title": "X"}, f)

    def propose(self, **kw):
        return correction.propose(correction.parse_form(form(**kw)), self.root, 12, "2026-09-25")


def fake_fetch(entities=None, images=None, seasons=(), fail=False):
    entities = entities if entities is not None else {"tv/38693": {"name": "乐高幻影忍者", "original_name": "Ninjago",
                                                                    "first_air_date": "2011-01-14"}}
    images = images if images is not None else {"tv/38693": {"backdrops": [{"file_path": "/bd.jpg"}],
                                                             "posters": [{"file_path": "/poster.jpg"}]}}
    calls = []

    def fetch(path, params=None):
        calls.append(path)
        if fail:
            raise TimeoutError()
        parts = path.strip("/").split("/")
        ref = "/".join(parts[:2])
        if len(parts) == 2:
            return (200, entities[ref]) if ref in entities else (404, None)
        if parts[2] == "images":
            return 200, images.get(ref, {})
        if parts[2] == "season":
            return (200, {}) if ref in seasons else (404, None)
        raise AssertionError(path)
    fetch.calls = calls
    return fetch


class ParseFormTest(unittest.TestCase):
    def test_fields(self):
        f = correction.parse_form(form(backdrop="https://image.tmdb.org/t/p/original/bd.jpg"))
        self.assertEqual(f["bgm_id"], "554346")
        self.assertEqual(f["tmdb"], "tv/38693")
        self.assertEqual(f["backdrop"], "https://image.tmdb.org/t/p/original/bd.jpg")
        self.assertEqual(f["stills"], "")
        self.assertEqual(f["reason"], "备选里第一个就是这部")

    def test_crlf(self):
        self.assertEqual(correction.parse_form(form().replace("\n", "\r\n"))["tmdb"], "tv/38693")

    def test_not_the_form(self):
        self.assertIsNone(correction.parse_form("随便写点什么"))
        self.assertIsNone(correction.parse_form("### Bangumi id\n\n1\n"))
        self.assertIsNone(correction.parse_form(None))

    def test_heading_inside_reason_is_reason(self):
        f = correction.parse_form(form(reason="看这里\n### TMDB 条目\n\ntv/999"))
        self.assertEqual(f["tmdb"], "tv/38693")
        self.assertIn("tv/999", f["reason"])


class ProposeTest(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.dir.cleanup()

    def test_set_from_urls(self):
        p = self.repo.propose(tmdb="https://www.themoviedb.org/tv/38693-ninjago?language=zh-CN",
                              backdrop="https://image.tmdb.org/t/p/w780/bd.jpg?x=1")
        self.assertEqual(p.errors, [])
        self.assertEqual(p.action, "set")
        o = p.override
        self.assertEqual((o["none"], o["backdrop"], o["backdrop_path"], o["stills"]), (False, "tv/38693", "/bd.jpg", []))
        self.assertEqual(o["note"], "备选里第一个就是这部 (修正请求 #12)")
        self.assertEqual(o["auto_was"], "(自动: 没有对应)")
        self.assertEqual(o["updated"], "2026-09-25")

    def test_bgm_id_from_url(self):
        self.assertEqual(self.repo.propose(bgm_id="https://bgm.tv/subject/554346").sid, 554346)

    def test_stills_season_zero(self):
        p = self.repo.propose(stills="https://www.themoviedb.org/tv/38693-ninjago/season/0")
        self.assertEqual(p.override["stills"], ["tv/38693/season/0"])

    def test_stills_other_season_rejected(self):
        p = self.repo.propose(stills="tv/38693/season/3")
        self.assertTrue(any("不能指定别的季" in e for e in p.errors), p.errors)

    def test_two_stills_rejected(self):
        p = self.repo.propose(stills="tv/1, movie/2")
        self.assertTrue(any("只能填一个" in e for e in p.errors), p.errors)

    def test_none(self):
        p = self.repo.propose(tmdb="无")
        self.assertEqual(p.errors, [])
        self.assertEqual(p.action, "none")
        self.assertTrue(p.override["none"])

    def test_none_with_image_rejected(self):
        self.assertTrue(self.repo.propose(tmdb="无", backdrop="/bd.jpg").errors)

    def test_revert_needs_existing(self):
        self.assertTrue(any("不用撤销" in e for e in self.repo.propose(tmdb="撤销").errors))
        p = self.repo.propose(bgm_id="317", tmdb="撤销")
        self.assertEqual((p.errors, p.action), ([], "revert"))

    def test_bad_inputs(self):
        self.assertTrue(any("Bangumi id" in e for e in self.repo.propose(bgm_id="abc").errors))
        self.assertTrue(any("没有 id 为 999" in e for e in self.repo.propose(bgm_id="999").errors))
        self.assertTrue(any("看不出是哪个" in e for e in self.repo.propose(tmdb="幻影忍者").errors))
        self.assertTrue(any("看不出是哪张图" in e for e in self.repo.propose(backdrop="好看的那张").errors))

    def test_same_as_existing_manual_is_noop(self):
        p = self.repo.propose(bgm_id="317", tmdb="tv/1", backdrop="/m.jpg")
        self.assertTrue(p.noop)

    def test_same_as_auto_warns(self):
        p = self.repo.propose(bgm_id="265", tmdb="tv/890", backdrop="/eva.jpg")
        self.assertFalse(p.noop)
        self.assertTrue(any("固定下来" in w for w in p.warnings), p.warnings)
        self.assertEqual(p.override["auto_was"], "tv/890 /eva.jpg tv/890")


class VerifyTest(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.dir.cleanup()

    def test_entity_and_image(self):
        p = self.repo.propose(backdrop="/bd.jpg")
        correction.verify(p, fake_fetch())
        self.assertEqual((p.errors, p.warnings), ([], []))
        self.assertTrue(p.verified and p.image_ok)
        self.assertEqual(p.override["title"], "乐高幻影忍者")

    def test_poster_is_not_backdrop(self):
        p = self.repo.propose(backdrop="/poster.jpg")
        correction.verify(p, fake_fetch())
        self.assertTrue(any("海报" in w for w in p.warnings), p.warnings)

    def test_unknown_image(self):
        p = self.repo.propose(backdrop="/other.jpg")
        correction.verify(p, fake_fetch())
        self.assertTrue(any("不在" in w for w in p.warnings), p.warnings)

    def test_missing_entity(self):
        p = self.repo.propose(tmdb="tv/1")
        correction.verify(p, fake_fetch())
        self.assertTrue(any("TMDB 上没有 tv/1" in e for e in p.errors), p.errors)

    def test_missing_season_zero(self):
        p = self.repo.propose(stills="tv/38693/season/0")
        correction.verify(p, fake_fetch())
        self.assertTrue(any("第 0 季" in e for e in p.errors), p.errors)
        p = self.repo.propose(stills="tv/38693/season/0")
        correction.verify(p, fake_fetch(seasons={"tv/38693"}))
        self.assertEqual(p.errors, [])

    def test_network_failure_only_warns(self):
        p = self.repo.propose()
        correction.verify(p, fake_fetch(fail=True))
        self.assertEqual(p.errors, [])
        self.assertFalse(p.verified)
        self.assertTrue(any("没能向 TMDB 核实" in w for w in p.warnings))


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.dir.cleanup()

    def test_pr_body(self):
        p = self.repo.propose(backdrop="/bd.jpg", reason="@everyone <img src=x> ~~~\n看这里")
        correction.verify(p, fake_fetch())
        body = correction.render_pr(p, 12, "someone")
        self.assertIn("Closes #12", body)
        self.assertIn("@someone", body)
        self.assertIn("https://image.tmdb.org/t/p/w300/bd.jpg", body)
        self.assertIn("[tv/38693](https://www.themoviedb.org/tv/38693)", body)
        self.assertIn("自动 · 没有对应", body)
        self.assertIn("TMDB 核实：条目存在，背景图属于这个条目", body)
        # 说明原样放进代码块, 结束标记被换掉
        reason_block = body.split("~~~text\n", 1)[1].split("\n~~~\n", 1)[0]
        self.assertIn("@everyone <img src=x> ～～～", reason_block)

    def test_cells_escape(self):
        self.assertEqual(correction.cell("a|b <c> @d"), "a\\|b &lt;c&gt; @​d")

    def test_invalid_reply_escapes_mentions(self):
        p = self.repo.propose(tmdb="@someone")
        text = correction.render_invalid(p)
        self.assertNotIn("@someone", text)
        self.assertIn("#554346", text)

    def test_revert_rows(self):
        p = self.repo.propose(bgm_id="317", tmdb="撤销")
        body = correction.render_pr(p, 3, "u")
        self.assertIn("人工 · 有对应", body)
        self.assertIn("回到自动匹配", body)


class MainTest(unittest.TestCase):
    """整个脚本跑一遍 (不连 TMDB): 结果文件、修正文件与提交说明."""

    def run_main(self, repo, body, out):
        env = dict(os.environ, ISSUE_NUMBER="12", ISSUE_BODY=body, ISSUE_AUTHOR="someone", ISSUE_AUTHOR_ID="42",
                   TMDB_API_TOKEN="", PYTHONIOENCODING="utf-8")
        subprocess.run([sys.executable, os.path.join(HERE, "correction.py"), "--out", out, "--root", repo.root,
                        "--today", "2026-09-25"], env=env, check=True, capture_output=True)
        with open(os.path.join(out, "result.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_ok(self):
        repo = Repo()
        with tempfile.TemporaryDirectory() as out:
            result = self.run_main(repo, form(backdrop="/bd.jpg"), out)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["file"], "overrides/554346.json")
            self.assertEqual(result["pr_title"], "修正 554346 乐高幻影忍者 第三季 → tv/38693")
            self.assertEqual(result["issue_title"], "修正 554346 乐高幻影忍者 第三季")
            with open(os.path.join(repo.root, "overrides", "554346.json"), encoding="utf-8") as f:
                written = json.load(f)
            self.assertEqual(list(written), ["none", "backdrop", "backdrop_path", "note", "updated", "auto_was"])
            with open(os.path.join(out, "commit.txt"), encoding="utf-8") as f:
                self.assertIn("Co-authored-by: someone <42+someone@users.noreply.github.com>", f.read())
            with open(os.path.join(out, "pr.md"), encoding="utf-8") as f:
                self.assertIn("没有核实", f.read())
        repo.dir.cleanup()

    def test_revert_deletes_file(self):
        repo = Repo()
        with tempfile.TemporaryDirectory() as out:
            result = self.run_main(repo, form(bgm_id="317", tmdb="撤销"), out)
            self.assertEqual((result["status"], result["action"]), ("ok", "revert"))
            self.assertFalse(os.path.exists(os.path.join(repo.root, "overrides", "317.json")))
        repo.dir.cleanup()

    def test_skip_and_invalid(self):
        repo = Repo()
        with tempfile.TemporaryDirectory() as out:
            self.assertEqual(self.run_main(repo, "别的 issue", out)["status"], "skip")
            self.assertEqual(self.run_main(repo, form(tmdb="?"), out)["status"], "invalid")
            self.assertFalse(os.path.exists(os.path.join(repo.root, "overrides", "554346.json")))
        repo.dir.cleanup()


if __name__ == "__main__":
    unittest.main()
