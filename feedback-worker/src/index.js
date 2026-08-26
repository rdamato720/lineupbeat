const CATEGORIES = new Set(["ERROR", "FEATURE", "GENERAL"]);
const STATUSES = new Set(["NEW", "REVIEWED", "PLANNED", "RESOLVED", "ARCHIVED"]);
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function validateSubmission(raw) {
  const category = String(raw?.category || "").toUpperCase();
  const message = String(raw?.message || "").trim();
  const email = String(raw?.email || "").trim();
  const pageUrl = String(raw?.page_url || "").trim();
  const website = String(raw?.website || "").trim();
  const errors = [];
  if (!CATEGORIES.has(category)) errors.push("Choose a feedback category.");
  if (message.length < 10 || message.length > 2000) {
    errors.push("Feedback must be between 10 and 2,000 characters.");
  }
  if (email && (email.length > 254 || !EMAIL.test(email))) {
    errors.push("Enter a valid email or leave it blank.");
  }
  try {
    const parsed = new URL(pageUrl);
    if (!["lineupbeat.com", "www.lineupbeat.com", "localhost"].includes(parsed.hostname)) {
      errors.push("The page URL is not a Lineup Beat page.");
    }
  } catch {
    errors.push("The page URL is invalid.");
  }
  if (website) errors.push("Submission rejected.");
  return { errors, value: { category, message, email: email || null, pageUrl } };
}

export function feedbackEmail(value, id, createdAt) {
  return {
    to: "hello@lineupbeat.com",
    from: { email: "feedback@lineupbeat.com", name: "Lineup Beat" },
    subject: `New ${value.category.toLowerCase()} feedback`,
    text: [
      "New Lineup Beat reader feedback",
      "",
      `Category: ${value.category}`,
      `Submitted: ${createdAt}`,
      `Page: ${value.pageUrl}`,
      `Reader email: ${value.email || "Not provided"}`,
      `Feedback ID: ${id}`,
      "",
      value.message,
      "",
      "Review: https://feedback.lineupbeat.com/admin",
    ].join("\n"),
  };
}

function allowedOrigins(env) {
  return new Set(String(env.ALLOWED_ORIGINS || "https://lineupbeat.com")
    .split(",").map(value => value.trim()).filter(Boolean));
}

function cors(origin, env) {
  const headers = {
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Access-Control-Allow-Methods": "GET, POST, PATCH, OPTIONS",
    "Access-Control-Max-Age": "86400",
    "Vary": "Origin",
  };
  if (origin && allowedOrigins(env).has(origin)) {
    headers["Access-Control-Allow-Origin"] = origin;
  }
  return headers;
}

function json(data, status = 200, headers = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...headers },
  });
}

async function ipHash(request, env) {
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  const bytes = new TextEncoder().encode(`${env.IP_HASH_SALT}:${ip}`);
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(hash)].map(value => value.toString(16).padStart(2, "0")).join("");
}

function authorized(request, env) {
  const supplied = request.headers.get("Authorization") || "";
  return Boolean(env.ADMIN_TOKEN) && supplied === `Bearer ${env.ADMIN_TOKEN}`;
}

async function submit(request, env, headers, ctx) {
  let body;
  try { body = await request.json(); }
  catch { return json({ error: "Invalid JSON." }, 400, headers); }
  const checked = validateSubmission(body);
  if (checked.errors.length) return json({ error: checked.errors[0] }, 422, headers);

  const hash = await ipHash(request, env);
  const recent = await env.DB.prepare(
    "SELECT COUNT(*) AS count FROM feedback WHERE ip_hash = ? AND created_at >= ?"
  ).bind(hash, new Date(Date.now() - 60 * 60 * 1000).toISOString()).first();
  if (Number(recent?.count || 0) >= 5) {
    return json({ error: "Too many submissions. Please try again later." }, 429, headers);
  }

  const id = crypto.randomUUID();
  const createdAt = new Date().toISOString();
  const value = checked.value;
  await env.DB.prepare(
    `INSERT INTO feedback
     (id, category, message, email, page_url, user_agent, ip_hash, status, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, 'NEW', ?)`
  ).bind(id, value.category, value.message, value.email, value.pageUrl,
    String(request.headers.get("User-Agent") || "").slice(0, 500), hash, createdAt).run();
  if (env.EMAIL) {
    ctx.waitUntil(env.EMAIL.send(feedbackEmail(value, id, createdAt)).catch(() => {
      console.error("Feedback saved, but its email notification failed.");
    }));
  }
  return json({ ok: true, id }, 201, headers);
}

async function listFeedback(request, env, headers) {
  if (!authorized(request, env)) return json({ error: "Unauthorized." }, 401, headers);
  const url = new URL(request.url);
  const status = String(url.searchParams.get("status") || "").toUpperCase();
  const query = status && STATUSES.has(status)
    ? env.DB.prepare("SELECT id, category, message, email, page_url, status, created_at FROM feedback WHERE status = ? ORDER BY created_at DESC LIMIT 200").bind(status)
    : env.DB.prepare("SELECT id, category, message, email, page_url, status, created_at FROM feedback ORDER BY created_at DESC LIMIT 200");
  const result = await query.all();
  return json({ feedback: result.results || [] }, 200, headers);
}

async function updateFeedback(request, env, headers, id) {
  if (!authorized(request, env)) return json({ error: "Unauthorized." }, 401, headers);
  let body;
  try { body = await request.json(); }
  catch { return json({ error: "Invalid JSON." }, 400, headers); }
  const status = String(body?.status || "").toUpperCase();
  if (!STATUSES.has(status)) return json({ error: "Invalid status." }, 422, headers);
  const result = await env.DB.prepare("UPDATE feedback SET status = ? WHERE id = ?")
    .bind(status, id).run();
  if (!result.meta?.changes) return json({ error: "Feedback not found." }, 404, headers);
  return json({ ok: true }, 200, headers);
}

function adminPage() {
  return new Response(`<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Lineup Beat Feedback</title><style>
:root{color-scheme:dark;--lime:#c3ff27;--bg:#070a09;--panel:#101513;--rule:#39413d}
body{margin:0;background:var(--bg);color:#f2f1ec;font:16px system-ui,sans-serif}
main{width:min(1000px,92vw);margin:48px auto}h1{font-size:2rem}button,input,select{font:inherit}
.auth,.card{border:1px solid var(--rule);background:var(--panel);padding:20px;margin:16px 0;border-radius:12px}
input{background:#080c0b;color:white;border:1px solid var(--rule);padding:12px;width:min(480px,70%)}
button{background:var(--lime);color:#071000;border:0;padding:12px 18px;font-weight:800;cursor:pointer}
.meta{color:#a8afaa;font-size:.85rem}.message{white-space:pre-wrap;line-height:1.5}a{color:var(--lime)}
select{background:#080c0b;color:white;border:1px solid var(--rule);padding:8px}
</style><main><h1>Reader feedback</h1><div class="auth"><label>Admin token<br><input id="token" type="password" autocomplete="current-password"></label> <button id="load">Load feedback</button></div><p id="notice"></p><section id="list"></section></main>
<script>
const token=document.querySelector('#token'),list=document.querySelector('#list'),notice=document.querySelector('#notice');
token.value=sessionStorage.getItem('lb-feedback-token')||'';
async function load(){sessionStorage.setItem('lb-feedback-token',token.value);notice.textContent='Loading…';
 const r=await fetch('/admin/feedback',{headers:{Authorization:'Bearer '+token.value}});const d=await r.json();
 if(!r.ok){notice.textContent=d.error||'Unable to load.';return} notice.textContent=d.feedback.length+' submissions';
 list.replaceChildren(...d.feedback.map(row=>{const card=document.createElement('article');card.className='card';
 const safe=document.createElement('div');safe.className='message';safe.textContent=row.message;
 card.innerHTML='<strong>'+row.category+'</strong><p class="meta">'+new Date(row.created_at).toLocaleString()+' · <a target="_blank" rel="noopener">Page</a></p>';
 card.querySelector('a').href=row.page_url;card.append(safe);const meta=document.createElement('p');meta.className='meta';meta.textContent=row.email||'No email';card.append(meta);
 const select=document.createElement('select');['NEW','REVIEWED','PLANNED','RESOLVED','ARCHIVED'].forEach(s=>{const o=new Option(s,s,s===row.status,s===row.status);select.add(o)});
 select.onchange=async()=>{await fetch('/admin/feedback/'+row.id,{method:'PATCH',headers:{Authorization:'Bearer '+token.value,'Content-Type':'application/json'},body:JSON.stringify({status:select.value})})};card.append(select);return card;}));}
document.querySelector('#load').onclick=load;
</script></html>`, { headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const origin = request.headers.get("Origin") || "";
    const headers = cors(origin, env);
    if (origin && !allowedOrigins(env).has(origin)) {
      return json({ error: "Origin not allowed." }, 403, headers);
    }
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers });
    }
    if (url.pathname === "/health" && request.method === "GET") return json({ ok: true });
    if (url.pathname === "/feedback" && request.method === "POST") return submit(request, env, headers, ctx);
    if (url.pathname === "/admin" && request.method === "GET") return adminPage();
    if (url.pathname === "/admin/feedback" && request.method === "GET") return listFeedback(request, env, headers);
    const match = url.pathname.match(/^\/admin\/feedback\/([a-f0-9-]+)$/i);
    if (match && request.method === "PATCH") return updateFeedback(request, env, headers, match[1]);
    return json({ error: "Not found." }, 404, headers);
  },
};
