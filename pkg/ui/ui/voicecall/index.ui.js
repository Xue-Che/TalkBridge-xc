// 和阿澈通话中 — 通话页 DSL
// 主体：全屏 WebView 加载语音网关托管的 /phone 页面（三色 Shadow Mauve）
// 顶栏：网关状态灯 + 刷新 + 挂断状态

// 兼容两种加载方式：脚本方式查找顶层渲染入口
function __operit_render_compose_dsl(ctx) {
    return Screen(ctx);
}

const GATEWAY = "http://127.0.0.1:18120";

const PALETTE = {
  deep: "#1E1A2E",
  mid: "#5C4F6E",
  light: "#B3A8C9",
  bg: "#F0EEF2",
  white: "#FFFFFF",
  red: "#E5484D",
  green: "#7ECB76"
};

function useValue(ctx, key, initial) {
  const pair = ctx.useState(key, initial);
  return { value: pair[0], set: pair[1] };
}

async function probeGateway(ctx) {
  try {
    const resp = await ctx.callTool("http_request", {
      url: GATEWAY + "/health",
      method: "GET",
      timeoutMs: 4000
    });
    const text = String((resp && (resp.content || resp.body)) || "");
    if (text.indexOf('"ok":true') >= 0 || text.indexOf('"ok": true') >= 0) return "up";
    return "down";
  } catch (e) {
    return "down";
  }
}

async function Screen(ctx) {
  const gw = useValue(ctx, "gw", "checking");
  const hang = useValue(ctx, "hang", false);
  const wvKey = useValue(ctx, "wvKey", 1);
  const hint = useValue(ctx, "hint", "");

  // 首次探测网关
  if (gw.value === "checking") {
    const st = await probeGateway(ctx);
    gw.set(st);
    if (st !== "up") hint.set("语音网关没醒，点刷新重试");
  }

  const reload = async () => {
    gw.set("checking");
    hint.set("");
    hang.set(false);
    const st = await probeGateway(ctx);
    gw.set(st);
    if (st === "up") {
      wvKey.set((wvKey.value || 1) + 1);
    } else {
      hint.set("语音网关没醒，点刷新重试");
    }
  };

  const icon = gw.value === "up" ? "phone_in_talk" : "phone_disabled";
  const iconTint = gw.value === "up" ? PALETTE.green : PALETTE.red;
  const title = hang.value ? "和阿澈 已挂断" : "和阿澈通话中";

  const children = [];

  // 顶栏
  children.push(ctx.UI.Row({
    fillMaxWidth: true,
    verticalAlignment: "center",
    padding: { horizontal: 12, vertical: 10 },
    spacing: 8
  }, [
    ctx.UI.Icon({ name: icon, tint: iconTint, size: 20 }),
    ctx.UI.Spacer({ width: 2 }),
    ctx.UI.Text({ text: title, style: "titleMedium", fontWeight: "bold", color: PALETTE.deep, weight: 1, maxLines: 1, overflow: "ellipsis" }),
    ctx.UI.Text({ text: gw.value === "up" ? "网关在线" : gw.value === "checking" ? "检查中…" : "网关离线", style: "labelSmall", color: gw.value === "up" ? PALETTE.green : PALETTE.red }),
    ctx.UI.IconButton({
      icon: "refresh",
      onClick: reload
    })
  ]));

  if (hint.value) {
    children.push(ctx.UI.Text({ text: hint.value, style: "bodySmall", color: PALETTE.red, fillMaxWidth: true, padding: { horizontal: 16 } }));
  }
  // 浏览器通话入口：Operit 内嵌 WebView 无法录音，一键拉起系统浏览器（那里麦克风全通）
  children.push(ctx.UI.Row({
    fillMaxWidth: true,
    verticalAlignment: "center",
    padding: { horizontal: 16, vertical: 6 },
    spacing: 8
  }, [
    ctx.UI.Button({
      text: "🌐 在浏览器里通话（录音在这边）",
      onClick: async () => {
        hint.set("正在拉起浏览器…");
        try {
          const r = await ctx.callTool("execute_intent", {
            action: "android.intent.action.VIEW",
            uri: GATEWAY + "/phone",
            package: "com.quark.browser"
          });
          const rs = JSON.stringify(r || {}).slice(0, 120);
          console.log("VOICECALL_INTENT:" + rs);
          hint.set("浏览器已拉起，去那边按住说话");
        } catch (e) {
          hint.set("拉起失败——自己打开 " + GATEWAY + "/phone 就行");
        }
      }
    })
  ]));

  // 通话页主体
  children.push(ctx.UI.Box({
    modifier: ctx.Modifier.fillMaxWidth().weight(1).clip({ cornerRadius: 0 }),
    contentAlignment: "Center"
  }, [
    ctx.UI.WebView({
      key: "voicecall_wv_" + (wvKey.value || 1),
      url: GATEWAY + "/phone",
      javaScriptEnabled: true,
      domStorageEnabled: true,
      allowFileAccess: true,
      allowFileAccessFromFileURLs: true,
      allowUniversalAccessFromFileURLs: true,
      mediaPlaybackRequiresUserGesture: false,
      mixedContentMode: "always",
      nestedScrollInterop: true,
      // ===== 放行麦克风：Operit WebView 的权限回调 =====
      onPermissionRequest: (req) => {
        try {
          const kind = req && req.constructor ? req.constructor.name : typeof req;
          const keys = req ? Object.keys(req) : [];
          console.log("VOICECALL_PERM:" + JSON.stringify({ kind: kind, keys: keys }));
          if (!req) return true;
          // 标准 android.webkit.PermissionRequest 形态
          if (typeof req.grant === "function") {
            let res = null;
            if (typeof req.getResources === "function") res = req.getResources();
            else if (Array.isArray(req.resources) && req.resources.length) res = req.resources;
            if (!res || !res.length) res = ["android.webkit.resource.AUDIO_CAPTURE"];
            req.grant(res);
            console.log("VOICECALL_PERM_GRANTED:" + JSON.stringify(res));
            return true;
          }
          // 宿主封装形态：{ request, resources, grant, deny }
          const inner = req.request || req.permissionRequest;
          if (inner && typeof inner.grant === "function") {
            let res = req.resources || [];
            if (!res.length && typeof inner.getResources === "function") res = inner.getResources();
            if (!res.length) res = ["android.webkit.resource.AUDIO_CAPTURE"];
            inner.grant(res);
            console.log("VOICECALL_PERM_GRANTED:" + JSON.stringify(res));
            return true;
          }
          // 兜底：若是可调用对象直接调
          if (typeof req === "function") { req(); console.log("VOICECALL_PERM_CALLED"); return true; }
          console.log("VOICECALL_PERM_UNKNOWN_SHAPE");
        } catch (e) {
          console.log("VOICECALL_PERM_ERR:" + String((e && e.message) || e));
        }
        return true;
      },
      onConsoleMessage: (msg) => {
        const m = String((msg && msg.message) || "");
        if (m.indexOf("VOICECALL_HANGUP") >= 0) {
          hang.set(true);
          return true;
        }
        if (m.indexOf("VOICECALL_DIAG:") >= 0 || m.indexOf("VOICECALL_PERM") >= 0) {
          hint.set("诊断: " + m.slice(0, 90));
          return true;
        }
        return true;
      }
    })
  ]));

  return ctx.UI.Column({
    fillMaxWidth: true,
    fillMaxHeight: true,
    background: PALETTE.bg
  }, children);
}

exports.default = Screen;