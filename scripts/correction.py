"""修正请求 (issue 表单) → 校验、向 TMDB 核实 → 写好 overrides/<bgm_id>.json 与 PR 说明.

由 .github/workflows/correction.yml 在修正请求新建、编辑、重开时调用; 建分支、开 PR、回复 issue 由 correction_pr.sh 做.
issue 是任何人都能写的: 这里只把它当数据, 每个字段按格式取出来再校验, 写进仓库的只有规整后的 JSON.

输入 (环境变量):
  ISSUE_NUMBER / ISSUE_BODY / ISSUE_AUTHOR / ISSUE_AUTHOR_ID
  TMDB_API_TOKEN  可空; 空时不向 TMDB 核实, PR 里注明
输出 (--out 目录):
  result.json  {"status": skip | invalid | noop | ok, "bgm_id", "action": set | none | revert,
                "file", "pr_title", "issue_title"}
  reply.md     回复 issue 的内容 (ok 时 correction_pr.sh 在前面加上 PR 编号)
  pr.md / commit.txt  PR 说明与提交说明 (ok)
  修正文件直接写进 (撤销时删出) 仓库的 overrides/.
"""
import argparse
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from common import normalize_override
from merge import SHARD, effective

PAGE = "https://grahamzen.github.io/bangumi-tmdb-map/"
TMDB_SITE = "https://www.themoviedb.org/"
IMG = "https://image.tmdb.org/t/p/"
API = "https://api.tmdb.org/3"

# issue 表单 (.github/ISSUE_TEMPLATE/correction.yml) 各项的标题 → 键
FORM = {"Bangumi id": "bgm_id", "TMDB 条目": "tmdb", "背景图": "backdrop", "分集数据": "stills", "说明": "reason"}
NONE_WORDS = {"无", "没有", "无对应", "没有对应", "none"}
REVERT_WORDS = {"撤销", "撤销修正", "撤销人工修正", "恢复自动", "revert"}
REF_IN_TEXT = re.compile(r"\b(tv|movie|collection)/(\d+)")
SEASON_IN_TEXT = re.compile(r"\btv/(\d+)[^/\s]*/season/(\d+)")
PATH_IN_TEXT = re.compile(r"/([A-Za-z0-9_\-]+\.(?:jpg|jpeg|png|webp))(?:[?#]\S*)?$")
REASON_MAX = 1500
FILE_KEYS = ("none", "backdrop", "backdrop_path", "stills", "title", "note", "updated", "auto_was")


def parse_form(body):
    """issue 表单渲染出的正文 → {键: 值}; 不是这张表单 (缺 Bangumi id 或 TMDB 条目那一项) 返回 None.

    表单每一项渲染成 `### 标题` 加一段值, 没填的可空项是 `_No response_`. 每个标题只认第一次出现:
    排在最后的「说明」里就算写了同名的标题行, 也只是说明的内容.
    """
    sections, key = {}, None
    for line in (body or "").replace("\r\n", "\n").split("\n"):
        m = re.match(r"^###\s+(.+?)\s*$", line)
        heading = FORM.get(m.group(1)) if m else None
        if heading and heading not in sections:
            key = heading
            sections[key] = []
        elif key is not None:
            sections[key].append(line)
    fields = {}
    for k, lines in sections.items():
        value = "\n".join(lines).strip()
        fields[k] = "" if value == "_No response_" else value
    if "bgm_id" not in fields or "tmdb" not in fields:
        return None
    return fields


def parse_bgm_id(text):
    text = text.strip()
    if re.fullmatch(r"\d{1,8}", text):
        return int(text)
    m = re.search(r"/subject/(\d{1,8})\b", text)
    return int(m.group(1)) if m else None


def parse_ref(text):
    m = REF_IN_TEXT.search(text)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def parse_path(text):
    m = PATH_IN_TEXT.search(text.strip())
    return f"/{m.group(1)}" if m else None


def parse_stills(text):
    """分集数据 → ([出处], [认不出的片段]). 季只认 S0 (衍生作挂在本篇特别篇下), 其余的季交给校验报错."""
    refs, bad = [], []
    for part in re.split(r"[\s,，]+", text.strip()):
        if not part:
            continue
        m = SEASON_IN_TEXT.search(part)
        ref = f"tv/{m.group(1)}/season/{m.group(2)}" if m else parse_ref(part)
        if ref is None:
            bad.append(part)
        elif ref not in refs:
            refs.append(ref)
    return refs, bad


def load_entry(root, sid):
    """(索引行, 条目记录). 不是动画条目时索引行为 None; 没查过的条目记录为空."""
    row = None
    with open(os.path.join(root, "docs", "data", "index.tsv"), encoding="utf-8") as f:
        prefix = f"{sid}\t"
        for line in f:
            if line.startswith(prefix):
                p = line.rstrip("\n").split("\t")
                row = {"name": p[1], "cn": p[2], "date": p[3], "platform": p[4]}
                break
    rec = {}
    shard = os.path.join(root, "docs", "data", "s", f"{sid // SHARD}.json")
    if row is not None and os.path.isfile(shard):
        with open(shard, encoding="utf-8") as f:
            rec = json.load(f).get(str(sid), {})
    return row, rec


class Proposal:
    def __init__(self):
        self.errors, self.warnings = [], []
        self.sid = None
        self.row = None
        self.rec = {}
        self.action = None          # set / none / revert
        self.override = None        # set / none: 要写进文件的内容
        self.existing = None        # 仓库里现有的修正 (规整后); 文件不合法时为 {}
        self.noop = False
        self.reason = ""
        self.verified = False       # 向 TMDB 核实过
        self.image_ok = None        # 背景图在不在这个条目的背景图里 (没给图或没核实时为 None)
        self.entity = None          # 背景图 (或分集数据) 条目在 TMDB 上的名字与日期

    @property
    def name(self):
        return (self.row or {}).get("cn") or (self.row or {}).get("name") or ""

    @property
    def verb(self):
        return {"set": "修正", "none": "确认无对应", "revert": "撤销人工修正"}[self.action]

    @property
    def file(self):
        return f"overrides/{self.sid}.json"


def auto_was(rec):
    """修正时的自动结果, 与核对页生成的同一写法."""
    auto = rec.get("auto")
    if not auto:
        return None
    if auto.get("status") == "hit":
        return " ".join(x for x in (auto.get("backdrop"), auto.get("backdropPath"), auto.get("stillsSource")) if x)
    return "(自动: 没有对应)"


def same_target(a, b):
    return all(a.get(k) == b.get(k) for k in ("none", "backdrop", "backdrop_path", "stills"))


def propose(fields, root, issue, today):
    p = Proposal()
    p.reason = fields.get("reason", "")
    sid = parse_bgm_id(fields.get("bgm_id", ""))
    if sid is None:
        p.errors.append("「Bangumi id」要填条目页网址里的数字，例如 bgm.tv/subject/554346 就填 554346")
        return p
    p.sid = sid
    p.row, p.rec = load_entry(root, sid)
    if p.row is None:
        p.errors.append(f"本表里没有 id 为 {sid} 的条目 (只收 Bangumi 上的动画条目)")
        return p
    path = os.path.join(root, p.file)
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                p.existing = normalize_override(json.load(f))
        except (ValueError, json.JSONDecodeError):
            p.existing = {}

    word = fields.get("tmdb", "").strip()
    backdrop_text, stills_text = fields.get("backdrop", "").strip(), fields.get("stills", "").strip()
    if word.lower() in REVERT_WORDS:
        p.action = "revert"
        if p.existing is None:
            p.errors.append("这个条目没有人工修正，不用撤销")
        if backdrop_text or stills_text:
            p.warnings.append("撤销时「背景图」「分集数据」不用填，已忽略")
        return p

    raw = {"none": word.lower() in NONE_WORDS}
    if raw["none"]:
        p.action = "none"
        if backdrop_text or stills_text:
            p.errors.append("「TMDB 条目」填了「无」，就不要再填「背景图」和「分集数据」")
    else:
        p.action = "set"
        ref = parse_ref(word)
        if not word:
            p.errors.append("「TMDB 条目」必填：tv/123、movie/123、collection/123，或 TMDB 网址")
        elif ref is None:
            p.errors.append(f"「TMDB 条目」看不出是哪个：{clip(word, 80)}（要写 tv/123、movie/123、collection/123，"
                            "或粘贴 TMDB 网址；没有对应就填「无」，撤销人工修正就填「撤销」）")
        raw["backdrop"] = ref
        if backdrop_text:
            raw["backdrop_path"] = parse_path(backdrop_text)
            if raw["backdrop_path"] is None:
                p.errors.append(f"「背景图」看不出是哪张图：{clip(backdrop_text, 80)}（要写 /xxxx.jpg，或粘贴 TMDB 图片网址）")
        stills, bad = parse_stills(stills_text)
        for part in bad:
            p.errors.append(f"「分集数据」看不出是哪个：{clip(part, 80)}（要写 tv/123、tv/123/season/0 或 movie/123）")
        for ref in stills:
            if "/season/" in ref and not ref.endswith("/season/0"):
                p.errors.append(f"「分集数据」只认整部 (tv/123) 或特别篇 (tv/123/season/0)，不能指定别的季：{ref}")
        if len(stills) > 1:
            p.errors.append("「分集数据」只能填一个出处")
        if stills:
            raw["stills"] = stills
    if p.errors:
        return p

    first = next((line.strip() for line in p.reason.splitlines() if line.strip()), "")
    raw["note"] = f"{clip(first, 80)} (修正请求 #{issue})" if first else f"修正请求 #{issue}"
    raw["updated"] = today
    was = auto_was(p.rec)
    if was:
        raw["auto_was"] = was
    try:
        p.override = normalize_override(raw)
    except ValueError as e:
        p.errors.append(f"内容不合法：{e}")
        return p

    if p.existing and same_target(p.existing, p.override):
        p.noop = True
    elif p.existing is None and not p.override["none"]:
        auto = p.rec.get("auto") or {}
        if auto.get("status") == "hit" and auto.get("backdrop") == p.override["backdrop"] \
                and auto.get("backdropPath") == p.override["backdrop_path"]:
            p.warnings.append("和现在的自动结果一样：合并后这个条目就固定下来，自动匹配不再改它")
    return p


def make_fetch(token):
    """TMDB 接口的 GET; 返回 (状态码, JSON). 404 不抛, 其他失败重试两次后抛."""
    def fetch(path, params=None):
        url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/json",
            "User-Agent": "bangumi-tmdb-map/correction (+https://github.com/GrahamZen/bangumi-tmdb-map)",
        })
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    return r.status, json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return 404, None
                if e.code not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise
            except (urllib.error.URLError, TimeoutError):
                if attempt == 2:
                    raise
            time.sleep(2 * (attempt + 1))
    return fetch


def describe(ref, fetch):
    """TMDB 条目的 {name, original, date}; 不存在返回 None."""
    kind, tid = ref.split("/")[:2]
    status, data = fetch(f"/{kind}/{tid}", {"language": "zh-CN"})
    if status == 404 or not data:
        return None
    return {
        "name": data.get("name") or data.get("title") or "",
        "original": data.get("original_name") or data.get("original_title") or "",
        "date": data.get("first_air_date") or data.get("release_date") or "",
    }


def verify(p, fetch):
    """向 TMDB 核实条目、图片与分集数据的出处, 顺带把条目名字填进 title. 条目不存在算错, 图片对不上只提醒."""
    o = p.override
    if p.action != "set":
        return
    try:
        main_ref = o["backdrop"] or o["stills"][0].split("/season/")[0]
        p.entity = describe(main_ref, fetch)
        if p.entity is None:
            p.errors.append(f"TMDB 上没有 {main_ref}")
            return
        if p.entity["name"]:
            o["title"] = p.entity["name"]
        if o["backdrop_path"]:
            kind, tid = o["backdrop"].split("/")
            _, images = fetch(f"/{kind}/{tid}/images")
            images = images or {}
            p.image_ok = any(i.get("file_path") == o["backdrop_path"] for i in images.get("backdrops") or [])
            if not p.image_ok:
                other = next((label for key, label in (("posters", "海报"), ("logos", "标志"))
                              if any(i.get("file_path") == o["backdrop_path"] for i in images.get(key) or [])), None)
                p.warnings.append(f"这张图是 {o['backdrop']} 的{other}，不是背景图" if other else
                                  f"这张图不在 {o['backdrop']} 的背景图里 (可能是剧照或别的条目的图)")
        for ref in o["stills"]:
            if ref.endswith("/season/0"):
                tid = ref.split("/")[1]
                status, _ = fetch(f"/tv/{tid}/season/0", {"language": "zh-CN"})
                if status == 404:
                    p.errors.append(f"tv/{tid} 在 TMDB 上没有第 0 季 (特别篇)")
            elif ref != main_ref and describe(ref, fetch) is None:
                p.errors.append(f"TMDB 上没有 {ref}")
        p.verified = True
    except Exception as e:  # noqa: BLE001 — 网络或接口出错时不挡请求, 交给维护者看
        p.warnings.append(f"没能向 TMDB 核实 ({type(e).__name__})，请维护者自己看一眼")


def clip(text, limit):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def safe(text):
    """外来文字放进 issue / PR: 不让它变成标签或 @ 提醒."""
    return str(text).replace("<", "&lt;").replace(">", "&gt;").replace("@", "@​")


def cell(text):
    """表格单元格: 另外不让竖线把表格拆开, 过长的截断."""
    return safe(clip(text, 200)).replace("|", "\\|")


def ref_md(ref):
    m = re.match(r"^(tv|movie|collection)/(\d+)(?:/season/(\d+))?$", ref or "")
    if not m:
        return cell(ref or "—")
    url = f"{TMDB_SITE}{m.group(1)}/{m.group(2)}" + (f"/season/{m.group(3)}" if m.group(3) else "")
    return f"[{ref}]({url})"


def img_md(path):
    return f'<img src="{IMG}w300{path}" width="240" alt="{path}">' if path else "—"


def current_rows(rec):
    status, source, backdrop, path, stills, title, _ = effective(rec)
    if source == "manual":
        result = "人工 · 确认没有对应" if status == "miss" else "人工 · 有对应"
    elif source == "auto":
        result = {"hit": "自动 · 有对应", "miss": "自动 · 没有对应", "err": "自动 · 失败待重试"}[status]
    else:
        result = "失败待重试" if status == "err" else "还没查"
    entity = (ref_md(backdrop) + (f" {cell(title)}" if title else "")) if backdrop else "—"
    return result, entity, img_md(path), ref_md(stills) if stills else "—"


def target_rows(p):
    o = p.override
    if p.action == "revert":
        auto = p.rec.get("auto") or {}
        was = "—"
        if auto.get("status") == "hit":
            was = ref_md(auto.get("backdrop")) + (f" {cell((auto.get('tmdb') or {}).get('name') or '')}")
        elif auto:
            was = "没有对应"
        return "回到自动匹配 (下一轮重新查)", f"上次的自动结果：{was}", "—", "—"
    if o["none"]:
        return "人工 · 确认没有对应", "— (客户端不出图)", "—", "—"
    e = p.entity or {}
    entity = ref_md(o["backdrop"]) if o["backdrop"] else "—"
    if e.get("name"):
        entity += f" {cell(e['name'])}" + (f" ({cell(e['date'])})" if e.get("date") else "")
        if e.get("original") and e["original"] != e["name"]:
            entity += f"<br>{cell(e['original'])}"
    image = img_md(o["backdrop_path"]) if o["backdrop_path"] else "没指定，客户端按条目自己挑"
    stills = ref_md(o["stills"][0]) if o["stills"] else "跟着背景图条目"
    return "人工 · 有对应", entity, image, stills


def render_pr(p, issue, author):
    sid, row = p.sid, p.row
    lines = [f"修正请求 #{issue}，由 @{author} 提交。", ""]
    head = f"**{cell(p.name)}**"
    if row["cn"] and row["name"] and row["cn"] != row["name"]:
        head += f" ({cell(row['name'])})"
    lines.append(head + f" · Bangumi [{sid}](https://bgm.tv/subject/{sid}) · [核对页]({PAGE}#{sid})")
    lines += ["", "| | 现在 | 改成 |", "|---|---|---|"]
    for label, now, then in zip(("结果", "TMDB 条目", "背景图", "分集数据"), current_rows(p.rec), target_rows(p)):
        lines.append(f"| {label} | {now} | {then} |")
    lines.append("")
    if p.action == "set":
        lines.append("TMDB 核实：" + ("没有核实" if not p.verified else
                                   "条目存在" + ("，背景图属于这个条目" if p.image_ok else "")))
    for w in p.warnings:
        lines.append(f"- 请留意：{cell(w)}")
    if p.reason.strip():
        reason = p.reason.replace("\r", "").replace("~~~", "～～～")
        if len(reason) > REASON_MAX:
            reason = reason[:REASON_MAX] + "\n…(太长，截断了；全文见 issue)"
        lines += ["", "<details><summary>提交者的说明</summary>", "", "~~~text", reason, "~~~", "", "</details>"]
    lines += ["", "合并后 apply-overrides 几分钟内把它并进对应表与核对页；不采纳就直接关闭这个 PR，issue 会一起关掉。",
              "", f"Closes #{issue}", ""]
    return "\n".join(lines)


def render_summary(p):
    """回复 issue 用: 改成什么、要留意什么."""
    if p.action == "revert":
        lines = [f"- 撤销 {p.sid} 的人工修正，回到自动匹配，下一轮重新查"]
    elif p.override["none"]:
        lines = [f"- {p.sid} 确认 TMDB 上没有对应，客户端不出图"]
    else:
        o, e = p.override, p.entity or {}
        what = o["backdrop"] + (f" {e['name']}" if e.get("name") else "")
        if o["backdrop_path"]:
            what += f"，背景图 {o['backdrop_path']}"
        if o["stills"]:
            what += f"，分集数据 {o['stills'][0]}"
        lines = [f"- {p.sid} 改成 {cell(what)}"]
    lines += [f"- 请留意：{cell(w)}" for w in p.warnings]
    lines += ["", "要调整就直接编辑这个 issue，PR 会跟着更新；不想改了就关掉这个 issue。"]
    return "\n".join(lines) + "\n"


def render_invalid(p):
    lines = ["这个修正请求没法处理：", ""] + [f"- {safe(e)}" for e in p.errors]
    lines += ["", f"改好后直接编辑这个 issue，会自动重新检查。也可以在[核对页]({PAGE}"
                  + (f"#{p.sid}" if p.sid else "") + ")找到条目后点「提交修正请求」，内容会自动填好。"]
    return "\n".join(lines) + "\n"


def commit_message(p, pr_title, issue, author, author_id):
    msg = f"{pr_title}\n\n修正请求 #{issue}\n"
    if author and re.fullmatch(r"\d+", author_id or "") and not author.endswith("[bot]"):
        msg += f"\nCo-authored-by: {author} <{author_id}+{author}@users.noreply.github.com>\n"
    return msg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="结果目录")
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--today", default=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d"))
    args = ap.parse_args()
    issue = int(os.environ["ISSUE_NUMBER"])
    author = os.environ.get("ISSUE_AUTHOR", "")
    author_id = os.environ.get("ISSUE_AUTHOR_ID", "")
    token = os.environ.get("TMDB_API_TOKEN", "")
    os.makedirs(args.out, exist_ok=True)

    def write(name, text):
        with open(os.path.join(args.out, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    fields = parse_form(os.environ.get("ISSUE_BODY", ""))
    if fields is None:
        write("result.json", json.dumps({"status": "skip"}))
        print("不是修正请求表单, 跳过")
        return
    p = propose(fields, args.root, issue, args.today)
    if not p.errors and not p.noop and p.action == "set":
        if token:
            verify(p, make_fetch(token))
        else:
            p.warnings.append("没有 TMDB 令牌，没向 TMDB 核实，请维护者自己看一眼")

    result = {"status": "invalid", "bgm_id": p.sid, "action": p.action}
    if p.errors:
        write("reply.md", render_invalid(p))
    else:
        issue_title = f"{p.verb} {p.sid} {p.name}".strip()
        pr_title = issue_title + (f" → {p.override['backdrop'] or p.override['stills'][0]}" if p.action == "set" else "")
        result.update({"file": p.file, "pr_title": clip(pr_title, 120), "issue_title": clip(issue_title, 120)})
        if p.noop:
            result["status"] = "noop"
            write("reply.md", "这个条目现在的人工修正已经是这样了，不需要改。\n")
        else:
            result["status"] = "ok"
            target = os.path.join(args.root, p.file)
            if p.action == "revert":
                os.remove(target)
            else:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                # 与核对页生成的同一字段顺序; 空字段不写
                content = {k: p.override[k] for k in FILE_KEYS if k == "none" or p.override.get(k) not in (None, [], "")}
                with open(target, "w", encoding="utf-8", newline="\n") as f:
                    json.dump(content, f, ensure_ascii=False, indent=2)
                    f.write("\n")
            write("reply.md", render_summary(p))
            write("pr.md", render_pr(p, issue, author))
            write("commit.txt", commit_message(p, result["pr_title"], issue, author, author_id))
    write("result.json", json.dumps(result, ensure_ascii=False))
    print(json.dumps(result, ensure_ascii=False))
    for e in p.errors:
        print("  错误:", e)
    for w in p.warnings:
        print("  留意:", w)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
