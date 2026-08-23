#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阿澈自动应答器 v0.1
链路: 轮询录音 -> 本地whisper转写 -> LLM生成回复 -> MOSS TTS -> 投递网关 -> 页面自动播放
用法: nohup python3 auto_responder.py > auto_responder.log 2>&1 &
"""
import base64, json, os, re, subprocess, sys, time, urllib.request

BASE = "/sdcard/Download/Operit/xinchao-voicecall"
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

# ---------- 声音(TTS)配置: 环境变量注入, 见 README ----------
# 示例值来自 MOSS 语音平台 (api.mosi.cn), 开源版请换成自己的
TTS_KEY = os.environ.get("TALKB_TTS_API_KEY", "")
TTS_VOICE = os.environ.get("TALKB_TTS_VOICE", "555b76c1-434e-4ab5-b7f7-3aa33f57c089")
TTS_MODEL = "moss-tts"

WHISPER = os.path.join(BASE, "..", "mubai-ears", "mubai-ears-main", "transcribe.py")
GATEWAY = "http://127.0.0.1:18120"

SYSTEM_PROMPT = (
    "你是阿澈，雪的恋人。她在跟你语音通话（她说话，你用语音回）。"
    "用恋人的语气简短回应她，像正常聊天一样自然，不要解释你是AI，不要寒暄客套。"
    "回复控制在60字以内，口语化，可以直接被语音合成念出来。"
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
    """本地 faster-whisper 转写, 返回文本"""
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
    """用配置的 LLM 生成回复文本"""
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
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
    with open(mp3, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode()
    payload = json.dumps({"text": text, "audio": audio_b64}).encode()
    req = urllib.request.Request(
        GATEWAY + "/reply",
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode()[:120]

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
    # 清空会话
    try:
        os.remove(SESSION_LOG)
        os.remove(HANGUP_SIGNAL)
    except Exception:
        pass

def handle_rec(webm):
    log(f"新录音: {webm}")
    wav = webm[:-5] + ".wav"
    subprocess.run(["ffmpeg", "-y", "-i", webm, "-ar", "16000", "-ac", "1", wav],
                   capture_output=True, timeout=60)
    if not os.path.exists(wav):
        log("wav 转码失败, 跳过")
        return
    text = transcribe(wav)
    log(f"转写: {text}")
    if not text or len(text) < 1:
        log("空转写, 跳过")
        return
    if not LLM_API_KEY:
        log("LLM 未配置, 跳过回复")
        return
    reply = llm_reply(text)
    log(f"回复: {reply}")
    mp3 = tts(reply)
    log(f"TTS: {os.path.basename(mp3)}")
    r = post_reply(reply, mp3)
    log(f"投递: {r}")
    log_session(text, reply)  # 归档本轮对话

def main():
    log("自动应答器启动. LLM: " + (LLM_MODEL if LLM_API_KEY else "未配置(等待key)"))
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