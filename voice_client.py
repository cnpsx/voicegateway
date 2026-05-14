# -*- coding: utf-8 -*-
"""
语音室客户端 — Python tkinter 增强版 v3
重点: 多地址自动探测+手动选档、录音文件生成与保存、调试面板
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import warnings
warnings.filterwarnings("ignore", category=Warning)
import requests
import io
import wave
import time
import tempfile
import os
import re
import struct
import json
import datetime
import logging

# ===== 候选服务器地址 =====
SERVER_CANDIDATES = [
    {"label": "IPv4 局域网", "url": "http://192.168.1.21:12054"},
    {"label": "Tailscale",   "url": "http://100.80.121.48:12054"},
    {"label": "IPv6",        "url": "http://[2408:8256:3500:1f3:a66e:47a9:2ac0:e5dc]:12054"},
]

SERVER = "http://100.80.121.48:12054"  # 默认，启动后自动探测
SERVER_LABEL = "自动探测"

ASR_URL = f"{SERVER}/api/asr"
CHAT_URL = f"{SERVER}/api/chat"
TTS_URL = f"{SERVER}/api/tts"
TTS_FALLBACK_URL = f"{SERVER}/api/tts-fallback"
DIAG_URL = f"{SERVER}/api/diag/check"
VERIFY = False

# 录音文件保存目录
RECORDINGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recordings")
os.makedirs(RECORDINGS_DIR, exist_ok=True)

# 日志
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    filename=os.path.join(RECORDINGS_DIR, "voice_client.log"),
    filemode="a",
)
log = logging.getLogger("voice-client")
log.info("=" * 60)
log.info("客户端启动")


def set_server(idx_or_url):
    """切换服务器地址"""
    global SERVER, SERVER_LABEL, ASR_URL, CHAT_URL, TTS_URL, TTS_FALLBACK_URL, DIAG_URL
    if isinstance(idx_or_url, int):
        s = SERVER_CANDIDATES[idx_or_url]
        SERVER = s["url"]
        SERVER_LABEL = s["label"]
    else:
        SERVER = idx_or_url
        SERVER_LABEL = "自定义"
    ASR_URL = f"{SERVER}/api/asr"
    CHAT_URL = f"{SERVER}/api/chat"
    TTS_URL = f"{SERVER}/api/tts"
    TTS_FALLBACK_URL = f"{SERVER}/api/tts-fallback"
    DIAG_URL = f"{SERVER}/api/diag/check"
    log.info(f"切换到服务器: {SERVER_LABEL} -> {SERVER}")
    if 'status_label' in globals():
        status_label.config(text=f"已切换: {SERVER_LABEL}", fg="#FFB74D")


def auto_detect_server():
    """依次探测所有候选地址，返回最快响应的"""
    results = []
    for s in SERVER_CANDIDATES:
        try:
            t0 = time.time()
            r = requests.get(f"{s['url']}/api/network/config", timeout=3)
            elapsed = time.time() - t0
            if r.status_code == 200:
                data = r.json()
                auto_mode = data.get("auto_mode", "")
                mode_text = f" (模式:{auto_mode})" if auto_mode else ""
                results.append((elapsed, s["url"], s["label"], data))
                log.info(f"   {s['label']} {s['url']} -> ✅ {elapsed:.1f}s{mode_text}")
        except Exception as e:
            log.info(f"   {s['label']} {s['url']} -> ❌ {e}")
    if results:
        # 按响应时间排序
        results.sort(key=lambda x: x[0])
        best = results[0]
        set_server_by_url(best[1], best[2])
        return best[2], best[0], best[3]
    return None, None, None


def set_server_by_url(url, label):
    global SERVER, SERVER_LABEL, ASR_URL, CHAT_URL, TTS_URL, TTS_FALLBACK_URL, DIAG_URL
    SERVER = url
    SERVER_LABEL = label
    ASR_URL = f"{SERVER}/api/asr"
    CHAT_URL = f"{SERVER}/api/chat"
    TTS_URL = f"{SERVER}/api/tts"
    TTS_FALLBACK_URL = f"{SERVER}/api/tts-fallback"
    DIAG_URL = f"{SERVER}/api/diag/check"
    log.info(f"自动选中: {label} -> {SERVER}")


# ===== 录音状态 =====
_recording = False
_frames = []
_stream = None
_pyaudio_inst = None
_last_wav_path = None


def get_pyaudio():
    global _pyaudio_inst
    if _pyaudio_inst is None:
        import pyaudio
        _pyaudio_inst = pyaudio.PyAudio()
        log.info("PyAudio 初始化成功")
    return _pyaudio_inst


def list_audio_devices():
    """列出所有音频输入设备"""
    try:
        p = get_pyaudio()
        devices = []
        for i in range(p.get_device_count()):
            dev = p.get_device_info_by_index(i)
            if dev["maxInputChannels"] > 0:
                devices.append(f"[{i}] {dev['name']} (输入:{dev['maxInputChannels']}ch)")
        return devices
    except Exception as e:
        log.error(f"列出音频设备失败: {e}")
        return [f"错误: {e}"]


def start_recording():
    global _recording, _frames, _stream
    if _recording:
        return
    try:
        import pyaudio as _pa
        _recording = True
        _frames = []
        p = get_pyaudio()
        _stream = p.open(
            format=_pa.paInt16, channels=1, rate=16000,
            input=True, frames_per_buffer=1024,
        )
        log.info("录音开始")
        def _read():
            while _recording:
                try:
                    data = _stream.read(1024, exception_on_overflow=False)
                    _frames.append(data)
                except Exception as e:
                    log.error(f"录音读取错误: {e}")
                    break
        threading.Thread(target=_read, daemon=True).start()
    except Exception as e:
        log.error(f"启动录音失败: {e}")
        raise


def stop_recording():
    global _recording, _stream, _last_wav_path
    if not _recording:
        return None
    _recording = False
    try:
        if _stream:
            _stream.stop_stream()
            _stream.close()
    except:
        pass
    _stream = None
    if not _frames:
        log.warning("录音帧为空")
        return None

    # 生成 WAV
    frames_data = b"".join(_frames)
    frames_count = len(_frames)
    duration = frames_count * 1024 / 16000

    # 保存到文件
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    _last_wav_path = os.path.join(RECORDINGS_DIR, f"rec_{timestamp}.wav")
    with wave.open(_last_wav_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(frames_data)

    log.info(f"录音保存: {_last_wav_path}, {os.path.getsize(_last_wav_path)} bytes, {duration:.1f}s")
    return frames_data


def play_audio(data, ext=".wav"):
    if not data:
        log.warning("play_audio: 空数据")
        return
    try:
        import pygame
        suffix = ".mp3" if data[:3] == b'ID3' else ".wav"
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp.write(data)
        tmp_path = tmp.name
        tmp.close()
        log.info(f"播放音频: {tmp_path}, {len(data)} bytes")
        pygame.mixer.init(frequency=24000)
        pygame.mixer.music.load(tmp_path)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            time.sleep(0.1)
        pygame.mixer.quit()
        try:
            os.unlink(tmp_path)
        except:
            pass
    except Exception as e:
        log.error(f"播放失败: {e}")


def generate_test_wav(duration=2, freq=440, sample_rate=16000):
    """生成测试 WAV 并保存"""
    frames = []
    for i in range(sample_rate * duration):
        val = int(16000 * 0.5 * __import__('math').sin(2 * 3.14159 * freq * i / sample_rate))
        frames.append(struct.pack('<h', val))

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    save_path = os.path.join(RECORDINGS_DIR, f"test_{freq}Hz_{timestamp}.wav")
    with wave.open(save_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"".join(frames))

    with open(save_path, "rb") as f:
        data = f.read()
    log.info(f"测试WAV: {save_path}, {len(data)} bytes")
    return data, save_path


# ===== GUI =====
root = tk.Tk()
root.title("语音室 v3 - Hermes")
root.geometry("560x760")
root.configure(bg="#0a0a0f")
root.resizable(False, False)

style = ttk.Style()
style.theme_use("clam")
style.configure("TFrame", background="#0a0a0f")

# ── 顶部 ──
top = ttk.Frame(root)
top.pack(fill="x", padx=10, pady=(8, 2))
tk.Label(top, text="语音室 v3", font=("Microsoft YaHei", 14, "bold"),
         fg="#e0e0e0", bg="#0a0a0f").pack(side="left")
net_label = tk.Label(top, text="", font=("Microsoft YaHei", 8),
                     fg="#888", bg="#0a0a0f")
net_label.pack(side="right", padx=(0, 8))
status_label = tk.Label(top, text="检测中...", font=("Microsoft YaHei", 10),
                        fg="#FFB74D", bg="#0a0a0f")
status_label.pack(side="right")

# ── 服务器地址选择 ──
addr_frame = ttk.Frame(root)
addr_frame.pack(fill="x", padx=10, pady=(2, 2))
tk.Label(addr_frame, text="服务器:", font=("Microsoft YaHei", 9),
         fg="#888", bg="#0a0a0f").pack(side="left")
addr_var = tk.StringVar(value="自动探测")
addr_menu = ttk.OptionMenu(addr_frame, addr_var, "自动探测",
    "自动探测",
    *[s["label"] for s in SERVER_CANDIDATES],
    command=lambda v: on_addr_select(v))
addr_menu.pack(side="left", padx=(4, 0))

addr_label = tk.Label(addr_frame, text="未连接",
                      font=("Microsoft YaHei", 8), fg="#888", bg="#0a0a0f")
addr_label.pack(side="left", padx=(8, 0))

def on_addr_select(label):
    if label == "自动探测":
        status_label.config(text="探测中...", fg="#FFB74D")
        threading.Thread(target=_do_auto_detect, daemon=True).start()
        return
    for i, s in enumerate(SERVER_CANDIDATES):
        if s["label"] == label:
            set_server(i)
            addr_label.config(text=s["url"])
            status_label.config(text=f"已选: {label}", fg="#4CAF50")
            refresh_net_info()
            return

def _do_auto_detect():
    label, elapsed, data = auto_detect_server()
    if label:
        root.after(0, lambda: addr_label.config(text=SERVER))
        mode = data.get("auto_mode", "") if data else ""
        hour = data.get("current_hour", datetime.datetime.now().hour) if data else ""
        root.after(0, lambda: status_label.config(
            text=f"✅ {label} {elapsed:.1f}s{mode_text(mode, hour)}", fg="#4CAF50"))
    else:
        root.after(0, lambda: status_label.config(text="❌ 所有地址不可达", fg="#f44336"))

def mode_text(mode, hour):
    if mode == "office":
        return f" 🏢工作时间({hour}时)"
    elif mode == "home":
        return f" 🏠在家模式({hour}时)"
    return ""

# ── 录音按钮 ──
btn_frame = ttk.Frame(root)
btn_frame.pack(fill="x", padx=10, pady=4)
record_btn = tk.Button(btn_frame, text="按住说话",
                       font=("Microsoft YaHei", 12),
                       bg="#7C5CFC", fg="white",
                       relief="flat", padx=20, pady=10,
                       activebackground="#6a4be0")
record_btn.pack(fill="x", expand=True)

# 音频设备信息
dev_frame = ttk.Frame(root)
dev_frame.pack(fill="x", padx=10, pady=(0, 2))
dev_label = tk.Label(dev_frame, text="设备: 检测中...",
                     font=("Microsoft YaHei", 8), fg="#888", bg="#0a0a0f")
dev_label.pack(anchor="w")

# ── 工具按钮行 ──
tool_frame = ttk.Frame(root)
tool_frame.pack(fill="x", padx=10, pady=2)

def test_asr():
    add_msg("系统", "测试: 生成 440Hz 测试音→ASR...", "#888")
    threading.Thread(target=_do_test_asr, daemon=True).start()

def _do_test_asr():
    try:
        wav_bytes, save_path = generate_test_wav()
        add_msg("系统", f"已保存: {os.path.basename(save_path)}, {len(wav_bytes)} bytes", "#888")
        resp = requests.post(ASR_URL, data=wav_bytes, timeout=30)
        result = resp.json()
        add_msg("系统", f"ASR 响应: HTTP {resp.status_code}", "#888")
        add_msg("系统", f"ASR 结果: {result}", "#FFB74D")
        log.info(f"测试ASR完成: {result}")
    except Exception as e:
        add_msg("系统", f"ASR测试失败: {str(e)}", "#f44336")
        log.error(f"测试ASR异常: {e}")

def upload_wav():
    path = filedialog.askopenfilename(
        title="选择 WAV 音频文件",
        filetypes=[("WAV 文件", "*.wav"), ("所有文件", "*.*")]
    )
    if not path:
        return
    add_msg("系统", f"选择文件: {os.path.basename(path)}", "#888")
    try:
        with wave.open(path, "rb") as wf:
            params = (wf.getnchannels(), wf.getsampwidth(), wf.getframerate(), wf.getnframes())
            add_msg("系统", f"WAV参数: 声道={params[0]} 位宽={params[1]} 采样率={params[2]}Hz 帧数={params[3]}", "#888")
        with open(path, "rb") as f:
            data = f.read()
        add_msg("系统", f"发送 ASR ({len(data)} bytes)...", "#888")
        threading.Thread(target=_do_upload_asr, args=(path, data), daemon=True).start()
    except Exception as e:
        add_msg("系统", f"文件读取失败: {e}", "#f44336")

def _do_upload_asr(path, wav_data):
    try:
        resp = requests.post(ASR_URL, data=wav_data, timeout=30)
        result = resp.json()
        add_msg("系统", f"ASR 响应: HTTP {resp.status_code}", "#888")
        add_msg("系统", f"ASR 结果: {result}", "#FFB74D")
    except Exception as e:
        add_msg("系统", f"ASR失败: {e}", "#f44336")

def diag_check():
    add_msg("系统", "诊断: 检查各服务状态...", "#888")
    threading.Thread(target=_do_diag, daemon=True).start()

def _do_diag():
    for svc in ["asr", "tts", "hermes"]:
        try:
            r = requests.get(f"{DIAG_URL}?service={svc}", timeout=10)
            data = r.json()
            alive = data.get("alive", False)
            host = data.get("host", "")
            port = data.get("port", "")
            err = data.get("error", "")
            status = "🟢 正常" if alive else "🔴 异常"
            msg = f"{svc.upper()} {status} ({host}:{port})"
            if err:
                msg += f" [{err}]"
            add_msg("系统", msg, "#4CAF50" if alive else "#f44336")
        except Exception as e:
            add_msg("系统", f"{svc.upper()} 检测失败: {e}", "#f44336")

def show_recordings():
    try:
        import subprocess
        subprocess.Popen(["xdg-open", RECORDINGS_DIR])
    except:
        add_msg("系统", f"录音目录: {RECORDINGS_DIR}", "#888")

def show_last_wav():
    global _last_wav_path
    if not _last_wav_path or not os.path.exists(_last_wav_path):
        add_msg("系统", "暂无录音记录", "#888")
        return
    try:
        size = os.path.getsize(_last_wav_path)
        with wave.open(_last_wav_path, "rb") as wf:
            dur = wf.getnframes() / wf.getframerate()
        add_msg("系统", f"最后录音: {os.path.basename(_last_wav_path)}", "#888")
        add_msg("系统", f"大小: {size} bytes, 时长: {dur:.1f}s", "#888")
    except Exception as e:
        add_msg("系统", f"读取失败: {e}", "#f44336")

def refresh_net_info():
    """刷新网络延迟信息"""
    threading.Thread(target=_do_refresh_net, daemon=True).start()

def _do_refresh_net():
    try:
        r = requests.get(f"{SERVER}/api/network/preview", timeout=5)
        data = r.json()
        for p in data.get("previews", []):
            if p.get("to") == "tts" and p.get("latency", -1) >= 0:
                lat = p["latency"]
                c = p.get("chosen", "")
                color = "#4CAF50" if lat < 10 else "#FFB74D" if lat < 50 else "#f44336"
                root.after(0, lambda c=c, lat=lat, color=color: net_label.config(
                    text=f"{c.upper()} {lat:.1f}ms", fg=color))
                return
    except:
        pass

# 工具按钮
btn_config = [
    ("测试ASR", test_asr),
    ("📁上传WAV", upload_wav),
    ("🔍诊断", diag_check),
    ("📂录音", show_recordings),
    ("📄最后录音", show_last_wav),
    ("🔄刷新网络", refresh_net_info),
]
for text, cmd in btn_config:
    tk.Button(tool_frame, text=text, font=("Microsoft YaHei", 8),
              bg="#444", fg="#e0e0e0", relief="flat", padx=5,
              command=cmd).pack(side="left", padx=(0, 2))

# ── 文字输入 ──
input_frame = ttk.Frame(root)
input_frame.pack(fill="x", padx=10, pady=4)
text_input = tk.Entry(input_frame, font=("Microsoft YaHei", 10),
                       bg="#1a1a24", fg="#e0e0e0",
                       insertbackground="#e0e0e0",
                       relief="flat", bd=8)
text_input.pack(side="left", fill="x", expand=True, ipady=4)
text_input.bind("<Return>", lambda e: send_text())
send_btn = tk.Button(input_frame, text="发送",
                     font=("Microsoft YaHei", 10),
                     bg="#7C5CFC", fg="white",
                     relief="flat", padx=12,
                     command=lambda: send_text())
send_btn.pack(side="right", padx=(4, 0))

# ── 聊天区 ──
chat_frame = ttk.Frame(root)
chat_frame.pack(fill="both", expand=True, padx=10, pady=4)
chat_canvas = tk.Canvas(chat_frame, bg="#0d0d14", highlightthickness=0)
scrollbar = ttk.Scrollbar(chat_frame, orient="vertical", command=chat_canvas.yview)
msg_frame = ttk.Frame(chat_canvas)
msg_frame.bind("<Configure>",
    lambda e: chat_canvas.configure(scrollregion=chat_canvas.bbox("all")))
chat_canvas.create_window((0, 0), window=msg_frame, anchor="nw")
chat_canvas.configure(yscrollcommand=scrollbar.set)
chat_canvas.pack(side="left", fill="both", expand=True)
scrollbar.pack(side="right", fill="y")


def on_press(e):
    try:
        start_recording()
        status_label.config(text="🔴 录音中", fg="#f44336")
        record_btn.config(text="🔴 录音中", bg="#f44336")
    except Exception as ex:
        add_msg("系统", f"录音启动失败: {ex}", "#f44336")
        log.error(f"on_press: {ex}")

def on_release(e):
    wav = stop_recording()
    if not wav:
        status_label.config(text="空闲", fg="#4CAF50")
        record_btn.config(text="按住说话", bg="#7C5CFC")
        add_msg("系统", "录音为空", "#FFB74D")
        return
    status_label.config(text="⏳ 识别中", fg="#FFB74D")
    record_btn.config(text="处理中", bg="#FFB74D")
    threading.Thread(target=process_audio, args=(wav,), daemon=True).start()

record_btn.bind("<ButtonPress-1>", on_press)
record_btn.bind("<ButtonRelease-1>", on_release)


def add_msg(sender, text, color="#e0e0e0"):
    frame = ttk.Frame(msg_frame)
    frame.pack(fill="x", pady=4, padx=4)
    align = "w" if sender == "Hermes" else "e"
    label = tk.Label(frame, text=f"{sender}: {text}",
                     wraplength=480, justify="left",
                     font=("Microsoft YaHei", 10),
                     fg=color, bg="#0d0d14")
    label.pack(anchor=align)
    root.after(50, lambda: chat_canvas.yview_moveto(1.0))


def show_error(msg):
    status_label.config(text=f"错误: {msg}", fg="#f44336")
    record_btn.config(text="按住说话", bg="#7C5CFC")
    add_msg("系统", f"错误: {msg}", "#f44336")


def process_audio(wav_data):
    try:
        # ASR
        url = ASR_URL
        resp = requests.post(url, data=wav_data, verify=VERIFY, timeout=30)
        result = resp.json()
        log.info(f"ASR: {result}")
        if "error" in result:
            root.after(0, lambda: show_error(result["error"]))
            return
        text = result.get("text", "").strip()
        if not text:
            root.after(0, lambda: status_label.config(text="未识别", fg="#FFB74D"))
            root.after(0, lambda: record_btn.config(text="按住说话", bg="#7C5CFC"))
            add_msg("系统", "ASR 返回空文本", "#FFB74D")
            return
        root.after(0, lambda: add_msg("你", text, "#4FC3F7"))

        # Chat
        status_label.config(text="思考中")
        chat_resp = requests.post(CHAT_URL, json={"text": text}, verify=VERIFY, timeout=60)
        reply = chat_resp.json().get("text", "")
        log.info(f"Chat: {reply[:80]}...")
        if not reply:
            root.after(0, lambda: show_error("Hermes 无回复"))
            return
        root.after(0, lambda: add_msg("Hermes", reply, "#7C5CFC"))

        # TTS
        root.after(0, lambda: status_label.config(text="播放中", fg="#7C5CFC"))
        try:
            tts_resp = requests.post(TTS_URL, json={"text": reply[:200]},
                                     verify=VERIFY, timeout=30)
            ct = tts_resp.headers.get("content-type", "")
            if tts_resp.status_code == 200 and ct.startswith("audio/"):
                play_audio(tts_resp.content)
            else:
                log.warning(f"TTS not audio: HTTP {tts_resp.status_code}")
                fb = requests.post(TTS_FALLBACK_URL, json={"text": reply[:200]},
                                   verify=VERIFY, timeout=30)
                if fb.status_code == 200:
                    play_audio(fb.content)
        except Exception as e:
            log.warning(f"TTS error: {e}")
            try:
                fb = requests.post(TTS_FALLBACK_URL, json={"text": reply[:200]},
                                   verify=VERIFY, timeout=30)
                if fb.status_code == 200:
                    play_audio(fb.content)
            except:
                pass

        root.after(0, lambda: status_label.config(text="空闲", fg="#4CAF50"))
        root.after(0, lambda: record_btn.config(text="按住说话", bg="#7C5CFC"))
    except Exception as e:
        log.error(f"process_audio: {e}")
        root.after(0, lambda: show_error(str(e)))


def send_text():
    text = text_input.get().strip()
    if not text:
        return
    text_input.delete(0, "end")
    add_msg("你", text, "#4FC3F7")
    status_label.config(text="思考中", fg="#FFB74D")
    record_btn.config(text="处理中", bg="#FFB74D")
    threading.Thread(target=lambda: process_text(text), daemon=True).start()


def process_text(text):
    try:
        chat_resp = requests.post(CHAT_URL, json={"text": text}, verify=VERIFY, timeout=60)
        reply = chat_resp.json().get("text", "")
        if not reply:
            root.after(0, lambda: show_error("Hermes 无回复"))
            return
        root.after(0, lambda: add_msg("Hermes", reply, "#7C5CFC"))
        root.after(0, lambda: status_label.config(text="播放中", fg="#7C5CFC"))
        try:
            tts_resp = requests.post(TTS_URL, json={"text": reply[:200]},
                                     verify=VERIFY, timeout=30)
            ct = tts_resp.headers.get("content-type", "")
            if tts_resp.status_code == 200 and ct.startswith("audio/"):
                play_audio(tts_resp.content)
        except:
            pass
        root.after(0, lambda: status_label.config(text="空闲", fg="#4CAF50"))
        root.after(0, lambda: record_btn.config(text="按住说话", bg="#7C5CFC"))
    except Exception as e:
        root.after(0, lambda: show_error(str(e)))


def check_net_loop():
    """定期刷新延迟"""
    refresh_net_info()
    root.after(15000, check_net_loop)


def show_devices():
    def _show():
        devs = list_audio_devices()
        text = "; ".join(devs) if devs else "无输入设备"
        root.after(0, lambda: dev_label.config(text=f"设备: {text}"))
    threading.Thread(target=_show, daemon=True).start()


# ── 启动自动探测 ──
add_msg("系统", "🔍 正在自动探测可用的服务器地址...", "#888")
threading.Thread(target=_do_auto_detect, daemon=True).start()
add_msg("系统", "按住按钮说话, 松开自动识别", "#888")
add_msg("系统", f"录音目录: {RECORDINGS_DIR}", "#888")
add_msg("系统", "工具: 测试ASR | 上传WAV | 诊断 | 查看录音 | 刷新网络", "#888")
check_net_loop()
show_devices()
root.mainloop()
