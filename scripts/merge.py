"""把匹配结果与人工修正并进各张表.

- 条目记录 (docs/data/s/<id // 2000>.json): 每个查过或修正过的条目一条, 含 Bangumi 概要、自动匹配结果
  (连同匹配时收到的 TMDB 信息与备选) 与人工修正. 这是核对页面的数据, 也是其他几张表的来源.
- 人工修正 (overrides/<bgm_id>.json) 优先于自动结果; 有修正的条目不再自动匹配, 结果也不会被覆盖.
- 对应表 (map/bgm-tmdb.tsv): 给客户端用, 收有对应的条目与人工确认没有对应的条目, 末列注明来源 (auto / manual).
- 页面索引 (docs/data/index.tsv): 全部动画条目一行, 列表与搜索用.
- 状态表 (state/state.tsv): 决定下一轮什么时候再查 (见 prepare.py).

结果里请求失败的条目 (ok=false) 不改自动结果: 旧结果照用, 下一轮重跑.
--apply-only: 只应用人工修正 (推送修正后立即生效用), 不需要导出与匹配结果.
"""
import argparse
import datetime
import glob
import json
import os
import sys

from common import load_overrides, load_state, save_state

SHARD = 2000
MAP_HEADER = ("# bangumi-tmdb-map v1. 列: bgm_id, backdrop (TMDB 条目), backdrop_path (图片路径), "
              "stills (分集数据出处: tv/<id> 整部 / tv/<id>/season/0 只取 S0 / movie/<id>), "
              "source (auto 自动 / manual 人工). 说明见 README.")
INDEX_COLUMNS = ["id", "name", "cn", "date", "platform", "pop", "status", "source", "ref", "title", "checked"]


def clean(value) -> str:
    return "" if value is None else str(value).replace("\t", " ").replace("\r", " ").replace("\n", " ")


def load_records(site):
    records = {}
    for path in glob.glob(os.path.join(site, "data", "s", "*.json")):
        with open(path, encoding="utf-8") as f:
            for sid, rec in json.load(f).items():
                records[int(sid)] = rec
    return records


def save_records(site, records):
    directory = os.path.join(site, "data", "s")
    os.makedirs(directory, exist_ok=True)
    shards = {}
    for sid, rec in records.items():
        shards.setdefault(sid // SHARD, {})[str(sid)] = rec
    for path in glob.glob(os.path.join(directory, "*.json")):
        if int(os.path.basename(path)[:-5]) not in shards:
            os.remove(path)
    for shard, content in shards.items():
        with open(os.path.join(directory, f"{shard}.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(dict(sorted(content.items(), key=lambda kv: int(kv[0]))), f, ensure_ascii=False,
                      separators=(",", ":"))
            f.write("\n")


def load_index(site):
    rows = {}
    path = os.path.join(site, "data", "index.tsv")
    if not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            rows[int(parts[0])] = dict(zip(INDEX_COLUMNS, parts))
    return rows


def effective(rec):
    """(status, source, backdrop, backdrop_path, stills_source, title): 人工修正优先.

    stills_source 是给客户端的分集数据出处 (一个 TMDB 路径或空), 见 runner 的 stillsSourceOf.
    """
    manual = rec.get("manual")
    if manual:
        if manual["none"]:
            return "miss", "manual", None, None, None, manual.get("title")
        stills = manual["stills"]
        return "hit", "manual", manual["backdrop"], manual["backdrop_path"], stills[0] if stills else None, \
            manual.get("title")
    auto = rec.get("auto")
    if not auto:
        return ("err" if rec.get("error") else ""), "", None, None, None, None
    title = (auto.get("tmdb") or {}).get("name")
    if auto["status"] == "hit":
        return "hit", "auto", auto.get("backdrop"), auto.get("backdropPath"), auto.get("stillsSource"), title
    return ("err" if rec.get("error") else "miss"), "auto", None, None, None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", help="prepare.py 的输出目录 (--apply-only 时不需要)")
    ap.add_argument("--results")
    ap.add_argument("--state", required=True)
    ap.add_argument("--map", required=True)
    ap.add_argument("--meta", required=True)
    ap.add_argument("--site", default="docs", help="核对页面目录")
    ap.add_argument("--overrides", default="overrides")
    ap.add_argument("--apply-only", action="store_true")
    ap.add_argument("--dump-name", default="")
    ap.add_argument("--matcher", default="", help="izuko-tv 的提交, 记进 meta")
    ap.add_argument("--summary-out", help="一行本轮摘要 (给提交说明用)")
    ap.add_argument("--today", default=datetime.datetime.now(datetime.timezone.utc).date().isoformat())
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    state = load_state(args.state)
    records = load_records(args.site)
    index = load_index(args.site)
    overrides, override_errors = load_overrides(args.overrides)
    stats = {"processed": 0, "hit": 0, "miss": 0, "err": 0, "tmdb_requests": 0}
    errors = []

    if args.apply_only:
        anime = None
        if not index:
            print("还没有页面数据 (第一轮全量匹配还没跑完), 修正留到那一轮一起应用")
            if args.summary_out:
                open(args.summary_out, "w", encoding="utf-8").close()
            return
    else:
        anime = {}
        with open(f"{args.work}/anime.jsonl", encoding="utf-8") as f:
            for line in f:
                a = json.loads(line)
                anime[a["id"]] = a
        hashes = {}
        with open(f"{args.work}/jobs.jsonl", encoding="utf-8") as f:
            for line in f:
                j = json.loads(line)
                hashes[j["id"]] = j["hash"]
        if args.results and os.path.isfile(args.results):
            with open(args.results, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    r = json.loads(line)
                    sid = r["id"]
                    if sid not in hashes or sid in overrides:
                        continue
                    stats["processed"] += 1
                    stats["tmdb_requests"] += r.get("requests", 0)
                    rec = records.setdefault(sid, {})
                    if not (r.get("ok") and r.get("unresolvedPaths", 0) == 0):
                        prev = state.get(sid)
                        state[sid] = {
                            "checked": args.today, "status": "err",
                            # 指纹沿用旧的: 输入变了而这次没跑成, 下一轮仍按"输入变了"排在前面
                            "hash": prev["hash"] if prev else "-",
                            "fails": (prev["fails"] if prev and prev["status"] == "err" else 0) + 1,
                        }
                        rec["error"] = {"date": args.today, "message": r.get("error") or
                                        f"{r.get('unresolvedPaths')} 张图找不到出处", "fails": state[sid]["fails"]}
                        stats["err"] += 1
                        if len(errors) < 20:
                            errors.append({"id": sid, "error": rec["error"]["message"]})
                        continue
                    hit = bool(r.get("backdrop")) or bool(r.get("stillsSource")) or bool(r.get("stills"))
                    state[sid] = {"checked": args.today, "status": "hit" if hit else "miss", "hash": hashes[sid],
                                  "fails": 0}
                    rec.pop("error", None)
                    rec["auto"] = {
                        "checked": args.today,
                        "status": "hit" if hit else "miss",
                        "backdrop": r.get("backdrop"),
                        "backdropPath": r.get("backdropPath") if r.get("backdrop") else None,
                        "stills": r.get("stills") or [],
                        "stillsSource": r.get("stillsSource"),
                        "stillCount": r.get("stillCount", 0),
                        "tmdb": r.get("tmdb"),
                        "hitQuery": r.get("hitQuery"),
                        "candidates": r.get("candidates") or [],
                        "requests": r.get("requests", 0),
                    }
                    stats["hit" if hit else "miss"] += 1

    # 人工修正: 生效的写进记录; 撤掉的 (记录里有、目录里没了) 清掉, 状态表标成要重跑
    for sid, manual in overrides.items():
        rec = records.setdefault(sid, {})
        rec["manual"] = manual
        prev = state.get(sid)
        state[sid] = {"checked": args.today, "status": "manual",
                      "hash": prev["hash"] if prev else "-", "fails": 0}
    for sid, rec in records.items():
        if "manual" in rec and sid not in overrides:
            del rec["manual"]
            if sid in state and state[sid]["status"] == "manual":
                state[sid]["status"] = "err"
                state[sid]["hash"] = "-"

    # Bangumi 概要与删掉/合并掉的条目
    if anime is not None:
        for sid in [s for s in records if s not in anime]:
            del records[sid]
        for sid in [s for s in state if s not in anime]:
            del state[sid]
        for sid, rec in records.items():
            rec["bgm"] = {k: v for k, v in anime[sid].items() if k != "id" and v not in (None, [], "")}
    for sid in [s for s, rec in records.items() if not rec]:
        del records[sid]

    # 对应表
    os.makedirs(os.path.dirname(args.map) or ".", exist_ok=True)
    map_rows = 0
    with open(args.map, "w", encoding="utf-8", newline="\n") as f:
        f.write(MAP_HEADER + "\n")
        for sid in sorted(records):
            status, source, backdrop, path, stills, _ = effective(records[sid])
            # 人工确认没有对应的也进表 (客户端据此不再去搜); 自动没匹配到的不进 (以后 TMDB 可能补上, 客户端自己搜)
            if status != "hit" and source != "manual":
                continue
            f.write("\t".join([str(sid), backdrop or "", path or "", stills or "", source]) + "\n")
            map_rows += 1

    # 页面索引: 全部动画条目
    if anime is not None:
        base = {sid: {"name": a["name"], "cn": a.get("nameCN", ""), "date": a.get("date", ""),
                      "platform": a.get("platform", ""), "pop": a.get("pop", 0)} for sid, a in anime.items()}
    else:
        base = {sid: {k: row.get(k, "") for k in ("name", "cn", "date", "platform", "pop")}
                for sid, row in index.items()}
    os.makedirs(os.path.join(args.site, "data"), exist_ok=True)
    with open(os.path.join(args.site, "data", "index.tsv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("# " + "\t".join(INDEX_COLUMNS) + "\n")
        for sid in sorted(base):
            rec = records.get(sid, {})
            status, source, backdrop, _, stills, title = effective(rec)
            ref = backdrop or (stills.split("/season/")[0] if stills else "")
            checked = ((rec.get("manual") or {}).get("updated") or (rec.get("auto") or {}).get("checked")
                       or (rec.get("error") or {}).get("date") or "")
            b = base[sid]
            f.write("\t".join(clean(v) for v in [sid, b["name"], b["cn"], b["date"], b["platform"], b["pop"],
                                                  status, source, ref, title, checked]) + "\n")
    save_records(args.site, records)
    save_state(args.state, state)

    old_meta = {}
    if os.path.isfile(args.meta):
        with open(args.meta, encoding="utf-8") as f:
            old_meta = json.load(f)
    counts = {"hit": 0, "miss": 0, "err": 0}
    for rec in records.values():
        status = effective(rec)[0]
        if status in counts:
            counts[status] += 1
    meta = {
        "generated": old_meta.get("generated", args.today) if args.apply_only else args.today,
        "overrides_applied": args.today,
        "bangumi_dump": args.dump_name or old_meta.get("bangumi_dump", ""),
        "matcher": args.matcher or old_meta.get("matcher", ""),
        "anime": len(base),
        "checked": counts["hit"] + counts["miss"],
        "entries": map_rows,
        "manual": len(overrides),
        "hit": counts["hit"], "miss": counts["miss"], "err": counts["err"],
        "last_run": old_meta.get("last_run", stats) if args.apply_only else stats,
    }
    for path in (args.meta, os.path.join(args.site, "meta.json")):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
            f.write("\n")
    print(json.dumps(meta, ensure_ascii=False))
    if args.summary_out:
        with open(args.summary_out, "w", encoding="utf-8") as f:
            if args.apply_only:
                f.write(f"apply {len(overrides)} manual override(s)")
            else:
                f.write(f"{stats['processed']} checked, {stats['hit']} hit, {stats['miss']} miss, {stats['err']} err")
    if errors:
        print("errors (first 20):")
        for e in errors:
            print(" ", json.dumps(e, ensure_ascii=False))
    if override_errors:
        # 每日那一轮只报告, 不能因为一个坏文件丢掉整轮的匹配结果; 推送修正时那一轮失败, 让人看到
        print(("::error::" if args.apply_only else "::warning::") + "人工修正有不合法的文件, 未生效:")
        for name, message in override_errors:
            print(f"  overrides/{name}: {message}")
        if args.apply_only:
            sys.exit(1)


if __name__ == "__main__":
    main()
