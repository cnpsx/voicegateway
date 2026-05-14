# Rhasspy 语音前端集成 — 需求与测试文档 v2

## 一、架构概述

用户语音 → Rhasspy（WebRTC降噪 + VAD打断）
              ↓ 16kHz mono 16-bit PCM wav
          POST /api/asr（A3B llama.cpp）
              ↓ text
          POST /api/chat（Hermes DeepSeek）
              ↓ reply text
          POST /api/tts（Qwen TTS / Edge fallback）
              ↓ audio
          播放 + Rhasspy VAD打断监测

Rhasspy 只做音频前端，不参与 ASR/TTS/Chat。

## 二、部署方案

### 方案：Docker（首选）
```bash
# 安装 Docker
sudo apt-get install docker.io
sudo usermod -aG docker $USER
# 重启终端后：
docker pull rhasspy/rhasspy:2.5
docker run -d \
  --name rhasspy \
  --restart unless-stopped \
  -v ~/.config/rhasspy:/home/rhasspy/.config/rhasspy \
  -p 12101:12101 \
  rhasspy/rhasspy:2.5
```
磁盘占用约800MB，运行时内存约300MB。

### 备选：原生 Python 安装
```bash
sudo apt install python3-venv python3-dev build-essential
python3 -m venv ~/rhasspy-env
source ~/rhasspy-env/bin/activate
pip install rhasspy
```

## 三、Rhasspy 配置

配置文件：~/.config/rhasspy/profiles/zh/profile.json

### 核心配置
- ASR: remote_http → http://127.0.0.1:12054/api/asr
- TTS: dummy（禁用）
- VAD: webrtcvad mode 3（最敏感）
- 降噪: webrtc（denoising + auto_gain + echo_cancellation）
- 唤醒词: dummy
- 意图识别: dummy
- 对话管理: dummy

## 四、/api/asr 接口要求（Rhasspy 对接用）

| 项目 | 要求 |
|------|------|
| 接收格式 | audio/wav, 16kHz, mono, 16-bit PCM |
| 传输方式 | POST 整段 wav 文件 |
| 返回格式 | {"text": "识别结果"} |
| 超时 | 30秒 |
| 错误返回 | {"error": "错误描述"} |
| 内部流程 | wav → noisereduce降噪 → A3B llama.cpp ASR |

## 五、测试用例 v2（含豆包优化建议）

### 5.1 部署测试
| ID | 测试项 | 操作 | 预期结果 |
|----|--------|------|----------|
| T1 | Docker启动 | docker run rhasspy | 容器正常运行，12101可访问 |
| T1-1 | 容器重启 | docker restart rhasspy | 正常重启，端口仍可用 |
| T1-2 | 端口冲突 | 12101被占用时启动 | 启动失败，日志提示端口冲突 |
| T2 | Web UI | 访问 http://localhost:12101 | Rhasspy控制台加载 |
| T3 | 模块状态 | 查看Settings→System | ASR=remote, TTS=dummy, VAD=webrtc |
| T3-1 | 配置持久化 | 修改配置后重启容器 | 配置不丢失，仍为remote_http |

### 5.2 降噪效果测试
测试话术统一：5句固定测试语（"今天天气怎么样""打开空调""测试测试""我要去北京""好的谢谢"）

| ID | 测试项 | 操作 | 预期结果 |
|----|--------|------|----------|
| N1 | 安静环境 | 无噪音时说测试语 | 识别率≥95% |
| N2 | 办公噪音 | 60dB背景噪音时说测试语 | 识别率≥80% |
| N3 | 极端噪音 | 风扇+键盘声时说测试语 | 识别率≥60% |
| N4 | SNR对比 | 对比降噪前后的音频信噪比 | 降噪后SNR提升≥10dB |

### 5.3 音频传输测试
| ID | 测试项 | 操作 | 预期结果 |
|----|--------|------|----------|
| A1 | 基础传输 | 说短词"测试" | 收到有效16kHz mono wav |
| A2 | 长音频 | 说10秒句子 | wav时长正确，识别完整 |
| A2-1 | 临界超时 | 说29秒内容 | 正常识别 |
| A2-2 | 超时测试 | 说31秒内容 | 触发30秒超时，返回超时错误 |
| A3 | 格式正确 | 抓包检查POST | Content-Type: audio/wav, 16kHz, mono, 16-bit |
| A3-1 | 错误格式 | 传8kHz/24bit wav | 返回明确错误提示 {"error":"..."} |
| A4 | 返回值 | 检查/api/asr返回 | {"text": "..."} JSON |
| A5 | 断网测试 | 传输中断开网络 | Rhasspy重试/返回超时，不崩溃 |

### 5.4 VAD打断测试
| ID | 测试项 | 操作 | 预期结果 |
|----|--------|------|----------|
| V1 | 说话即打断 | TTS播放时说"停" | 立即打断TTS |
| V2 | 静音不打断 | 不说话5秒 | 不误触发 |
| V3 | 噪音不误触 | 60dB办公噪音 | 不触发打断 |
| V3-1 | VAD灵敏度调优 | 调高低灵敏度后测试 | 噪音误触率<5% |
| V4 | 短语音打断 | 说1秒短词"停" | 仍能触发打断 |

### 5.5 端到端测试
| ID | 测试项 | 操作 | 预期结果 |
|----|--------|------|----------|
| E1 | 完整对话 | 说"今天天气怎么样" | 降噪<500ms + ASR<1s + Chat+TTS<1.5s，全程<3s |
| E2 | 连续对话 | 连续3个问题 | 每次正确识别回复，流程不中断 |
| E3 | 打断重试 | TTS播放时说新问题 | TTS中断，新语音识别 |
| E4 | 无效语音 | 说无意义音节"啊啦啦" | ASR返回空text，Chat/TTS不报错 |
| E5 | 稳定性 | 连续100次端到端对话 | 成功率≥95%（无崩溃/无漏识别） |

## 六、验收标准

| 指标 | 当前 | 目标 | 测试方法 |
|------|------|------|----------|
| 降噪效果 | noisereduce轻度 | 60dB噪音下识别率≥80% | N2测试 |
| 端到端延迟 | 3-5s | <3s | 逐段计时 |
| VAD打断 | 手动按钮 | 语音自动打断 | V1-V4 |
| 安静识别率 | >90% | >95% | N1测试，100次统计 |
| 噪音识别率 | <60% | >80% | N2测试，100次统计 |
| 稳定性 | 未测试 | 100次≥95%成功率 | E5测试 |
| 配置持久化 | 未测试 | 重启不丢失 | T3-1测试 |
