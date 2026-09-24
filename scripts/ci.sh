#!/usr/bin/env bash
# 每日更新的全部步骤. GitHub Actions (.github/workflows/update.yml) 与本地跑的是同一份,
# 工作流只另外负责装 JDK、存取 Gradle 缓存与提交.
#
# 用法: scripts/ci.sh <步骤>...
#   download  取 Bangumi 最新导出 (同名的已下载过就跳过)
#   plan      挑本轮任务 (scripts/prepare.py); 输出 jobs=<个数>
#   matcher   取 izuko-tv (matcher.ref), 拷入匹配器入口, 写 local.properties
#   match     构建并运行匹配器, 结果逐条写进 $WORK/results.jsonl
#   merge     合并结果与人工修正 (scripts/merge.py); 没有结果时跳过
#   apply     只应用人工修正 (推送 overrides/ 后的那一轮用); 修正文件不合法时失败
#   local     本地验证: download plan matcher match merge 依次跑, 不提交
#
# 环境变量:
#   WORK            工作目录; 默认 $RUNNER_TEMP/work, 本地为仓库下的 .work
#   TMDB_API_TOKEN  TMDB 读取令牌 (matcher 写进 izuko-tv 的 local.properties)
#   JAVA_HOME       带 JCEF 的 JBR 21 (izuko-tv 的构建要 JetBrains 厂商, 桌面端代码要 JCEF 的类)
#   MAX_JOBS (20000) / ONLY_IDS (逗号分隔, 调试用) / MATCH_MINUTES (290)
#   BGM_TMDB_CONCURRENCY (10) / BGM_TMDB_RPS (35)
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
if [ -z "${WORK:-}" ]; then
  if [ -n "${RUNNER_TEMP:-}" ]; then WORK="$RUNNER_TEMP/work"; else WORK="$ROOT/.work"; fi
fi
mkdir -p "$WORK"
MAX_JOBS=${MAX_JOBS:-20000}
ONLY_IDS=${ONLY_IDS:-}
MATCH_MINUTES=${MATCH_MINUTES:-290}

# 给后续 GitHub 步骤用的输出/环境变量; 本地跑时只打印
gh_output() { echo "$1"; if [ -n "${GITHUB_OUTPUT:-}" ]; then echo "$1" >> "$GITHUB_OUTPUT"; fi; }
gh_env() { if [ -n "${GITHUB_ENV:-}" ]; then echo "$1" >> "$GITHUB_ENV"; fi; }

step_download() {
  curl -fsSL https://raw.githubusercontent.com/bangumi/Archive/master/aux/latest.json -o "$WORK/latest.json"
  local url name digest
  read -r url name digest < <(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["browser_download_url"], d["name"], d.get("digest") or "-")' "$WORK/latest.json")
  if [ -f "$WORK/dump.zip" ] && [ "$(cat "$WORK/dump.name" 2>/dev/null)" = "$name" ]; then
    echo "导出 $name 已下载"
  else
    curl -fsSL "$url" -o "$WORK/dump.zip"
    if [ "$digest" != "-" ]; then
      echo "${digest#sha256:}  $WORK/dump.zip" | sha256sum -c -
    fi
    echo "$name" > "$WORK/dump.name"
  fi
}

step_plan() {
  local args=(--dump "$WORK/dump.zip" --state "$ROOT/state/state.tsv" --overrides "$ROOT/overrides" --work "$WORK" --max-jobs "$MAX_JOBS")
  if [ -n "$ONLY_IDS" ]; then args+=(--ids "$ONLY_IDS"); fi
  python3 "$ROOT/scripts/prepare.py" "${args[@]}"
  gh_output "jobs=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["jobs"])' "$WORK/plan.json")"
}

step_matcher() {
  : "${TMDB_API_TOKEN:?缺少 TMDB_API_TOKEN}"
  local ref dir="$WORK/izuko-tv"
  ref=$(tr -d '[:space:]' < "$ROOT/matcher.ref")
  rm -rf "$dir"
  git clone -q --depth 1 --branch "$ref" https://github.com/GrahamZen/izuko-tv.git "$dir"
  git -C "$dir" rev-parse HEAD > "$WORK/matcher.sha"
  gh_env "MATCHER_SHA=$(cat "$WORK/matcher.sha")"
  cp "$ROOT/runner/BgmTmdbMapRunner.kt" "$dir/app/shared/app-data/src/desktopTest/kotlin/data/network/"
  {
    echo "ani.tmdb.api.token=$TMDB_API_TOKEN"
    echo "ani.enable.firebase=false"
    echo "jvm.toolchain.version=21"
    echo "kotlin.native.ignoreDisabledTargets=true"
  } > "$dir/local.properties"
  echo "matcher: $(cat "$WORK/matcher.sha") ($ref)"
}

step_match() {
  cd "$WORK/izuko-tv"
  export BGM_TMDB_WORK="$WORK"
  export BGM_TMDB_OUT="$WORK/results.jsonl"
  export BGM_TMDB_DEADLINE=$(( $(date +%s) + MATCH_MINUTES * 60 ))
  # TMDB 按 IP 限流 (约 40 次/秒); 平均每个条目 3 秒、十来个请求, 10 路并发刚好把 35 次/秒用满
  export BGM_TMDB_CONCURRENCY=${BGM_TMDB_CONCURRENCY:-10}
  export BGM_TMDB_RPS=${BGM_TMDB_RPS:-35}
  ./gradlew :app:shared:app-data:desktopTest --tests "*BgmTmdbMapRunner*" \
    --init-script "$ROOT/runner/runner.init.gradle" \
    "-Dorg.gradle.jvmargs=-Xmx6g -Dfile.encoding=UTF-8" \
    "-Dkotlin.daemon.jvm.options=-Xmx5g"
}

step_merge() {
  if [ ! -s "$WORK/results.jsonl" ]; then
    echo "一条结果也没有, 不合并"
    gh_output "skip=1"
    return 0
  fi
  python3 "$ROOT/scripts/merge.py" --work "$WORK" --results "$WORK/results.jsonl" \
    --state "$ROOT/state/state.tsv" --map "$ROOT/map/bgm-tmdb.tsv" --meta "$ROOT/map/meta.json" \
    --site "$ROOT/docs" --overrides "$ROOT/overrides" \
    --dump-name "$(cat "$WORK/dump.name")" --matcher "$(cat "$WORK/matcher.sha" 2>/dev/null || true)" \
    --summary-out "$WORK/summary.txt"
}

step_apply() {
  python3 "$ROOT/scripts/merge.py" --apply-only --state "$ROOT/state/state.tsv" --map "$ROOT/map/bgm-tmdb.tsv" \
    --meta "$ROOT/map/meta.json" --site "$ROOT/docs" --overrides "$ROOT/overrides" --summary-out "$WORK/summary.txt"
}

[ $# -gt 0 ] || { sed -n '2,22p' "$0"; exit 2; }
for s in "$@"; do
  case "$s" in
    download | plan | matcher | match | merge | apply) echo "== $s"; "step_$s" ;;
    local) for t in download plan matcher match merge; do echo "== $t"; "step_$t"; done ;;
    *) echo "未知步骤: $s" >&2; exit 2 ;;
  esac
done
