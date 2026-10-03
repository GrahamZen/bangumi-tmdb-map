// app 里的「标题 logo 不对」报告 → 在本仓库开一个标题 logo 修正请求 issue (表单 .github/ISSUE_TEMPLATE/logo.yml 的写法).
// 之后的校验、向 TMDB 核实、开 PR 都由 correction 工作流做, 这里只管: 字段格式、按 IP 限频、同一条请求不重复开.
//
// POST /logo-report  {"bgm_id": 135275, "tmdb": "tv/65844", "language": "ja", "logo": "/x.png" 或 null (= 用文字), "app": "1.0.4"}
// → 200 {"status": "created" | "duplicate", "issue": 12}; 格式不对 400, 太频繁 429, GitHub 出错 502.
//
// 绑定: REPORT_GITHUB_TOKEN (secret, 只给本仓库 Issues 读写的 fine-grained token), REPO (vars),
//       REPORT_LIMITER (ratelimit, 每个 IP 每分钟), REPORTS (KV, 可省: 每个 IP 每天的上限).

const DAILY_LIMIT = 30;
const REF_RE = /^(tv|movie|collection)\/\d{1,9}$/;
const LANG_RE = /^[a-z]{2}$/;
const LOGO_RE = /^\/[A-Za-z0-9_-]{1,64}\.png$/;
const APP_RE = /^[\w.+\- ]{1,40}$/;

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json; charset=utf-8" } });
}

/** 请求体 → 规整后的报告; 不合法返回 null. 只认这几个字段, 别的一律不要 (issue 是公开的). */
function parseReport(body) {
  if (!body || typeof body !== "object") return null;
  const id = body.bgm_id;
  if (!Number.isInteger(id) || id <= 0 || id > 99999999) return null;
  if (typeof body.tmdb !== "string" || !REF_RE.test(body.tmdb)) return null;
  if (typeof body.language !== "string" || !LANG_RE.test(body.language)) return null;
  const logo = body.logo ?? null;
  if (logo !== null && (typeof logo !== "string" || !LOGO_RE.test(logo))) return null;
  const app = typeof body.app === "string" && APP_RE.test(body.app) ? body.app : "";
  return { id, tmdb: body.tmdb, language: body.language, logo, app };
}

function issueBody(r) {
  const logo = r.logo ?? "无";
  const from = r.app ? `来自 Izuko TV ${r.app} 的报告` : "来自 Izuko TV 的报告";
  return `### Bangumi id\n\n${r.id}\n\n### TMDB 条目\n\n${r.tmdb}\n\n### 语言\n\n${r.language}\n\n` +
    `### 标题 logo\n\n${logo}\n\n### 说明\n\n${from}。\n`;
}

async function github(env, path, init = {}) {
  const response = await fetch(`https://api.github.com${path}`, {
    ...init,
    headers: {
      authorization: `Bearer ${env.REPORT_GITHUB_TOKEN}`,
      accept: "application/vnd.github+json",
      "x-github-api-version": "2022-11-28",
      "user-agent": "bangumi-tmdb-map-report",
      ...(init.body ? { "content-type": "application/json" } : {}),
    },
  });
  if (!response.ok) throw new Error(`GitHub ${response.status} ${path}`);
  return response.json();
}

/** 已经开着的同一条请求 (同条目、同语言、同一张图): 有就返回 issue 编号. */
async function findOpen(env, r) {
  const q = `repo:${env.REPO} is:issue is:open in:title "标题 logo ${r.id}"`;
  const result = await github(env, `/search/issues?q=${encodeURIComponent(q)}&per_page=20`);
  const lang = `### 语言\n\n${r.language}\n`;
  const logo = `### 标题 logo\n\n${r.logo ?? "无"}\n`;
  const same = (result.items || []).find((i) => {
    const body = (i.body || "").replace(/\r\n/g, "\n");
    return body.includes(lang) && body.includes(logo);
  });
  return same ? same.number : null;
}

async function overDailyLimit(env, ip) {
  if (!env.REPORTS) return false;
  const key = `ip:${ip}:${new Date().toISOString().slice(0, 10)}`;
  const count = parseInt((await env.REPORTS.get(key)) || "0", 10);
  if (count >= DAILY_LIMIT) return true;
  await env.REPORTS.put(key, String(count + 1), { expirationTtl: 2 * 24 * 3600 });
  return false;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== "/logo-report") return json({ error: "not_found" }, 404);
    if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
    const ip = request.headers.get("cf-connecting-ip") || "unknown";
    if (env.REPORT_LIMITER) {
      const { success } = await env.REPORT_LIMITER.limit({ key: ip });
      if (!success) return json({ error: "rate_limited" }, 429);
    }
    let report;
    try {
      report = parseReport(await request.json());
    } catch {
      report = null;
    }
    if (!report) return json({ error: "bad_request" }, 400);
    if (await overDailyLimit(env, ip)) return json({ error: "rate_limited" }, 429);
    try {
      const existing = await findOpen(env, report);
      if (existing) {
        await github(env, `/repos/${env.REPO}/issues/${existing}/comments`, {
          method: "POST",
          body: JSON.stringify({ body: `又收到一次同样的报告${report.app ? ` (Izuko TV ${report.app})` : ""}。` }),
        });
        return json({ status: "duplicate", issue: existing });
      }
      const issue = await github(env, `/repos/${env.REPO}/issues`, {
        method: "POST",
        body: JSON.stringify({ title: `标题 logo ${report.id}`, body: issueBody(report), labels: ["修正请求"] }),
      });
      return json({ status: "created", issue: issue.number });
    } catch (e) {
      return json({ error: "upstream", message: String(e.message || e) }, 502);
    }
  },
};
