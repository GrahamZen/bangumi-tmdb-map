"""把匹配器的结果并进映射表与状态表.

映射表 (map/bgm-tmdb.tsv) 只收匹配到的条目; 状态表 (state/state.tsv) 记每个条目上次检查的日期与结果,
决定下一轮什么时候再查 (见 prepare.py 的 due_bucket).

结果里请求失败的条目 (ok=false) 不改映射表: 旧结果照用, 下一轮重跑.
"""
import argparse
import datetime
import json
import os
import sys

from common import load_state, save_state

MAP_HEADER = ("# bangumi-tmdb-map v1. 列: bgm_id, backdrop (TMDB 条目), backdrop_path (图片路径), "
              "stills (分集剧照出处, 逗号分隔). 说明见 README.")


def load_map(path):
    rows = {}
    if not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            rows[int(parts[0])] = parts[1:]
    return rows


def save_map(path, rows):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(MAP_HEADER + "\n")
        for sid in sorted(rows):
            f.write("\t".join([str(sid), *rows[sid]]) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--results", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--map", required=True)
    ap.add_argument("--meta", required=True)
    ap.add_argument("--dump-name", default="")
    ap.add_argument("--matcher", default="", help="izuko-tv 的提交, 记进 meta")
    ap.add_argument("--summary-out", help="一行本轮摘要 (给提交说明用)")
    ap.add_argument("--today", default=datetime.datetime.now(datetime.timezone.utc).date().isoformat())
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    anime_ids = set()
    with open(f"{args.work}/anime.jsonl", encoding="utf-8") as f:
        for line in f:
            anime_ids.add(json.loads(line)["id"])
    hashes = {}
    with open(f"{args.work}/jobs.jsonl", encoding="utf-8") as f:
        for line in f:
            j = json.loads(line)
            hashes[j["id"]] = j["hash"]

    state = load_state(args.state)
    rows = load_map(args.map)
    stats = {"processed": 0, "hit": 0, "miss": 0, "err": 0, "tmdb_requests": 0}
    errors = []
    if os.path.isfile(args.results):
        with open(args.results, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                sid = r["id"]
                if sid not in hashes:
                    continue
                stats["processed"] += 1
                stats["tmdb_requests"] += r.get("requests", 0)
                ok = r.get("ok", False) and r.get("unresolvedPaths", 0) == 0
                if not ok:
                    prev = state.get(sid)
                    state[sid] = {
                        "checked": args.today, "status": "err",
                        # 指纹沿用旧的: 输入变了而这次没跑成, 下一轮仍按"输入变了"排在前面
                        "hash": prev["hash"] if prev else "-",
                        "fails": (prev["fails"] if prev and prev["status"] == "err" else 0) + 1,
                    }
                    stats["err"] += 1
                    if len(errors) < 20:
                        errors.append({"id": sid, "error": r.get("error"), "unresolved": r.get("unresolvedPaths", 0)})
                    continue
                backdrop = r.get("backdrop") or ""
                stills = r.get("stills") or []
                hit = bool(backdrop) or bool(stills)
                state[sid] = {"checked": args.today, "status": "hit" if hit else "miss", "hash": hashes[sid], "fails": 0}
                if hit:
                    rows[sid] = [backdrop, (r.get("backdropPath") or "") if backdrop else "", ",".join(stills)]
                    stats["hit"] += 1
                else:
                    rows.pop(sid, None)
                    stats["miss"] += 1

    # Bangumi 上删掉/合并掉的条目
    for sid in [s for s in state if s not in anime_ids]:
        del state[sid]
    for sid in [s for s in rows if s not in anime_ids]:
        del rows[sid]

    save_state(args.state, state)
    save_map(args.map, rows)
    checked = sum(1 for r in state.values() if r["status"] != "err")
    meta = {
        "generated": args.today,
        "bangumi_dump": args.dump_name,
        "matcher": args.matcher,
        "anime": len(anime_ids),
        "checked": checked,
        "entries": len(rows),
        "with_backdrop": sum(1 for r in rows.values() if r[0]),
        "with_stills": sum(1 for r in rows.values() if r[2]),
        "last_run": stats,
    }
    with open(args.meta, "w", encoding="utf-8", newline="\n") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(json.dumps(meta, ensure_ascii=False))
    if args.summary_out:
        with open(args.summary_out, "w", encoding="utf-8") as f:
            f.write(f"{stats['processed']} checked, {stats['hit']} hit, {stats['miss']} miss, {stats['err']} err")
    if errors:
        print("errors (first 20):")
        for e in errors:
            print(" ", json.dumps(e, ensure_ascii=False))


if __name__ == "__main__":
    main()
