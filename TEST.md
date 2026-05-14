# Rhasspy 集成 — 测试步骤

## 环境信息

| 项目 | 值 |
|------|-----|
| Rhasspy Web UI | http://192.168.1.21:12101 |
| ASR 接口 | http://192.168.1.21:12054/api/asr |
| 配置目录 | ~/.config/rhasspy/profiles/zh/profile.json |
| 容器名 | rhasspy |

## 第一步：确认服务状态

在 bt2 服务器上运行：
```bash
# 检查 Rhasspy 容器
podman ps
# 检查日志
podman logs rhasspy --tail 10
# 测试 Web UI
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:12101
```

预期：容器运行中，HTTP 200。

## 第二步：检查 /api/asr 接口

```bash
# 生成测试音频
ffmpeg -y -f lavfi -i "sine=frequency=440:duration=3" -ar 16000 -ac 1 /tmp/test.wav 2>/dev/null
# 测试 ASR
curl -s -X POST --data-binary @/tmp/test.wav \
  -H "Content-Type: application/octet-stream" \
  http://127.0.0.1:12054/api/asr
```

预期：返回 `{"text":"..."}`。

## 第三步：在 Win10 上访问 Rhasspy Web UI

浏览器打开 http://192.168.1.21:12101

## 第四步：测试降噪效果

测试话术（5句固定）：
1. "今天天气怎么样"
2. "打开空调"
3. "测试测试"
4. "我要去北京"
5. "好的谢谢"

测试条件：
- N1：安静环境 → 识别率≥95%
- N2：60dB 办公噪音 → 识别率≥80%
- N3：风扇+键盘噪音 → 识别率≥60%

## 第五步：测试 VAD 打断

| 测试 | 操作 | 预期 |
|------|------|------|
| V1 打断 | TTS 播放时说"停" | 立即打断 |
| V2 静音 | 不说话 5 秒 | 不误触发 |
| V3 噪音 | 60dB 噪音 | 不误触发 |
| V4 短语音 | 说 1 秒"停" | 能触发 |
| **V5 说完才打断** | 说 10 秒长句，中间停顿 2 次 | 不停顿不打断，说完 2 秒后触发 ASR |
| **V6 快语速** | 快说 20 字 | 不截断 |
| **V7 慢语速** | 每字间隔 1-2 秒 | 不提前结束 |
| **V8 思考停顿** | 说话中"呃…那个…"停顿 3 秒 | 不误打断 |

## 第六步：端到端测试

| 测试 | 操作 | 预期 |
|------|------|------|
| E1 完整对话 | 说"今天天气" | 全程<3s |
| E2 连续对话 | 连续 3 个问题 | 每次正确，不中断 |
| E3 打断重试 | TTS 播放时说新问题 | TTS 中断，新语音识别 |
| E4 无效语音 | 说"啊啦啦" | 不报错 |
| E5 稳定性 | 连续 100 次 | 成功率≥95% |

## 常见问题

### Q: Rhasspy Web UI 打不开
```bash
podman logs rhasspy --tail 10
podman restart rhasspy
```

### Q: ASR 返回错误
```bash
curl -v -X POST --data-binary @test.wav http://127.0.0.1:12054/api/asr
```

### Q: 容器端口被占用
```bash
fuser -k 12101/tcp
podman restart rhasspy
```
