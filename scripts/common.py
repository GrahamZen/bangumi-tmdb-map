"""prepare.py / merge.py 共用: 信息框解析与状态表读写."""
import os

ANIME = 2

STATE_HEADER = "# bgm_id\tchecked\tstatus\thash\tfails"


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


def load_state(path):
    """{id: {checked, status, hash, fails}}; status 是 hit / miss / err."""
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
