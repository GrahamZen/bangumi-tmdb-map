# 标题 logo 报告的中转 (Cloudflare Worker)

Izuko TV 详情页里点「标题 logo 不对」选好正确的那张后，app 把报告发到这里；Worker 在本仓库开一个标题 logo 修正请求 issue
(和 `.github/ISSUE_TEMPLATE/logo.yml` 同一写法)，之后由 `correction` 工作流核实、开 PR，维护者合并后写进对应表。
电视上没法登录 GitHub，令牌也不能放进 app，所以经这里转一道。

Worker 只做三件事：检查字段格式；按 IP 限频 (每分钟 3 次，配了 KV 再加每天 30 次)；同一条目、同一语言、同一张图已经有开着的请求时，
只在那个 issue 里留一句「又收到一次」，不重复开。

## 部署

1. GitHub 建一个 fine-grained token：Repository access 只选 `GrahamZen/bangumi-tmdb-map`，Permissions 只给 Issues: Read and write。
2. 在这个目录：

   ```
   npx wrangler login
   npx wrangler secret put GITHUB_TOKEN     # 粘贴上面的 token
   npx wrangler deploy
   ```

   部署完给出 `https://bangumi-tmdb-map-report.<子域>.workers.dev`，app 要的地址是它加上 `/logo-report`。
3. (可省) 每天的上限：`npx wrangler kv namespace create REPORTS`，把 id 填进 `wrangler.toml` 再 deploy。

没装 Node 也可以在 Cloudflare 控制台里做：Workers & Pages → Create → 从 Hello World 建一个名为 `bangumi-tmdb-map-report` 的 Worker，
编辑代码换成 `src/index.js` 的内容并部署；Settings → Variables and Secrets 里加变量 `REPO` = `GrahamZen/bangumi-tmdb-map`、
加 Secret `GITHUB_TOKEN`。控制台建的没有每分钟限频绑定 (那个只能在 `wrangler.toml` 里配)，可以在 Settings → Bindings 绑一个
KV 命名空间到 `REPORTS`，至少有每天的上限。

`workers.dev` 在中国大陆多半连不上；要给大陆用户用，给 Worker 绑一个自定义域名，把地址加进 app 的报告地址清单 (izuko-tv 仓库根的
`report-endpoints.json`)，app 按清单顺序试。

## 试一下

```
curl -X POST https://<地址>/logo-report -H 'content-type: application/json' \
  -d '{"bgm_id": 135275, "tmdb": "tv/65844", "language": "ja", "logo": "/8sW8IRMpZNvPHtTZIBxbCjyYEfX.png", "app": "test"}'
```
