"""
中台语音通话 — 独立服务
/voice 页面：和 Hermes 半双工语音对话
端口 12054（HTTP）/ 12056（HTTPS）
"""

import asyncio
import base64
import io
import json
import logging
import os
import re
import struct
import subprocess
import tempfile
import time
from datetime import datetime

import aiohttp
from aiohttp import web

# ══════════════════════════════════════════
# 配置
# ══════════════════════════════════════════
HOST = "0.0.0.0"
PORT = 12054        # HTTP
HTTPS_PORT = 12056  # HTTPS

CERT_DIR = os.path.expanduser("~/report2db/certs")
SSL_CERT = os.path.join(CERT_DIR, "cert.pem")
SSL_KEY = os.path.join(CERT_DIR, "key.pem")

# ASR — A3B llama.cpp
ASR_URL = os.environ.get("ASR_URL", "http://100.80.121.48:12026/v1/audio/transcriptions")

# TTS — Qwen 8B on LM Studio (Win10)
TTS_URL = os.environ.get("TTS_URL", "http://100.80.121.48:1234/v1/chat/completions")
TTS_KEY = "sk-lm-VO1DtDh1:u7vi9gihLIlBIaWOEt08"
TTS_MODEL = "tts2-emo-qwen3-8b-192k"

# Hermes (DeepSeek)
# Hermes API Server（本地，让我能使用工具和记忆）
HERMES_API_URL = "http://127.0.0.1:8642/v1/chat/completions"
HERMES_API_KEY="voice-mid-bridge-key"

# 历史记录目录
HISTORY_DIR = os.path.expanduser("~/report2db/voice_history")
os.makedirs(HISTORY_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("voice-server")

# ══════════════════════════════════════════
# 聊天管理
# ══════════════════════════════════════════
class VoiceChat:
    def __init__(self):
        self.messages = []  # [{role, content, summary, timestamp}]
        self._load()

    def _load(self):
        today = datetime.now().strftime("%Y-%m-%d")
        path = os.path.join(HISTORY_DIR, f"{today}.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.messages = json.load(f)
            except:
                self.messages = []

    def _save(self):
        today = datetime.now().strftime("%Y-%m-%d")
        path = os.path.join(HISTORY_DIR, f"{today}.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.messages, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning(f"历史记录保存跳过: {e}")

    def add(self, role: str, content: str, summary: str = ""):
        item = {
            "role": role,
            "content": content,
            "summary": summary,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.messages.append(item)
        self._save()
        return item

    def clear(self):
        self.messages = []
        self._save()

    def get_all(self):
        return self.messages


chat = VoiceChat()

async def _send_to_asr(audio_path: str):
    """发送 wav 文件到 A3B llama.cpp ASR，结果同步到聊天记录"""
    async with aiohttp.ClientSession() as session:
        with open(audio_path, "rb") as f:
            form = aiohttp.FormData()
            form.add_field("file", f, filename="audio.wav", content_type="audio/wav")
            form.add_field("model", "whisper-1")
            form.add_field("language", "zh")
            async with session.post(ASR_URL, data=form, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    err_text = await resp.text()
                    return web.json_response({"error": f"ASR 失败: {err_text}"}, status=502)
                result = await resp.json()
                text = result.get("text", "").strip()
                if not text:
                    return web.json_response({"error": "未识别到语音"}, status=400)
                # 同步识别结果到聊天记录，方便联合调试
                chat.add("user", f"[🎤语音] {text}")
                log.info(f"ASR 识别: {text}")
                return web.json_response({"text": text})


# ══════════════════════════════════════════
# ASR：录制音频 → 文字
# ══════════════════════════════════════════
async def api_asr(request):
    """接收音频，检测格式并转码为 wav，调用 A3B llama.cpp ASR"""
    import subprocess as _sp
    try:
        data = await request.read()
        if not data:
            return web.json_response({"error": "无音频数据"}, status=400)

        # 检测文件魔数
        is_webm = data[:4] == b'\x1a\x45\xdf\xa3'
        is_wav = data[:4] == b'RIFF'
        # Rhasspy 来的 wav 直接走快速通道（跳过 ffmpeg 和降噪，Rhasspy 已降噪）
        if is_wav and len(data) < 500000:
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            tmp.write(data)
            p = tmp.name
            tmp.close()
            try:
                return await _send_to_asr(p)
            finally:
                os.unlink(p)

        suffix = ".webm" if is_webm else ".wav"
        tmp_in = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp_in.write(data)
        tmp_in_path = tmp_in.name
        tmp_in.close()

        if not is_wav:
            tmp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            tmp_wav_path = tmp_wav.name
            tmp_wav.close()
            try:
                _sp.run(
                    ["ffmpeg", "-y", "-i", tmp_in_path,
                     "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
                     tmp_wav_path],
                    capture_output=True, timeout=30, check=True
                )
                audio_path = tmp_wav_path
            except _sp.CalledProcessError as e:
                os.unlink(tmp_in_path)
                return web.json_response({"error": f"音频转码失败: {e.stderr.decode()[:200]}"}, status=400)
            finally:
                os.unlink(tmp_in_path)
        else:
            audio_path = tmp_in_path

        # noisereduce 降噪
        try:
            _sp.run(
                ["python3", "-c", f"""
import wave, numpy as np, noisereduce as nr
with wave.open('{audio_path}', 'rb') as w:
    sr = w.getframerate()
    frames = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
reduced = nr.reduce_noise(y=frames, sr=sr, stationary=False, prop_decrease=0.8)
with wave.open('{audio_path}', 'wb') as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
    w.writeframes(reduced.astype(np.int16).tobytes())
"""],
                capture_output=True, timeout=10
            )
        except Exception as _de:
            log.warning(f"降噪跳过: {_de}")

        try:
            return await _send_to_asr(audio_path)
        finally:
            os.unlink(audio_path)

    except asyncio.TimeoutError:
        return web.json_response({"error": "ASR 超时"}, status=504)
    except Exception as e:
        log.error(f"ASR 错误: {e}")
        return web.json_response({"error": str(e)}, status=500)


# ══════════════════════════════════════════
# TTS：文字 → 音频流
# ══════════════════════════════════════════
def _decode_qwen_tts(tokens_text: str) -> bytes | None:
    """
    Qwen TTS 返回的是 audio codec tokens（多行数字）。
    解码为 wav 音频。
    格式：每个 token 是一个 16-bit PCM sample，采样率 192kHz。
    """
    try:
        # 提取所有数字
        numbers = re.findall(r'-?\d+', tokens_text)
        if not numbers:
            return None
        samples = [int(n) for n in numbers]
        # 裁剪到合理范围
        samples = [max(-32768, min(32767, s)) for s in samples]
        # 转 PCM16 wav
        pcm_data = struct.pack('<' + 'h' * len(samples), *samples)

        # 构建 wav
        sample_rate = 24000  # 降采样到 24kHz
        byte_rate = sample_rate * 2
        block_align = 2
        data_size = len(pcm_data)
        header = b'RIFF'
        header += struct.pack('<I', 36 + data_size)
        header += b'WAVE'
        header += b'fmt '
        header += struct.pack('<I', 16)  # chunk size
        header += struct.pack('<H', 1)   # PCM
        header += struct.pack('<H', 1)   # mono
        header += struct.pack('<I', sample_rate)
        header += struct.pack('<I', byte_rate)
        header += struct.pack('<H', block_align)
        header += struct.pack('<H', 16)  # bits per sample
        header += b'data'
        header += struct.pack('<I', data_size)

        return header + pcm_data

    except Exception as e:
        log.error(f"TTS 解码失败: {e}")
        return None


async def api_tts(request):
    """接收文字，调用 Qwen TTS 返回 wav 音频"""
    try:
        body = await request.json()
        text = body.get("text", "").strip()
        if not text:
            return web.json_response({"error": "缺少 text"}, status=400)

        async def _edge_fallback_bytes():
            import edge_tts as _et
            clean = re.sub(r'[\U0001F300-\U0001FAFF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\u2600-\u26FF\u2700-\u27BF\u2000-\u206F\u2E00-\u2E7F\u2B00-\u2BFF\u3000-\u303F]', '', text)
            if not clean.strip():
                return b""
            _com = _et.Communicate(clean, "zh-CN-XiaoxiaoNeural")
            _buf = io.BytesIO()
            async for _chunk in _com.stream():
                if _chunk["type"] == "audio":
                    _buf.write(_chunk["data"])
            return _buf.getvalue()

        # 调用 Qwen TTS
        payload = {
            "model": TTS_MODEL,
            "messages": [{"role": "user", "content": f"<|tts|>{text}"}],
            "max_tokens": 4096,
            "temperature": 0.3,
        }
        headers = {
            "Authorization": f"Bearer {TTS_KEY}",
            "Content-Type": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(TTS_URL, json=payload, headers=headers,
                                        timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        err_text = await resp.text()
                        return web.json_response({"error": f"TTS 失败: {err_text}"}, status=502)

                    result = await resp.json()
                    tokens_text = result.get("choices", [{}])[0].get("message", {}).get("content", "")

                    wav_data = _decode_qwen_tts(tokens_text)
                    if not wav_data:
                        log.warning("Qwen TTS 解码失败，回退 edge-tts")
                        wav_data = await _edge_fallback_bytes()

                    return web.Response(
                        body=wav_data,
                        content_type="audio/wav",
                        headers={"Access-Control-Allow-Origin": "*"}
                    )
            except (aiohttp.ClientConnectorError, aiohttp.ClientOSError, OSError) as e:
                log.warning(f"Qwen TTS 连接失败，回退 edge-tts: {e}")
                wav_data = await _edge_fallback_bytes()
                return web.Response(
                    body=wav_data,
                    content_type="audio/wav",
                    headers={"Access-Control-Allow-Origin": "*"}
                )

    except asyncio.TimeoutError:
        return web.json_response({"error": "TTS 超时"}, status=504)
    except Exception as e:
        log.error(f"TTS 错误: {e}")
        return web.json_response({"error": str(e)}, status=500)


# ══════════════════════════════════════════
# Edge TTS 回退
# ══════════════════════════════════════════
async def api_tts_fallback(request):
    """edge-tts 回退接口"""
    try:
        body = await request.json()
        text = body.get("text", "").strip()
        if not text:
            return web.json_response({"error": "缺少 text"}, status=400)

        import edge_tts
        clean = re.sub(r'[\U0001F300-\U0001FAFF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\u2600-\u26FF\u2700-\u27BF\u2000-\u206F\u2E00-\u2E7F\u2B00-\u2BFF\u3000-\u303F]', '', text)
        if not clean.strip():
            return web.json_response({"error": "空文本"}, status=400)

        communicate = edge_tts.Communicate(clean, "zh-CN-XiaoxiaoNeural")
        audio = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio.write(chunk["data"])

        wav_data = audio.getvalue()
        return web.Response(
            body=wav_data,
            content_type="audio/mpeg",
            headers={"Access-Control-Allow-Origin": "*"}
        )

    except Exception as e:
        log.error(f"Edge TTS 错误: {e}")
        return web.json_response({"error": str(e)}, status=500)


# ══════════════════════════════════════════
# LLM 对话（Hermes Gateway）
# ══════════════════════════════════════════

async def api_chat(request):
    """接收文字，调用 Hermes Gateway 生成回答，返回完整文本"""
    try:
        body = await request.json()
        user_text = body.get("text", "").strip()
        if not user_text:
            return web.json_response({"error": "缺少 text"}, status=400)

        # 构建消息历史（传给 Hermes 让它有上下文）
        system_prompt = "你是 Hermes，运行在 Linux 服务器 bt2 上的 AI 助手。你有完整的工具集（终端、文件操作、代码执行等），可以执行用户的指令。请用中文回答，简洁准确。需要执行操作时直接去做，不需要先请示。"
        messages = [
            {"role": "system", "content": system_prompt},
        ]
        # 添加上下文（最近 5 条）
        for m in chat.get_all()[-5:]:
            role = "assistant" if m["role"] == "assistant" else "user"
            messages.append({"role": role, "content": m["content"]})
        # 当前问题
        messages.append({"role": "user", "content": user_text})

        payload = {
            "model": "deepseek-chat",
            "messages": messages,
            "max_tokens": 4096,
            "temperature": 0.7,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {HERMES_API_KEY}",
            "Content-Type": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(HERMES_API_URL, json=payload, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=120)) as resp:
                if resp.status != 200:
                    err_text = await resp.text()
                    return web.json_response({"error": f"Hermes 失败: {err_text[:200]}"}, status=502)

                result = await resp.json()
                reply = result.get("choices", [{}])[0].get("message", {}).get("content", "")

                if not reply:
                    return web.json_response({"error": "Hermes 返回为空"}, status=502)

                return web.json_response({"text": reply})

    except asyncio.TimeoutError:
        return web.json_response({"error": "Hermes 超时"}, status=504)
    except Exception as e:
        log.error(f"Hermes 错误: {e}")
        return web.json_response({"error": str(e)}, status=500)


# ══════════════════════════════════════════
# 聊天记录接口
# ══════════════════════════════════════════
async def api_sync_chat(request):
    """同步一条聊天记录"""
    try:
        body = await request.json()
        role = body.get("role", "user")
        content = body.get("content", "")
        summary = body.get("summary", "")

        item = chat.add(role, content, summary)
        return web.json_response({"status": "success", "item": item})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def api_get_current_chat(request):
    """获取当前所有聊天记录"""
    return web.json_response({"chat": chat.get_all()})


async def api_clear_current_chat(request):
    """清空当前聊天"""
    chat.clear()
    return web.json_response({"status": "success"})


async def api_history_dates(request):
    """获取有历史记录的日期列表"""
    try:
        dates = []
        for f in os.listdir(HISTORY_DIR):
            if f.endswith(".json"):
                dates.append(f.replace(".json", ""))
        dates.sort(reverse=True)
        return web.json_response({"dates": dates[:30]})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def api_history_by_date(request):
    """获取某天的历史记录"""
    date = request.query.get("date", "")
    if not date:
        return web.json_response({"error": "缺少 date"}, status=400)

    path = os.path.join(HISTORY_DIR, f"{date}.json")
    if not os.path.exists(path):
        return web.json_response({"chat": []})

    try:
        with open(path, "r", encoding="utf-8") as f:
            records = json.load(f)
        return web.json_response({"chat": records})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


# ══════════════════════════════════════════
# 网络管理 — 多设备多地址优先级配置
# ══════════════════════════════════════════

NET_CONFIG_FILE = os.path.expanduser("~/report2db/network_config.json")

DEVICES = [
    {"id": "bt2", "name": "主服务器 BT2", "icon": "🖥️"},
    {"id": "a3b", "name": "AI服务器 A3B", "icon": "🧠"},
    {"id": "tts", "name": "TTS服务器", "icon": "🔊"},
    {"id": "company-pc", "name": "公司电脑", "icon": "💻"},
    {"id": "phone", "name": "手机", "icon": "📱"},
]

NET_TYPES = ["ipv6", "tailscale", "ipv4"]

# 办公模式默认
OFFICE_DEFAULTS = {
    "bt2": {"ipv6": 6, "tailscale": 5, "ipv4": 10},
    "a3b": {"ipv6": 6, "tailscale": 5, "ipv4": 10},
    "tts": {"ipv6": 6, "tailscale": 5, "ipv4": 10},
    "company-pc": {"ipv6": 10, "tailscale": 5, "ipv4": -1},
    "phone": {"ipv6": 10, "tailscale": 5, "ipv4": -1},
}

# 远程模式默认
REMOTE_DEFAULTS = {
    "bt2": {"ipv6": 6, "tailscale": 10, "ipv4": 10},
    "a3b": {"ipv6": 6, "tailscale": 10, "ipv4": 10},
    "tts": {"ipv6": 6, "tailscale": 10, "ipv4": 10},
    "company-pc": {"ipv6": 5, "tailscale": 10, "ipv4": -1},
    "phone": {"ipv6": 5, "tailscale": 10, "ipv4": -1},
}

# 在家模式默认（手机用 IPv4 最大速度）
HOME_DEFAULTS = {
    "bt2": {"ipv6": 6, "tailscale": 10, "ipv4": 10},
    "a3b": {"ipv6": 6, "tailscale": 10, "ipv4": 10},
    "tts": {"ipv6": 6, "tailscale": 10, "ipv4": 10},
    "company-pc": {"ipv6": 5, "tailscale": 10, "ipv4": -1},
    "phone": {"ipv6": -1, "tailscale": -1, "ipv4": 10},
}

def _load_net_config():
    """加载网络配置"""
    if os.path.exists(NET_CONFIG_FILE):
        try:
            with open(NET_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {"mode": "office", "devices": {d["id"]: dict(OFFICE_DEFAULTS[d["id"]]) for d in DEVICES}}

def _save_net_config(cfg):
    """保存网络配置"""
    os.makedirs(os.path.dirname(NET_CONFIG_FILE), exist_ok=True)
    with open(NET_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


async def api_network_config_get(request):
    """获取当前网络配置"""
    cfg = _load_net_config()
    return web.json_response({
        "mode": cfg["mode"],
        "devices": cfg["devices"],
        "device_list": DEVICES,
        "net_types": NET_TYPES,
    })


async def api_network_config_save(request):
    """保存网络配置"""
    try:
        body = await request.json()
        cfg = _load_net_config()
        cfg["mode"] = body.get("mode", cfg["mode"])
        cfg["devices"] = body.get("devices", cfg["devices"])
        _save_net_config(cfg)
        return web.json_response({"status": "success"})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def api_network_defaults(request):
    """获取默认配置"""
    mode = request.query.get("mode", "office")
    if mode == "home":
        defaults = HOME_DEFAULTS
    elif mode == "remote":
        defaults = REMOTE_DEFAULTS
    else:
        defaults = OFFICE_DEFAULTS
    return web.json_response({"mode": mode, "devices": defaults})


async def api_network_preview(request):
    """获取当前网络调用路径预览（含协议检测和延迟）"""
    import subprocess as _sp
    cfg = _load_net_config()
    devices = cfg["devices"]
    previews = []

    # 各设备的实际 IP 地址
    DEVICE_ADDRS = {
        "bt2": {"ipv4": "192.168.1.21", "ipv6": "2408:8256:3500:1f3:a66e:47a9:2ac0:e5dc", "tailscale": "100.80.121.48"},
        "a3b": {"ipv4": "192.168.1.21", "ipv6": "2408:8256:3500:1f3:a66e:47a9:2ac0:e5dc", "tailscale": "100.80.121.48"},
        "tts": {"ipv4": "192.168.1.5", "ipv6": "", "tailscale": "100.73.220.74"},
        "company-pc": {"ipv4": "192.168.1.5", "ipv6": "2408:8256:3500:1f3:a66e:47a9:2ac0:e5dc", "tailscale": "100.73.220.74"},
        "phone": {"ipv4": "", "ipv6": "", "tailscale": ""},
    }

    # 做一次实时的 ping 检测获取延迟（超时快、不阻塞）
    def _ping_latency(host):
        if not host:
            return -1
        try:
            cmd = ["ping", "-c", "1", "-W", "1", host]
            r = _sp.run(cmd, capture_output=True, text=True, timeout=2)
            if r.returncode != 0:
                return -1
            # 提取 time=XX ms
            import re
            m = re.search(r'time=(\d+\.?\d*)', r.stdout)
            return float(m.group(1)) if m else -1
        except:
            return -1

    for src in DEVICES:
        sid = src["id"]
        for dst in DEVICES:
            did = dst["id"]
            if sid == did:
                continue
            src_cfg = devices.get(sid, {})
            dst_cfg = devices.get(did, {})

            addrs = []
            for nt in NET_TYPES:
                sp = src_cfg.get(nt, -1)
                dp = dst_cfg.get(nt, -1)
                if sp >= 0 and dp >= 0:
                    addrs.append((nt, min(sp, dp)))

            addrs.sort(key=lambda x: x[1], reverse=True)
            chosen_type = addrs[0][0] if addrs else None

            # 检测选中协议的延迟
            src_ip = DEVICE_ADDRS.get(sid, {}).get(chosen_type, "") if chosen_type else ""
            dst_ip = DEVICE_ADDRS.get(did, {}).get(chosen_type, "") if chosen_type else ""

            latency = -1
            if dst_ip and chosen_type != "tailscale":
                latency = _ping_latency(dst_ip)
            elif dst_ip and chosen_type == "tailscale":
                latency = _ping_latency(dst_ip)

            previews.append({
                "from": sid,
                "from_name": src["name"],
                "to": did,
                "to_name": dst["name"],
                "chosen": chosen_type or "无可用路径",
                "priority": addrs[0][1] if addrs else -1,
                "latency": latency,
                "src_ip": src_ip,
                "dst_ip": dst_ip,
                "all": [{"type": a[0], "priority": a[1]} for a in addrs],
            })

    return web.json_response({"previews": previews})


async def api_diag_check(request):
    service = (request.query.get("service") or "").strip().lower()
    if service not in ("asr", "tts", "hermes"):
        return web.json_response({"alive": False, "error": "service 必须是 asr/tts/hermes"}, status=400)

    from urllib.parse import urlparse

    url_map = {
        "asr": ASR_URL,
        "tts": TTS_URL,
        "hermes": HERMES_API_URL,
    }
    u = urlparse(url_map[service])
    host = u.hostname or ""
    port = int(u.port or (443 if u.scheme == "https" else 80))

    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=3.0)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        alive, err = True, ""
    except Exception as e:
        alive, err = False, str(e)
    return web.json_response({"service": service, "alive": alive, "host": host, "port": port, "error": err})
# 页面路由
# ══════════════════════════════════════════
VOICE_HTML = None
NETWORK_HTML = None
TEST_HTML = None


async def voice_page(request):
    global VOICE_HTML
    if VOICE_HTML is None:
        html_path = os.path.join(os.path.dirname(__file__), "voice.html")
        with open(html_path, "r", encoding="utf-8") as f:
            VOICE_HTML = f.read()
    return web.Response(text=VOICE_HTML, content_type="text/html", charset="utf-8")



async def test_page(request):
    global TEST_HTML
    if TEST_HTML is None:
        html_path = os.path.join(os.path.dirname(__file__), "test_page.html")
        if os.path.exists(html_path):
            with open(html_path, "r", encoding="utf-8") as f:
                TEST_HTML = f.read()
        else:
            TEST_HTML = "<html><body>OK</body></html>"
    return web.Response(text=TEST_HTML, content_type="text/html", charset="utf-8")

async def network_page(request):
    global NETWORK_HTML
    if NETWORK_HTML is None:
        html_path = os.path.join(os.path.dirname(__file__), "network.html")
        with open(html_path, "r", encoding="utf-8") as f:
            NETWORK_HTML = f.read()
    return web.Response(text=NETWORK_HTML, content_type="text/html", charset="utf-8")


async def voice_client_download(request):
    """下载语音室客户端 Python 文件"""
    py_path = os.path.join(os.path.dirname(__file__), "voice_client.py")
    if not os.path.exists(py_path):
        return web.Response(text="文件未找到", status=404)
    with open(py_path, "r", encoding="utf-8") as f:
        content = f.read()
    return web.Response(
        text=content,
        content_type="text/x-python",
        headers={"Content-Disposition": "attachment; filename=voice_client.py"}
    )


async def voice_export_download(request):
    """下载所有源代码压缩包"""
    tar_path = os.path.join(os.path.dirname(__file__), "voice_export.tar.gz")
    if not os.path.exists(tar_path):
        return web.Response(text="文件未找到", status=404)
    with open(tar_path, "rb") as f:
        content = f.read()
    return web.Response(
        body=content,
        content_type="application/gzip",
        headers={"Content-Disposition": "attachment; filename=voice_export.tar.gz"}
    )


async def redirect_to_voice(request):
    raise web.HTTPFound("/voice")


# ══════════════════════════════════════════
# 主程序
# ══════════════════════════════════════════
def main():
    app = web.Application()

    # CORS 中间件
    @web.middleware
    async def cors_middleware(request, handler):
        if request.method == "OPTIONS":
            resp = web.Response()
            resp.headers["Access-Control-Allow-Origin"] = "*"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            return resp
        resp = await handler(request)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    app.middlewares.append(cors_middleware)

    # 页面路由
    app.router.add_get("/", redirect_to_voice)
    app.router.add_get("/voice", voice_page)
    app.router.add_get("/voice.html", voice_page)
    app.router.add_get("/network", network_page)
    app.router.add_get("/test", test_page)
    app.router.add_get("/voice_client.py", voice_client_download)
    app.router.add_get("/voice_export.tar.gz", voice_export_download)

    # API
    app.router.add_post("/api/asr", api_asr)
    app.router.add_post("/api/tts", api_tts)
    app.router.add_post("/api/tts-fallback", api_tts_fallback)
    app.router.add_post("/api/chat", api_chat)
    app.router.add_post("/api/sync_chat", api_sync_chat)
    app.router.add_get("/api/get_current_chat", api_get_current_chat)
    app.router.add_post("/api/clear_current_chat", api_clear_current_chat)
    app.router.add_get("/api/history/dates", api_history_dates)
    app.router.add_get("/api/history/by-date", api_history_by_date)
    app.router.add_get("/api/diag/check", api_diag_check)
    # 网络管理 API
    app.router.add_get("/api/network/config", api_network_config_get)
    app.router.add_post("/api/network/config", api_network_config_save)
    app.router.add_get("/api/network/defaults", api_network_defaults)
    app.router.add_get("/api/network/preview", api_network_preview)

    # HTTP
    runner = web.AppRunner(app)
    asyncio.get_event_loop().run_until_complete(runner.setup())

    http_site = web.TCPSite(runner, HOST, PORT)
    asyncio.get_event_loop().run_until_complete(http_site.start())
    log.info(f"Voice Server HTTP: http://[::]:{PORT}/voice")

    # HTTPS（如果有证书）
    if os.path.exists(SSL_CERT) and os.path.exists(SSL_KEY):
        try:
            import ssl
            ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ssl_ctx.load_cert_chain(SSL_CERT, SSL_KEY)
            https_site = web.TCPSite(runner, HOST, HTTPS_PORT, ssl_context=ssl_ctx)
            asyncio.get_event_loop().run_until_complete(https_site.start())
            log.info(f"Voice Server HTTPS: https://[::]:{HTTPS_PORT}/voice")
        except Exception as e:
            log.warning(f"HTTPS 启动失败: {e}")

    log.info("Voice Server 已启动")
    asyncio.get_event_loop().run_forever()


if __name__ == "__main__":
    main()
