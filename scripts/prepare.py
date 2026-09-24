"""从 Bangumi 官方数据导出挑出本轮要匹配的动画条目, 生成匹配器 (runner/BgmTmdbMapRunner.kt) 的输入.

输出到 --work 目录:
  anime.jsonl      全部动画条目的概要 (合成关联接口时给出关联条目的名字; 核对页面展示的 Bangumi 信息)
  relations.jsonl  动画条目之间的关联, 每条目一行, 按 (order, id) 排好 (与接口顺序一致)
  jobs.jsonl       本轮任务, 按优先级排序; 每行自带匹配所需的全部输入与输入指纹
  plan.json        本轮各类任务的数量
"""
import argparse
import datetime
import hashlib
import io
import json
import os
import sys
import zipfile

from common import ANIME, PLATFORMS, load_overrides, load_state, parse_infobox, popularity

EPISODE_CAP = 3000  # 与 app 取分集的上限一致 (EpisodeService.MAX_EPISODES)
HASH_KEYS = {"别名", "上映年度", "上映日期", "其他上映日期", "其他上映年度", "放送开始"}


def open_member(zf, name):
    return io.TextIOWrapper(zf.open(name), encoding="utf-8")


def input_hash(subject, infobox, episodes, today):
    """影响匹配结果的 Bangumi 侧输入. 变了就重跑 (连载中的番每播一集, 最新已播日期都会变)."""
    relevant = [item for item in infobox if item["key"] in HASH_KEYS]
    aired = [e["airdate"] for e in episodes if e["airdate"] and e["airdate"] <= today]
    payload = json.dumps(
        [subject["name"], subject["name_cn"], subject.get("date") or "", relevant,
         [e["name"] for e in episodes], max(aired) if aired else ""],
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def days_between(a, b):
    return (datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days


def due_bucket(state_row, new_hash, subject_date, today, subject_id):
    """返回优先级 (越小越先), 不到期返回 None."""
    if state_row is None:
        return 0
    # status 为 manual 却走到这里 = 人工修正刚被撤掉, 按输入变了重跑
    if state_row["hash"] != new_hash or state_row["status"] == "manual":
        return 1
    since = days_between(state_row["checked"], today)
    status = state_row["status"]
    if status == "err":
        return 2 if state_row["fails"] < 3 or since >= 28 else None
    if status == "miss":
        recent = not subject_date or days_between(subject_date, today) <= 548
        return 3 if since >= (7 if recent else 91) else None
    # hit: TMDB 条款要求缓存不超过 6 个月; 按 id 错开, 免得同一周集中重验
    return 4 if since >= 120 + subject_id % 45 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", required=True, help="Bangumi Archive 的 zip")
    ap.add_argument("--state", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--overrides", default="overrides", help="人工修正目录; 有修正的条目不跑")
    ap.add_argument("--today", default=datetime.datetime.now(datetime.timezone.utc).date().isoformat())
    ap.add_argument("--max-jobs", type=int, default=20000)
    ap.add_argument("--ids", help="只跑这些条目 (逗号分隔), 忽略到期规则; 调试用")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    only = {int(x) for x in args.ids.split(",")} if args.ids else None
    os.makedirs(args.work, exist_ok=True)

    state = load_state(args.state)
    manual = set(load_overrides(args.overrides)[0])
    subjects = {}
    with zipfile.ZipFile(args.dump) as zf:
        with open_member(zf, "subject.jsonlines") as f:
            for line in f:
                s = json.loads(line)
                if s["type"] == ANIME:
                    subjects[s["id"]] = s
        relations = {}
        with open_member(zf, "subject-relations.jsonlines") as f:
            for line in f:
                r = json.loads(line)
                if r["subject_id"] in subjects and r["related_subject_id"] in subjects:
                    relations.setdefault(r["subject_id"], []).append(
                        (r["order"], r["related_subject_id"], r["relation_type"]))
        episodes = {}
        with open_member(zf, "episode.jsonlines") as f:
            for line in f:
                e = json.loads(line)
                if e["subject_id"] in subjects:
                    episodes.setdefault(e["subject_id"], []).append(
                        (e.get("type", 0), float(e.get("sort") or 0), e["id"], e.get("name") or "", e.get("airdate") or ""))

    candidates = []
    for sid, s in subjects.items():
        infobox = parse_infobox(s.get("infobox") or "")
        eps = [{"name": name, "airdate": airdate}
               for _, _, _, name, airdate in sorted(episodes.get(sid, []))][:EPISODE_CAP]
        h = input_hash(s, infobox, eps, args.today)
        if sid in manual:
            continue
        if only is not None:
            if sid not in only:
                continue
            bucket = 0
        else:
            bucket = due_bucket(state.get(sid), h, s.get("date") or "", args.today, sid)
            if bucket is None:
                continue
        candidates.append((bucket, -popularity(s), sid, infobox, eps, h))
    candidates.sort(key=lambda c: (c[0], c[1], c[2]))
    candidates = candidates[:args.max_jobs]

    with open(f"{args.work}/anime.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for sid, s in subjects.items():
            infobox = parse_infobox(s.get("infobox") or "")
            aliases = [v["v"] for item in infobox if item["key"] == "别名" for v in item["values"] if v["v"].strip()]
            f.write(json.dumps({
                "id": sid, "name": s["name"], "nameCN": s["name_cn"], "date": s.get("date") or "",
                "eps": sum(1 for e in episodes.get(sid, []) if e[0] == 0),
                "nsfw": bool(s.get("nsfw")), "pop": popularity(s),
                "platform": PLATFORMS.get(s.get("platform"), str(s.get("platform"))),
                "aliases": aliases[:10],
                "score": s.get("score") or None, "rank": s.get("rank") or None,
                "tags": [t["name"] for t in (s.get("tags") or [])[:8]],
                "meta": s.get("meta_tags") or [],
            }, ensure_ascii=False) + "\n")
    with open(f"{args.work}/relations.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for sid, rows in relations.items():
            rows.sort()
            f.write(json.dumps({"id": sid, "rel": [[rid, rtype, order] for order, rid, rtype in rows]}) + "\n")
    buckets = {}
    with open(f"{args.work}/jobs.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for bucket, _, sid, infobox, eps, h in candidates:
            s = subjects[sid]
            buckets[bucket] = buckets.get(bucket, 0) + 1
            f.write(json.dumps({"id": sid, "name": s["name"], "nameCN": s["name_cn"], "date": s.get("date") or "",
                                "nsfw": bool(s.get("nsfw")), "infobox": infobox, "episodes": eps, "hash": h},
                               ensure_ascii=False) + "\n")
    names = {0: "new", 1: "changed", 2: "retry_error", 3: "retry_miss", 4: "reverify"}
    plan = {"today": args.today, "anime": len(subjects), "jobs": len(candidates),
            "by_reason": {names[b]: n for b, n in sorted(buckets.items())}}
    with open(f"{args.work}/plan.json", "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    print(json.dumps(plan, ensure_ascii=False))


if __name__ == "__main__":
    main()
