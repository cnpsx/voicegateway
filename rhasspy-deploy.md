# Rhasspy 语音前端集成 — 需求与测试文档 v3

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
# 拉取镜像
docker pull rhasspy/rhasspy:2.5
# 启动
docker run -d \
  --name rhasspy \
  --restart unless-stopped \
  -v ~/.config/rhasspy:/home/rhasspy/.config/rhasspy \
  -p 12101:12101 \
  rhasspy/rhasspy:2.5
```
磁盘~800MB，内存~300MB

## 三、核心需求点（新增）

### 3.1 打断逻辑：用户说话优先
- 用户说话时，Rhasspy 的 VAD 必须识别为"用户语音"，不能误触发打断
- 用户说完后，停顿约 2 秒，ASR 开始识别
- TTS 播放过程中用户一开口，立即打断 TTS，开始录音

### 3.2 语速兼容性
- 快语速（如连续说 20 个字不停顿）：音频完整录制，不截断
- 慢语速（字间停顿 1-2 秒）：不提前结束录音，等用户说完
- 正常语速：停顿 2 秒触发 ASR

### 3.3 VAD 参数调整方向
VAD mode 3（最敏感），但配合较长的静音超时（~2s），确保：
- 短停顿（思考、换气）不打断
- 真正说完后 2 秒开始识别

## 四、Rhasspy 配置

配置文件：~/.config/rhasspy/profiles/zh/profile.json

### 4.1 语音转文字（ASR）— Remote HTTP
```json
{
  "speech_to_text": {
    "system": "remote",
    "remote": {
      "url": "http://127.0.0.1:12054/api/asr"
    }
  }
}
```

### 4.2 其他模块 — 全部禁用
- TTS: dummy
- 唤醒词: dummy
- 意图识别: dummy
- 对话管理: dummy

### 4.3 音频处理
- 降噪: webrtc（denoising + auto_gain + echo_cancellation）
- VAD: webrtcvad mode 3
- 静音超时: ~2000ms（待实际调试确定）

## 五、/api/asr 接口要求

| 项目 | 要求 |
|------|------|
| 接收格式 | audio/wav, 16kHz, mono, 16-bit PCM |
| 传输 | POST 整段 wav 文件 |
| 返回 | {"text": "..."} 或 {"error": "..."} |
| 超时 | 30秒 |
| 内部流程 | wav → noisereduce降噪 → A3B llama.cpp ASR |

## 六、测试用例 v3（含打断/语速专项）

### 6.1 部署与配置
| ID | 测试项 | 操作 | 预期 |
|----|--------|------|------|
| T1 | Docker启动 | docker run | 容器正常，12101可访问 |
| T1-1 | 重启保持 | docker restart | 正常重启，端口可用 |
| T1-2 | 端口冲突 | 12101被占时启动 | 失败，日志提示冲突 |
| T2 | Web UI | 访问 localhost:12101 | 控制台加载 |
| T3 | 模块状态 | Settings→System | ASR=remote, TTS=dummy |
| T3-1 | 持久化 | 修改后重启 | 配置不丢 |

### 6.2 降噪效果
测试话术（5句固定）："今天天气怎么样""打开空调""测试测试""我要去北京""好的谢谢"

| ID | 测试项 | 操作 | 预期 |
|----|--------|------|------|
| N1 | 安静环境 | 无噪音说测试语 | 识别率≥95% |
| N2 | 办公噪音 | 60dB背景噪音 | 识别率≥80% |
| N3 | 极端噪音 | 风扇+键盘声 | 识别率≥60% |
| N4 | SNR对比 | 降噪前后音频 | SNR提升≥10dB |

### 6.3 音频传输
| ID | 测试项 | 操作 | 预期 |
|----|--------|------|------|
| A1 | 基础传输 | 说"测试" | 收到16kHz mono wav |
| A2 | 长音频 | 说10秒句子 | wav完整，识别不截断 |
| A2-1 | 临界超时 | 说29秒 | 正常识别 |
| A2-2 | 超时 | 说31秒 | 返回超时错误 |
| A3 | 格式正确 | 抓包检查 | audio/wav, 16kHz, mono, 16-bit |
| A3-1 | 错误格式 | 传8kHz wav | 返回明确错误 |
| A4 | 返回值 | 检查返回 | {"text": "..."} |

### 6.4 VAD打断与语速兼容性（核心）
| ID | 测试项 | 操作 | 预期 |
|----|--------|------|------|
| V1 | 说话即打断 | TTS播放时说"停" | 立即打断TTS |
| V2 | 静音不打断 | 不说话5秒 | 不误触发 |
| V3 | 噪音不误触 | 60dB办公噪音 | 不触发打断 |
| V4 | 短语音打断 | 说1秒"停" | 能触发打断 |
| **V5** | **正常说完才打断** | 说10秒长句，中间停顿2次（换气） | **不停顿不打断，说完后2秒触发ASR** |
| **V6** | **快语速** | 连续快说20字不停顿 | **音频完整，不截断** |
| **V7** | **慢语速** | 每字间隔1-2秒说 | **不提前结束，完整录完** |
| **V8** | **思考停顿** | 说话中间"呃…那个…"停顿3秒 | **不误打断，等真正说完** |

### 6.5 端到端
| ID | 测试项 | 操作 | 预期 |
|----|--------|------|------|
| E1 | 完整对话 | 说"今天天气" | 全程<3s |
| E2 | 连续对话 | 连续3个问题 | 每次正确，不中断 |
| E3 | TTS打断 | 播放时说新问题 | TTS中断，新语音识别 |
| E4 | 无效语音 | 说"啊啦啦" | ASR空返回，流程不崩 |
| E5 | 稳定性 | 连续100次 | 成功率≥95% |

## 七、验收标准

| 指标 | 当前 | 目标 | 测试方法 |
|------|------|------|----------|
| 安静识别率 | >90% | >95% | N1, 100次 |
| 办公噪音识别率 | <60% | >80% | N2, 100次 |
| 端到端延迟 | 3-5s | <3s | E1逐段计时 |
| 打断反应 | 手动按钮 | 语音自动 | V1, V4 |
| **不误打断** | 未测试 | 思考停顿不打断 | V5, V8 |
| **语速兼容** | 未测试 | 快慢语速均可 | V6, V7 |
|| 稳定性 | 未测试 | 100次≥95% | E5 |

## 八、部署实战经验（踩坑记录）

### 8.1 容器运行时选择
Podman（无 root 守护进程）可替代 Docker，命令完全兼容。适用场景：服务器没有 sudo 密码，apt 安装 Docker 被网络/锁阻塞。

### 8.2 Docker Hub 国内访问
直连 registry-1.docker.io 超时（GFW）。国内镜像站（中科大/网易/百度/阿里云）DNS 解析经常失败。可靠方法：开代理 export https_proxy=http://192.168.1.234:7890 再 pull。镜像 tag 用 latest，2.5 不存在。

### 8.3 容器启动要点
--profile zh 必传，否则报错退出。配置文件挂载到容器内 /root/.config/rhasspy/profiles 而不是 /home/rhasspy/。容器内无 ALSA，mic/sound 必须设成 dummy。

### 8.4 配置验证
用 curl 调 API 验证才是真实配置，Web UI 可能显示旧数据。

### 8.5 后端优化
/api/asr 提取 _send_to_asr() 让 Rhasspy wav 和 webm 共用。Rhasspy 来的 wav 直接走快速通道（跳过 ffmpeg 和 noisereduce）。识别结果自动同步到聊天记录方便调试。
