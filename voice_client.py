# -*- coding: utf-8 -*-
"""
语音室客户端 — Python tkinter 增强版 v3
重点: 服务器自动探测/手动选择、录音文件生成与保存、调试面板、无麦上传WAV
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
import subprocess

# ===== 候选服务器地址（按优先级排序） =====
SERVER_CANDIDATES = [
    {"label": "IPv4 局域网", "url": "http://192.168.1.21:12054"},
    {"label": "Tailscale",   "url": "http://100.80.121.48:12054"},
    {"label": "IPv6",        "url": "http://[2408:8256:3500:1f3:a66e:47a9:2ac0:e5dc]:12054"},
]

SERVER = None
SERVER_LABEL = None
ASR_URL = None
CHAT_URL = None
TTS_URL = None
TTS_FALLBACK_URL = None
DIAG_URL = None
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


def probe_server(candidate):
    """探测候选服务器是否可达，返回延迟ms或None"""
    url = candidate["url"]
    try:
        t0 = time.time()
        r = requests.get(f"{url}/voice", timeout=3, verify=False)
        elapsed = (time.time() - t0) * 1000
        if r.status_code in (200, 302):
            log.info(f"探测 {candidate['label']} {url}: 可达 {elapsed:.0f}ms")
            return elapsed
    except Exception as e:
        log.info(f"探测 {candidate['label']} {url}: 不可达 ({e})")
    return None


def choose_server():
    """
    自动探测所有候选服务器：
    1. 并行探测所有候选（超时3秒）
    2. 按可达性排序（延迟越短越优先）
    3. 弹出选择窗口让用户确认/手动选
    返回选择的 (label, url)
    """
    # 并行探测
    results = []
    done = []
    lock = threading.Lock()

    def probe_and_collect(c):
        lat = probe_server(c)
        with lock:
            results.append((lat, c["label"], c["url"]))

    threads = [threading.Thread(target=probe_and_collect, args=(c,)) for c in SERVER_CANDIDATES]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 排序：可达的按延迟排，不可达的放后面
    reachable = [(lat, label, url) for lat, label, url in results if lat is not None]
    unreachable = [(lat, label, url) for lat, label, url in results if lat is None]
    reachable.sort(key=lambda x: x[0])
    sorted_all = reachable + unreachable

    # 构建选择列表
    choices = []
    for lat, label, url in sorted_all:
        if lat is not None:
            choices.append(f"🟢 {label} ({lat:.0f}ms) — {url}")
        else:
            choices.append(f"🔴 {label} (不可达) — {url}")

    # 弹出选择窗口
    return _show_server_dialog(choices, sorted_all)


def _show_server_dialog(choices, sorted_all):
    """弹窗让用户选择服务器"""
    win = tk.Toplevel(root)
    win.title("选择服务器")
    win.geometry("480x320")
    win.configure(bg="#0a0a0f")
    win.resizable(False, False)
    win.transient(root)
    win.grab_set()

    result = {"label": None, "url": None}

    tk.Label(win, text="请选择语音服务器", font=("Microsoft YaHei", 14, "bold"),
             fg="#e0e0e0", bg="#0a0a0f").pack(pady=(16, 4))
    tk.Label(win, text="系统已自动探测以下地址：", font=("Microsoft YaHei", 9),
             fg="#888", bg="#0a0a0f").pack(pady=(0, 8))

    listbox = tk.Listbox(win, bg="#1a1a24", fg="#e0e0e0",
                          selectbackground="#7C5CFC", relief="flat",
                          font=("Microsoft YaHei", 10), height=8)
    for c in choices:
        listbox.insert(tk.END, c)
    # 默认选中第一个（最快可达的）
    if choices:
        listbox.selection_set(0)
    listbox.pack(fill="both", expand=True, padx=20, pady=(0, 8))

    def on_ok():
        sel = listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        _, label, url = sorted_all[idx]
        result["label"] = label
        result["url"] = url
        win.destroy()

    def on_manual():
        """手动输入地址"""
        manual_win = tk.Toplevel(win)
        manual_win.title("手动输入")
        manual_win.geometry("420x180")
        manual_win.configure(bg="#0a0a0f")
        manual_win.transient(win)
        manual_win.grab_set()

        tk.Label(manual_win, text="输入服务器地址", font=("Microsoft YaHei", 12),
                 fg="#e0e0e0", bg="#0a0a0f").pack(pady=(16, 8))

        entry = tk.Entry(manual_win, font=("Microsoft YaHei", 10),
                          bg="#1a1a24", fg="#e0e0e0",
                          insertbackground="#e0e0e0", relief="flat", bd=8)
        entry.insert(0, "http://192.168.1.21:12054")
        entry.pack(fill="x", padx=20, pady=(0, 8), ipady=4)

        def ok_manual():
            url = entry.get().strip()
            if url:
                # 自动补 http://
                if not url.startswith("http"):
                    url = "http://" + url
                result["label"] = "手动输入"
                result["url"] = url
                manual_win.destroy()
                win.destroy()

        tk.Button(manual_win, text="确认", font=("Microsoft YaHei", 10),
                  bg="#7C5CFC", fg="white", relief="flat", padx=20,
                  command=ok_manual).pack(pady=8)

    btn_frame = tk.Frame(win, bg="#0a0a0f")
    btn_frame.pack(fill="x", padx=20, pady=(0, 12))
    tk.Button(btn_frame, text="手动输入", font=("Microsoft YaHei", 10),
              bg="#444", fg="#e0e0e0", relief="flat", padx=12,
              command=on_manual).pack(side="left")
    tk.Button(btn_frame, text="  确 认  ", font=("Microsoft YaHei", 10),
              bg="#7C5CFC", fg="white", relief="flat", padx=20,
              command=on_ok).pack(side="right")

    root.wait_window(win)

    if result["url"]:
        return result["label"], result["url"]
    # 用户关闭窗口，用默认
    return SERVER_CANDIDATES[0]["label"], SERVER_CANDIDATES[0]["url"]


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
    try:
        p = get_pyaudio()
        devices = []
        for i in range(p.get_device_count()):
            dev = p.get_device_info_by_index(i)
            if dev["maxInputChannels"] > 0:
                devices.append(f"[{i}] {dev['name']} (输入:{dev['maxInputChannels']}ch)")
        return devices
    except Exception as e:
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
                except:
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
        return None

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    _last_wav_path = os.path.join(RECORDINGS_DIR, f"rec_{timestamp}.wav")
    with open(_last_wav_path, "wb") as f:
        with wave.open(f, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"".join(_frames))

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"".join(_frames))
    wav_bytes = buf.getvalue()

    dur = len(_frames) * 1024 / 16000
    log.info(f"录音结束: {_last_wav_path}, {len(wav_bytes)} bytes, {dur:.1f}s")
    return wav_bytes


def play_audio(data):
    if not data:
        return
    try:
        import pygame
        suffix = ".mp3" if data[:3] == b'ID3' else ".wav"
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp.write(data)
        tmp_path = tmp.name
        tmp.close()
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


def generate_test_wav(duration=2, freq=440):
    frames = []
    for i in range(16000 * duration):
        val = int(16000 * 0.5 * __import__('math').sin(2 * 3.14159 * freq * i / 16000))
        frames.append(struct.pack('<h', val))
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    save_path = os.path.join(RECORDINGS_DIR, f"test_{freq}Hz_{timestamp}.wav")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"".join(frames))
    with open(save_path, "wb") as f:
        f.write(buf.getvalue())
    log.info(f"测试WAV: {save_path}, {len(buf.getvalue())} bytes")
    return buf.getvalue(), save_path


# ========== GUI ==========
root = tk.Tk()
root.title("语音室 v3 - Hermes")
root.geometry("520x740")
root.configure(bg="#0a0a0f")
root.resizable(False, False)

style = ttk.Style()
style.theme_use("clam")
style.configure("TFrame", background="#0a0a0f")

# 启动时先选服务器
_label, _url = choose_server()
SERVER = _url
SERVER_LABEL = _label
ASR_URL = f"{SERVER}/api/asr"
CHAT_URL = f"{SERVER}/api/chat"
TTS_URL = f"{SERVER}/api/tts"
TTS_FALLBACK_URL = f"{SERVER}/api/tts-fallback"
DIAG_URL = f"{SERVER}/api/diag/check"

log.info(f"选定服务器: {SERVER_LABEL} — {SERVER}")

# 顶部
top = ttk.Frame(root)
top.pack(fill="x", padx=10, pady=(8, 2))
tk.Label(top, text="语音室 v3", font=("Microsoft YaHei", 14, "bold"),
         fg="#e0e0e0", bg="#0a0a0f").pack(side="left")
server_label = tk.Label(top, text=f"🟢 {SERVER_LABEL}", font=("Microsoft YaHei", 8),
                        fg="#4CAF50", bg="#0a0a0f")
server_label.pack(side="left", padx=(8, 0))
net_label = tk.Label(top, text="", font=("Microsoft YaHei", 8),
                     fg="#888", bg="#0a0a0f")
net_label.pack(side="right", padx=(0, 8))
status_label = tk.Label(top, text="空闲", font=("Microsoft YaHei", 10),
                        fg="#4CAF50", bg="#0a0a0f")
status_label.pack(side="right")

# 录音按钮
btn_frame = ttk.Frame(root)
btn_frame.pack(fill="x", padx=10, pady=4)
record_btn = tk.Button(btn_frame, text="按住说话",
                       font=("Microsoft YaHei", 12),
                       bg="#7C5CFC", fg="white",
                       relief="flat", padx=20, pady=10,
                       activebackground="#6a4be0")
record_btn.pack(fill="x", expand=True)

dev_frame = ttk.Frame(root)
dev_frame.pack(fill="x", padx=10, pady=(0, 2))
dev_label = tk.Label(dev_frame, text="设备: 检测中...",
                     font=("Microsoft YaHei", 8), fg="#888", bg="#0a0a0f")
dev_label.pack(anchor="w")

# ===== 工具按钮行 =====
tool_frame = ttk.Frame(root)
tool_frame.pack(fill="x", padx=10, pady=2)

def test_asr():
    add_msg("系统", "测试: 生成 440Hz 测试音→ASR...", "#888")
    threading.Thread(target=_do_test_asr, daemon=True).start()

def _do_test_asr():
    try:
        wav_bytes, save_path = generate_test_wav()
        add_msg("系统", f"已保存: {os.path.basename(save_path)}, {len(wav_bytes)} bytes", "#888")
        add_msg("系统", "发送 ASR...", "#888")
        resp = requests.post(ASR_URL, data=wav_bytes, timeout=30)
        result = resp.json()
        add_msg("系统", f"ASR: HTTP {resp.status_code}, 结果:{result}", "#FFB74D")
    except Exception as e:
        add_msg("系统", f"ASR失败: {e}", "#f44336")

def test_direct():
    threading.Thread(target=_do_test_direct, daemon=True).start()

def _do_test_direct():
    try:
        r = requests.get("http://127.0.0.1:12026/v1/models", timeout=5)
        add_msg("系统", f"A3B直连: HTTP {r.status_code}", "#888")
        add_msg("系统", f"响应: {r.text[:200]}", "#4CAF50" if r.status_code == 200 else "#f44336")
    except Exception as e:
        add_msg("系统", f"A3B直连失败: {e}", "#f44336")

def upload_wav():
    path = filedialog.askopenfilename(
        title="选择 WAV",
        filetypes=[("WAV 文件", "*.wav"), ("所有文件", "*.*")]
    )
    if not path:
        return
    add_msg("系统", f"选择: {os.path.basename(path)}", "#888")
    try:
        with wave.open(path, "rb") as wf:
            params = (wf.getnchannels(), wf.getsampwidth(), wf.getframerate(), wf.getnframes())
            add_msg("系统", f"WAV: 声道={params[0]} 位宽={params[1]} 采样率={params[2]}Hz 帧数={params[3]}", "#888")
            data = wf.readframes(wf.getnframes())
        threading.Thread(target=_do_upload_asr, args=(data,), daemon=True).start()
    except Exception as e:
        add_msg("系统", f"读取失败: {e}", "#f44336")

def _do_upload_asr(wav_data):
    try:
        resp = requests.post(ASR_URL, data=wav_data, timeout=30)
        result = resp.json()
        add_msg("系统", f"ASR: HTTP {resp.status_code}, 结果:{result}", "#FFB74D")
    except Exception as e:
        add_msg("系统", f"ASR失败: {e}", "#f44336")

def diag_check():
    add_msg("系统", "诊断: 检查各服务...", "#888")
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
            status = "🟢" if alive else "🔴"
            msg = f"{svc.upper()} {status} ({host}:{port})"
            if err:
                msg += f" [{err}]"
            add_msg("系统", msg, "#4CAF50" if alive else "#f44336")
        except Exception as e:
            add_msg("系统", f"{svc.upper()} 检测失败: {e}", "#f44336")

def show_recordings():
    try:
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
        add_msg("系统", f"大小:{size} bytes, 时长:{dur:.1f}s", "#888")
    except Exception as e:
        add_msg("系统", f"读取失败: {e}", "#f44336")

def change_server():
    """重新选择服务器"""
    label, url = choose_server()
    if url:
        global SERVER, SERVER_LABEL, ASR_URL, CHAT_URL, TTS_URL, TTS_FALLBACK_URL, DIAG_URL
        SERVER = url
        SERVER_LABEL = label
        ASR_URL = f"{SERVER}/api/asr"
        CHAT_URL = f"{SERVER}/api/chat"
        TTS_URL = f"{SERVER}/api/tts"
        TTS_FALLBACK_URL = f"{SERVER}/api/tts-fallback"
        DIAG_URL = f"{SERVER}/api/diag/check"
        server_label.config(text=f"🟢 {SERVER_LABEL}")
        add_msg("系统", f"已切换到: {SERVER_LABEL} ({SERVER})", "#4CAF50")
        log.info(f"切换服务器: {SERVER_LABEL} — {SERVER}")

tk.Button(tool_frame, text="测试ASR",
          font=("Microsoft YaHei", 8), bg="#444", fg="#e0e0e0",
          relief="flat", padx=6, command=test_asr).pack(side="left", padx=(0, 3))
tk.Button(tool_frame, text="直连A3B",
          font=("Microsoft YaHei", 8), bg="#444", fg="#e0e0e0",
          relief="flat", padx=6, command=test_direct).pack(side="left", padx=(0, 3))
tk.Button(tool_frame, text="📁上传WAV",
          font=("Microsoft YaHei", 8), bg="#444", fg="#e0e0e0",
          relief="flat", padx=6, command=upload_wav).pack(side="left", padx=(0, 3))
tk.Button(tool_frame, text="🔍诊断",
          font=("Microsoft YaHei", 8), bg="#444", fg="#e0e0e0",
          relief="flat", padx=6, command=diag_check).pack(side="left", padx=(0, 3))
tk.Button(tool_frame, text="📂录音",
          font=("Microsoft YaHei", 8), bg="#444", fg="#e0e0e0",
          relief="flat", padx=6, command=show_recordings).pack(side="left", padx=(0, 3))
tk.Button(tool_frame, text="🔄换服务器",
          font=("Microsoft YaHei", 8), bg="#444", fg="#e0e0e0",
          relief="flat", padx=6, command=change_server).pack(side="left", padx=(0, 3))

# 文字输入
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

# 聊天区
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

# ===== 按钮事件 =====
def on_press(e):
    try:
        start_recording()
        status_label.config(text="🔴 录音中", fg="#f44336")
        record_btn.config(text="🔴 录音中", bg="#f44336")
    except Exception as ex:
        add_msg("系统", f"录音启动失败: {ex}", "#f44336")

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
                     wraplength=450, justify="left",
                     font=("Microsoft YaHei", 10),
                     fg=color, bg="#0d0d14")
    label.pack(anchor=align)
    root.after(50, lambda: chat_canvas.yview_moveto(1.0))


def show_error(msg):
    status_label.config(text=f"错误: {msg}", fg="#f44336")
    record_btn.config(text="按住说话", bg="#7C5CFC")
    add_msg("系统", f"错误: {msg}", "#f44336")


def process_audio(wav_data):
    global _last_wav_path
    try:
        resp = requests.post(ASR_URL, data=wav_data, verify=VERIFY, timeout=30)
        result = resp.json()
        log.info(f"ASR: {result}")
        if "error" in result:
            root.after(0, lambda: show_error(result["error"]))
            return
        text = result.get("text", "").strip()
        if not text:
            root.after(0, lambda: add_msg("系统", "ASR 返回空", "#FFB74D"))
            root.after(0, lambda: status_label.config(text="空闲", fg="#4CAF50"))
            root.after(0, lambda: record_btn.config(text="按住说话", bg="#7C5CFC"))
            return
        root.after(0, lambda: add_msg("你", text, "#4FC3F7"))

        status_label.config(text="思考中")
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
            else:
                fb = requests.post(TTS_FALLBACK_URL, json={"text": reply[:200]},
                                   verify=VERIFY, timeout=30)
                if fb.status_code == 200:
                    play_audio(fb.content)
        except:
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


def check_net():
    try:
        r = requests.get(f"{SERVER}/api/network/preview", verify=VERIFY, timeout=5)
        data = r.json()
        for p in data.get("previews", []):
            if p.get("to") == "tts" and p.get("latency", -1) >= 0:
                lat = p["latency"]
                color = "#4CAF50" if lat < 10 else "#FFB74D" if lat < 50 else "#f44336"
                net_label.config(text=f"{p['chosen'].upper()} {lat:.1f}ms", fg=color)
                break
    except:
        net_label.config(text="检测失败", fg="#f44336")
    root.after(15000, check_net)


def show_devices():
    def _show():
        devs = list_audio_devices()
        text = "; ".join(devs) if devs else "无输入设备"
        root.after(0, lambda: dev_label.config(text=f"设备: {text}"))
    threading.Thread(target=_show, daemon=True).start()


# 初始信息
add_msg("系统", f"服务器: {SERVER_LABEL} ({SERVER})", "#4CAF50")
add_msg("系统", "按住按钮说话, 松开自动识别", "#888")
add_msg("系统", f"录音目录: {RECORDINGS_DIR}", "#888")
add_msg("系统", "工具->测试ASR | 上传WAV | 诊断 | 录音 | 换服务器", "#888")
check_net()
show_devices()
root.mainloop()
