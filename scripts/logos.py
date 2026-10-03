"""按 TMDB 条目自动挑标题 logo, 存进 state/logos.tsv (merge.py 据此写对应表的 logo 列).

对应表里的背景图条目 (去重) 逐个查: 详情取原语言, `/images` 只取日 / 中 / 英与原语言的 logo, 每种语言挑一张
(只要 png 且宽高比有效的; 中文先简体 (CN / SG) 后繁体; 同一档里评分最高的, 再看票数). 没有的记 `-`.

TMDB 不标 logo 属于哪一季, 多季的剧自动挑的可能是别的季的 —— 这类错由人工修正 (logo-overrides/) 纠正, 本脚本不管.

到期: 没查过的; 查到过 logo 的 60 天后; 一种也没有的 14 天后 (logo 是社区陆续上传的).
请求失败的条目不改, 下一轮再查. 按时间预算跑, 剩下的下一轮接着.

用法: python3 scripts/logos.py --map map/bgm-tmdb.tsv --logos state/logos.tsv [--minutes 40] [--summary-out f]
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

from common import LOGO_LANGS, load_logos, save_logos

API = "https://api.tmdb.org/3"
RECHECK_HIT_DAYS = 60
RECHECK_MISS_DAYS = 14
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
    """一个条目的 (原语言, {语言: logo}); 条目在 TMDB 上没了返回 ("", {})."""
    kind, tid = ref.split("/")
    detail = fetch(f"/{kind}/{tid}")
    if detail is None:
        return "", {}
    # 合集没有原语言字段; 动画条目绝大多数是日文
    original = (detail.get("original_language") or "").lower() or "ja"
    langs = sorted(set(LOGO_LANGS) | {original})
    images = fetch(f"/{kind}/{tid}/images", {"include_image_language": ",".join(langs)}) or {}
    logos = images.get("logos") or []
    return original, {lang: pick(logos, lang) or {"none": True} for lang in langs}


def due(entry, today):
    if entry is None:
        return True
    try:
        checked = datetime.date.fromisoformat(entry["checked"])
    except ValueError:
        return True
    found = any(not e.get("none") for e in entry["logos"].values())
    return (today - checked).days >= (RECHECK_HIT_DAYS if found else RECHECK_MISS_DAYS)


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
    refs = map_refs(args.map)
    # 表里没有了的条目不再留
    for ref in [r for r in logos if r not in refs]:
        del logos[ref]
    # 从没查过的排前面, 其余按上次查的日期
    todo = sorted((r for r in refs if due(logos.get(r), today)),
                  key=lambda r: (r in logos, logos[r]["checked"] if r in logos else "", r))
    print(f"条目 {len(refs)}, 到期 {len(todo)}")

    fetch = make_fetch(token, RateLimiter(args.rps))
    deadline = time.monotonic() + args.minutes * 60
    stats = {"checked": 0, "found": 0, "none": 0, "err": 0}
    errors = []
    lock = threading.Lock()

    def work(ref):
        if time.monotonic() > deadline:
            return
        try:
            original, entries = check(ref, fetch)
        except Exception as e:  # noqa: BLE001 — 失败的这一轮不改, 下一轮再查
            with lock:
                stats["err"] += 1
                if len(errors) < 20:
                    errors.append(f"{ref}: {type(e).__name__}: {e}")
            return
        with lock:
            logos[ref] = {"checked": args.today, "original": original, "logos": entries}
            stats["checked"] += 1
            stats["found" if any(not e.get("none") for e in entries.values()) else "none"] += 1

    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(work, todo))

    save_logos(args.logos, logos)
    left = len(todo) - stats["checked"] - stats["err"]
    summary = f"{stats['checked']} checked, {stats['found']} with logo, {stats['none']} none, {stats['err']} err" + \
              (f", {left} left" if left else "")
    print(summary)
    for e in errors:
        print("  ", e)
    if args.summary_out:
        with open(args.summary_out, "w", encoding="utf-8") as f:
            f.write(summary)


if __name__ == "__main__":
    main()
