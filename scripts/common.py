"""prepare.py / merge.py 共用: 信息框解析、状态表与人工修正的读写."""
import json
import os
import re

ANIME = 2

STATE_HEADER = "# bgm_id\tchecked\tstatus\thash\tfails"

# bangumi/common subject_platforms.yml 的动画平台
PLATFORMS = {0: "其他", 1: "TV", 2: "OVA", 3: "剧场版", 4: "短片", 5: "WEB", 2006: "动态漫画"}

REF_RE = re.compile(r"^(tv|movie|collection)/\d+$")
STILL_REF_RE = re.compile(r"^(tv/\d+(/season/0)?|movie/\d+|collection/\d+)$")
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

    字段: backdrop (tv|movie|collection/<id>), backdrop_path (/xxx.jpg),
    stills ([tv/<id>] 整部剧 / [tv/<id>/season/0] 只取 S0 / [movie/<id>]; 只认一个),
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
            raise ValueError(f"stills 格式不对: {ref!r} (应为 tv/123、tv/123/season/0 或 movie/123)")
    if len(stills) > 1:
        raise ValueError("stills 只能给一个出处")
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


# ---- 标题 logo ----
# 对应表末列与 state/logos.tsv 里的写法: 空格分隔的几段, o=<原语言> 与 <语言>=<logo>; logo 写 /xxx.png:宽高比, 没有这种语言的 logo 写 -.
# 例: o=ja ja=/a.png:2.38 zh=/b.png:3.1 en=-

LOGO_LANGS = ("ja", "zh", "en")  # 每个条目都挑的语言, 另加条目的原语言
LOGO_LANG_RE = re.compile(r"^[a-z]{2}$")
LOGO_PATH_RE = re.compile(r"^/[A-Za-z0-9_\-]+\.png$")
LOGOS_HEADER = "# ref\tchecked\tlogos (o=<原语言> <语言>=/xxx.png:宽高比 或 -)"


def format_logo_entry(entry) -> str:
    """一种语言的 logo: /xxx.png:宽高比; 没有为 -."""
    if not entry or entry.get("none") or not entry.get("logo"):
        return "-"
    return f"{entry['logo']}:{entry['aspect']:.3f}".rstrip("0").rstrip(".")


def parse_logo_entry(text) -> dict:
    """format_logo_entry 的反向; 认不出抛 ValueError."""
    if text == "-":
        return {"none": True}
    path, _, aspect = text.rpartition(":")
    if not LOGO_PATH_RE.match(path):
        raise ValueError(f"logo 格式不对: {text!r}")
    return {"logo": path, "aspect": float(aspect)}


def format_logos(original, entries) -> str:
    """一个条目的各语言 logo → 一格; 常用的三种在前, 其余按语言码."""
    parts = [f"o={original}"] if original else []
    for lang in sorted(entries, key=lambda x: (LOGO_LANGS.index(x) if x in LOGO_LANGS else len(LOGO_LANGS), x)):
        parts.append(f"{lang}={format_logo_entry(entries[lang])}")
    return " ".join(parts)


def parse_logos(text):
    """format_logos 的反向: (原语言, {语言: logo}); 认不出的段抛 ValueError."""
    original, entries = "", {}
    for part in (text or "").split():
        key, _, value = part.partition("=")
        if key == "o":
            original = value
        elif LOGO_LANG_RE.match(key):
            entries[key] = parse_logo_entry(value)
        else:
            raise ValueError(f"认不出: {part!r}")
    return original, entries


LOGO_FILE_RE = re.compile(r"^(\d+)\.([a-z]{2})\.json$")
LOGO_FILE_KEYS = ("tmdb", "logo", "aspect", "none", "auto_was", "title", "note", "updated")


def normalize_logo_override(raw: dict) -> dict:
    """校验并规整一条标题 logo 修正 (logo-overrides/<bgm_id>.<语言>.json, 一种语言一个文件: 同一条目不同语言的修正各改各的文件,
    同时开着的 PR 不互相冲突); 不合法抛 ValueError.

    字段: tmdb (这条修正针对的 TMDB 条目; 与对应表里这个条目现在的背景图条目一致才生效, 条目改了修正就作废),
    logo (/xxx.png) 与 aspect (宽 / 高), 或 none (true = 这种语言不用 logo, 客户端显示文字标题); auto_was, title, note, updated 可省.
    """
    if not isinstance(raw, dict):
        raise ValueError("不是 JSON 对象")
    tmdb = (raw.get("tmdb") or "").strip()
    if not REF_RE.match(tmdb):
        raise ValueError(f"tmdb 格式不对: {tmdb!r} (应为 tv/123、movie/123 或 collection/123)")
    if raw.get("none"):
        if raw.get("logo"):
            raise ValueError("none 为 true 时不能再填 logo")
        out = {"tmdb": tmdb, "none": True, "logo": None, "aspect": None}
    else:
        logo = (raw.get("logo") or "").strip()
        aspect = raw.get("aspect")
        if not LOGO_PATH_RE.match(logo):
            raise ValueError(f"logo 格式不对: {logo!r} (应为 /xxxx.png; 不用 logo 就写 none: true)")
        if not isinstance(aspect, (int, float)) or isinstance(aspect, bool) or not 0.05 <= aspect <= 50:
            raise ValueError(f"aspect 应为 logo 的宽高比 (宽 / 高): {aspect!r}")
        out = {"tmdb": tmdb, "none": False, "logo": logo, "aspect": round(float(aspect), 3)}
    for key in ("auto_was", "title", "note", "updated"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()
    return out


def logo_override_content(entry: dict) -> dict:
    """写进 logo-overrides/<bgm_id>.<语言>.json 的内容: 固定字段顺序, 空字段不写."""
    return {k: entry[k] for k in LOGO_FILE_KEYS
            if (k == "none" and entry["none"]) or (k != "none" and entry.get(k) not in (None, ""))}


def load_logo_overrides(directory):
    """返回 ({id: {语言: 规整后的修正}}, [(文件名, 错误)]), 同 load_overrides."""
    overrides, errors = {}, []
    if not os.path.isdir(directory):
        return overrides, errors
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        m = LOGO_FILE_RE.match(name)
        if not m:
            errors.append((name, "文件名应为 <bgm_id>.<语言>.json (如 135275.ja.json)"))
            continue
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as f:
                overrides.setdefault(int(m.group(1)), {})[m.group(2)] = normalize_logo_override(json.load(f))
        except (ValueError, json.JSONDecodeError) as e:
            errors.append((name, str(e)))
    return overrides, errors


def load_logos(path):
    """按 TMDB 条目自动挑的标题 logo (logos.py 写): {ref: {checked, original, logos: {语言: logo}}}."""
    logos = {}
    if not os.path.isfile(path):
        return logos
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip() or line.startswith("#"):
                continue
            ref, checked, text = (line.rstrip("\n").split("\t") + [""])[:3]
            original, entries = parse_logos(text)
            logos[ref] = {"checked": checked, "original": original, "logos": entries}
    return logos


def save_logos(path, logos):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(LOGOS_HEADER + "\n")
        for ref in sorted(logos):
            r = logos[ref]
            f.write(f"{ref}\t{r['checked']}\t{format_logos(r.get('original'), r.get('logos') or {})}\n")


def logo_cell(ref, overrides, auto) -> str:
    """对应表 logo 列: 自动挑的 (按 ref) 上面叠各语言的人工修正 ([overrides]: {语言: 修正}, 针对的条目与 ref 一致才算); 都没有 (还没查) 为空."""
    if not ref:
        return ""
    entries = dict((auto or {}).get("logos") or {})
    for lang, entry in (overrides or {}).items():
        if entry["tmdb"] == ref:
            entries[lang] = {"none": True} if entry["none"] else {"logo": entry["logo"], "aspect": entry["aspect"]}
    if not entries:
        return ""
    return format_logos((auto or {}).get("original"), entries)
