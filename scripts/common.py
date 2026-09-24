"""prepare.py / merge.py 共用: 信息框解析、状态表与人工修正的读写."""
import json
import os
import re

ANIME = 2

STATE_HEADER = "# bgm_id\tchecked\tstatus\thash\tfails"

# bangumi/common subject_platforms.yml 的动画平台
PLATFORMS = {0: "其他", 1: "TV", 2: "OVA", 3: "剧场版", 4: "短片", 5: "WEB", 2006: "动态漫画"}

REF_RE = re.compile(r"^(tv|movie|collection)/\d+$")
STILL_REF_RE = re.compile(r"^(tv/\d+/season/\d+|movie/\d+|collection/\d+)$")
PATH_RE = re.compile(r"^/[A-Za-z0-9_\-]+\.(jpg|jpeg|png|webp)$")


def parse_infobox(wiki: str) -> list:
    """Bangumi wiki 信息框 → p1 接口的 infobox 结构 ([{key, values: [{k?, v}]}]).

    与 next.bgm.tv/p1/subjects/{id} 的返回逐项一致 (空值字段与空数组也保留, app 判「剧场版」要看字段在不在).
    """
    lines = wiki.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    cur = None  # 正在收集的数组字段
    for raw in lines:
        line = raw.strip()
        if cur is not None:
            if line == "}":
                out.append(cur)
                cur = None
                continue
            if line.startswith("[") and line.endswith("]"):
                inner = line[1:-1]
                if "|" in inner:
                    k, v = inner.split("|", 1)
                    cur["values"].append({"k": k.strip(), "v": v.strip()})
                else:
                    cur["values"].append({"v": inner.strip()})
            continue
        if not line.startswith("|"):
            continue
        body = line[1:]
        if "=" not in body:
            continue
        key, value = body.split("=", 1)
        key, value = key.strip(), value.strip()
        if value == "{":
            cur = {"key": key, "values": []}
        else:
            out.append({"key": key, "values": [{"v": value}]})
    if cur is not None:
        out.append(cur)
    return out


def popularity(subject) -> int:
    """收藏人数合计 (想看/看过/在看/搁置/抛弃)."""
    fav = subject.get("favorite") or {}
    return sum(v for v in fav.values() if isinstance(v, int))


def load_state(path):
    """{id: {checked, status, hash, fails}}; status 是 hit / miss / err / manual."""
    state = {}
    if not os.path.isfile(path):
        return state
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip() or line.startswith("#"):
                continue
            sid, checked, status, h, fails = line.rstrip("\n").split("\t")
            state[int(sid)] = {"checked": checked, "status": status, "hash": h, "fails": int(fails)}
    return state


def save_state(path, state):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(STATE_HEADER + "\n")
        for sid in sorted(state):
            r = state[sid]
            f.write(f"{sid}\t{r['checked']}\t{r['status']}\t{r['hash']}\t{r['fails']}\n")


def normalize_override(raw: dict) -> dict:
    """校验并规整一条人工修正; 不合法抛 ValueError.

    字段: backdrop (tv|movie|collection/<id>), backdrop_path (/xxx.jpg), stills ([tv/<id>/season/<n>, ...]),
    none (true = 确认 TMDB 上没有对应), title, note, updated, auto_was. 除 none 外都可省.
    """
    if not isinstance(raw, dict):
        raise ValueError("不是 JSON 对象")
    none = bool(raw.get("none"))
    backdrop = (raw.get("backdrop") or "").strip() or None
    path = (raw.get("backdrop_path") or "").strip() or None
    stills = [str(x).strip() for x in (raw.get("stills") or []) if str(x).strip()]
    if backdrop and not REF_RE.match(backdrop):
        raise ValueError(f"backdrop 格式不对: {backdrop!r} (应为 tv/123、movie/123 或 collection/123)")
    if path and not PATH_RE.match(path):
        raise ValueError(f"backdrop_path 格式不对: {path!r} (应为 /xxxx.jpg)")
    for ref in stills:
        if not STILL_REF_RE.match(ref):
            raise ValueError(f"stills 格式不对: {ref!r} (应为 tv/123/season/1、movie/123 或 collection/123)")
    if none and (backdrop or path or stills):
        raise ValueError("none 为 true 时不能再填条目")
    if not none and not backdrop and not stills:
        raise ValueError("既没有条目也没有 none: 要么填 backdrop / stills, 要么 none: true")
    if path and not backdrop:
        raise ValueError("给了 backdrop_path 却没有 backdrop")
    out = {"none": none, "backdrop": backdrop, "backdrop_path": path, "stills": stills}
    for key in ("title", "note", "updated", "auto_was"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()
    return out


def load_overrides(directory):
    """返回 ({id: 规整后的修正}, [(文件名, 错误)]). 不合法的修正不生效, 由调用方报出来."""
    overrides, errors = {}, []
    if not os.path.isdir(directory):
        return overrides, errors
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        stem = name[:-5]
        if not stem.isdigit():
            errors.append((name, "文件名应为 <bgm_id>.json"))
            continue
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as f:
                overrides[int(stem)] = normalize_override(json.load(f))
        except (ValueError, json.JSONDecodeError) as e:
            errors.append((name, str(e)))
    return overrides, errors
