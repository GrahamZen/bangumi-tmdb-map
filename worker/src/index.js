// Izuko TV 详情页「反馈」里的报告 → 在本仓库开修正请求 issue (表单的写法), 之后的校验、向 TMDB 核实、开 PR 都由 correction 工作流做.
// 这里只管: 字段格式、防刷、同一处已经有没处理的修正就不再开.
//
// POST /logo-report   标题 logo 不对 (表单 .github/ISSUE_TEMPLATE/logo.yml)
//   {"bgm_id": 135275, "tmdb": "tv/65844", "language": "ja", "logo": "/x.png" 或 null (= 用文字), "app": "1.0.4"}
//   该用的不在 app 列出的候选里 (TMDB 接口不列的 SVG logo 之类): "not_listed": true, 不带 logo —— 维护者审核时补图
// POST /entry-report  对应的 TMDB 条目不对 (表单 .github/ISSUE_TEMPLATE/correction.yml)
//   {"bgm_id": 135275, "tmdb": "tv/65844" 或 null (= TMDB 上没有对应), "backdrop": "/x.jpg" 或 null, "app": "1.0.4"}
// 请求要带 X-Izuko-Client 头 (app 填版本号), 没有的 403 —— 挡掉随手乱发的, 不算防护 (头谁都能伪造).
// → 200 {"status": "created" | "duplicate" | "pending", "issue": 12}; 格式不对 400, 没带头 403, 太频繁 429, GitHub 出错 502.
//
// 同一处 (同一条目的标题 logo 的同一种语言 / 同一条目对应的作品) 已经有开着的修正请求 (= 维护者还没处理: PR 合并或关掉时 issue 跟着关),
// 就不再开新的, 也不写任何东西: 内容一样回 duplicate, 不一样回 pending —— 同一处不堆互相冲突的 PR. 核对页提交的条目修正也算.
// 查的是开着的「修正请求」issue 列表 (不用搜索接口: 搜索有索引延迟, 连着两次报告会都查不到对方).
//
// 防刷靠封顶: 每个 IP 每分钟 5 次 (REPORT_LIMITER), 每个 IP 每天 30 次, 全站每天开 100 个 issue (计数在 KV, 跨地区最终一致, 是约数)
// —— 最坏一天多出一百来个 issue, 正常的反馈量到不了.
//
// 绑定: REPORT_GITHUB_TOKEN (secret, 只给本仓库 Issues 读写的 fine-grained token), REPO (vars),
//       REPORT_LIMITER (ratelimit), REPORTS (KV).
//
// GET /bgm/callback 是另一件事 (手机登录 Bangumi 的回调中转), 见 bgm-callback.js.

import { bgmCallback } from "./bgm-callback.js";

const IP_DAILY_LIMIT = 30;
const SITE_DAILY_LIMIT = 100;
const CLIENT_HEADER = "x-izuko-client";
const LABEL = "修正请求";
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

// 表单值的写法 (同 scripts/correction.py 与 logo_correction.py): 网页上手填的 issue 可能写「无」「日文」或贴网址
const NONE_WORDS = new Set(["无", "没有", "无对应", "没有对应", "none"]);
// 标题 logo「列表里没有」(同 scripts/logo_correction.py 的 NOT_LISTED_WORDS); 开 issue 时写第一个
const NOT_LISTED_WORDS = ["列表里没有", "列表裡沒有", "待补", "待补图", "not listed"];
const LANG_WORDS = { 日文: "ja", 日语: "ja", 中文: "zh", 汉语: "zh", 英文: "en", 英语: "en" };

/** issue 正文 (表单渲染出的) 里「### [heading]」那一项的值; 没填 (_No response_) 或没有这一项为空串. */
function section(issue, heading) {
  const lines = (issue.body || "").replace(/\r\n/g, "\n").split("\n");
  const start = lines.findIndex((l) => l.trim() === `### ${heading}`);
  if (start < 0) return "";
  const end = lines.findIndex((l, i) => i > start && /^###\s/.test(l));
  const value = lines.slice(start + 1, end < 0 ? undefined : end).join("\n").trim();
  return value === "_No response_" ? "" : value;
}

function isNone(value) {
  return NONE_WORDS.has(value.trim().toLowerCase());
}

function isNotListed(value) {
  return NOT_LISTED_WORDS.includes(value.trim().toLowerCase());
}

/** logo 值 → 文件名 (不带扩展名, SVG 与同名 PNG 算同一张); 认不出为 null. */
function logoName(value) {
  const m = /\/([A-Za-z0-9_-]+)\.(?:png|svg)(?:[?#]\S*)?$/.exec(value.trim());
  return m ? m[1] : null;
}

/** 条目值 (tv/123 或 TMDB 网址) → tv/123; 认不出为 null. */
function refOf(value) {
  const m = /\b(tv|movie|collection)\/(\d+)/.exec(value);
  return m ? `${m[1]}/${m[2]}` : null;
}

function langOf(value) {
  const v = value.trim();
  return LANG_WORDS[v] || v.toLowerCase();
}

/**
 * 两种报告: 请求体 → {title, body, samePlace (开着的 issue 是不是同一处的修正), sameContent (是不是连内容都一样)}; 不合法返回 null.
 * 只认这几个字段, 别的一律不要 (issue 是公开的). 标题会被 correction 工作流改成「<前缀> <id> <名字>」, 按前缀与 id 认.
 */
const KINDS = {
  "/logo-report": (b) => {
    if (typeof b.tmdb !== "string" || !REF_RE.test(b.tmdb)) return null;
    if (typeof b.language !== "string" || !LANG_RE.test(b.language)) return null;
    const notListed = b.not_listed === true;
    const logo = notListed ? null : b.logo ?? null;
    if (logo !== null && (typeof logo !== "string" || !LOGO_RE.test(logo))) return null;
    const titleRe = new RegExp(`^标题 logo ${b.bgm_id}( |$)`);
    const name = logo && logoName(logo);
    const value = notListed ? NOT_LISTED_WORDS[0] : logo ?? "无";
    return {
      title: `标题 logo ${b.bgm_id}`,
      body: `### Bangumi id\n\n${b.bgm_id}\n\n### TMDB 条目\n\n${b.tmdb}\n\n### 语言\n\n${b.language}\n\n` +
        `### 标题 logo\n\n${value}\n\n### 说明\n\n${from(b.app)}。\n`,
      samePlace: (i) => titleRe.test(i.title) && langOf(section(i, "语言")) === b.language,
      sameContent: (i) => {
        const v = section(i, "标题 logo");
        if (notListed) return isNotListed(v);
        return name ? logoName(v) === name : isNone(v);
      },
    };
  },
  "/entry-report": (b) => {
    const tmdb = b.tmdb ?? null;
    if (tmdb !== null && (typeof tmdb !== "string" || !REF_RE.test(tmdb))) return null;
    const backdrop = b.backdrop ?? null;
    if (backdrop !== null && (tmdb === null || typeof backdrop !== "string" || !BACKDROP_RE.test(backdrop))) return null;
    // correction_pr.sh 改的标题: 修正 / 确认无对应 / 撤销人工修正
    const titleRe = new RegExp(`^(修正|确认无对应|撤销人工修正) ${b.bgm_id}( |$)`);
    return {
      title: `修正 ${b.bgm_id}`,
      body: `### Bangumi id\n\n${b.bgm_id}\n\n### TMDB 条目\n\n${tmdb ?? "无"}\n\n### 背景图\n\n${backdrop ?? "_No response_"}\n\n` +
        `### 分集数据\n\n_No response_\n\n### 说明\n\n${from(b.app)}。\n`,
      samePlace: (i) => titleRe.test(i.title),
      sameContent: (i) => {
        const value = section(i, "TMDB 条目");
        return tmdb ? refOf(value) === tmdb : isNone(value);
      },
    };
  },
};

function parseReport(path, body) {
  const kind = KINDS[path];
  if (!kind || !body || typeof body !== "object") return null;
  const id = body.bgm_id;
  if (!Number.isInteger(id) || id <= 0 || id > 99999999) return null;
  const app = typeof body.app === "string" && APP_RE.test(body.app) ? body.app : "";
  return kind({ ...body, app });
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

/** 开着的修正请求 issue (不含 PR), 最多三页. */
async function openRequests(env) {
  const issues = [];
  for (let page = 1; page <= 3; page++) {
    const batch = await github(env, `/repos/${env.REPO}/issues?state=open&labels=${encodeURIComponent(LABEL)}&per_page=100&page=${page}`);
    issues.push(...batch.filter((i) => !i.pull_request));
    if (batch.length < 100) break;
  }
  return issues;
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
    if (url.pathname === "/bgm/callback") return bgmCallback(request);
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
    try {
      // 同一处有没处理的修正: 不开新的, 也不写任何东西
      const open = (await openRequests(env)).filter(report.samePlace);
      const same = open.find(report.sameContent);
      if (same) return json({ status: "duplicate", issue: same.number });
      if (open.length > 0) return json({ status: "pending", issue: open[0].number });
      // 全站封顶只算真的开 issue
      if (await overLimit(env, `site:${today()}`, SITE_DAILY_LIMIT)) return json({ error: "rate_limited" }, 429);
      const issue = await github(env, `/repos/${env.REPO}/issues`, {
        method: "POST",
        body: JSON.stringify({ title: report.title, body: report.body, labels: [LABEL] }),
      });
      return json({ status: "created", issue: issue.number });
    } catch (e) {
      return json({ error: "upstream", message: String(e.message || e) }, 502);
    }
  },
};
