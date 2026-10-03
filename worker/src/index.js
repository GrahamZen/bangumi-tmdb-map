// Izuko TV 详情页「反馈」里的报告 → 在本仓库开修正请求 issue (表单的写法), 之后的校验、向 TMDB 核实、开 PR 都由 correction 工作流做.
// 这里只管: 字段格式、按 IP 限频、同一条请求不重复开.
//
// POST /logo-report   标题 logo 不对 (表单 .github/ISSUE_TEMPLATE/logo.yml)
//   {"bgm_id": 135275, "tmdb": "tv/65844", "language": "ja", "logo": "/x.png" 或 null (= 用文字), "app": "1.0.4"}
// POST /entry-report  对应的 TMDB 条目不对 (表单 .github/ISSUE_TEMPLATE/correction.yml)
//   {"bgm_id": 135275, "tmdb": "tv/65844" 或 null (= TMDB 上没有对应), "backdrop": "/x.jpg" 或 null, "app": "1.0.4"}
// 请求要带 X-Izuko-Client 头 (app 填版本号), 没有的 403 —— 挡掉随手乱发的, 不算防护 (头谁都能伪造).
// → 200 {"status": "created" | "duplicate", "issue": 12}; 格式不对 400, 没带头 403, 太频繁 429, GitHub 出错 502.
//
// 防刷靠封顶: 每个 IP 每分钟 5 次 (REPORT_LIMITER), 每个 IP 每天 30 次、全站每天 100 次写 GitHub (开 issue 或在已有的里记一笔;
// 计数在 KV, 跨地区最终一致, 是约数) —— 最坏一天多出一百来个 issue, 正常的反馈量到不了.
//
// 绑定: REPORT_GITHUB_TOKEN (secret, 只给本仓库 Issues 读写的 fine-grained token), REPO (vars),
//       REPORT_LIMITER (ratelimit), REPORTS (KV).

const IP_DAILY_LIMIT = 30;
const SITE_DAILY_LIMIT = 100;
const CLIENT_HEADER = "x-izuko-client";
const REF_RE = /^(tv|movie|collection)\/\d{1,9}$/;
const LANG_RE = /^[a-z]{2}$/;
const LOGO_RE = /^\/[A-Za-z0-9_-]{1,64}\.png$/;
const BACKDROP_RE = /^\/[A-Za-z0-9_-]{1,64}\.(jpg|jpeg|png|webp)$/;
const APP_RE = /^[\w.+\- ]{1,40}$/;

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json; charset=utf-8" } });
}

function from(app) {
  return app ? `来自 Izuko TV ${app} 的报告` : "来自 Izuko TV 的报告";
}

/** 两种报告: 请求体 → {title, body, same (同一条请求在正文里的特征)}; 不合法返回 null. 只认这几个字段, 别的一律不要 (issue 是公开的). */
const KINDS = {
  "/logo-report": (b) => {
    if (typeof b.tmdb !== "string" || !REF_RE.test(b.tmdb)) return null;
    if (typeof b.language !== "string" || !LANG_RE.test(b.language)) return null;
    const logo = b.logo ?? null;
    if (logo !== null && (typeof logo !== "string" || !LOGO_RE.test(logo))) return null;
    const lang = `### 语言\n\n${b.language}\n`;
    const logoText = `### 标题 logo\n\n${logo ?? "无"}\n`;
    return {
      title: `标题 logo ${b.bgm_id}`,
      body: `### Bangumi id\n\n${b.bgm_id}\n\n### TMDB 条目\n\n${b.tmdb}\n\n${lang}\n${logoText}\n### 说明\n\n${from(b.app)}。\n`,
      same: [lang, logoText],
    };
  },
  "/entry-report": (b) => {
    const tmdb = b.tmdb ?? null;
    if (tmdb !== null && (typeof tmdb !== "string" || !REF_RE.test(tmdb))) return null;
    const backdrop = b.backdrop ?? null;
    if (backdrop !== null && (tmdb === null || typeof backdrop !== "string" || !BACKDROP_RE.test(backdrop))) return null;
    const tmdbText = `### TMDB 条目\n\n${tmdb ?? "无"}\n`;
    return {
      title: `修正 ${b.bgm_id}`,
      body: `### Bangumi id\n\n${b.bgm_id}\n\n${tmdbText}\n### 背景图\n\n${backdrop ?? "_No response_"}\n\n` +
        `### 分集数据\n\n_No response_\n\n### 说明\n\n${from(b.app)}。\n`,
      same: [tmdbText],
    };
  },
};

function parseReport(path, body) {
  const kind = KINDS[path];
  if (!kind || !body || typeof body !== "object") return null;
  const id = body.bgm_id;
  if (!Number.isInteger(id) || id <= 0 || id > 99999999) return null;
  const app = typeof body.app === "string" && APP_RE.test(body.app) ? body.app : "";
  const issue = kind({ ...body, app });
  return issue && { ...issue, app };
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

/** 已经开着的同一条请求 (标题开头相同、正文特征都在): 有就返回 issue 编号. */
async function findOpen(env, report) {
  const q = `repo:${env.REPO} is:issue is:open in:title "${report.title}"`;
  const result = await github(env, `/search/issues?q=${encodeURIComponent(q)}&per_page=20`);
  const same = (result.items || []).find((i) => {
    const body = (i.body || "").replace(/\r\n/g, "\n");
    return (i.title === report.title || i.title.startsWith(report.title + " ")) && report.same.every((s) => body.includes(s));
  });
  return same ? same.number : null;
}

function today() {
  return new Date().toISOString().slice(0, 10);
}

/** KV 里的计数到了 [limit] 就返回 true; 没到就加一 (先查后加, 并发时可能多放过一两次, 够用). 没绑 KV 时不限. */
async function overLimit(env, key, limit) {
  if (!env.REPORTS) return false;
  const count = parseInt((await env.REPORTS.get(key)) || "0", 10);
  if (count >= limit) return true;
  await env.REPORTS.put(key, String(count + 1), { expirationTtl: 2 * 24 * 3600 });
  return false;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (!KINDS[url.pathname]) return json({ error: "not_found" }, 404);
    if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
    if (!APP_RE.test(request.headers.get(CLIENT_HEADER) || "")) return json({ error: "forbidden" }, 403);
    const ip = request.headers.get("cf-connecting-ip") || "unknown";
    if (env.REPORT_LIMITER) {
      const { success } = await env.REPORT_LIMITER.limit({ key: ip });
      if (!success) return json({ error: "rate_limited" }, 429);
    }
    let report;
    try {
      report = parseReport(url.pathname, await request.json());
    } catch {
      report = null;
    }
    if (!report) return json({ error: "bad_request" }, 400);
    if (await overLimit(env, `ip:${ip}:${today()}`, IP_DAILY_LIMIT)) return json({ error: "rate_limited" }, 429);
    // 全站封顶: 每次写 GitHub (开 issue 或记一笔) 都算
    if (await overLimit(env, `site:${today()}`, SITE_DAILY_LIMIT)) return json({ error: "rate_limited" }, 429);
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
        body: JSON.stringify({ title: report.title, body: report.body, labels: ["修正请求"] }),
      });
      return json({ status: "created", issue: issue.number });
    } catch (e) {
      return json({ error: "upstream", message: String(e.message || e) }, 502);
    }
  },
};
