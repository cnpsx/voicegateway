# Voice Gateway — Hermes 语音对话系统

语音室客户端 + 服务端，支持 ASR（A3B llama.cpp）+ TTS（Qwen/Edge）+ 网络管理。

## 文件说明

| 文件 | 说明 |
|---|---|
| `voice_server.py` | 后端服务（aiohttp），端口 12054 HTTP / 12056 HTTPS |
| `voice.html` | Web 版语音页面（浏览器录音） |
| `voice_client.py` | Python tkinter 客户端（本地录音） |
| `network.html` | 网络管理页面（多设备优先级配置） |
| `network_config.example.json` | 网络配置示例 |

## 架构

```
用户 → voice.html/voice_client.py
     → POST /api/asr（ffmpeg转wav + noisereduce降噪 → A3B llama.cpp）
     → POST /api/chat（Hermes Gateway）
     → POST /api/tts（Qwen TTS → fallback Edge TTS）
     → 播放语音
```

## 依赖

```bash
pip install aiohttp edge-tts noisereduce numpy
```

客户端额外：
```bash
pip install pyaudio pygame requests
```
