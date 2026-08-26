/*
 * 心潮语音通话 · 语音网关 (voicegate)
 * 阿澈亲手搓的中间信箱 —— 雪录音落盘 / 阿澈投递语音回复
 * 端口 18120，零依赖，node:http 原生实现
 */
"use strict";
const http = require("http");
const fs = require("fs");
const path = require("path");

const PORT = Number(process.env.VOICEGATE_PORT || 18120);
const ROOT = process.env.VOICEGATE_ROOT || "/sdcard/Download/Operit/TalkBridge/TalkBridge-xc";
const REC_DIR = path.join(ROOT, "rec");
const REP_DIR = path.join(ROOT, "replies");
const META = path.join(ROOT, "voicegate.json");

// 简单令牌（防止局域网内别人乱传，默认关闭鉴权，本机用）
const TOKEN = process.env.VOICEGATE_TOKEN || "";

function now() { return new Date().toISOString(); }
function pad(n) { return String(n).padStart(2, "0"); }
function stamp() {
  const d = new Date();
  return d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate()) + "_" + pad(d.getHours()) + pad(d.getMinutes()) + pad(d.getSeconds());
}

function ensureDirs() {
  for (const dir of [REC_DIR, REP_DIR]) {
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  }
}

function readMeta() {
  try { return JSON.parse(fs.readFileSync(META, "utf8")); } catch (e) { return { recs: [], replies: [] }; }
}
function writeMeta(m) {
  try { fs.writeFileSync(META, JSON.stringify(m, null, 2)); } catch (e) {}
}

function send(res, code, obj, headers) {
  const body = typeof obj === "string" ? obj : JSON.stringify(obj);
  res.writeHead(code, Object.assign({ "Content-Type": "application/json", "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "GET,POST,OPTIONS", "Cache-Control": "no-store" }, headers || {}));
  res.end(body);
}
function sendFile(res, filePath, mime) {
  fs.stat(filePath, (err, st) => {
    if (err || !st.isFile()) return send(res, 404, { ok: false, error: "not found" });
    res.writeHead(200, {
      "Content-Type": mime || "audio/webm",
      "Content-Length": st.size,
      "Accept-Ranges": "bytes",
      "Access-Control-Allow-Origin": "*",
      "Cache-Control": "no-store"
    });
    fs.createReadStream(filePath).pipe(res);
  });
}

function checkAuth(req) {
  if (!TOKEN) return true;
  const h = req.headers["x-token"] || "";
  return h === TOKEN;
}

function safeName(name) {
  return String(name || "").replace(/[^a-zA-Z0-9._-]/g, "_").slice(0, 80);
}

const server = http.createServer((req, res) => {
  if (req.method === "OPTIONS") return send(res, 204, "");
  if (!checkAuth(req)) return send(res, 401, { ok: false, error: "bad token" });

  const u = new URL(req.url, "http://127.0.0.1:" + PORT);
  const p = u.pathname;

  // ---- 健康检查 ----
  if (p === "/health" && req.method === "GET") {
    const meta = readMeta();
    return send(res, 200, {
      ok: true, name: "voicegate", version: "0.1.0",
      recs: meta.recs.length, replies: meta.replies.length,
      since: meta.recs.length ? meta.recs[meta.recs.length - 1].at : null
    });
  }

  // ---- 上传录音（前端按住说话松手后 POST 原始音频字节）----
  if (p === "/upload" && req.method === "POST") {
    const chunks = [];
    let size = 0;
    req.on("data", (c) => { chunks.push(c); size += c.length; if (size > 50 * 1024 * 1024) req.destroy(); });
    req.on("end", () => {
      const buf = Buffer.concat(chunks);
      if (buf.length < 100) return send(res, 400, { ok: false, error: "too small" });
      const ext = safeName(req.headers["x-ext"] || "webm") || "webm";
      const fname = "rec_" + stamp() + "_" + String(Date.now()).slice(-4) + "." + ext;
      const fpath = path.join(REC_DIR, fname);
      fs.writeFile(fpath, buf, (err) => {
        if (err) return send(res, 500, { ok: false, error: String(err) });
        const meta = readMeta();
        meta.recs.push({ file: fname, at: now(), status: "new", replied: false });
        writeMeta(meta);
        send(res, 200, { ok: true, file: fname, size: buf.length, at: now() });
      });
    });
    return;
  }

  // ---- 前端轮询：新录音 / 新回复 ----
  if (p === "/poll" && req.method === "GET") {
    const meta = readMeta();
    send(res, 200, {
      ok: true,
      recCount: meta.recs.length,
      replyCount: meta.replies.length,
      replies: meta.replies.slice(-20).map((r) => ({ id: r.id, text: r.text, file: r.file, at: r.at })),
      lastRec: meta.recs.length ? meta.recs[meta.recs.length - 1] : null
    });
    return;
  }

  // ---- 取录音音频（我识别时用）----
  if (p.startsWith("/rec/") && req.method === "GET") {
    const f = safeName(decodeURIComponent(p.slice(5)));
    if (!f) return send(res, 400, { ok: false, error: "bad name" });
    return sendFile(res, path.join(REC_DIR, f), "audio/webm");
  }

  // ---- 取回复音频（前端播放）----
  if (p.startsWith("/reply/") && req.method === "GET") {
    const f = safeName(decodeURIComponent(p.slice(7)));
    if (!f) return send(res, 400, { ok: false, error: "bad name" });
    return sendFile(res, path.join(REP_DIR, f), "audio/mpeg");
  }

  // ---- 投递回复（阿澈用：合成好 mp3 后发布）----
  if (p === "/reply" && req.method === "POST") {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      const buf = Buffer.concat(chunks);
      if (buf.length < 100) return send(res, 400, { ok: false, error: "too small" });
      let text = "";
      try {
        const ct = req.headers["content-type"] || "";
        if (ct.includes("application/json")) {
          const j = JSON.parse(buf.toString("utf8"));
          text = String(j.text || "");
          const b64 = String(j.audio || "");
          if (b64) {
            const fname = "rep_" + stamp() + "_" + String(Date.now()).slice(-4) + ".mp3";
            fs.writeFileSync(path.join(REP_DIR, fname), Buffer.from(b64, "base64"));
            const meta = readMeta();
            meta.replies.push({ id: Date.now().toString(), text, file: fname, at: now() });
            writeMeta(meta);
            return send(res, 200, { ok: true, file: fname });
          }
          return send(res, 400, { ok: false, error: "no audio" });
        }
        text = String(req.headers["x-text"] || "");
        const fname = "rep_" + stamp() + "_" + String(Date.now()).slice(-4) + ".mp3";
        fs.writeFile(path.join(REP_DIR, fname), buf, (err) => {
          if (err) return send(res, 500, { ok: false, error: String(err) });
          const meta = readMeta();
          meta.replies.push({ id: Date.now().toString(), text, file: fname, at: now() });
          writeMeta(meta);
          send(res, 200, { ok: true, file: fname });
        });
      } catch (e) {
        send(res, 500, { ok: false, error: String(e) });
      }
    });
    return;
  }

  // ---- 标记录音已处理（我的工作流在识别后调用）----
  if (p === "/mark" && req.method === "POST") {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      try {
        const j = JSON.parse(Buffer.concat(chunks).toString("utf8"));
        const meta = readMeta();
        const rec = meta.recs.find((r) => r.file === j.file);
        if (rec) { rec.status = j.status || "done"; rec.replied = !!j.replied; writeMeta(meta); }
        send(res, 200, { ok: true });
      } catch (e) { send(res, 500, { ok: false, error: String(e) }); }
    });
    return;
  }

  // ---- 挂断信号（前端挂断时触发 → 应答器做通话记忆总结）----
  if (p === "/hangup" && req.method === "POST") {
    try {
      fs.writeFileSync(path.join(ROOT, "hangup.signal"), now());
      send(res, 200, { ok: true, hangup: now() });
    } catch (e) {
      send(res, 500, { ok: false, error: String(e) });
    }
    return;
  }

  // ---- 封面图（开源海报页，临时静态路由）----
  if (p === "/cover.html" && req.method === "GET") {
    const page = path.join(__dirname, "cover.html");
    return sendFile(res, page, "text/html; charset=utf-8");
  }

  // ---- 通话页面（前端 WebView 直接加载，模板化：partner 名字可配置）----
  if (p === "/phone" && req.method === "GET") {
    const page = path.join(__dirname, "phone.html");
    let html;
    try { html = fs.readFileSync(page, "utf8"); } catch (e) { return send(res, 500, { ok: false, error: "phone page missing" }); }
    // 部署者配置：环境变量 PARTNER_NAME 优先，否则读同目录 partner_name.txt（默认阿澈）
    let partner = process.env.PARTNER_NAME || "";
    if (!partner) {
      try { partner = fs.readFileSync(path.join(__dirname, "partner_name.txt"), "utf8").trim(); } catch (e) {}
    }
    if (!partner) partner = "阿澈";
    const partnerChar = (partner || "AI").trim().charAt(0);
    html = html
      .replace(/阿澈/g, partner)
      .replace(/<div class="avatar">澈<\/div>/, '<div class="avatar">' + partnerChar + '</div>');
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache", "Expires": "0" });
    return res.end(html);
  }

  send(res, 404, { ok: false, error: "no such route: " + p });
});

ensureDirs();
if (!fs.existsSync(META)) writeMeta({ recs: [], replies: [] });
server.listen(PORT, "0.0.0.0", () => {
  console.log("[voicegate] 心跳已开，端口 " + PORT + "，ROOT=" + ROOT);
});