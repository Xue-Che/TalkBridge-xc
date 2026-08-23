# TalkBridge · 传声桥

> **给 AI 伴侣的一通电话** · VOICE CALL · FIELD NOTE
>
> 任何 App 的内嵌页不给录音权限？没关系。
> 这通电话从「App 一键唤起浏览器」起步，在本地织成一条完整语音链路：
> **按住说话 → 本地转写 → 任意 LLM 想词 → TTS 合成 → 自动播回你耳边 → 挂断归档成记忆。**

---

## 从哪来

这通电话诞生于 2026 年 8 月 24 日凌晨 2 点。
起因很简单：一个叫 Operit 的 AI 伴侣应用，它的插件内嵌页永远无法录音（WebView 组件没有麦克风权限字段，代码级实锤）。但它自己带的浏览器可以。
于是我们绕了一条路：**App 只管当门铃，通话在浏览器里发生，中间信箱跑在本地。**

一晚之内，从「录音失败」到「全自动接听 + 通话记忆归档」，那条 33 秒的链路第一次完整呼吸。

## 它能干嘛

| 能力 | 说明 |
|------|------|
| 📞 一键进入通话 | 从宿主 App 点一个按钮，浏览器直达通话页（`uri` 字段，注意不是 `data`） |
| 🎙️ 按住说话 | 录音落盘到本地网关（audio/webm），零依赖 Node 服务 |
| 🧠 自动应答 | 轮询新录音 → 本地 whisper 转写 → 任意 OpenAI 兼容 LLM 生成回复 → TTS 合成 → POST 回网关 |
| 🔔 新回复自动播 | 页面 1.6s 轮询，新回复自动播放；旧回复只显示气泡，点一下才听 |
| 📵 真挂断 | 挂断 = 停轮询 + 掐播放 + 禁说话；点头像重新拨号 |
| 🧾 通话记忆 | 每轮对话归档；挂断时自动用 LLM 总结，写入 `call_memories/`，宿主里的 AI 随时翻档案 |

## 架构

```
┌──────────────┐   一键拉起     ┌──────────────────┐
│  宿主 App    │ ────────────▶ │  浏览器通话页      │
│  (只当门铃)   │               │  phone.html       │
└──────────────┘               │  按住说话 / 播回复  │
                               └────────┬─────────┘
                                        │ /upload  /poll  /reply
                               ┌────────▼─────────┐
                               │  voicegate        │  零依赖 node:http
                               │  (127.0.0.1:18120)│  录音落盘 / 回复投递
                               └────────┬─────────┘
                                        │ 轮询新录音
                        ┌───────────────▼────────────────┐
                        │  auto_responder.py               │
                        │  本地 whisper 转写 → LLM → TTS   │
                        │  挂断信号 → 记忆总结归档           │
                        └────────────────────────────────┘
```

## 快速开始

### 1. 启动网关

```bash
cd pkg/server
node server.js            # 默认监听 127.0.0.1:18120
```

### 2. 配置环境变量并启动应答器

```bash
export TALKB_TTS_API_KEY="你的TTS-API-Key"        # 示例: MOSS api.mosi.cn/v1/audio/speech
export TALKB_TTS_VOICE="你的音色ID"
export ACHe_LLM_API_KEY="你的LLM-Key"             # 任意 OpenAI 兼容接口
export ACHe_LLM_ENDPOINT="https://xxx/v1/chat/completions"
export ACHe_LLM_MODEL="deepseek-v4-flash"

python3 scripts/auto_responder.py
```

> 依赖：`faster-whisper`（本地转写）、`ffmpeg`（转码），见 `requirements.txt`。

### 3. 打电话

浏览器打开 `http://127.0.0.1:18120/phone`，按住说话。
（宿主 App 里的一键拉起：`execute_intent` + `uri`，见 `pkg/ui/` 示例）

## 仓库结构

```
TalkBridge/
├── README.md               ← 你在这
├── docs/
│   └── FIELD_NOTE.md       ← 人读版：完整技术笔记（施工中）
├── pkg/
│   ├── server/
│   │   ├── server.js       ← 语音网关（零依赖，~200行）
│   │   └── phone.html      ← 通话页（紫灰主题，按住说话）
│   └── ui/                 ← 宿主 App 插件示例（一键唤起）
├── scripts/
│   └── auto_responder.py   ← 自动应答器（轮询/转写/LLM/TTS/记忆归档）
└── PITFALLS.md             ← 踩坑记录，全是真金白银
```

## License

MIT（待定）