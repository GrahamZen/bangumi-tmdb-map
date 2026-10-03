# 标题 logo 报告的中转 (Cloudflare Worker)

Izuko TV 详情页里点「标题 logo」选好正确的那张后，app 把报告发到这里；Worker 在本仓库开一个标题 logo 修正请求 issue
(和 `.github/ISSUE_TEMPLATE/logo.yml` 同一写法)，之后由 `correction` 工作流核实、开 PR，维护者合并后写进对应表。
电视上没法登录 GitHub，令牌也不能放进 app，所以经这里转一道。

Worker 只做三件事：检查字段格式；按 IP 限频 (每分钟 3 次，配了 KV 再加每天 30 次)；同一条目、同一语言、同一张图已经有开着的请求时，
只在那个 issue 里留一句「又收到一次」，不重复开。

## 部署

由 `worker` 工作流 (`.github/workflows/worker.yml`) 部署：`worker/` 有改动推到 main 时、或手动触发时跑；部署完把 Worker 的地址补进仓库根目录的
`report-endpoints.json` (app 每天拉一次，按顺序试)。要三个仓库密钥，没配齐就跳过：

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

(可省) 每天的上限：建一个 KV 命名空间，把 id 填进 `wrangler.toml` 里注释掉的那段。

`workers.dev` 在中国大陆多半连不上；要给大陆用户用，给 Worker 绑一个自定义域名，把地址 (带 `/logo-report`) 加在 `report-endpoints.json` 的前面。

## 试一下

格式不对的请求返回 400，不会开 issue，可以拿来看 Worker 在不在：

```
curl -X POST https://<地址>/logo-report -H 'content-type: application/json' -d '{}'
```
