"""worker 工作流部署完之后: 把 Worker 的地址写进 report-endpoints.json (Izuko TV 每天拉一次, 按顺序试, 见 app 的 SubjectFeedbackService).

地址取 wrangler 给的 DEPLOY_URL, 没有就从部署输出 DEPLOY_OUTPUT 里找 workers.dev 的地址. 已经在清单里就不动;
不在就加到最后 —— 前面留给手动加的地址 (如大陆连得上的自定义域名).
"""
import json
import os
import re
import sys

PATH = "report-endpoints.json"
COMMENT = ("Izuko TV「反馈」(标题 logo / 对应的作品不对) 的中转地址 (worker/), 只写根地址, app 在后面接 /logo-report 或 /entry-report. "
           "app 每天拉一次这个文件, 按顺序试, 连不上换下一个. workers.dev 在中国大陆多半连不上: 给 Worker 绑了自定义域名就把它加在前面. "
           "部署时 worker 工作流会把 workers.dev 的地址补在最后.")


def main():
    url = (os.environ.get("DEPLOY_URL") or "").strip()
    if not url:
        m = re.search(r"https://[A-Za-z0-9.\-]+\.workers\.dev", os.environ.get("DEPLOY_OUTPUT") or "")
        url = m.group(0) if m else ""
    if not url:
        sys.exit("部署输出里找不到 Worker 的地址")
    endpoint = url.rstrip("/")
    data = {}
    if os.path.isfile(PATH):
        with open(PATH, encoding="utf-8") as f:
            data = json.load(f)
    endpoints = [e for e in data.get("endpoints") or [] if isinstance(e, str)]
    if endpoint not in endpoints:
        endpoints.append(endpoint)
    out = {"_comment": COMMENT, "endpoints": endpoints}
    with open(PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
