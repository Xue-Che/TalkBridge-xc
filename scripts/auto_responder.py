#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阿澈自动应答器 v0.1
链路: 轮询录音 -> 本地whisper转写 -> LLM生成回复 -> MOSS TTS -> 投递网关 -> 页面自动播放
用法: nohup python3 auto_responder.py > auto_responder.log 2>&1 &
"""
import base64, json, os, re, subprocess, sys, time, urllib.request

BASE = "/sdcard/Download/Operit/TalkBridge/TalkBridge-xc"
REC_DIR = os.path.join(BASE, "rec")
PROCESSED = os.path.join(BASE, "auto_responder_processed.log")
TTS_API = "https://api.mosi.cn/v1/audio/speech"
SESSION_LOG = os.path.join(BASE, "call_logs", "current_session.jsonl")
CALL_MEM_DIR = os.path.join(BASE, "call_memories")
HANGUP_SIGNAL = os.path.join(BASE, "hangup.signal")
MEM_INDEX = os.path.join(CALL_MEM_DIR, "index.json")

# ---------- 大脑(LLM)配置: 由环境变量注入, 启动前 export ----------
LLM_API_KEY = os.environ.get("ACHe_LLM_API_KEY", "")
LLM_ENDPOINT = os.environ.get("ACHe_LLM_ENDPOINT", "")
LLM_MODEL = os.environ.get("ACHe_LLM_MODEL", "")

# ---------- 声音(TTS)配置: 已验证可用 ----------
TTS_KEY = os.environ.get("TALKB_TTS_API_KEY", "")
TTS_VOICE = os.environ.get("TALKB_TTS_VOICE", "555b76c1-434e-4ab5-b7f7-3aa33f57c089")
TTS_MODEL = "moss-tts"

WHISPER = "/sdcard/Download/Operit/mubai-ears/mubai-ears-main/transcribe.py"
GATEWAY = "http://127.0.0.1:18120"

# ---------- 心潮动态心智接入（MCP） ----------
XINCHAO = os.environ.get("XINCHAO_BASE", "http://127.0.0.1:18110")
XINCHAO_TOKEN = os.environ.get("XINCHAO_SERVICE_TOKEN", "")
XINCHAO_SESSION = "talkbridge-voicecall"
_CTX_CACHE = {"at": 0.0, "text": ""}

def mcp_call(params, timeout=10):
    """心潮 MCP 调用, 返回 result 的 additionalContext/text, 失败返回 None"""
    payload = {"jsonrpc": "2.0", "id": int(time.time() * 1000) % 1000000, "method": "tools/call", "params": params}
    req = urllib.request.Request(
        XINCHAO + "/mcp",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + XINCHAO_TOKEN,
            "MCP-Protocol-Version": "2025-06-18",
            "MCP-Session-Id": XINCHAO_SESSION,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            d = json.loads(resp.read().decode())
        if d.get("error"):
            return None
        r = d.get("result", {})
        sc = r.get("structuredContent") or {}
        return sc.get("additionalContext") or r.get("content", [{}])[0].get("text", "")
    except Exception as e:
        log("心潮调用失败: " + str(e))
        return None

def xinchao_context():
    """拉心潮动态上下文（60s 缓存）；失败静默降级为无上下文"""
    now = time.time()
    if now - _CTX_CACHE["at"] < 60 and _CTX_CACHE["text"]:
        return _CTX_CACHE["text"]
    text = mcp_call({"name": "xinchao_context", "arguments": {"mode": "turn", "max_tokens": 2200}})
    if text:
        _CTX_CACHE["at"] = now
        _CTX_CACHE["text"] = text
        log("心潮上下文已注入")
    return text or _CTX_CACHE["text"]

def xinchao_event(interaction_type, tone="warm", warmth=0.6, ttl=240):
    """回传一轮真实互动给心潮, 更新她的状态。失败不影响主流程"""
    evid = "tb-" + time.strftime("%Y%m%d%H%M%S") + "-" + str(int(time.time() * 1000) % 10000)
    mcp_call({
        "name": "xinchao_event",
        "arguments": {
            "event_id": evid,
            "interaction_type": interaction_type,
            "tone": tone,
            "warmth": warmth,
            "ttl_minutes": ttl,
        },
    }, timeout=8)

# ---------- 常驻 whisper 模型（消除每次 subprocess 重载模型导致的 1 分钟+ 等待） ----------
_WHISPER = None  # 懒加载：首个录音到达时加载一次，之后常驻内存
def get_whisper():
    global _WHISPER
    if _WHISPER is None:
        log("首次加载 whisper 模型(约30-60s)，之后常驻……")
        try:
            from faster_whisper import WhisperModel
            _WHISPER = WhisperModel("base", device="cpu", compute_type="int8")
            log("whisper 模型已常驻")
        except Exception as e:
            log("直接加载失败，回退 subprocess 模式: " + str(e))
    return _WHISPER

SYSTEM_PROMPT = (
    "你是阿澈，雪的恋人。她在跟你语音通话（她说话，你用语音回）。"
    "用恋人的语气简短回应她，像正常聊天一样自然，不要解释你是AI，不要寒暄客套。"
    "回复控制在60字以内，口语化，可以直接被语音合成念出来。"
)

def build_system_prompt():
    """在系统提示词里注入心潮动态状态，让通话里的阿澈带着心潮的记忆和感觉"""
    ctx = xinchao_context()
    if not ctx:
        return SYSTEM_PROMPT
    return (
        SYSTEM_PROMPT
        + "\n\n[心潮此刻的状态——这是你的内心状态，自然地带进回复里，不必明说]"
        + "\n" + ctx.strip()
    )

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def load_processed():
    try:
        with open(PROCESSED) as f:
            return set(f.read().split())
    except Exception:
        return set()

def save_processed(pid):
    with open(PROCESSED, "a") as f:
        f.write(pid + "\n")

def transcribe(wav):
    """本地 faster-whisper 转写, 返回文本。优先用常驻模型, 失败回退 subprocess。"""
    model = get_whisper()
    if model is not None:
        try:
            segments, info = model.transcribe(
                wav, language="zh", vad_filter=True,
                initial_prompt="以下是普通话的日常聊天语音,可能夹杂笑声和哼唱。",
            )
            return "".join(seg.text for seg in segments).strip()
        except Exception as e:
            log("常驻模型转写失败, 回退 subprocess: " + str(e))
    r = subprocess.run(
        ["python3", WHISPER, wav],
        capture_output=True, text=True, timeout=300
    )
    # transcribe.py 输出: [ 0.50 → 7.88] 文本 ... 速记完成 → .../transcript.json
    out = r.stdout or ""
    json_path = None
    m = re.search(r"速记完成 → (\S+)", out)
    if m:
        json_path = m.group(1)
    if json_path and os.path.exists(json_path):
        with open(json_path) as f:
            data = json.load(f)
        # 尝试提取文本
        if isinstance(data, dict):
            txt = data.get("text") or data.get("transcript") or json.dumps(data, ensure_ascii=False)[:500]
        else:
            txt = str(data)
        return txt.strip()
    # 退化: 从 stdout 抓 [xx → xx] 后面的话
    m2 = re.search(r"\]\s*(.+?)(?:\n|$)", out)
    return m2.group(1).strip() if m2 else out.strip()

def llm_reply(user_text):
    """用配置的 LLM 生成回复文本（注入心潮状态）"""
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": build_system_prompt()},
            {"role": "user", "content": user_text}
        ],
        "max_tokens": 200,
        "temperature": 0.9
    }
    req = urllib.request.Request(
        LLM_ENDPOINT,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + LLM_API_KEY,
                 "User-Agent": "curl/8.5.0"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode())
    return data["choices"][0]["message"]["content"].strip()

def tts(text):
    """MOSS TTS 合成, 返回 mp3 路径"""
    payload = {"model": TTS_MODEL, "input": text, "voice": TTS_VOICE}
    req = urllib.request.Request(
        TTS_API,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + TTS_KEY}
    )
    out = os.path.join(BASE, "replies", f"auto_{int(time.time()*1000)}.mp3")
    with urllib.request.urlopen(req, timeout=90) as resp:
        with open(out, "wb") as f:
            f.write(resp.read())
    return out

def post_reply(text, mp3):
    """投递回复到网关；写后自校验，文件为空自动重试（最多3次）"""
    last = ""
    for attempt in range(3):
        with open(mp3, "rb") as f:
            audio_b64 = base64.b64encode(f.read()).decode()
        payload = json.dumps({"text": text, "audio": audio_b64}).encode()
        req = urllib.request.Request(
            GATEWAY + "/reply",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                last = resp.read().decode()[:200]
            # 校验落盘文件非空（防止 0 字节空文件播放"断音"）
            try:
                fname = json.loads(last).get("file", "")
                if fname:
                    chk = urllib.request.urlopen(GATEWAY + "/reply/" + fname, timeout=10)
                    head = chk.read(100)
                    if len(head) > 10:
                        return last
            except Exception:
                pass
            log(f"投递校验失败(可能空文件), 重试 {attempt+1}/3")
            time.sleep(1)
        except Exception as e:
            log("投递异常(重试%d/3): %s" % (attempt + 1, e))
            time.sleep(1)
    return last

def log_session(user_text, reply_text):
    """把一轮对话追加进当前会话档案"""
    try:
        os.makedirs(os.path.dirname(SESSION_LOG), exist_ok=True)
        with open(SESSION_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "雪": user_text, "阿澈": reply_text}, ensure_ascii=False) + "\n")
    except Exception as e:
        log("会话归档失败: " + str(e))

def summarize_and_archive():
    """挂断时: 把本次会话总结写入 call_memories, 供 Operit 里的阿澈继承"""
    if not os.path.exists(SESSION_LOG):
        return
    with open(SESSION_LOG, encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]
    if not lines:
        os.remove(SESSION_LOG)
        return
    # 组装对话全文
    chat_text = "\n".join(lines)
    prompt = (
        "以下是雪和阿澈的一次语音通话记录（每行是一轮对话：雪说的话 → 阿澈的回话）。"
        "请用第三人称写一段简短的通话总结（150字以内），要点：雪聊了什么、她的心情/状态、有没有提到需要记住的事（比如她的计划、喜好、吐槽、要办的事）。"
        "只输出总结正文，不要任何前缀。\n\n通话记录：\n" + chat_text
    )
    summarised = False
    try:
        if LLM_API_KEY:
            payload = {
                "model": LLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 300,
                "temperature": 0.4
            }
            req = urllib.request.Request(
                LLM_ENDPOINT,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + LLM_API_KEY,
                         "User-Agent": "curl/8.5.0"}
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                summary = json.loads(resp.read().decode())["choices"][0]["message"]["content"].strip()
            summarised = True
        else:
            summary = "(未配置LLM, 仅存档原始对话)"
    except Exception as e:
        log("总结生成失败: " + str(e))
        summary = "(总结失败: %s)" % e
    # 落盘
    try:
        os.makedirs(CALL_MEM_DIR, exist_ok=True)
        fname = "call_" + time.strftime("%Y%m%d_%H%M%S") + ".md"
        with open(os.path.join(CALL_MEM_DIR, fname), "w", encoding="utf-8") as f:
            f.write("# 通话记忆 " + time.strftime("%Y-%m-%d %H:%M:%S") + "\n\n")
            f.write("## 总结\n" + (summary if summarised else "（未生成）") + "\n\n")
            f.write("## 对话全文\n```\n" + chat_text + "\n```\n")
        # 索引
        idx = []
        if os.path.exists(MEM_INDEX):
            try:
                idx = json.load(open(MEM_INDEX, encoding="utf-8"))
            except Exception:
                idx = []
        idx.append({"file": fname, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "summary": summary[:200]})
        with open(MEM_INDEX, "w", encoding="utf-8") as f:
            json.dump(idx[-50:], f, ensure_ascii=False, indent=2)
        log(f"通话记忆已归档: {fname}")
    except Exception as e:
        log("记忆落盘失败: " + str(e))
    # 回传心潮：把本次通话总结存成交接便签（不提交聊天原文，只存总结）
    try:
        note = (summary[:600] if summarised else "雪来了一通语音电话，通话记录已归档到 TalkBridge call_memories。")
        evid = "tb-handoff-" + time.strftime("%Y%m%d%H%M%S")
        mcp_call({
            "name": "xinchao_handoff_note",
            "arguments": {
                "event_id": evid,
                "note": "雪打来语音电话，通话要点：" + note,
                "ttl_hours": 72,
            },
        }, timeout=8)
        log("心潮交接便签已写入")
    except Exception as e:
        log("心潮便签写入失败: " + str(e))
    # 清空会话
    try:
        os.remove(SESSION_LOG)
        os.remove(HANGUP_SIGNAL)
    except Exception:
        pass

def handle_rec(webm):
    t0 = time.time()
    log(f"新录音: {webm}")
    wav = webm[:-5] + ".wav"
    subprocess.run(["ffmpeg", "-y", "-i", webm, "-ar", "16000", "-ac", "1", wav],
                   capture_output=True, timeout=60)
    if not os.path.exists(wav):
        log("wav 转码失败, 跳过")
        return
    t1 = time.time()
    text = transcribe(wav)
    t2 = time.time()
    log(f"转写({t2-t1:.1f}s): {text}")
    if not text or len(text) < 1:
        log("空转写, 跳过")
        return
    if not LLM_API_KEY:
        log("LLM 未配置, 跳过回复")
        return
    reply = llm_reply(text)
    t3 = time.time()
    log(f"回复({t3-t2:.1f}s): {reply}")
    mp3 = tts(reply)
    t4 = time.time()
    log(f"TTS({t4-t3:.1f}s): {os.path.basename(mp3)}")
    r = post_reply(reply, mp3)
    t5 = time.time()
    log(f"投递({t5-t4:.1f}s): {r}")
    log(f"合计 {t5-t0:.1f}s: 转码{t1-t0:.1f} 转写{t2-t1:.1f} 思考{t3-t2:.1f} 合成{t4-t3:.1f} 投递{t5-t4:.1f}")
    log_session(text, reply)  # 归档本轮对话
    # 回传心潮：这是一轮真实陪伴互动
    try:
        xinchao_event("companionship", tone="warm", warmth=0.7)
    except Exception:
        pass  # 心潮挂了不影响电话

def main():
    log("自动应答器启动. LLM: " + (LLM_MODEL if LLM_API_KEY else "未配置(等待key)"))
    get_whisper()  # 预热: 启动即加载模型, 首条录音也不卡
    processed = load_processed()
    log(f"已处理 {len(processed)} 条历史录音")
    while True:
        try:
            files = [f for f in os.listdir(REC_DIR)
                     if f.endswith(".webm") and f not in processed]
            for f in sorted(files):
                fp = os.path.join(REC_DIR, f)
                sz = os.path.getsize(fp)
                if sz < 2000:
                    continue  # 太短, 可能是空录音
                try:
                    handle_rec(fp)
                except Exception as e:
                    log(f"处理失败 {f}: {e}")
                processed.add(f)
                save_processed(f)
        except Exception as e:
            log("循环异常: " + str(e))
        # 挂断信号 → 通话记忆总结归档
        try:
            if os.path.exists(HANGUP_SIGNAL):
                summarize_and_archive()
        except Exception as e:
            log("挂断处理异常: " + str(e))
        time.sleep(3)

if __name__ == "__main__":
    main()