"""标题 logo 修正请求 (issue 表单 .github/ISSUE_TEMPLATE/logo.yml; Izuko TV 详情页里的报告也开这张) → 校验、向 TMDB 核实
→ 写好 logo-overrides/<bgm_id>.json 与 PR 说明.

由 .github/workflows/correction.yml 在 correction.py 认不出表单 (不是条目修正) 时调用; 输入输出与 correction.py 相同,
建分支、开 PR、回复 issue 仍由 correction_pr.sh 做. issue 是任何人都能写的: 只把它当数据, 写进仓库的只有规整后的 JSON.

一个条目的 logo 修正按语言分开记 (logos.<语言>), 每次请求只改一种语言; 修正针对对应表里这个条目现在的 TMDB 条目
(背景图条目), 条目改了旧修正就不再生效 (见 merge.py).
"""
import argparse
import datetime
import json
import os
import re
import sys

from common import LOGO_LANG_RE, format_logo_entry, load_logos, normalize_logo_override
from correction import (IMG, NONE_WORDS, PAGE, REASON_MAX, REVERT_WORDS, cell, clip, commit_message, load_entry,
                        make_fetch, parse_bgm_id, parse_ref, ref_md, safe)
from merge import effective

FORM = {"Bangumi id": "bgm_id", "TMDB 条目": "tmdb", "语言": "language", "标题 logo": "logo", "说明": "reason"}
LOGO_IN_TEXT = re.compile(r"/([A-Za-z0-9_\-]+\.png)(?:[?#]\S*)?$")
LANG_WORDS = {"日文": "ja", "日语": "ja", "中文": "zh", "汉语": "zh", "英文": "en", "英语": "en"}
LANG_NAMES = {"ja": "日文", "zh": "中文", "en": "英文"}
GALLERY_MAX = 12


def parse_form(body):
    """issue 表单正文 → {键: 值}; 不是这张表单 (缺 Bangumi id 或标题 logo 那一项) 返回 None. 写法同 correction.parse_form."""
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
    if "bgm_id" not in fields or "logo" not in fields:
        return None
    return fields


def lang_name(lang):
    return LANG_NAMES.get(lang, lang)


class Proposal:
    def __init__(self):
        self.errors, self.warnings = [], []
        self.sid = None
        self.row = None
        self.rec = {}
        self.ref = None             # 对应表里这个条目现在的 TMDB 条目 (logo 从它里面挑)
        self.lang = None
        self.action = None          # set / none / revert
        self.entry = None           # 这种语言改成的 logo ({logo, aspect} 或 {none: True}); 撤销时 None
        self.existing = None        # 仓库里现有的 logo 修正 (规整后); 文件不合法时为 {}
        self.override = None        # 写进文件的整条修正; 撤销后一种语言也不剩时 None (删文件)
        self.auto = None            # 这种语言自动挑的 (state/logos.tsv)
        self.noop = False
        self.reason = ""
        self.verified = False
        self.candidates = []        # TMDB 上这个条目这种语言的全部 logo
        self.seasons = []           # 条目对应 TMDB 的哪几季 (季号, 季名, 首播日)
        self.override_logos = {}    # 改完后这个条目各语言的 logo 修正
        self.note = ""
        self.today = ""

    @property
    def name(self):
        return (self.row or {}).get("cn") or (self.row or {}).get("name") or ""

    @property
    def file(self):
        return f"logo-overrides/{self.sid}.json"


def parse_lang(text):
    text = text.strip()
    lang = LANG_WORDS.get(text, text.lower())
    return lang if LOGO_LANG_RE.match(lang) else None


def parse_logo(text):
    m = LOGO_IN_TEXT.search(text.strip())
    return f"/{m.group(1)}" if m else None


def current_entry(p):
    """这种语言现在生效的 logo 与来源: (entry, "人工" / "自动" / None)."""
    if p.existing and p.existing.get("tmdb") == p.ref and p.lang in p.existing["logos"]:
        return p.existing["logos"][p.lang], "人工"
    if p.auto is not None:
        return p.auto, "自动"
    return None, None


def propose(fields, root, issue, today):
    p = Proposal()
    p.reason = fields.get("reason", "")
    sid = parse_bgm_id(fields.get("bgm_id", ""))
    if sid is None:
        p.errors.append("「Bangumi id」要填条目页网址里的数字，例如 bgm.tv/subject/135275 就填 135275")
        return p
    p.sid = sid
    p.row, p.rec = load_entry(root, sid)
    if p.row is None:
        p.errors.append(f"本表里没有 id 为 {sid} 的条目 (只收 Bangumi 上的动画条目)")
        return p
    p.ref = effective(p.rec)[2]
    if not p.ref:
        p.errors.append("对应表里这个条目没有 TMDB 条目，标题 logo 要从它里面挑；先提交条目修正 (修正对应的 TMDB 条目)")
        return p
    tmdb_text = fields.get("tmdb", "").strip()
    if tmdb_text:
        ref = parse_ref(tmdb_text)
        if ref is None:
            p.errors.append(f"「TMDB 条目」看不出是哪个：{clip(tmdb_text, 80)}（可以不填）")
        elif ref != p.ref:
            p.errors.append(f"标题 logo 只能从对应表里这个条目现在的 TMDB 条目 {p.ref} 里挑，填的是 {ref}；"
                            "条目本身不对的话先提交条目修正")
    p.lang = parse_lang(fields.get("language", ""))
    if p.lang is None:
        p.errors.append("「语言」要填 ja / zh / en (或其他两个字母的语言码)")

    path = os.path.join(root, p.file)
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                p.existing = normalize_logo_override(json.load(f))
        except (ValueError, json.JSONDecodeError):
            p.existing = {}
    base = dict(p.existing["logos"]) if p.existing and p.existing["tmdb"] == p.ref else {}
    if p.existing and p.existing["tmdb"] != p.ref:
        p.warnings.append(f"这个条目原来的 logo 修正针对的是 {p.existing['tmdb']}，条目已经改成 {p.ref}，原来的修正一并清掉")

    auto = load_logos(os.path.join(root, "state", "logos.tsv")).get(p.ref)
    if auto and p.lang:
        p.auto = auto["logos"].get(p.lang)

    text = fields.get("logo", "").strip()
    if text.lower() in REVERT_WORDS:
        p.action = "revert"
        if p.lang and p.lang not in base:
            p.errors.append(f"这个条目的{lang_name(p.lang)} logo 没有人工修正，不用撤销")
    elif text.lower() in NONE_WORDS:
        p.action = "none"
        p.entry = {"none": True}
    else:
        p.action = "set"
        logo = parse_logo(text)
        if logo is None:
            p.errors.append(f"「标题 logo」看不出是哪张图：{clip(text, 80)}（要写 /xxxx.png，或粘贴 TMDB 图片网址；"
                            "该显示文字就填「无」，撤销就填「撤销」）")
        p.entry = {"logo": logo}
    if p.errors:
        return p

    if p.action == "revert":
        base.pop(p.lang, None)
    else:
        was = format_logo_entry(p.auto) if p.auto is not None else None
        entry = dict(p.entry)
        if was:
            entry["auto_was"] = was
        base[p.lang] = entry
        old = (p.existing or {}).get("logos", {}).get(p.lang) if p.existing and p.existing["tmdb"] == p.ref else None
        if old and old.get("none") == p.entry.get("none") and old.get("logo") == p.entry.get("logo"):
            p.noop = True
        elif p.auto is not None and bool(p.auto.get("none")) == bool(p.entry.get("none"))                 and p.auto.get("logo") == p.entry.get("logo"):
            p.warnings.append("和现在自动挑的一样：合并后这种语言就固定下来，自动挑的不再改它")
    p.override_logos = base
    first = next((line.strip() for line in p.reason.splitlines() if line.strip()), "")
    p.note = f"{clip(first, 80)} (修正请求 #{issue})" if first else f"修正请求 #{issue}"
    p.today = today
    return p


def finish(p):
    """核实之后 (set 时有了宽高比) 规整成要写的整条修正."""
    if not p.override_logos:
        p.override = None
        return
    try:
        p.override = normalize_logo_override({
            "tmdb": p.ref, "logos": p.override_logos, "title": p.name, "note": p.note, "updated": p.today,
        })
    except ValueError as e:
        p.errors.append(f"内容不合法：{e}")


def verify(p, fetch):
    """向 TMDB 核实这张图是这个条目的 logo, 取宽高比; 顺带列出这种语言的全部 logo 与条目对应的季."""
    kind, tid = p.ref.split("/")
    try:
        status, images = fetch(f"/{kind}/{tid}/images")
        logos = (images or {}).get("logos") or []
        p.candidates = [x for x in logos if (x.get("iso_639_1") or "").lower() == p.lang
                        and (x.get("file_path") or "").lower().endswith(".png")]
        if p.action == "set":
            hit = next((x for x in logos if x.get("file_path") == p.entry["logo"]), None)
            if hit is None:
                p.errors.append(f"这张图不在 {p.ref} 的 logo 里 (TMDB 上的 logo 才能用)")
                return
            if not (hit.get("aspect_ratio") or 0) > 0:
                p.errors.append("TMDB 上这张 logo 没有宽高比，没法用")
                return
            p.override_logos[p.lang]["aspect"] = round(float(hit["aspect_ratio"]), 3)
            image_lang = (hit.get("iso_639_1") or "").lower()
            if image_lang != p.lang:
                p.warnings.append(f"这张 logo 在 TMDB 上标的语言是 {image_lang or '无'}，这次用在{lang_name(p.lang)}下")
        if kind == "tv":
            numbers = sorted({int(n) for n in re.findall(r"S(\d+)E", (p.rec.get("auto") or {}).get("episodes") or "")})
            if numbers:
                _, detail = fetch(f"/tv/{tid}", {"language": "ja-JP"})
                by_number = {s.get("season_number"): s for s in (detail or {}).get("seasons") or []}
                p.seasons = [(n, (by_number.get(n) or {}).get("name") or "", (by_number.get(n) or {}).get("air_date") or "")
                             for n in numbers]
        p.verified = True
    except Exception as e:  # noqa: BLE001 — 网络或接口出错: set 拿不到宽高比只能报错, 其余交给维护者看
        if p.action == "set":
            p.errors.append(f"没能向 TMDB 核实 ({type(e).__name__})，稍后编辑一下这个 issue 会重新检查")
        else:
            p.warnings.append(f"没能向 TMDB 核实 ({type(e).__name__})")


def logo_md(entry, width=240):
    if entry is None:
        return "还没查"
    if entry.get("none") or not entry.get("logo"):
        return "不用 logo，显示文字标题"
    path = entry["logo"]
    return f'<img src="{IMG}w300{path}" width="{width}" alt="{path}"><br><code>{path}</code>'


def target_md(p):
    if p.action == "revert":
        auto = "回到自动挑的：" + (logo_md(p.auto) if p.auto is not None else "还没查")
        return "撤销人工修正", auto
    return "人工", logo_md(p.override_logos.get(p.lang) if p.override_logos else p.entry)


def render_pr(p, issue, author):
    lines = [f"标题 logo 修正请求 #{issue}，由 @{author} 提交。", ""]
    head = f"**{cell(p.name)}**"
    if p.row["cn"] and p.row["name"] and p.row["cn"] != p.row["name"]:
        head += f" ({cell(p.row['name'])})"
    lines.append(head + f" · Bangumi [{p.sid}](https://bgm.tv/subject/{p.sid}) · [核对页]({PAGE}#{p.sid})")
    lines.append(f"TMDB 条目 {ref_md(p.ref)} · 语言：{lang_name(p.lang)} ({p.lang})")
    if p.seasons:
        lines.append("这个条目对应 TMDB " + "、".join(
            f"第 {n} 季" + (f"「{cell(name)}」" if name else "") + (f" ({date})" if date else "") for n, name, date in p.seasons))
    now, source = current_entry(p)
    then_source, then = target_md(p)
    lines += ["", "| | 现在 | 改成 |", "|---|---|---|",
              f"| 来源 | {source or '—'} | {then_source} |",
              f"| logo | {logo_md(now) if source else '还没查'} | {then} |", ""]
    if p.candidates:
        chosen = (p.entry or {}).get("logo")
        shown = sorted(p.candidates, key=lambda x: (-(x.get("vote_average") or 0), -(x.get("vote_count") or 0)))[:GALLERY_MAX]
        lines.append(f"TMDB 上这个条目的{lang_name(p.lang)} logo 共 {len(p.candidates)} 张"
                     + (f" (只列前 {GALLERY_MAX} 张)" if len(p.candidates) > GALLERY_MAX else "") + "：")
        lines.append("")
        cells = []
        for x in shown:
            mark = " ✅" if x["file_path"] == chosen else ""
            cells.append(f'<img src="{IMG}w185{x["file_path"]}" width="160" alt="{x["file_path"]}"><br>'
                         f'<code>{x["file_path"]}</code>{mark}')
        for i in range(0, len(cells), 4):
            row = cells[i:i + 4]
            if i == 0:
                lines += ["| " + " | ".join(" " for _ in row) + " |", "|" + "---|" * len(row)]
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")
    lines.append("TMDB 核实：" + ("没有核实" if not p.verified else
                               ("这张图是这个条目的 logo" if p.action == "set" else "条目的 logo 已列出")))
    for w in p.warnings:
        lines.append(f"- 请留意：{cell(w)}")
    if p.reason.strip():
        reason = p.reason.replace("\r", "").replace("~~~", "～～～")
        if len(reason) > REASON_MAX:
            reason = reason[:REASON_MAX] + "\n…(太长，截断了；全文见 issue)"
        lines += ["", "<details><summary>提交者的说明</summary>", "", "~~~text", reason, "~~~", "", "</details>"]
    lines += ["", "合并后 apply-overrides 几分钟内把它并进对应表 (app 经 jsDelivr 取表, 一般一天内用上)；不采纳就直接关闭这个 PR，issue 会一起关掉。",
              "", f"Closes #{issue}", ""]
    return "\n".join(lines)


def render_summary(p):
    what = {"set": f"改用 {(p.entry or {}).get('logo')}", "none": "不用 logo，显示文字标题",
            "revert": "撤销人工修正，回到自动挑的"}[p.action]
    lines = [f"- {p.sid} 的{lang_name(p.lang)}标题 logo：{cell(what)}"]
    lines += [f"- 请留意：{cell(w)}" for w in p.warnings]
    lines += ["", "要调整就直接编辑这个 issue，PR 会跟着更新；不想改了就关掉这个 issue。"]
    return "\n".join(lines) + "\n"


def render_invalid(p):
    lines = ["这个标题 logo 修正请求没法处理：", ""] + [f"- {safe(e)}" for e in p.errors]
    lines += ["", "改好后直接编辑这个 issue，会自动重新检查。"]
    return "\n".join(lines) + "\n"


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
        print("不是标题 logo 修正请求表单, 跳过")
        return
    p = propose(fields, args.root, issue, args.today)
    if not p.errors and not p.noop:
        if token:
            verify(p, make_fetch(token))
        elif p.action == "set":
            p.errors.append("没有 TMDB 令牌，取不到这张 logo 的宽高比")
        else:
            p.warnings.append("没有 TMDB 令牌，没向 TMDB 核实")
    if not p.errors and not p.noop:
        finish(p)

    result = {"status": "invalid", "bgm_id": p.sid, "action": p.action}
    if p.errors:
        write("reply.md", render_invalid(p))
    else:
        issue_title = f"标题 logo {p.sid} {p.name}".strip()
        target = {"set": (p.entry or {}).get("logo") or "", "none": "用文字", "revert": "撤销"}[p.action]
        pr_title = f"{issue_title} ({p.lang}) → {target}"
        result.update({"file": p.file, "pr_title": clip(pr_title, 120), "issue_title": clip(issue_title, 120)})
        if p.noop:
            result["status"] = "noop"
            write("reply.md", f"这个条目的{lang_name(p.lang)}标题 logo 现在的人工修正已经是这样了，不需要改。\n")
        else:
            result["status"] = "ok"
            target_path = os.path.join(args.root, p.file)
            if p.override is None:
                if os.path.isfile(target_path):
                    os.remove(target_path)
            else:
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                with open(target_path, "w", encoding="utf-8", newline="\n") as f:
                    json.dump(p.override, f, ensure_ascii=False, indent=2)
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
