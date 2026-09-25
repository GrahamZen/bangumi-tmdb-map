#!/usr/bin/env bash
# 修正请求的第二步 (见 .github/workflows/correction.yml): 按 correction.py 的结果建分支、开或更新 PR、回复 issue.
# 每个请求一条分支 correction/issue-<编号>, 只改 overrides/<bgm_id>.json; 提交者改了 issue 就重新生成、强推这条分支.
#
# 用法: scripts/correction_pr.sh <correction.py 的输出目录>
# 环境变量: GH_TOKEN, ISSUE_NUMBER
set -euo pipefail

OUT=$1
cd "$(dirname "$0")/.."
result() { jq -r ".$1 // empty" "$OUT/result.json"; }
branch="correction/issue-$ISSUE_NUMBER"
open_pr() { gh pr list --head "$branch" --state open --json number --jq '.[0].number // empty'; }

# 请求内容变得不需要或不能用了: 撤掉之前为它开的 PR
drop_pr() {
  local pr
  pr=$(open_pr)
  if [ -n "$pr" ]; then gh pr close "$pr" --delete-branch --comment "$1"; fi
}

# 标题统一成「修正 <id> <名字>」, 核对页按它找待审核的请求
retitle() {
  local title
  title=$(result issue_title)
  if [ -n "$title" ] && [ "$title" != "$(gh issue view "$ISSUE_NUMBER" --json title --jq .title)" ]; then
    gh issue edit "$ISSUE_NUMBER" --title "$title" > /dev/null
  fi
}

case "$(result status)" in
  skip)
    echo "不是修正请求表单, 跳过"
    ;;
  invalid)
    drop_pr "修正请求改成了没法处理的内容，先关掉这个 PR；issue 改好后会重新生成。"
    gh issue comment "$ISSUE_NUMBER" --body-file "$OUT/reply.md"
    ;;
  noop)
    drop_pr "修正请求现在和已有的人工修正一样，这个 PR 不需要了。"
    retitle
    gh issue close "$ISSUE_NUMBER" --reason "not planned" --comment "$(cat "$OUT/reply.md")"
    ;;
  ok)
    retitle
    git config user.name "github-actions[bot]"
    git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
    git checkout -q -B "$branch"
    git add -A -- "$(result file)"
    if git diff --cached --quiet; then
      echo "::error::correction.py 说要改, 但 $(result file) 没有变化"
      exit 1
    fi
    git commit -q -F "$OUT/commit.txt"
    git push -q --force origin "$branch"
    title=$(result pr_title)
    pr=$(open_pr)
    if [ -n "$pr" ]; then
      gh pr edit "$pr" --title "$title" --body-file "$OUT/pr.md" > /dev/null
      head="已更新修正 PR #$pr"
    else
      url=$(gh pr create --base main --head "$branch" --title "$title" --body-file "$OUT/pr.md" --label "修正请求")
      head="已生成修正 PR #${url##*/}"
    fi
    { echo "$head，等维护者审核，合并后几分钟内生效。"; echo; cat "$OUT/reply.md"; } > "$OUT/reply_full.md"
    gh issue comment "$ISSUE_NUMBER" --body-file "$OUT/reply_full.md"
    ;;
  *)
    echo "::error::correction.py 的结果不认识: $(cat "$OUT/result.json")"
    exit 1
    ;;
esac
