# 详情页「反馈」的中转 (Cloudflare Worker)

Izuko TV 详情页的「反馈」里报告的两类问题经这里转成本仓库的修正请求 issue，之后由 `correction` 工作流核实、开 PR，维护者合并后写进对应表：

- `POST /logo-report`：标题 logo 不对 (表单 `.github/ISSUE_TEMPLATE/logo.yml`)；
- `POST /entry-report`：对应的 TMDB 条目不对，选的是核对页里的备选或「TMDB 上没有对应」(表单 `.github/ISSUE_TEMPLATE/correction.yml`)。

电视上没法登录 GitHub，令牌也不能放进 app，所以经这里转一道。

Worker 只做这几件事：
- 检查字段格式 (只收条目 id、`tv/123`、图片路径、两个字母的语言码，issue 正文是模板拼的，写不进任意文字)；请求要带 `X-Izuko-Client` 头
  (app 填版本号)，没有的拒掉 —— 只挡随手乱发的，头谁都能伪造；
- 封顶防刷：每个 IP 每分钟 5 次、每天 30 次，全站每天 100 次写 GitHub (开 issue 或在已有的里记一笔)；计数在 KV，是约数；
- 同一条请求已经有开着的 issue 时，只在里面留一句「又收到一次」，不重复开。

## 部署

由 `worker` 工作流 (`.github/workflows/worker.yml`) 部署：`worker/` 有改动推到 main 时、或手动触发时跑；部署完把 Worker 的地址补进仓库根目录的
`report-endpoints.json` (只写根地址；app 每天拉一次，按顺序试)。要三个仓库密钥，没配齐就跳过：

| 密钥 | 怎么来 |
|---|---|
| `CLOUDFLARE_API_TOKEN` | Cloudflare 控制台 → My Profile → API Tokens → Create Token → 模板「Edit Cloudflare Workers」 |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare 控制台 → Workers & Pages 页右侧的 Account ID |
| `REPORT_GITHUB_TOKEN` | GitHub fine-grained token：Repository access 只选 `GrahamZen/bangumi-tmdb-map`，权限只给 Issues: Read and write；部署成 Worker 的密钥 |

```
gh secret set CLOUDFLARE_API_TOKEN -R GrahamZen/bangumi-tmdb-map
gh secret set CLOUDFLARE_ACCOUNT_ID -R GrahamZen/bangumi-tmdb-map
gh secret set REPORT_GITHUB_TOKEN -R GrahamZen/bangumi-tmdb-map
gh workflow run worker.yml -R GrahamZen/bangumi-tmdb-map
```

Cloudflare 账号第一次用 Workers 时要先在控制台的 Workers & Pages 里点开一次，注册 `workers.dev` 子域，否则部署会报没有子域。

`workers.dev` 在中国大陆多半连不上；要给大陆用户用，给 Worker 绑一个自定义域名，把根地址加在 `report-endpoints.json` 的前面。

## 试一下

格式不对的请求返回 400，不会开 issue，可以拿来看 Worker 在不在：

```
curl -X POST https://<地址>/logo-report -H 'content-type: application/json' -H 'x-izuko-client: test' -d '{}'
```
