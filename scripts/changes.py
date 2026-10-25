"""对应表的更新记录 (docs/data/changes/): 核对页「更新记录」的数据.

merge.py 每次重写对应表 (每日匹配 update / 应用人工修正 apply / logos 之后) 都比较新旧两版, 有变化就记一轮:
  {"date", "kind", "matcher"?, "changes": [条目变化], "images": [只换了背景图的 bgm id]}
条目变化 = 有没有这一行、TMDB 条目 (背景图条目 / 分集数据出处)、来源 (自动 / 人工)、逐集对位 任一变了, 带上 Bangumi 名字与
前后两版的 TMDB 名字 (人工修正带上它的说明, 说明里写着修正请求的编号). 背景图路径单独变了的多是 logos 按 TMDB 排名换图, 只记 id.

按月一个文件 (<年-月>.json, 一行一轮, 新的在前), 页面先只取最近一个月; index.json 列出有记录的月份 (新的在前) 与各月轮数、条目变化数.
"""
import json
import os

KINDS = ("update", "apply", "logos")


def parse_map(text):
    """对应表正文 → {bgm_id: (backdrop, backdrop_path, stills, source, episodes)}."""
    rows = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        cols = line.split("\t") + [""] * 6
        rows[int(cols[0])] = tuple(cols[1:6])
    return rows


def read_map(path):
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return parse_map(f.read())


def diff(old_rows, new_rows):
    """(条目变了的 id, 只换了背景图的 id), 都按 id 排序."""
    entity, images = [], []
    for sid in sorted(set(old_rows) | set(new_rows)):
        a, b = old_rows.get(sid), new_rows.get(sid)
        if a == b:
            continue
        if a is None or b is None or (a[0], a[2], a[3], a[4]) != (b[0], b[2], b[3], b[4]):
            entity.append(sid)
        else:
            images.append(sid)
    return entity, images


def record_title(rec):
    """条目记录里生效那一方的 TMDB 名字 (人工修正优先)."""
    if not rec:
        return ""
    manual = rec.get("manual")
    if manual:
        return manual.get("title") or ""
    return ((rec.get("auto") or {}).get("tmdb") or {}).get("name") or ""


def side(row, rec, with_episodes):
    if row is None:
        return None
    ref, _, stills, source, episodes = row
    out = {"ref": ref, "stills": stills, "source": source, "title": record_title(rec)}
    if source == "manual" and (rec or {}).get("manual", {}).get("note"):
        out["note"] = rec["manual"]["note"]
    if with_episodes:
        out["episodes"] = episodes
    return out


def make_run(date, kind, matcher, old_rows, new_rows, old_record, new_record):
    """一轮的记录; 没有变化返回 None. old_record / new_record: bgm id → 前后两版的条目记录 (没有返回 None)."""
    entity, images = diff(old_rows, new_rows)
    if not entity and not images:
        return None
    changes = []
    for sid in entity:
        a, b = old_rows.get(sid), new_rows.get(sid)
        # 逐集对位只在条目没换、只有它变了时才记 (换了条目时对位跟着变, 不用看)
        with_episodes = a is not None and b is not None and (a[0], a[2]) == (b[0], b[2]) and a[4] != b[4]
        before, after = old_record(sid), new_record(sid)
        bgm = (after or before or {}).get("bgm") or {}
        changes.append({
            "id": sid, "name": bgm.get("name", ""), "cn": bgm.get("nameCN", ""),
            "old": side(a, before, with_episodes), "new": side(b, after, with_episodes),
        })
    run = {"date": date, "kind": kind}
    if matcher:
        run["matcher"] = matcher
    run["changes"] = changes
    run["images"] = images
    return run


def directory(site):
    return os.path.join(site, "data", "changes")


def load_month(site, month):
    path = os.path.join(directory(site), f"{month}.json")
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def record(site, run):
    """把这一轮加到它那个月文件的最前面, 再按目录里全部月份重写 index.json."""
    d = directory(site)
    os.makedirs(d, exist_ok=True)
    month = run["date"][:7]
    runs = [run] + load_month(site, month)
    with open(os.path.join(d, f"{month}.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write("[\n" + ",\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in runs) + "\n]\n")
    index = []
    for name in sorted(os.listdir(d), reverse=True):
        if name.endswith(".json") and name != "index.json":
            runs = load_month(site, name[:-5])
            index.append({"month": name[:-5], "runs": len(runs), "changes": sum(len(r["changes"]) for r in runs)})
    with open(os.path.join(d, "index.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
        f.write("\n")
