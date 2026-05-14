# 语音通话项目状态汇总

## 当前进度

### ✅ 已完成
- **voice_server.py** (bt2:12054/12056): WebRTC VAD 持续监听 + 按住说话双模式
- **ASR 管线**: 浏览器录音 → /api/asr → ffmpeg 转码 → noisereduce 降噪 → A3B llama.cpp (12026)
- **LLM 对话**: /api/chat → Hermes Gateway (127.0.0.1:8642) → DeepSeek API
- **TTS 播放**: /api/tts → Qwen TTS (192.168.1.5:1234) / Edge TTS fallback
- **打字输入 + 语音重放** ✅
- **流水线圈 UI**: 麦克风90秒倒计时 + ASR/Hermes/TTS 三圈状态显示
- **网络检测**: /api/network/preview 定时 ping 判断绿/黄/红
- **后端诊断 API**: /api/diag/check?service=xxx 端口探测，/api/diag/summary

### ⚠️ 当前问题

**1. 圆圈点击诊断无反应**
- 前端 TimerRing 构造函数改了，添加了 click 监听
- 但 `handleCheck` 中的 `this.err` 判断可能有问题——`err.classList.contains('show')` 的检查在点击时可能不是 '✕'
- debug: 红叉状态下 err 元素 classList 包含 'show'，textContent 是 '✕'
- 需要进一步定位是 click 事件没绑上还是 _onClick 里条件判断不通过

**2. 录音功能"没发送，没有 chunk"**
- 默认模式是"按住说话"（hold mode），但 mouseup 可能和 click 冲突
- 已修 mouseup 只在按钮 mousedown 之后才触发
- 但用户说"点了按钮没反应"——可能 VAD 库或 MediaRecorder 的 dataavailable 没触发
- 建议：改"按住说话"模式直接 click 切换（不依赖 mouseup）

**3. TTS 服务（192.168.1.5:1234）不稳定**
- 用户 Win10 上跑的 Qwen TTS
- 后端端口探测有时通有时不通

**4. CDT 快捷键可能与 MCP 冲突**
- 用户电脑上有 Macro Toolworks 等自动化工具的快捷键

## 待办
- [ ] 修复圆圈点击诊断
- [ ] 修复录音功能
- [ ] TTS 稳定性优化
- [ ] 看板错误记录集成
- [ ] 清理设计文档后推送到 GitHub 并打标签
