# 语音通话项目 — 当前状态与重难点总结

## 项目架构

```
用户（Win10/Cent Browser 或手机）
    │
    ├── Web版: https://[IPv6]:12056/voice
    │     └── voice.html（浏览器录音 → POST /api/asr）
    │
    ├── 桌面版: voice_client.py（Python tkinter + pyaudio）
    │
    ├── Rhasspy: http://192.168.1.21:12101（Docker）
    │     └── 音频前端（WebRTC降噪 + VAD打断）→ POST /api/asr
    │
    └── 后端: voice_server.py（aiohttp, 端口12054/12056）
          ├── /api/asr → ffmpeg转码/noisereduce降噪 → A3B llama.cpp (12026)
          ├── /api/chat → Hermes Gateway (8642) → DeepSeek
          ├── /api/tts → Qwen TTS (192.168.1.5:1234) → fallback Edge TTS
          └── /api/* → 聊天记录、网络管理等
```

## 后端接口状态

| 接口 | 状态 | 耗时 | 说明 |
|------|------|------|------|
| POST /api/asr | ✅ 可用 | ~1.5s | webm→ffmpeg→noisereduce→A3B，Rhasspy wav走快速通道 |
| POST /api/chat | ✅ 可用 | ~3-5s | 调 Hermes Gateway(8642) → DeepSeek |
| POST /api/tts | ⚠️ 有回退 | ~2s | Qwen解码不稳定，自动fallback到Edge TTS |
| POST /api/tts-fallback | ✅ 可用 | ~1s | Edge TTS直出mp3 |
| POST /api/sync_chat | ✅ 可用 | <0.1s | 存文件+备查 |
| /api/network/* | ✅ 可用 | <0.1s | 网络管理接口 |
| GET /voice | ✅ 可用 | <0.1s | 返回voice.html |
| GET /network | ✅ 可用 | <0.1s | 返回network.html |

## Git 仓库

https://github.com/cnpsx/voicegateway

| 文件 | 说明 |
|------|------|
| voice_server.py | 后端主服务（739行） |
| voice.html | Web版语音页面（901行） |
| voice_client.py | Python桌面客户端（368行） |
| network.html | 网络管理页面（466行） |
| rhasspy-deploy.md | Rhasspy集成需求与测试文档 v3 |
| TEST.md | 测试步骤文档 |
| README.md | 项目说明 |

## 重难点排序（从急到缓）

### ⭐⭐⭐ P0: 浏览器按钮无效（当前最大障碍）

**现象**：Win10 Cent Browser 上 voice 页面录音按钮点击无反应
**可能原因**：
1. `navigator.mediaDevices` 在非 HTTPS 下不可用 → 已用 HTTPS
2. Cent Browser 兼容性 → 需查看 Console 报错
**待解决**：需要在 Win10 上打开 F12 Console 看报错信息

### ⭐⭐⭐ P0: 录音停止按钮不稳定

**现象**：按住说话模式下松手不停止
**当前方案**：改用了 pointer events + setPointerCapture + pointercancel 兜底 + 全局 document pointerup + 60秒超时
**状态**：代码已修复但未在 Win10 Cent Browser 验证

### ⭐⭐ P1: TTS 解码不稳定

**现象**：Qwen TTS（192.168.1.5:1234）返回 audio codec tokens，解码为 wav 经常失败（返回92 bytes空数据）
**当前方案**：Qwen 失败自动回退 Edge TTS（需联网）
**状态**：可用但有延迟，Edge TTS 国内访问有时 503

### ⭐⭐ P1: Rhasspy ASR/TTS 配置已就绪但未联调

**状态**：
- Rhasspy Docker 已部署（端口12101）
- profile.json 已配置（ASR remote, TTS remote, VAD webrtc, 降噪 webrtc）
- TTS URL → http://192.168.1.21:12054/api/tts ✅
- ASR URL → http://192.168.1.21:12054/api/asr ✅
- 配置已生效（curl 验证通过）
**待解决**：未在 Win10 浏览器上打开 Rhasspy Web UI 测试语音

### ⭐ P2: noisereduce 降噪效果待验证

**状态**：已在 /api/asr 中加入 noisereduce 降噪（prop_decrease=0.8）
**待验证**：60dB 办公噪音环境下的识别率是否≥80%

### ⭐ P2: 持续监听模式待验证

**状态**：已实现 OVER 关键词检测，每3秒取增量 ASR，检测到 "over/结束/停止" 后处理全部音频
**待验证**：真实语音场景下是否能正确检测

### ⭐ P3: 桌面客户端未在 Win10 测试

**状态**：voice_client.py（pyaudio + tkinter）已写好
**问题**：pyaudio 导入报错，需安装正确

### ⭐ P3: GitHub 网络不通

**现象**：bt2 服务器连 GitHub 经常超时/504
**变通方案**：通过代理 192.168.1.234:7890 或 SSH key 推送

## 端口占用一览

| 端口 | 服务 | 状态 |
|------|------|------|
| 12026 | A3B llama.cpp | ✅ |
| 12050/12055 | 战情室 warroom.py | ✅ |
| 12051 | MCP Server | ✅ |
| 12052 | OCR API | ✅ |
| 12053 | 截屏工具 | ✅ |
| **12054/12056** | **语音服务 voice_server.py** | ✅ 本文档核心 |
| 12101 | Rhasspy Docker | ✅ |
| 8642 | Hermes Gateway | ✅ |
| 7890 | 科学上网 verge-mihomo | ⚠️ 关掉后可释放72G内存 |
| 8090 | Win10 MCP (Tailscale) | ⚠️ 需IPv6 |
