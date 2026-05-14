# Rhasspy 语音前端集成 — 需求与测试文档

## 一、架构概述

```
用户语音 → Rhasspy（降噪 + VAD + 打断）
              ↓ 16kHz 单声道 wav
          POST /api/asr（A3B llama.cpp）
              ↓ text
          POST /api/chat（Hermes DeepSeek）
              ↓ reply text
          POST /api/tts（Qwen / Edge TTS）
              ↓ 音频
          播放 + Rhasspy 打断监测
```

Rhasspy 只做音频前端处理，不参与 ASR/TTS/Chat。

## 二、安装部署

### 方案 A：Docker（推荐，30分钟）
```bash
# 拉取 Rhasspy 镜像
docker pull rhasspy/rhasspy:2.5
# 启动
docker run -d \
  --name rhasspy \
  --restart unless-stopped \
  -v ~/.config/rhasspy:/home/rhasspy/.config/rhasspy \
  -p 12101:12101 \
  rhasspy/rhasspy:2.5
```
- 磁盘占用：约 800MB（含镜像）
- 运行时内存：约 300MB

### 方案 B：原生安装（备选，60分钟）
```bash
# 依赖
sudo apt install python3-venv python3-dev build-essential \
  libatlas-base-dev libopenblas-dev
# 创建虚拟环境
python3 -m venv ~/rhasspy-env
source ~/rhasspy-env/bin/activate
# 安装 Rhasspy
pip install rhasspy  # 或从源码
```
- 磁盘占用：约 200MB
- 运行时内存：约 200MB

## 三、Rhasspy 配置（profile.json）

配置文件路径：~/.config/rhasspy/profiles/zh/profile.json

### 3.1 语音转文字（ASR）— Remote HTTP
```json
{
  "speech_to_text": {
    "system": "remote",
    "remote": {
      "url": "http://127.0.0.1:12054/api/asr",
      "type": "microphone"
    }
  }
}
```
预期行为：Rhasspy 录音完成后，POST 整段 16kHz 单声道 wav 到 /api/asr，接收 JSON 返回。

### 3.2 文字转语音（TTS）— 禁用
```json
{
  "text_to_speech": {
    "system": "dummy"
  }
}
```

### 3.3 语音活动检测（VAD）— WebRTC
```json
{
  "voice_activity_detection": {
    "system": "webrtcvad",
    "webrtcvad": {
      "mode": 3
    }
  }
}
```
mode 3 为最敏感模式，检测到语音立即停止 TTS 播放。

### 3.4 音频降噪
```json
{
  "audio_processing": {
    "denoising": "webrtc",
    "auto_gain": true,
    "echo_cancellation": true
  }
}
```

### 3.5 其他模块 — 全部禁用
```json
{
  "intent_recognition": {"system": "dummy"},
  "dialogue_manager": {"system": "dummy"},
  "wake_word": {"system": "dummy"},
  "handle": {"system": "dummy"}
}
```

### 3.6 音频输入输出
```json
{
  "microphone": {
    "system": "arec"  // Linux ALSA 录音
  },
  "sounds": {
    "system": "aplay"  // Linux ALSA 播放
  }
}
```

## 四、对接要求

### 4.1 我们的 /api/asr 需支持
- 接收 16kHz 单声道 16-bit PCM wav（直接识别，跳过 webm 检测和 ffmpeg 转码）
- 返回格式：`{"text": "..."}`
- 超时：30秒
- 内部流程：wav → noisereduce 降噪 → A3B llama.cpp ASR

### 4.2 音频流走向
```
麦克风 → Rhasspy（WebRTC 降噪 + VAD 检测）→ 整段 wav → POST → /api/asr
```

## 五、测试用例

### 5.1 部署测试
| 编号 | 测试项 | 操作 | 预期结果 |
|------|--------|------|----------|
| T1 | Docker 启动 | docker run rhasspy | 容器正常运行，端口12101可访问 |
| T2 | Web UI 访问 | 浏览器打开 http://localhost:12101 | Rhasspy 控制台页面加载 |
| T3 | 模块状态 | 查看 Settings → System | ASR=remote, TTS=dummy, VAD=webrtc, 降噪=webrtc |

### 5.2 降噪效果测试
| 编号 | 测试项 | 操作 | 预期结果 |
|------|--------|------|----------|
| N1 | 安静环境 | 无背景噪音时说"测试" | ASR 返回 "测试" |
| N2 | 办公室噪音 | 播放背景音乐/人声时说"测试" | ASR 仍能识别出 "测试" |
| N3 | 极端噪音 | 播放风扇/键盘声时说"测试" | 降噪后 ASR 能部分识别 |
| N4 | 降噪对比 | 分别录制带/不带 Rhasspy 降噪的音频 | 带降噪的波形更干净 |

### 5.3 音频传输测试
| 编号 | 测试项 | 操作 | 预期结果 |
|------|--------|------|----------|
| A1 | 基础传输 | 说一个短词"测试" | /api/asr 收到有效的 wav 数据 |
| A2 | 长音频 | 说约10秒的句子 | wav 时长正确，识别完整 |
| A3 | 音频格式 | 抓包检查 POST 内容 | Content-Type: audio/wav，16kHz，mono，16-bit |
| A4 | 返回值 | 检查 /api/asr 返回 | {"text": "..."} JSON |

### 5.4 VAD 打断测试
| 编号 | 测试项 | 操作 | 预期结果 |
|------|--------|------|----------|
| V1 | 说话即打断 | TTS 播放时说"停" | Rhasspy 检测到语音，立即打断 TTS |
| V2 | 静音不打断 | 不说话5秒 | 不会误触发打断 |
| V3 | 噪音不误触 | 咳嗽/敲桌子 | 不会触发打断（VAD 灵敏度可调） |

### 5.5 端到端测试
| 编号 | 测试项 | 操作 | 预期结果 |
|------|--------|------|----------|
| E1 | 完整对话 | 说"今天天气怎么样" | 降噪→ASR识别→Chat回复→TTS播放，全程<3s |
| E2 | 连续对话 | 连续说3个问题 | 每次都能正确识别和回复 |
| E3 | 打断重试 | TTS播放时说新问题 | TTS中断，新语音开始识别 |

## 六、验收标准

| 指标 | 当前 | 目标 |
|------|------|------|
| 降噪效果 | 轻度降噪（noisereduce） | 办公室环境无障碍 |
| 端到端延迟 | 3-5s | <3s |
| VAD 打断 | 手动按钮 | 语音自动打断 |
| 识别率（安静） | >90% | >95% |
| 识别率（噪音） | <60% | >80% |
