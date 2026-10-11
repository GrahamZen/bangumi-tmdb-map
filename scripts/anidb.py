"""从 bangumi-data 取「Bangumi 条目 → AniDB 条目」, 存进 state/anidb.tsv (merge.py 据此写对应表的 anidb 列).

bangumi-data (https://github.com/bangumi-data/bangumi-data, CC BY 4.0) 每个番组的 sites 里同时登了 bangumi 与 anidb 的编号.
弹弹 play 的作品编号就是 AniDB 的编号, 客户端拿它直接取那部作品的分集, 不用按名字搜 —— Bangumi 拆成几段的季
(如「第四季 夺还篇」) 在弹弹 play 合在前半那部里、Bangumi 映射只挂在前半上, bangumi-data 对后半也登了合并后的那部.

取 npm 上 latest 版本的 dist/data.json (按 registry 给的 sha512 校验). 同一个 Bangumi 条目登了几个不同的 AniDB 条目时不写 (认不准).
下载或校验失败时不改 state/anidb.tsv, 对应表沿用上次的结果.

用法: python3 scripts/anidb.py --out state/anidb.tsv [--summary-out f]
"""
import argparse
import base64
import hashlib
import io
import json
import sys
import tarfile
import urllib.request

from common import save_anidb

REGISTRY = "https://registry.npmjs.org/bangumi-data/latest"


def fetch(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "GrahamZen/bangumi-tmdb-map"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def load_bangumi_data():
    """(版本, data.json 的内容)."""
    meta = json.loads(fetch(REGISTRY))
    tarball = fetch(meta["dist"]["tarball"])
    algo, _, digest = meta["dist"]["integrity"].partition("-")
    if algo != "sha512" or base64.b64decode(digest) != hashlib.sha512(tarball).digest():
        raise RuntimeError(f"bangumi-data {meta['version']} 的压缩包校验不对")
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
        data = json.load(tar.extractfile("package/dist/data.json"))
    return meta["version"], data


def build(data):
    """({bgm_id: anidb_id}, 一对多而没写的条目数)."""
    seen = {}
    for item in data["items"]:
        sites = {s.get("site"): str(s.get("id") or "") for s in item.get("sites", [])}
        bgm, anidb = sites.get("bangumi", ""), sites.get("anidb", "")
        if bgm.isdigit() and anidb.isdigit():
            seen.setdefault(int(bgm), set()).add(int(anidb))
    mapping = {bgm: next(iter(ids)) for bgm, ids in seen.items() if len(ids) == 1}
    return mapping, len(seen) - len(mapping)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--summary-out")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        version, data = load_bangumi_data()
    except Exception as e:  # noqa: BLE001 拿不到就沿用上次的表, 不让整轮失败
        print(f"::warning::取 bangumi-data 失败, anidb 列沿用上次的: {e}")
        return
    mapping, ambiguous = build(data)
    save_anidb(args.out, mapping, version)
    summary = f"bangumi-data {version}: {len(mapping)} anidb, {ambiguous} ambiguous"
    print(summary)
    if args.summary_out:
        with open(args.summary_out, "w", encoding="utf-8") as f:
            f.write(summary)


if __name__ == "__main__":
    main()
