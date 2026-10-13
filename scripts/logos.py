"""按 TMDB 条目自动挑标题 logo, 存进 state/logos.tsv (merge.py 据此写对应表的 logo 列).

对应表里的背景图条目 (去重) 逐个查: 详情取原语言, `/images` 只取日 / 中 / 英与原语言的 logo, 每种语言挑一张
(只要 png 且宽高比有效的; 中文先简体 (CN / SG) 后繁体; 同一档里评分最高的, 再看票数). 没有的记 `-`.

TMDB 不标 logo 属于哪一季, 多季的剧自动挑的可能是别的季的 —— 这类错由人工修正 (logo-overrides/) 纠正, 本脚本不管.

查详情时顺带取条目的翻译 (`append_to_response=translations`, 不多发请求), 港台中文名存进 state/names.tsv (merge.py 写进对应表的 names 列);
还没取过译名的条目也算到期.

到期 (logo 是社区陆续上传的, 新番开播后几周里常常一种语言一种语言地补上):
  - 没查过的;
  - TMDB 上最近两天有改动的 (`/tv/changes`、`/movie/changes`, 与表求交集; 今天查过的不再查) —— 新传的 logo 第二天就进表;
  - 每种语言都查到了 logo 的 60 天后; 缺某种语言 (含一种也没有) 的 14 天后 —— 兜底变更列表漏掉的 (哪天没跑、合集没有变更列表).
条目在 TMDB 上已经没了 (详情 404, 多是开播前建的重复条目被删或并进了别的条目) 记成 gone: 对应表 logo 列写成各语言都没有 (app 不再去取),
prepare.py 把背景图是它的条目排进当天的重新匹配; 14 天后还在表里就再查一次.
请求失败的条目不改, 下一轮再查. 按时间预算跑, 剩下的下一轮接着.

用法: python3 scripts/logos.py --map map/bgm-tmdb.tsv --logos state/logos.tsv [--names state/names.tsv] [--minutes 40] [--summary-out f]
环境变量: TMDB_API_TOKEN
"""
import argparse
import concurrent.futures
import datetime
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from common import LOGO_LANGS, load_logos, load_names, names_from_translations, save_logos, save_names

API = "https://api.tmdb.org/3"
RECHECK_COMPLETE_DAYS = 60
RECHECK_MISSING_DAYS = 14
# 变更列表看最近几天 (含今天): 每天跑一次, 留一天余量
CHANGES_DAYS = 2
# 变更列表最多翻几页 (一页 100 条; 两天约剧集 50 页、电影 150 页)
CHANGES_MAX_PAGES = 500
# 中文 logo 的地区优先级: 简体在前
ZH_REGIONS = ("CN", "SG", "TW", "HK", "MO")


class RateLimiter:
    """全局每秒至多 rps 个请求 (TMDB 按 IP 限约 50/s)."""

    def __init__(self, rps):
        self.interval = 1.0 / rps
        self.lock = threading.Lock()
        self.next = time.monotonic()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            at = max(now, self.next)
            self.next = at + self.interval
        if at > now:
            time.sleep(at - now)


def make_fetch(token, limiter):
    def fetch(path, params=None):
        url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/json",
            "User-Agent": "bangumi-tmdb-map/logos (+https://github.com/GrahamZen/bangumi-tmdb-map)",
        })
        for attempt in range(3):
            limiter.wait()
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None
                if e.code not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise
            except (urllib.error.URLError, TimeoutError):
                if attempt == 2:
                    raise
            time.sleep(2 * (attempt + 1))
    return fetch


def pick(logos, lang):
    """一种语言挑一张: png、宽高比有效、语言对得上; 中文按地区分档. 没有返回 None."""
    usable = [x for x in logos if (x.get("file_path") or "").lower().endswith(".png") and (x.get("aspect_ratio") or 0) > 0
              and (x.get("iso_639_1") or "").lower() == lang]
    if not usable:
        return None

    def tier(x):
        if lang != "zh":
            return 0
        region = (x.get("iso_3166_1") or "").upper()
        return ZH_REGIONS.index(region) if region in ZH_REGIONS else len(ZH_REGIONS)

    best = min(usable, key=lambda x: (tier(x), -(x.get("vote_average") or 0), -(x.get("vote_count") or 0)))
    return {"logo": best["file_path"], "aspect": round(float(best["aspect_ratio"]), 3)}


def check(ref, fetch):
    """一个条目的 (原语言, {语言: logo}, {地区: 港台译名}); 条目在 TMDB 上没了返回 None."""
    kind, tid = ref.split("/")
    detail = fetch(f"/{kind}/{tid}", {"append_to_response": "translations"})
    if detail is None:
        return None
    names = names_from_translations((detail.get("translations") or {}).get("translations"))
    # 合集没有原语言字段; 动画条目绝大多数是日文
    original = (detail.get("original_language") or "").lower() or "ja"
    langs = sorted(set(LOGO_LANGS) | {original})
    images = fetch(f"/{kind}/{tid}/images", {"include_image_language": ",".join(langs)}) or {}
    logos = images.get("logos") or []
    return original, {lang: pick(logos, lang) or {"none": True} for lang in langs}, names


def due(entry, today, changed=False):
    """[changed]: 这个条目最近在 TMDB 上有改动 (见 changed_refs)."""
    if entry is None:
        return True
    try:
        checked = datetime.date.fromisoformat(entry["checked"])
    except ValueError:
        return True
    if changed and checked < today:
        return True
    if entry.get("gone"):
        return (today - checked).days >= RECHECK_MISSING_DAYS
    # 原语言与各语言都空 = 没记 gone 之前把 404 存成了空结果, 重查一次改记成 gone
    if not entry.get("original") and not entry["logos"]:
        return True
    complete = all(not e.get("none") for e in entry["logos"].values())
    return (today - checked).days >= (RECHECK_COMPLETE_DAYS if complete else RECHECK_MISSING_DAYS)


def changed_refs(fetch, today):
    """最近 CHANGES_DAYS 天在 TMDB 上有改动的剧集与电影 (`tv/<id>`、`movie/<id>`). 取不全不要紧 (到期规则兜底), 出错就用取到的部分."""
    start = (today - datetime.timedelta(days=CHANGES_DAYS - 1)).isoformat()
    refs = set()
    for kind in ("tv", "movie"):
        page, pages = 1, 1
        while page <= min(pages, CHANGES_MAX_PAGES):
            try:
                data = fetch(f"/{kind}/changes", {"start_date": start, "end_date": today.isoformat(), "page": page}) or {}
            except Exception as e:  # noqa: BLE001
                print(f"变更列表 {kind} 第 {page} 页取不到: {type(e).__name__}: {e}")
                break
            pages = data.get("total_pages") or 0
            refs.update(f"{kind}/{x['id']}" for x in data.get("results") or [] if x.get("id") is not None)
            page += 1
    return refs


def map_refs(path):
    """对应表里出现过的背景图条目."""
    refs = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            cols = line.rstrip("\n").split("\t")
            if len(cols) > 1 and cols[1]:
                refs.add(cols[1])
    return refs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", required=True)
    ap.add_argument("--logos", required=True)
    ap.add_argument("--names", help="港台译名存这里 (state/names.tsv); 不给就不取")
    ap.add_argument("--minutes", type=float, default=40)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--rps", type=float, default=30)
    ap.add_argument("--summary-out")
    ap.add_argument("--today", default=datetime.datetime.now(datetime.timezone.utc).date().isoformat())
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    token = os.environ.get("TMDB_API_TOKEN", "")
    if not token:
        sys.exit("缺少 TMDB_API_TOKEN")

    today = datetime.date.fromisoformat(args.today)
    logos = load_logos(args.logos)
    names = load_names(args.names) if args.names else None
    refs = map_refs(args.map)
    # 表里没有了的条目不再留
    for ref in [r for r in logos if r not in refs]:
        del logos[ref]
    for ref in [r for r in (names or {}) if r not in refs]:
        del names[ref]
    fetch = make_fetch(token, RateLimiter(args.rps))
    changed = changed_refs(fetch, today) & refs
    # 从没查过的排前面, 再是最近有改动的, 其余按上次查的日期
    todo = sorted((r for r in refs if due(logos.get(r), today, r in changed) or (names is not None and r not in names)),
                  key=lambda r: (r in logos, r not in changed, logos[r]["checked"] if r in logos else "", r))
    print(f"条目 {len(refs)}, 最近有改动 {len(changed)}, 到期 {len(todo)}")

    deadline = time.monotonic() + args.minutes * 60
    stats = {"checked": 0, "found": 0, "none": 0, "err": 0, "named": 0, "gone": 0}
    gone = []
    errors = []
    lock = threading.Lock()

    def work(ref):
        if time.monotonic() > deadline:
            return
        try:
            result = check(ref, fetch)
        except Exception as e:  # noqa: BLE001 — 失败的这一轮不改, 下一轮再查
            with lock:
                stats["err"] += 1
                if len(errors) < 20:
                    errors.append(f"{ref}: {type(e).__name__}: {e}")
            return
        if result is None:
            with lock:
                logos[ref] = {"checked": args.today, "original": "", "logos": {}, "gone": True}
                if names is not None:
                    names[ref] = {"checked": args.today, "names": {}}
                stats["checked"] += 1
                stats["gone"] += 1
                gone.append(ref)
            return
        original, entries, ref_names = result
        with lock:
            logos[ref] = {"checked": args.today, "original": original, "logos": entries}
            if names is not None:
                names[ref] = {"checked": args.today, "names": ref_names}
                stats["named"] += 1 if ref_names else 0
            stats["checked"] += 1
            stats["found" if any(not e.get("none") for e in entries.values()) else "none"] += 1

    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(work, todo))

    save_logos(args.logos, logos)
    if names is not None:
        save_names(args.names, names)
    left = len(todo) - stats["checked"] - stats["err"]
    summary = f"{stats['checked']} checked, {stats['found']} with logo, {stats['none']} none, {stats['named']} with tw/hk names, {stats['err']} err" + \
              (f", {stats['gone']} gone" if stats["gone"] else "") + (f", {left} left" if left else "")
    print(summary)
    for ref in sorted(gone):
        print(f"   TMDB 上已经没了: {ref} (用到它的条目今天重新匹配)")
    for e in errors:
        print("  ", e)
    if args.summary_out:
        with open(args.summary_out, "w", encoding="utf-8") as f:
            f.write(summary)


if __name__ == "__main__":
    main()
