# -*- coding: utf-8 -*-
"""
语音室客户端 — Python tkinter 版
按住说话，松开自动识别 + 对话 + 语音播放
"""

import tkinter as tk
from tkinter import ttk
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
import struct  # 生成测试音频

# ===== 配置 =====
SERVER = "http://[2408:8256:3500:1f3:a66e:47a9:2ac0:e5dc]:12054"
ASR_URL = f"{SERVER}/api/asr"
CHAT_URL = f"{SERVER}/api/chat"
TTS_URL = f"{SERVER}/api/tts"
TTS_FALLBACK_URL = f"{SERVER}/api/tts-fallback"
VERIFY = False

# ===== 录音状态 =====
_recording = False
_frames = []
_stream = None
_pyaudio_inst = None


def get_pyaudio():
    global _pyaudio_inst
    if _pyaudio_inst is None:
        import pyaudio
        _pyaudio_inst = pyaudio.PyAudio()
    return _pyaudio_inst


def start_recording():
    global _recording, _frames, _stream
    if _recording:
        return
    import pyaudio as _pa
    _recording = True
    _frames = []
    p = get_pyaudio()
    _stream = p.open(
        format=_pa.paInt16, channels=1, rate=16000,
        input=True, frames_per_buffer=1024,
    )
    def _read():
        while _recording:
            try:
                data = _stream.read(1024, exception_on_overflow=False)
                _frames.append(data)
            except:
                break
    threading.Thread(target=_read, daemon=True).start()


def stop_recording():
    global _recording, _stream
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
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"".join(_frames))
    return buf.getvalue()


# ===== GUI =====
root = tk.Tk()
root.title("语音室 - Hermes")
root.geometry("420x620")
root.configure(bg="#0a0a0f")
root.resizable(False, False)

style = ttk.Style()
style.theme_use("clam")
style.configure("TFrame", background="#0a0a0f")

# 顶部
top = ttk.Frame(root)
top.pack(fill="x", padx=10, pady=8)
tk.Label(top, text="语音室", font=("Microsoft YaHei", 14, "bold"),
         fg="#e0e0e0", bg="#0a0a0f").pack(side="left")
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


def on_press(e):
    start_recording()
    status_label.config(text="录音中", fg="#f44336")
    record_btn.config(text="录音中", bg="#f44336")


def on_release(e):
    wav = stop_recording()
    if not wav:
        status_label.config(text="空闲", fg="#4CAF50")
        record_btn.config(text="按住说话", bg="#7C5CFC")
        return
    status_label.config(text="识别中", fg="#FFB74D")
    record_btn.config(text="处理中", bg="#FFB74D")
    threading.Thread(target=process_audio, args=(wav,), daemon=True).start()


record_btn.bind("<ButtonPress-1>", on_press)
record_btn.bind("<ButtonRelease-1>", on_release)

# 测试区
test_frame = ttk.Frame(root)
test_frame.pack(fill="x", padx=10, pady=2)

def test_asr():
    """生成440Hz测试音→ASR→显示结果"""
    add_msg("系统", "测试: 生成测试音频并发送ASR...", "#888")
    threading.Thread(target=_do_test_asr, daemon=True).start()

def _do_test_asr():
    try:
        # 生成2秒440Hz正弦波 wav
        sample_rate = 16000
        duration = 2
        frames = []
        for i in range(sample_rate * duration):
            val = int(16000 * 0.5 * __import__('math').sin(2 * 3.14159 * 440 * i / sample_rate))
            frames.append(struct.pack('<h', val))
        wav_data = b''.join(frames)
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(wav_data)
        wav_bytes = buf.getvalue()
        add_msg("系统", f"测试音频: {len(wav_bytes)} bytes, 发送中...", "#888")
        
        resp = requests.post(ASR_URL, data=wav_bytes, timeout=30)
        add_msg("系统", f"ASR响应: HTTP {resp.status_code}", "#888")
        result = resp.json()
        add_msg("系统", f"ASR结果: {result}", "#FFB74D")
    except Exception as e:
        add_msg("系统", f"ASR测试失败: {str(e)}", "#f44336")

def test_direct():
    """直接测试A3B ASR接口"""
    add_msg("系统", "测试: 直接ping A3B ASR接口...", "#888")
    threading.Thread(target=_do_test_direct, daemon=True).start()

def _do_test_direct():
    try:
        r = requests.get("http://127.0.0.1:12026/v1/models", timeout=5)
        add_msg("系统", f"A3B 直连: HTTP {r.status_code}", "#888")
        if r.status_code == 200:
            add_msg("系统", f"A3B: {r.text[:200]}", "#4CAF50")
        else:
            add_msg("系统", f"A3B: {r.text[:200]}", "#f44336")
    except Exception as e:
        add_msg("系统", f"A3B 直连失败: {str(e)}", "#f44336")

test_asr_btn = tk.Button(test_frame, text="测试ASR",
                         font=("Microsoft YaHei", 9),
                         bg="#444", fg="#e0e0e0",
                         relief="flat", padx=8,
                         command=test_asr)
test_asr_btn.pack(side="left", padx=(0, 4))

test_direct_btn = tk.Button(test_frame, text="测试A3B直连",
                            font=("Microsoft YaHei", 9),
                            bg="#444", fg="#e0e0e0",
                            relief="flat", padx=8,
                            command=test_direct)
test_direct_btn.pack(side="left")

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


def add_msg(sender, text, color="#e0e0e0"):
    frame = ttk.Frame(msg_frame)
    frame.pack(fill="x", pady=4, padx=4)
    align = "w" if sender == "Hermes" else "e"
    label = tk.Label(frame, text=f"{sender}: {text}",
                     wraplength=350, justify="left",
                     font=("Microsoft YaHei", 10),
                     fg=color, bg="#0d0d14")
    label.pack(anchor=align)
    root.after(50, lambda: chat_canvas.yview_moveto(1.0))


def show_error(msg):
    status_label.config(text=f"错误: {msg}", fg="#f44336")
    record_btn.config(text="按住说话", bg="#7C5CFC")
    add_msg("系统", f"错误: {msg}", "#f44336")


def play_audio(data):
    try:
        import pygame
        pygame.mixer.init(frequency=24000)
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.write(data)
        tmp.close()
        pygame.mixer.music.load(tmp.name)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            time.sleep(0.1)
        pygame.mixer.quit()
        try:
            os.unlink(tmp.name)
        except:
            pass
    except Exception as e:
        print("play error:", e)


def process_audio(wav_data):
    try:
        resp = requests.post(ASR_URL, data=wav_data, verify=VERIFY, timeout=30)
        result = resp.json()
        if "error" in result:
            root.after(0, lambda: show_error(result["error"]))
            return
        text = result.get("text", "").strip()
        if not text:
            root.after(0, lambda: status_label.config(text="未识别到语音", fg="#FFB74D"))
            root.after(0, lambda: record_btn.config(text="按住说话", bg="#7C5CFC"))
            return
        root.after(0, lambda: add_msg("你", text, "#4FC3F7"))
        status_label.config(text="思考中")
        chat_resp = requests.post(CHAT_URL, json={"text": text}, verify=VERIFY, timeout=60)
        chat_result = chat_resp.json()
        reply = chat_result.get("text", "")
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
            pass
        root.after(0, lambda: status_label.config(text="空闲", fg="#4CAF50"))
        root.after(0, lambda: record_btn.config(text="按住说话", bg="#7C5CFC"))
    except Exception as e:
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


add_msg("系统", "按住按钮说话, 松开自动识别", "#888")
check_net()
root.mainloop()
