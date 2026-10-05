// Izuko TV 手机登录 Bangumi 的回调中转: Izuko TV 的第二个 bgm 应用把回调注册成本 Worker 的 /bgm/callback,
// 用户在手机上授权完, bgm 把手机浏览器跳到这里, 这里再把它跳回电视 Web 控制台的 /bgm-oauth, 电视拿 code 换 token.
// 用户不用把回调地址复制粘贴回控制台.
//
// GET /bgm/callback?code=...&state=<随机串>~<电视的局域网 IPv4>:<端口>   (用户拒绝授权时没有 code, 带 error)
// → 302 http://<电视>/bgm-oauth?<原样的查询串>
//
// 什么都不存: 电视的地址是电视发起授权时放进 state 的. 只跳内网 IPv4 (10/8、172.16/12、192.168/16、100.64/10), 不当任意跳转用.
// code 换 token 要 client secret, secret 只在电视上; code 只在这一跳的地址里经过这里.
// state 不是这个格式 (不是电视发起的, 或地址不是内网) → 一页说明; 地址栏里的网址仍可整个复制, 粘回控制台完成登录.

const STATE_RE = /^[0-9A-Za-z-]{8,64}~(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3}):(\d{1,5})$/;

/** state 里的电视地址 → 跳回去的地址; 不是内网 IPv4 时 null. 与 app 的 BangumiOAuthRelay.isPrivateLanHost 同一张表. */
export function tvReturnUrl(url) {
  const m = STATE_RE.exec(url.searchParams.get("state") || "");
  if (!m) return null;
  const [a, b, c, d] = m.slice(1, 5).map(Number);
  const port = Number(m[5]);
  if ([a, b, c, d].some((n) => n > 255) || port < 1 || port > 65535) return null;
  const privateRange = a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || (a === 100 && b >= 64 && b <= 127);
  if (!privateRange) return null;
  return `http://${a}.${b}.${c}.${d}:${port}/bgm-oauth${url.search}`;
}

const PAGE = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>没能回到电视</title>
<style>:root{color-scheme:light dark}body{margin:0;padding:48px 24px;font:17px/1.6 system-ui,-apple-system,sans-serif;text-align:center}
h1{font-size:24px;margin:0 0 12px}p{margin:8px 0;opacity:.85}</style></head><body>
<h1>没能回到电视</h1>
<p>把这个页面的网址整个复制，粘到 Izuko TV 控制台「账号」里的输入框，点「完成登录」。</p>
<p lang="en">Couldn't return to the TV. Copy this page's whole address, paste it into the box under “Account” in the Izuko TV console, and tap “Finish sign-in”.</p>
</body></html>`;

export function bgmCallback(request) {
  if (request.method !== "GET" && request.method !== "HEAD") return new Response("method not allowed", { status: 405 });
  const target = tvReturnUrl(new URL(request.url));
  if (target) return new Response(null, { status: 302, headers: { location: target, "cache-control": "no-store" } });
  return new Response(PAGE, { status: 400, headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });
}
