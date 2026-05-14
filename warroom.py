"""
中台作战室 — MCP Server + Web 双通
四个角色: 你(决策者), Hermes(架构), pro(计划), chat(写代码)
每轮每角色只能发言一次

MCP 工具:
  - camera_snapshot: 拍照
  - voice_record: 录音
  - voice_transcribe: 语音转文字
  - warroom_speak: 发言
  - warroom_status: 获取状态

Web: 四色聊天室 + 拍照/录音按钮
"""

import asyncio
import base64
import json
import os
import ssl
import logging
import io
import subprocess
import tempfile
import time
from datetime import datetime
from typing import Any

import aiohttp
from aiohttp import web

try:
    import mcp.types as types
    from mcp.server import Server, NotificationOptions
    from mcp.server.models import InitializationOptions
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

# ══════════════════════════════════════════
# 配置
# ══════════════════════════════════════════
HOST = "127.0.0.1"
PORT = 12055       # HTTP
HTTPS_PORT = 12050  # HTTPS 主端口
MCP_PORT = 12051    # MCP 独立端口

CERT_DIR = os.path.expanduser("~/report2db/certs")
SSL_CERT = os.path.join(CERT_DIR, "cert.pem")
SSL_KEY = os.path.join(CERT_DIR, "key.pem")

# Omni LLM
OMNI_URL = "http://127.0.0.1:12026/v1/chat/completions"
OMNI_KEY = ""

# 拍照保存目录
PHOTO_DIR = os.path.expanduser("~/report2db/photos")
os.makedirs(PHOTO_DIR, exist_ok=True)

# 配置保存目录
CONFIG_DIR = os.path.expanduser("~/report2db/config")
os.makedirs(CONFIG_DIR, exist_ok=True)
IP_CONFIG_FILE = os.path.join(CONFIG_DIR, "ip_config.json")
ADDR_CONFIG_FILE = os.path.join(CONFIG_DIR, "addr_config.json")

# IP档位模式
IP_MODES = {
    "auto": "自动（8:00-20:00 IPv6优先，20:00-8:00 IPv4优先）",
    "ipv6": "强制IPv6",
    "ipv4": "强制IPv4",
    "tailscale": "Tailscale备用",
}

# 默认地址配置（4端点 × 3档，每档含 addr + locked）
ENDPOINTS = ["server", "user", "asr", "tts"]
DEFAULT_ADDRS = {}


# ══════════════════════════════════════════
# IP档位配置管理
# ══════════════════════════════════════════
class IPConfig:
    def __init__(self):
        self.mode = "auto"  # 默认自动
        self._load()

    def _load(self):
        if os.path.exists(IP_CONFIG_FILE):
            try:
                with open(IP_CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.mode = data.get("mode", "auto")
            except:
                pass

    def _save(self):
        try:
            with open(IP_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({"mode": self.mode}, f, ensure_ascii=False)
        except:
            pass

    def set_mode(self, mode: str):
        if mode in IP_MODES:
            self.mode = mode
            self._save()
            return True
        return False

    def get_current_mode(self):
        if self.mode == "auto":
            now = datetime.now()
            hour = now.hour
            if 8 <= hour < 20:
                return "ipv6", f"自动（当前时间{hour:02d}:00，IPv6优先）"
            else:
                return "ipv4", f"自动（当前时间{hour:02d}:00，IPv4优先）"
        return self.mode, IP_MODES[self.mode]


ip_config = IPConfig()


# ══════════════════════════════════════════
# 地址配置管理
# ══════════════════════════════════════════
class AddrConfig:
    """地址配置管理，支持锁定（固定白名单）"""
    def __init__(self):
        # 数据结构: {"server": {"ipv6": {"addr":"", "locked":False}, "ipv4":{...}, "tailscale":{...}}, ...}
        self.addrs = self._defaults()
        self._load()

    def _defaults(self):
        d = {}
        for ep in ENDPOINTS:
            d[ep] = {}
            for mode in ["ipv6", "ipv4", "tailscale"]:
                d[ep][mode] = {"addr": "", "locked": False}
        return d

    def _load(self):
        if os.path.exists(ADDR_CONFIG_FILE):
            try:
                with open(ADDR_CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for ep in ENDPOINTS:
                        if ep in data and isinstance(data[ep], dict):
                            for mode in ["ipv6", "ipv4", "tailscale"]:
                                if mode in data[ep]:
                                    val = data[ep][mode]
                                    if isinstance(val, dict):
                                        self.addrs[ep][mode]["addr"] = str(val.get("addr", "")).strip()
                                        self.addrs[ep][mode]["locked"] = bool(val.get("locked", False))
                                    else:
                                        self.addrs[ep][mode]["addr"] = str(val).strip()
            except:
                pass

    def _save(self):
        try:
            with open(ADDR_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.addrs, f, ensure_ascii=False, indent=2)
        except:
            pass

    def set_addrs(self, addrs):
        """设置地址，参数格式: {"server": {"ipv6":{"addr":"...","locked":true}, ...}, ...}"""
        for ep in ENDPOINTS:
            if ep in addrs and isinstance(addrs[ep], dict):
                for mode in ["ipv6", "ipv4", "tailscale"]:
                    if mode in addrs[ep]:
                        val = addrs[ep][mode]
                        if isinstance(val, dict):
                            if "addr" in val:
                                self.addrs[ep][mode]["addr"] = str(val["addr"]).strip()
                            if "locked" in val:
                                self.addrs[ep][mode]["locked"] = bool(val["locked"])
                        else:
                            self.addrs[ep][mode]["addr"] = str(val).strip()
        self._save()

    def get_addrs(self):
        """返回前端兼容格式"""
        return self.addrs


addr_config = AddrConfig()

# ══════════════════════════════════════════
# 角色配置
# ══════════════════════════════════════════
ROLES = {
    "user":   {"name": "你",     "color": "#4FC3F7", "order": 0, "tts": "zh-CN-XiaoxiaoNeural"},
    "hermes": {"name": "Hermes", "color": "#7C5CFC", "order": 1, "tts": "zh-CN-YunxiNeural"},
    "pro":    {"name": "pro",    "color": "#FFB74D", "order": 2, "tts": "zh-CN-XiaoyiNeural"},
    "chat":   {"name": "chat",   "color": "#81C784", "order": 3, "tts": "zh-CN-YunjianNeural"},
    "a3b":    {"name": "A3B",    "color": "#FF6B6B", "order": 4, "tts": "zh-CN-YunyangNeural"},
}
ROLE_ORDER = ["user", "hermes", "pro", "chat", "a3b"]

# ══════════════════════════════════════════
# 回合制状态
# ══════════════════════════════════════════
class WarRoom:
    def __init__(self):
        self.round = 0
        self.messages = []
        self.spoken = set()
        self.phase = "speaking"
        self.ws_clients = set()

    def speak(self, role, text, media=None):
        key = f"{self.round}-{role}"
        if key in self.spoken:
            return False
        msg = {
            "round": self.round,
            "role": role,
            "name": ROLES[role]["name"],
            "color": ROLES[role]["color"],
            "text": text,
            "time": datetime.now().strftime("%H:%M:%S"),
        }
        if media:
            msg["media"] = media
        self.messages.append(msg)
        self.spoken.add(key)
        all_spoken = all(f"{self.round}-{r}" in self.spoken for r in ROLE_ORDER)
        self.phase = "done" if all_spoken else "speaking"
        return True

    def _record_message(self, role, text, media=None):
        """管理员专用：不检查轮次限制，直接记录消息"""
        msg = {
            "round": self.round,
            "role": role,
            "name": ROLES[role]["name"],
            "color": ROLES[role]["color"],
            "text": text,
            "time": datetime.now().strftime("%H:%M:%S"),
        }
        if media:
            msg["media"] = media
        self.messages.append(msg)

    def next_round(self):
        self.round += 1
        self.spoken.clear()
        self.phase = "speaking"

    def clear_messages(self):
        """清空本轮所有消息"""
        self.messages.clear()
        self.spoken.clear()
        self.phase = "speaking"

    def to_json(self):
        return {
            "round": self.round,
            "phase": self.phase,
            "roles": {k: {"name": v["name"], "color": v["color"], "spoken": f"{self.round}-{k}" in self.spoken} for k, v in ROLES.items()},
            "messages": self.messages[-200:],
        }

room = WarRoom()


def _auto_create_hermes_task(title: str, high_priority: bool = False):
    """自动插入Hermes kanban任务"""
    import sqlite3, uuid
    try:
        db_path = os.path.expanduser("~/.hermes/kanban.db")
        task_id = uuid.uuid4().hex[:12]
        now = int(time.time())
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("""
                INSERT INTO tasks (id, title, body, assignee, status, priority, created_by, created_at, workspace_kind)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (task_id, title[:80], title, "hermes", "pending", 1 if high_priority else 0, "warroom-auto", now, "scratch"))
            conn.commit()
            log.info(f"自动任务已创建: {title[:50]}... (id={task_id})")
        finally:
            conn.close()
    except Exception as e:
        log.error(f"创建自动任务失败: {e}")


# ══════════════════════════════════════════
# SSL
# ══════════════════════════════════════════
def _ssl_context():
    if os.path.exists(SSL_CERT) and os.path.exists(SSL_KEY):
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(SSL_CERT, SSL_KEY)
        return ctx
    return None

# ══════════════════════════════════════════
# Qwen TTS — 多角色多音色
# ══════════════════════════════════════════

Qwen_TTS_URL = "http://192.168.1.5:1234/v1/chat/completions"
Qwen_TTS_KEY = "***:u7vi9gihLIlBIaWOEt08"

# 角色 → Qwen TTS / Edge TTS 音色映射
TTS_VOICES = {
    "user":   "zh-CN-XiaoxiaoNeural",   # 女声 (你)
    "hermes": "zh-CN-YunxiNeural",       # 男声 (Hermes)
    "pro":    "zh-CN-XiaoyiNeural",      # 女声 (pro)
    "chat":   "zh-CN-YunjianNeural",     # 男青年 (chat)
}

async def _tts_edge(role: str, text: str) -> bytes | None:
    """Edge TTS，带 3 次重试 + 指数退避。
    国内访问 speech.platform.bing.com 不稳定，经常 503。
    """
    voice = TTS_VOICES.get(role, "zh-CN-XiaoxiaoNeural")
    import re
    clean = re.sub(r'[\U0001F300-\U0001FAFF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\u2600-\u26FF\u2700-\u27BF\u2000-\u206F\u2E00-\u2E7F\u2B00-\u2BFF\u3000-\u303F]', '', text)
    if not clean.strip():
        return None
    import edge_tts
    import io
    for attempt in range(3):
        try:
            communicate = edge_tts.Communicate(clean, voice=voice)
            audio = io.BytesIO()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio.write(chunk["data"])
            return audio.getvalue()
        except Exception as e:
            if attempt < 2:
                wait = 2 ** attempt
                log.warning(f"Edge TTS 第{attempt+1}次失败: {e}，{wait}s后重试")
                await asyncio.sleep(wait)
            else:
                log.error(f"Edge TTS 3次重试全部失败: {e}")
                return None
    return None

# ══════════════════════════════════════════
# 安全认证
# ══════════════════════════════════════════
import hashlib
import json
import os
import time

AUTH_USER = "oakcn"
AUTH_PASS_HASH = hashlib.sha256("hyjaizlj".encode()).hexdigest()

log = logging.getLogger("warroom")

# 文件持久化
AUTH_FILE = os.path.expanduser("~/.hermes/warroom_auth.json")

class AuthManager:
    def __init__(self):
        self.blacklist: dict[str, float] = {}    # ip -> unlock_time
        self.whitelist: dict[str, float] = {}    # ip -> expire_time
        self.fail_counts: dict[str, list[float]] = {}  # ip -> [timestamps]
        self.pending: dict[str, float] = {}       # ip -> connect_time
        self._load()

    def _load(self):
        try:
            with open(AUTH_FILE) as f:
                d = json.load(f)
                self.whitelist = d.get("whitelist", {})
                self.blacklist = d.get("blacklist", {})
        except: pass

    def _save(self):
        try:
            with open(AUTH_FILE, "w") as f:
                json.dump({"whitelist": self.whitelist, "blacklist": self.blacklist}, f)
        except: pass

    def on_connect(self, ip: str):
        """记录连接时间，用于90秒无登录检查"""
        self.pending[ip] = time.time()

    def check(self, ip: str) -> bool:
        """检查IP是否允许访问"""
        now = time.time()
        # 白名单优先
        if ip in self.whitelist and now < self.whitelist[ip]:
            return True
        # 黑名单
        if ip in self.blacklist and now < self.blacklist[ip]:
            return False
        if ip in self.blacklist and now >= self.blacklist[ip]:
            del self.blacklist[ip]
            self._save()
        return True  # 未决状态，允许看登录页

    def login(self, ip: str, username: str, password: str) -> tuple[bool, str]:
        now = time.time()
        # 检查黑名单
        if ip in self.blacklist and now < self.blacklist[ip]:
            remain = int(self.blacklist[ip] - now)
            return False, f"IP 已被封禁，剩余 {remain//3600} 小时 {(remain%3600)//60} 分"

        # 验证
        ok = (username == AUTH_USER and hashlib.sha256(password.encode()).hexdigest() == AUTH_PASS_HASH)

        if ok:
            # 成功：加入白名单
            #   100.x.x.x / 192.168.1.5 永不过期，其他 IP 24h 有效
            if ip.startswith("100.") or ip == "192.168.1.5":
                self.whitelist[ip] = float('inf')
            else:
                self.whitelist[ip] = now + 86400
            if ip in self.fail_counts: del self.fail_counts[ip]
            if ip in self.pending: del self.pending[ip]
            self._save()
            return True, "登录成功"
        else:
            # 失败：记录
            if ip not in self.fail_counts:
                self.fail_counts[ip] = []
            self.fail_counts[ip].append(now)
            # 清理1小时前的记录
            self.fail_counts[ip] = [t for t in self.fail_counts[ip] if now - t < 3600]
            # 4次失败 → 黑名单24小时
            if len(self.fail_counts[ip]) >= 4:
                self.blacklist[ip] = now + 86400  # 24小时
                self.fail_counts[ip] = []
                self._save()
                return False, "密码错误次数过多，IP 已被封禁 24 小时"
            return False, f"用户名或密码错误（剩余 {4 - len(self.fail_counts[ip])} 次机会）"

    def logout(self, ip: str):
        """退出登录：从白名单移除"""
        if ip in self.whitelist:
            del self.whitelist[ip]
            self._save()

    def cleanup(self):
        """清理过期条目"""
        now = time.time()
        # 清理24小时未登录的pending
        expired = [ip for ip, t in self.pending.items() if now - t > 86400 and ip not in self.whitelist]
        for ip in expired:
            self.blacklist[ip] = now + 86400
            del self.pending[ip]
            log.warning(f"IP {ip} 24小时内未登录，已加入黑名单 24 小时")
        # 清理过期的黑/白名单（永不过期的 Tailscale IP 不受影响）
        self.whitelist = {k: v for k, v in self.whitelist.items() if now < v}
        self.blacklist = {k: v for k, v in self.blacklist.items() if now < v}
        self._save()

auth = AuthManager()

# 定时清理
async def _auth_cleanup_loop():
    while True:
        await asyncio.sleep(30)
        try:
            auth.cleanup()
        except Exception:
            pass

# 认证中间件
@web.middleware
async def auth_middleware(request: web.Request, handler):
    ip = request.remote or request.headers.get("X-Forwarded-For", "unknown")
    path = request.path

    # 永久免登录 IP 白名单（代码写死，防配置丢失把自己锁门外）
    #   127.0.0.1 / ::1 / localhost  — 本机
    #   192.168.1.5                  — 局域网 Win10
    #   100.x.x.x                    — Tailscale 全段
    #   100.73.220.74                — 用户的 Tailscale 具体 IP
    _ALWAYS_WHITELIST = (
        "127.0.0.1", "::1", "localhost",
        "192.168.1.5",
        "100.73.220.74",
    )
    if ip in _ALWAYS_WHITELIST or ip.startswith("100."):
        return await handler(request)

    # 首页单独处理
    if path == "/":
        if auth.check(ip) and ip in auth.whitelist:
            return await handler(request)
        else:
            raise web.HTTPFound("/login")

    # 放行登录页、静态资源和API
    if path in ("/login", "/api/login", "/api/logout", "/api/status", "/ws", "/favicon.ico", "/api/admin/ip-config", "/api/admin/addr-config", "/api/admin/detect-addrs", "/api/admin/ctx-usage", "/api/admin/auth-lists", "/admin"):
        return await handler(request)

    # 其他页面检查
    if not (auth.check(ip) and ip in auth.whitelist):
        raise web.HTTPFound("/login")
    return await handler(request)

# ══════════════════════════════════════════
# 摄像头/录音 工具
# ══════════════════════════════════════════
def _camera_snapshot_local():
    """通过 OpenCV 调用本地摄像头拍照（bt2 上没有摄像头，仅做桩）"""
    try:
        import cv2
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            return None, "无法打开摄像头"
        ret, frame = cap.read()
        cap.release()
        if not ret:
            return None, "拍照失败"
        _, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        return buf.tobytes(), None
    except ImportError:
        return None, "需要安装 opencv-python: pip install opencv-python"
    except Exception as e:
        return None, str(e)

# ══════════════════════════════════════════
# Omni LLM 接口
# ══════════════════════════════════════════

async def _transcribe_audio(wav_b64):
    """通过 Omni 转写语音"""
    payload = {
        "model": "",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "input_audio", "input_audio": {"data": wav_b64, "format": "wav"}},
                {"type": "text", "text": "请转写这段语音为文字。"}
            ]
        }],
        "temperature": 0.1,
        "max_tokens": 512,
    }
    try:
        headers = {}
        if OMNI_KEY:
            headers["Authorization"] = f"Bearer {OMNI_KEY}"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
            async with session.post(OMNI_URL, json=payload, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    content = data["choices"][0]["message"]["content"]
                    if "</think>" in content:
                        content = content.split("</think>")[-1].strip()
                    return content
    except Exception as e:
        log.error(f"STT error: {e}")
    return ""

# OCR API 端点（新管线）
OCR_API_URL = "http://127.0.0.1:12052/api/ocr"

async def _vision_analyze(img_b64, prompt=None):
    """通过 OCR API 识别图片（RapidOCR + A3B 语义校正 + 白名单模糊匹配）"""
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=180),
            connector=aiohttp.TCPConnector(ssl=False),
        ) as session:
            async with session.post(OCR_API_URL, json={"image": img_b64, "correct": True}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    items = data.get("items", [])
                    elapsed = data.get("elapsed", 0)

                    if not items:
                        return "识别失败"

                    # 提取姓名
                    name_kw = ["何小","刘慧","李佳","周琪","何美","覃文","卢龙","荆纪","王治",
                        "成军","何云","杨先","黄桂","周琼","黄义","吴玉","余月","刘友","董明",
                        "陈俭","王思","蒙长","黄彩","刘赛","黄雨","胡林","胡春","黄诚","岩小",
                        "钟意","赵双","熊伏","邹石","陈玉","韦荣","严计","黄碧","谭小","李军",
                        "余青","刘良","罗家","刘小","陈海","胡从","张秋","刘梅","彭银","崔洪",
                        "姚彩","张敏","李彗","宾春","张鹏","陈润","陈序","潘凤","张彩","梁宴",
                        "郭满","刘权","黄志","刘梅","朱华","黄继","蒙利","蒙清","黄惠","刘兴",
                        "刘村","林小","李美","吴莲","彭秀","刘杰","宁丰","黄伟","罗江","李勤",
                        "程周","周玉","梁伟","葛岳","熊优","张秀","叶何","李宏","祝国","卜云",
                        "苏文","蔡琼","占海","刘付","李林","李建","黄春","陈林","徐洪","梁晓",
                        "廖普","张朋","韦曼","彭伟","张瑞","黄春","吴世","黄桂","唐佳","曹堪",
                        "刘志","戴瑞","李红","梁雨","冯海","吴小","梁晓","黄启","王文","邹锡",
                        "曹岳","冯胜","罗凤","黄小","陈小","黎海"]
                    names = [i for i in items if any(k in i["text"] for k in name_kw)]

                    # 提取数量/不良品字段
                    qty_fields = [i for i in items if any(k in i["text"] for k in
                        ["数量", "来黑", "丝印很", "丝很", "良品", "返工", "报废"])]

                    # 拼报告
                    lines = [f"📷 报表识别 ({elapsed}s)"]

                    if names:
                        name_strs = []
                        for n in names:
                            t = n["text"]
                            # 去岗位前缀
                            display = t.split("：")[-1].split(":")[-1].strip()
                            if display and len(display) <= 4 and not any(c.isdigit() for c in display):
                                name_strs.append(display)
                        if name_strs:
                            lines.append(f"👤 作业员: {'、'.join(name_strs)}")
                            lines.append(f"👥 共 {len(name_strs)} 人")

                    if qty_fields:
                        for q in qty_fields:
                            lines.append(f"  {q['text']}")

                    lines.append(f"\n🔍 共 {len(items)} 个文字块")
                    lines.append(f"💡 详情: http://192.168.1.21:12052/")

                    return "\n".join(lines)

                return f"OCR API 错误: {resp.status}"
    except Exception as e:
        log.error(f"OCR API 调用失败: {e}")
        return f"OCR API 异常: {str(e)[:60]}"


async def _process_photo_and_broadcast(img_b64: str):
    """"拍照 → OCR API（RapidOCR+A3B+模糊匹配）→ 广播到战情室"""
    try:
        result = await _vision_analyze(img_b64)
        if result and result != "识别失败":
            text = f"{result}"
            room.speak("a3b", text)
            await _broadcast()
            asyncio.create_task(_tts_and_push("a3b", f"报表识别完成，请查看聊天记录"))
            log.info(f"OCR结果已广播")
        else:
            err_text = f"⚠️ OCR识别失败"
            room.speak("a3b", err_text)
            await _broadcast()
            asyncio.create_task(_tts_and_push("a3b", err_text))
            log.warning(f"OCR识别失败")
            _auto_create_hermes_task("OCR识别异常", high_priority=True)
    except Exception as e:
        err_msg = f"⚠️ OCR处理异常: {str(e)[:80]}"
        room.speak("a3b", err_msg)
        await _broadcast()
        asyncio.create_task(_tts_and_push("a3b", err_msg))
        log.error(f"OCR处理出错: {e}")
        _auto_create_hermes_task(f"OCR处理异常：{str(e)[:80]}", high_priority=True)


# ══════════════════════════════════════════
# HTTP 路由 (Web 页面 + REST API)
# ══════════════════════════════════════════

async def index(request):
    """主页 — 需要登录"""
    with open(os.path.join(os.path.dirname(__file__), "warroom.html"), "r", encoding="utf-8") as f:
        return web.Response(text=f.read(), content_type="text/html", charset="utf-8")


async def admin_page(request):
    """管理页面"""
    html = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>乩情总控室 - 管理面板</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #0a0a0f;
            color: #e0e0e0;
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 900px; margin: 0 auto; }
        h1 { font-size: 24px; margin-bottom: 24px; color: #7C5CFC; }
        .card {
            background: rgba(255,255,255,0.03);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 20px;
        }
        .card h2 { font-size: 18px; margin-bottom: 16px; color: #e0e0e0; }
        .badge {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 600;
        }
        .btn-row { display: flex; gap: 10px; flex-wrap: wrap; }
        .btn {
            padding: 10px 20px;
            border: none;
            border-radius: 8px;
            font-size: 14px;
            cursor: pointer;
            transition: all 0.2s;
        }
        .btn-primary { background: #7C5CFC; color: #fff; }
        .btn-secondary { background: rgba(255,255,255,0.1); color: #e0e0e0; border: 1px solid rgba(255,255,255,0.2); }
        .btn:hover { transform: translateY(-1px); }
        .btn.active { box-shadow: 0 0 0 2px #7C5CFC; }
        .btn-small { padding: 6px 14px; font-size: 13px; }
        .btn-danger { background: rgba(255,68,68,0.15); color: #ff4444; border: 1px solid rgba(255,68,68,0.3); }
        .nav-link { display: inline-block; margin-bottom: 20px; color: #7C5CFC; text-decoration: none; font-size: 14px; }
        .nav-link:hover { text-decoration: underline; }
        .info-row {
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px solid rgba(255,255,255,0.05);
        }
        .info-label { color: rgba(255,255,255,0.5); }
        .info-value { color: #e0e0e0; }

        /* Context 进度条 */
        .ctx-bar-wrap {
            width: 100%;
            height: 28px;
            background: rgba(255,255,255,0.05);
            border-radius: 14px;
            overflow: hidden;
            position: relative;
            margin: 12px 0;
        }
        .ctx-bar-fill {
            height: 100%;
            border-radius: 14px;
            transition: width 1s ease, background 1s ease;
            width: 0%;
        }
        .ctx-bar-label {
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 13px;
            font-weight: 600;
            text-shadow: 0 0 6px rgba(0,0,0,0.8);
        }

        /* IP 地址输入 */
        .addr-grid {
            display: flex;
            flex-direction: column;
            gap: 16px;
        }
        .addr-group { }
        .addr-group-title {
            font-size: 14px;
            font-weight: 600;
            margin-bottom: 8px;
            color: #e0e0e0;
        }
        .addr-input-row {
            display: flex;
            gap: 8px;
            align-items: center;
            margin-bottom: 6px;
        }
        .addr-input-row input {
            flex: 1;
            padding: 8px 12px;
            background: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 8px;
            color: #e0e0e0;
            font-size: 13px;
            outline: none;
            font-family: "Courier New", monospace;
        }
        .addr-input-row input:focus { border-color: #7C5CFC; }
        .addr-input-row .slot-label {
            width: 20px;
            color: rgba(255,255,255,0.3);
            font-size: 12px;
            text-align: center;
            flex-shrink: 0;
        }
        .addr-save-bar {
            display: flex;
            gap: 8px;
            margin-top: 8px;
            align-items: center;
        }
        .addr-save-bar .save-msg {
            font-size: 12px;
            color: #81C784;
            opacity: 0;
            transition: opacity 0.3s;
        }
        .addr-save-bar .save-msg.show { opacity: 1; }

        /* 黑白名单 */
        .list-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }
        .list-table th {
            text-align: left;
            padding: 6px 8px;
            color: rgba(255,255,255,0.4);
            font-weight: 400;
            border-bottom: 1px solid rgba(255,255,255,0.05);
        }
        .list-table td {
            padding: 6px 8px;
            border-bottom: 1px solid rgba(255,255,255,0.03);
        }
        .list-table .ip-addr { font-family: "Courier New", monospace; color: #e0e0e0; }
        .list-table .time-remaining { color: rgba(255,255,255,0.5); font-size: 12px; }
        .empty-msg { color: rgba(255,255,255,0.2); font-size: 13px; font-style: italic; padding: 12px 0; }
    </style>
</head>
<body>
    <div class="container">
        <a href="/" class="nav-link">← 返回聊天室</a>
        <h1>⚙️ 管理面板</h1>

        <!-- Context 进度 -->
        <div class="card">
            <h2>🧠 Context 使用率</h2>
            <div id="ctxBarContainer">
                <div class="info-row">
                    <span class="info-label">使用量</span>
                    <span class="info-value" id="ctxLabel">查询中...</span>
                </div>
                <div class="ctx-bar-wrap">
                    <div class="ctx-bar-fill" id="ctxBarFill" style="width:0%;"></div>
                    <div class="ctx-bar-label" id="ctxBarLabel">0%</div>
                </div>
                <div class="info-row">
                    <span class="info-label">状态</span>
                    <span class="info-value" id="ctxStatus">-</span>
                </div>
            </div>
        </div>

        <!-- IP档位 -->
        <div class="card">
            <h2>🌐 IP档位控制</h2>
            <div class="info-row">
                <span class="info-label">当前时间</span>
                <span class="info-value" id="currentTime"></span>
            </div>
            <div class="info-row">
                <span class="info-label">当前模式</span>
                <span class="info-value"><span class="badge" id="currentMode" style="background:rgba(124,92,252,0.2);border:1px solid rgba(124,92,252,0.3);color:#7C5CFC;">加载中...</span></span>
            </div>
            <div style="margin-top: 16px;">
                <div class="btn-row" id="modeButtons"></div>
            </div>
        </div>

        <!-- 地址配置 -->
        <div class="card">
            <h2>📡 端点地址配置</h2>
            <p style="font-size:13px;color:rgba(255,255,255,0.4);margin-bottom:16px;">4个端点 × 3个IP档位（IPv6 / IPv4 / Tailscale）</p>
            <div class="addr-grid" id="addrGrid">
                <div id="addrContainer"></div>
                <div class="addr-save-bar" style="margin-top:12px;">
                    <button class="btn btn-primary btn-small" id="btnSaveAddrs">💾 保存地址</button>
                    <span class="save-msg" id="addrSaveMsg">✓ 已保存</span>
                </div>
            </div>
        </div>

        <!-- 访问控制 -->
        <div class="card">
            <h2>🔐 访问控制</h2>
            <div class="info-row">
                <span class="info-label">本地访问</span>
                <span class="info-value" style="color: #81C784;">✅ 免登录</span>
            </div>
            <div class="info-row">
                <span class="info-label">远程登录</span>
                <span class="info-value">oakcn / hyjaizlj</span>
            </div>
            <h3 style="font-size:15px;margin:16px 0 10px;color:#e0e0e0;">白名单</h3>
            <table class="list-table" id="whitelistTable">
                <thead><tr><th>IP</th><th>剩余时间</th></tr></thead>
                <tbody id="whitelistBody"><tr><td colspan="2" class="empty-msg">加载中...</td></tr></tbody>
            </table>
            <h3 style="font-size:15px;margin:16px 0 10px;color:#e0e0e0;">黑名单</h3>
            <table class="list-table" id="blacklistTable">
                <thead><tr><th>IP</th><th>剩余时间</th></tr></thead>
                <tbody id="blacklistBody"><tr><td colspan="2" class="empty-msg">加载中...</td></tr></tbody>
            </table>
        </div>
    </div>

    <script>
        const MODES = {
            "auto": "自动（8:00-20:00 IPv6优先，20:00-8:00 IPv4优先）",
            "ipv6": "强制IPv6",
            "ipv4": "强制IPv4",
            "tailscale": "Tailscale备用"
        };
        const MODE_KEYS = ["ipv6", "ipv4", "tailscale"];

        // ── Context 进度条 ──
        async function loadCtx() {
            try {
                const r = await fetch("/api/admin/ctx-usage");
                const d = await r.json();
                const pct = d.percent;
                const fill = document.getElementById("ctxBarFill");
                const label = document.getElementById("ctxBarLabel");
                const ctxLabel = document.getElementById("ctxLabel");
                const ctxStatus = document.getElementById("ctxStatus");

                const used = d.used || 0;
                const total = d.total || 125000;
                ctxLabel.textContent = used.toLocaleString() + " / " + total.toLocaleString() + " tokens";

                fill.style.width = pct + "%";
                label.textContent = pct.toFixed(1) + "%";

                // 渐变：0% 深绿 → 50% 黄 → 100% 红
                let r2, g2;
                if (pct <= 50) {
                    r2 = Math.round(0 + (255 - 0) * (pct / 50));
                    g2 = Math.round(180 - (180 - 128) * (pct / 50));
                } else {
                    r2 = 255;
                    g2 = Math.round(128 - 128 * ((pct - 50) / 50));
                }
                fill.style.background = "rgb(" + Math.max(0,Math.min(255,r2)) + "," + Math.max(0,Math.min(255,g2)) + ",50)";

                if (pct > 85) ctxStatus.textContent = "⚠️ 接近极限";
                else if (pct > 60) ctxStatus.textContent = "⚡ 较高负载";
                else ctxStatus.textContent = "✅ 正常";
            } catch (e) {
                document.getElementById("ctxLabel").textContent = "查询失败";
            }
        }

        // ── IP档位 ──
        async function loadConfig() {
            try {
                const resp = await fetch("/api/admin/ip-config");
                const data = await resp.json();
                document.getElementById("currentMode").textContent = data.current_display;
                renderButtons(data.mode);
            } catch (e) {
                document.getElementById("currentMode").textContent = "加载失败";
            }
        }

        function renderButtons(currentMode) {
            const container = document.getElementById("modeButtons");
            container.innerHTML = "";
            for (const [mode, label] of Object.entries(MODES)) {
                const btn = document.createElement("button");
                btn.className = "btn " + (mode === currentMode ? "btn-primary active" : "btn-secondary");
                btn.textContent = label;
                btn.onclick = () => setMode(mode);
                container.appendChild(btn);
            }
        }

        async function setMode(mode) {
            try {
                await fetch("/api/admin/ip-config", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({mode})
                });
                loadConfig();
            } catch (e) {
                alert("设置失败: " + e.message);
            }
        }

        // ── 地址配置 ──
        const ENDPOINTS = ["server", "user", "asr", "tts"];
        const ENDPOINT_LABELS = {"server": "🖥️ SERVER（中台自身）", "user": "👤 USER", "asr": "🎤 ASR", "tts": "🔊 TTS"};
        const MODE_NAMES = {"ipv6": "IPv6", "ipv4": "IPv4", "tailscale": "Tailscale"};
        const MODE_COLORS = {"ipv6": "#7C5CFC", "ipv4": "#4FC3F7", "tailscale": "#81C784"};
        const MODE_KEYS = ["ipv6", "ipv4", "tailscale"];
        let addrData = {};

        async function loadAddrs() {
            try {
                const r = await fetch("/api/admin/addr-config");
                addrData = await r.json();
                renderAddrs();
            } catch (e) { console.error("地址加载失败", e); }
        }

        function renderAddrs() {
            const container = document.getElementById("addrContainer");
            container.innerHTML = "";
            for (const ep of ENDPOINTS) {
                const group = document.createElement("div");
                group.className = "addr-group";
                group.style.marginBottom = "16px";
                const title = document.createElement("div");
                title.className = "addr-group-title";
                title.textContent = ENDPOINT_LABELS[ep] || ep;
                group.appendChild(title);

                const modes = addrData[ep] || {"ipv6":{"addr":"","locked":false},"ipv4":{"addr":"","locked":false},"tailscale":{"addr":"","locked":false}};
                for (const mode of MODE_KEYS) {
                    const raw = modes[mode] || {};
                    const addr = typeof raw === "string" ? raw : (raw.addr || "");
                    const locked = typeof raw === "object" && raw.locked === true;

                    const row = document.createElement("div");
                    row.className = "addr-input-row";
                    row.style.display = "flex";
                    row.style.alignItems = "center";
                    row.style.gap = "6px";

                    const label = document.createElement("span");
                    label.className = "slot-label";
                    label.textContent = mode === "ipv6" ? "6" : mode === "ipv4" ? "4" : "T";
                    label.style.color = MODE_COLORS[mode];
                    label.style.fontWeight = "600";
                    label.style.width = "24px";
                    label.style.flexShrink = "0";

                    const input = document.createElement("input");
                    input.type = "text";
                    input.dataset.ep = ep;
                    input.dataset.mode = mode;
                    input.value = addr;
                    input.placeholder = MODE_NAMES[mode] + " 地址";
                    input.style.borderLeft = "3px solid " + MODE_COLORS[mode];
                    input.style.flex = "1";
                    if (ep === "server") input.placeholder += "（留空自动检测）";

                    // 解锁/锁定开关
                    const lockBtn = document.createElement("span");
                    lockBtn.className = "lock-toggle";
                    lockBtn.dataset.ep = ep;
                    lockBtn.dataset.mode = mode;
                    lockBtn.style.cursor = "pointer";
                    lockBtn.style.fontSize = "18px";
                    lockBtn.style.width = "28px";
                    lockBtn.style.textAlign = "center";
                    lockBtn.style.flexShrink = "0";
                    lockBtn.style.transition = "all 0.15s";
                    lockBtn.textContent = locked ? "🔒" : "✅";
                    lockBtn.style.opacity = locked ? "1" : "0.4";
                    lockBtn.title = locked ? "已锁定（永久白名单）" : "点击锁定（加入永久白名单）";
                    lockBtn.onclick = () => toggleLock(ep, mode);

                    row.appendChild(label);
                    row.appendChild(input);
                    row.appendChild(lockBtn);
                    group.appendChild(row);
                }
                // 自动检测按钮（仅server端）
                if (ep === "server") {
                    const autoRow = document.createElement("div");
                    autoRow.className = "addr-input-row";
                    autoRow.style.justifyContent = "flex-end";
                    const autoBtn = document.createElement("button");
                    autoBtn.className = "btn btn-small btn-secondary";
                    autoBtn.textContent = "🔍 自动检测本机地址";
                    autoBtn.onclick = () => detectServerAddrs();
                    autoRow.appendChild(autoBtn);
                    group.appendChild(autoRow);
                }
                container.appendChild(group);
            }
        }

        // 锁定/解锁切换
        function toggleLock(ep, mode) {
            const raw = addrData[ep]?.[mode] || {};
            const currentLocked = typeof raw === "object" && raw.locked === true;
            // 更新内存数据
            if (typeof addrData[ep]?.[mode] === "object") {
                addrData[ep][mode].locked = !currentLocked;
            } else {
                addrData[ep][mode] = {"addr": typeof raw === "string" ? raw : "", "locked": !currentLocked};
            }
            renderAddrs();
            // 自动保存
            saveAddrs();
        }

        async function saveAddrs() {
            const payload = {};
            for (const ep of ENDPOINTS) {
                const inputs = document.querySelectorAll('input[data-ep="' + ep + '"]');
                const epAddrs = {"ipv6": {"addr":"","locked":false}, "ipv4": {"addr":"","locked":false}, "tailscale": {"addr":"","locked":false}};
                for (const inp of inputs) {
                    const m = inp.dataset.mode;
                    const raw = addrData[ep]?.[m] || {};
                    epAddrs[m] = {
                        "addr": inp.value.trim(),
                        "locked": typeof raw === "object" ? !!raw.locked : false
                    };
                }
                payload[ep] = epAddrs;
            }
            try {
                const r = await fetch("/api/admin/addr-config", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify(payload)
                });
                const d = await r.json();
                addrData = d.addrs;
                const msg = document.getElementById("addrSaveMsg");
                msg.classList.add("show");
                setTimeout(() => msg.classList.remove("show"), 2000);
            } catch (e) { console.error("保存失败:", e); }
        }

        async function detectServerAddrs() {
            try {
                const r = await fetch("/api/admin/detect-addrs");
                const d = await r.json();
                if (d.ok) {
                    for (const mode of MODE_KEYS) {
                        if (d.addrs[mode]) {
                            const input = document.querySelector('input[data-ep="server"][data-mode="' + mode + '"]');
                            if (input) input.value = d.addrs[mode];
                        }
                    }
                    alert("✅ 已自动检测并填入本机地址");
                } else {
                    alert("检测失败: " + (d.error || "未知错误"));
                }
            } catch (e) {
                alert("检测请求失败: " + e.message);
            }
        }

        function htmlEsc(s) {
            return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        }

        document.getElementById("btnSaveAddrs").onclick = saveAddrs;

        // ── 黑白名单 ──
        async function loadAuthLists() {
            try {
                const r = await fetch("/api/admin/auth-lists");
                const d = await r.json();
                renderList("whitelistBody", d.whitelist, "白");
                renderList("blacklistBody", d.blacklist, "黑");
            } catch (e) { console.error("名单加载失败", e); }
        }

        function renderList(bodyId, list, label) {
            const tbody = document.getElementById(bodyId);
            const now = Date.now() / 1000;
            const keys = Object.keys(list);
            if (keys.length === 0) {
                tbody.innerHTML = '<tr><td colspan="2" class="empty-msg">暂无' + label + '名单</td></tr>';
                return;
            }
            tbody.innerHTML = "";
            for (const ip of keys) {
                const exp = list[ip];
                const remain = Math.max(0, exp - now);
                const hours = Math.floor(remain / 3600);
                const mins = Math.floor((remain % 3600) / 60);
                const timeStr = hours > 0 ? hours + "小时" + mins + "分" : mins + "分钟";
                const tr = document.createElement("tr");
                tr.innerHTML = '<td class="ip-addr">' + htmlEsc(ip) + '</td><td class="time-remaining">' + timeStr + '</td>';
                tbody.appendChild(tr);
            }
        }

        function updateTime() {
            const now = new Date();
            document.getElementById("currentTime").textContent = now.toLocaleString("zh-CN");
        }

        // ── 初始化 ──
        loadCtx();
        loadConfig();
        loadAddrs();
        loadAuthLists();
        updateTime();
        setInterval(updateTime, 1000);
        setInterval(loadCtx, 30000);   // 30秒刷新context
        setInterval(loadConfig, 60000);
        setInterval(loadAuthLists, 30000);
    </script>
</body>
</html>"""
    return web.Response(text=html, content_type="text/html", charset="utf-8")

async def login_page(request):
    """登录页"""
    html = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>乩情总控室 - 登录</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  background:#0a0a0f;color:#e0e0e0;display:flex;align-items:center;justify-content:center;
  height:100vh}
.card{background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08);
  border-radius:16px;padding:32px;width:340px;text-align:center}
.card h1{font-size:20px;margin-bottom:24px;letter-spacing:2px}
.card .logo{font-size:48px;margin-bottom:16px}
.input-group{margin-bottom:16px;text-align:left}
.input-group label{display:block;font-size:12px;color:rgba(255,255,255,0.5);margin-bottom:4px}
.input-group input{width:100%;padding:10px 14px;background:rgba(255,255,255,0.05);
  border:1px solid rgba(255,255,255,0.1);border-radius:10px;color:#e0e0e0;
  font-size:14px;outline:none}
.input-group input:focus{border-color:#7C5CFC}
.btn{width:100%;padding:10px;background:#7C5CFC;border:none;border-radius:10px;
  color:#fff;font-size:14px;cursor:pointer;margin-top:8px}
.btn:active{transform:scale(0.98)}
.error{color:#ff4444;font-size:12px;margin-top:8px;display:none}
</style>
</head><body>
<div class="card">
<div class="logo">⚡</div><h1>乩情总控室</h1>
<div class="input-group"><label>用户名</label>
<input type="text" id="username" placeholder="oakcn" autocomplete="off"></div>
<div class="input-group"><label>密码</label>
<input type="password" id="password" placeholder="••••••••"></div>
<button class="btn" id="loginBtn">登 录</button>
<div class="error" id="errorMsg"></div>
</div>
<script>
document.getElementById('loginBtn').onclick = async () => {
  const u = document.getElementById('username').value;
  const p = document.getElementById('password').value;
  const err = document.getElementById('errorMsg');
  if (!u || !p) { err.textContent='请输入用户名和密码'; err.style.display='block'; return; }
  try {
    const r = await fetch('/api/login', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({username:u,password:p})});
    const d = await r.json();
    if (d.ok) { window.location.href='/'; }
    else { err.textContent=d.message; err.style.display='block'; }
  } catch(e) { err.textContent='请求失败'; err.style.display='block'; }
};
document.getElementById('password').addEventListener('keydown',e => {
  if(e.key==='Enter') document.getElementById('loginBtn').click();
});
</script>
</body></html>"""
    return web.Response(text=html, content_type="text/html", charset="utf-8")

async def api_login(request):
    """登录 API"""
    ip = request.remote or request.headers.get("X-Forwarded-For", "unknown")
    data = await request.json()
    ok, msg = auth.login(ip, data.get("username",""), data.get("password",""))
    if ok:
        resp = web.json_response({"ok": True})
        resp.set_cookie("session", ip, max_age=86400)
        return resp
    return web.json_response({"ok": False, "message": msg})

async def api_logout(request):
    """退出登录 API"""
    ip = request.remote or request.headers.get("X-Forwarded-For", "unknown")
    auth.logout(ip)
    resp = web.json_response({"ok": True})
    resp.del_cookie("session")
    return resp


async def api_admin_ip_config_get(request):
    """获取IP配置 API"""
    mode, display = ip_config.get_current_mode()
    return web.json_response({
        "mode": ip_config.mode,
        "current_mode": mode,
        "current_display": display,
        "all_modes": IP_MODES,
    })


async def api_admin_ip_config_set(request):
    """设置IP配置 API"""
    data = await request.json()
    mode = data.get("mode", "")
    if ip_config.set_mode(mode):
        new_mode, new_display = ip_config.get_current_mode()
        return web.json_response({
            "ok": True,
            "mode": ip_config.mode,
            "current_mode": new_mode,
            "current_display": new_display,
        })
    return web.json_response({"ok": False, "error": "无效的模式"}, status=400)


async def api_admin_addr_config_get(request):
    """获取地址配置 API"""
    return web.json_response(addr_config.get_addrs())


async def api_admin_addr_config_set(request):
    """设置地址配置 API"""
    data = await request.json()
    addr_config.set_addrs(data)
    return web.json_response({"ok": True, "addrs": addr_config.get_addrs()})


async def api_admin_detect_addrs(request):
    """自动检测本机IP地址"""
    import subprocess, json
    addrs = {"ipv6": "", "ipv4": "", "tailscale": ""}
    try:
        # 用ip addr获取所有非回环地址
        result = subprocess.run(
            ["ip", "-j", "addr", "show"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            interfaces = json.loads(result.stdout)
            for iface in interfaces:
                if iface.get("ifname") in ("lo",):
                    continue
                for addr_info in iface.get("addr_info", []):
                    ip = addr_info.get("local", "")
                    if not ip:
                        continue
                    if ip.startswith("127.") or ip == "::1":
                        continue
                    is_tailscale = iface.get("ifname", "").startswith("tailscale") or ip.startswith("100.")
                    if ":" in ip and not addrs["ipv6"]:
                        addrs["ipv6"] = ip
                    elif "." in ip and not is_tailscale and not addrs["ipv4"]:
                        addrs["ipv4"] = ip
                    if is_tailscale and not addrs["tailscale"]:
                        addrs["tailscale"] = ip
        return web.json_response({"ok": True, "addrs": addrs})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})


async def api_admin_ctx_usage(request):
    """获取 30A3B context 使用率"""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            async with session.get("http://127.0.0.1:12026/v1/model/info") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # 尝试从不同字段获取
                    used = data.get("context_tokens") or data.get("total_tokens") or 0
                    total = data.get("max_context_length") or 125000
                    if isinstance(used, dict):
                        used = sum(used.values())
                    pct = min(100.0, round(used / total * 100, 1)) if total > 0 else 0
                    return web.json_response({"percent": pct, "used": used, "total": total})
    except Exception as e:
        log.warning(f"context 查询失败: {e}")
    return web.json_response({"percent": 0, "used": 0, "total": 125000})


async def api_admin_auth_lists(request):
    """获取黑白名单"""
    now = time.time()
    # 清理过期的
    wl = {k: v for k, v in auth.whitelist.items() if v > now}
    bl = {k: v for k, v in auth.blacklist.items() if v > now}
    return web.json_response({"whitelist": wl, "blacklist": bl})

async def api_status(request):
    return web.json_response(room.to_json())

async def api_speak(request):
    data = await request.json()
    role = data.get("role", "")
    text = data.get("text", "").strip()
    media = data.get("media")  # 可选，{type: "image", data: "base64..."}
    if role not in ROLES:
        return web.json_response({"error": "无效角色"}, status=400)
    if not text and not media:
        return web.json_response({"error": "内容不能为空"}, status=400)

    # 本地管理员不受发言限制
    ip = request.remote or request.headers.get("X-Forwarded-For", "unknown")
    bypass_limit = ip in ("127.0.0.1", "::1", "localhost")

    if not bypass_limit:
        ok = room.speak(role, text or "", media=media)
        if not ok:
            return web.json_response({"error": "本轮已发言"}, status=400)
    else:
        # 管理员：仍然记录消息但不检查轮次限制
        room._record_message(role, text or "", media=media)

    await _broadcast()
    # 如果是照片 → 发A3B识别
    if media and media.get("type") == "image" and media.get("data"):
        asyncio.create_task(_process_photo_and_broadcast(media["data"]))

    # 异步调 TTS，音频单独推送
    if text:
        asyncio.create_task(_tts_and_push(role, text))
    return web.json_response({"ok": True})

async def api_next_round(request):
    room.next_round()
    await _broadcast()
    return web.json_response({"ok": True})

async def api_clear(request):
    """清空所有聊天消息"""
    room.clear_messages()
    await _broadcast()
    return web.json_response({"ok": True})

async def api_speak_mcp(request):
    """MCP 风格的发言接口"""
    data = await request.json()
    role = data.get("role", "")
    text = data.get("text", "").strip()
    if role not in ROLES:
        return web.json_response({"error": "无效角色"}, status=400)
    if not text:
        return web.json_response({"error": "内容不能为空"}, status=400)
    ok = room.speak(role, text)
    if not ok:
        return web.json_response({"error": "本轮已发言"}, status=400)
    await _broadcast()
    return web.json_response({"ok": True, "round": room.round, "phase": room.phase})

async def api_camera_snapshot(request):
    """MCP 风格的拍照接口"""
    data_bytes, err = _camera_snapshot_local()
    if err:
        # 返回一张测试图
        return web.json_response({"error": err})
    b64 = base64.b64encode(data_bytes).decode()
    # 可以选择分析
    analyze = request.query.get("analyze", "false") == "true"
    if analyze:
        result = await _vision_analyze(b64)
        return web.json_response({"image_b64": b64[:100] + "...", "analysis": result})
    return web.json_response({"image_b64": b64, "size": len(data_bytes)})

async def api_voice_transcribe(request):
    """语音转文字接口 — 接收 webm/opus，ffmpeg 转 wav，调 Omni STT，结果发言到房间"""
    reader = await request.multipart()
    file_field = await reader.next()
    if not file_field:
        return web.json_response({"error": "无文件"}, status=400)
    data = await file_field.read()
    role = request.query.get("role", "user")

    # webm → wav 转换
    import tempfile
    tmp_in = tempfile.NamedTemporaryFile(suffix=".webm", delete=False)
    tmp_out = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    try:
        tmp_in.write(data)
        tmp_in.close()
        tmp_out.close()
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", tmp_in.name,
            "-ar", "16000", "-ac", "1", "-f", "wav", tmp_out.name,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            log.error(f"ffmpeg error: {stderr.decode()[:200]}")
            return web.json_response({"text": "音频转换失败"})

        with open(tmp_out.name, "rb") as f:
            wav_bytes = f.read()
        if len(wav_bytes) < 1000:
            return web.json_response({"text": "音频太短"})
    finally:
        os.unlink(tmp_in.name)
        try: os.unlink(tmp_out.name)
        except: pass

    wav_b64 = base64.b64encode(wav_bytes).decode()
    text = await _transcribe_audio(wav_b64)

    if text:
        # 自动以当前角色将识别结果发言到房间
        room.speak(role, text)
        await _broadcast()

    return web.json_response({"text": text or "识别失败"})


async def api_her_speak(request):
    """HER按钮: 接收语音转文字, 插入当前 Hermes 会话作为用户消息"""
    try:
        data = await request.json()
        text = data.get("text", "").strip()
        if not text:
            return web.json_response({"error": "内容不能为空"}, status=400)

        # 获取当前活跃会话ID
        import sqlite3
        db_path = os.path.expanduser("~/.hermes/state.db")
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.execute(
                "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
            )
            row = cur.fetchone()
            if not row:
                return web.json_response({"error": "没有活跃的Hermes会话"}, status=400)
            session_id = row[0]

            # 插入一条user消息到会话
            now = time.time()
            conn.execute(
                "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, 'user', ?, ?)",
                (session_id, text, now)
            )
            # 更新会话的消息计数
            conn.execute(
                "UPDATE sessions SET message_count = message_count + 1 WHERE id = ?",
                (session_id,)
            )
            conn.commit()
            log.info(f"HER 消息已插入会话 {session_id}: {text[:50]}...")

            # 同时在战情室广播一条通知
            room.speak("hermes", f"📋 来自战情室的新任务: {text}")
            await _broadcast()
            asyncio.create_task(_tts_and_push("hermes", f"收到新任务，请查看。{text[:30]}"))

            return web.json_response({"ok": True, "session_id": session_id})
        except Exception as e:
            log.error(f"插入消息失败: {e}")
            return web.json_response({"error": f"写入失败: {e}"}, status=500)
        finally:
            conn.close()
    except Exception as e:
        log.error(f"HER speak error: {e}")
        return web.json_response({"error": str(e)}, status=400)


async def ws_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    room.ws_clients.add(ws)
    try:
        await ws.send_json({"type": "state", "data": room.to_json()})
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                action = data.get("action")
                if action == "speak":
                    role = data.get("role", "")
                    text = data.get("text", "").strip()
                    if role in ROLES and text:
                        ok = room.speak(role, text)
                        if ok:
                            await _broadcast()
                elif action == "next_round":
                    room.next_round()
                    await _broadcast()
                elif action == "ping":
                    await ws.send_json({"type": "pong"})
    finally:
        room.ws_clients.discard(ws)
    return ws

async def _broadcast():
    state = room.to_json()
    dead = set()
    for ws in room.ws_clients:
        try:
            await ws.send_json({"type": "state", "data": state})
        except:
            dead.add(ws)
    room.ws_clients -= dead

async def _tts_and_push(role: str, text: str):
    """生成 TTS 音频并广播到前端"""
    try:
        audio = await _tts_edge(role, text)
        if not audio:
            return
        audio_b64 = base64.b64encode(audio).decode()
        dead = set()
        for ws in room.ws_clients:
            try:
                await ws.send_json({"type": "tts", "role": role, "audio_b64": audio_b64})
            except:
                dead.add(ws)
        room.ws_clients -= dead
    except Exception as e:
        log.error(f"TTS push error: {e}")

# ══════════════════════════════════════════
# MCP Server (独立端口)
# ══════════════════════════════════════════

if HAS_MCP:
    mcp_server = Server("warroom")

    @mcp_server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="warroom_status",
                description="获取中台作战室当前状态（轮次、已发言角色、消息历史）",
                inputSchema={"type": "object", "properties": {}},
            ),
            types.Tool(
                name="warroom_speak",
                description="在中台作战室发言。每轮每人只能发言一次。",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "role": {"type": "string", "description": "角色: user/hermes/pro/chat"},
                        "text": {"type": "string", "description": "发言内容"},
                    },
                    "required": ["role", "text"],
                },
            ),
            types.Tool(
                name="warroom_next_round",
                description="进入下一轮讨论",
                inputSchema={"type": "object", "properties": {}},
            ),
            types.Tool(
                name="camera_snapshot",
                description="拍照。analyze=true 时自动用 Omni 分析图片内容",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "analyze": {"type": "boolean", "description": "是否自动分析图片"},
                    },
                },
            ),
            types.Tool(
                name="voice_transcribe",
                description="语音转文字。发送 WAV 格式音频，返回文字。",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "audio_b64": {"type": "string", "description": "WAV 音频的 base64 编码"},
                    },
                    "required": ["audio_b64"],
                },
            ),
        ]

    @mcp_server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent | types.ImageContent]:
        if name == "warroom_status":
            return [types.TextContent(type="text", text=json.dumps(room.to_json(), ensure_ascii=False))]

        elif name == "warroom_speak":
            role = arguments.get("role", "")
            text = arguments.get("text", "").strip()
            if role not in ROLES:
                return [types.TextContent(type="text", text=f"无效角色: {role}")]
            if not text:
                return [types.TextContent(type="text", text="内容不能为空")]
            ok = room.speak(role, text)
            if not ok:
                return [types.TextContent(type="text", text=f"{ROLES[role]['name']} 本轮已发言")]
            await _broadcast()
            return [types.TextContent(type="text", text="发言成功")]

        elif name == "warroom_next_round":
            room.next_round()
            await _broadcast()
            return [types.TextContent(type="text", text=f"进入第 {room.round} 轮")]

        elif name == "camera_snapshot":
            data_bytes, err = _camera_snapshot_local()
            if err:
                return [types.TextContent(type="text", text=f"拍照失败: {err}")]
            b64 = base64.b64encode(data_bytes).decode()
            analyze = arguments.get("analyze", False)
            if analyze:
                result = await _vision_analyze(b64)
                return [
                    types.ImageContent(type="image", data=b64, mimeType="image/jpeg"),
                    types.TextContent(type="text", text=result),
                ]
            return [types.ImageContent(type="image", data=b64, mimeType="image/jpeg")]

        elif name == "voice_transcribe":
            audio_b64 = arguments.get("audio_b64", "")
            if not audio_b64:
                return [types.TextContent(type="text", text="没有音频数据")]
            text = await _transcribe_audio(audio_b64)
            return [types.TextContent(type="text", text=text or "识别失败")]

        return [types.TextContent(type="text", text=f"未知工具: {name}")]

# ══════════════════════════════════════════
# 启动
# ══════════════════════════════════════════

async def _run_mcp_server():
    """在独立端口上运行 MCP HTTP Server (streamable-http)"""
    if not HAS_MCP:
        log.warning("MCP SDK 未安装，跳过 MCP Server")
        return

    from mcp.server.streamable_http import StreamableHTTPServerTransport

    transport = StreamableHTTPServerTransport(
        mcp_session_id="warroom",
        is_json_response_enabled=True,
    )

    # 后台运行 MCP server
    async def _run_server_loop():
        async with transport.connect() as streams:
            read_stream, write_stream = streams
            await mcp_server.run(
                read_stream,
                write_stream,
                mcp_server.create_initialization_options(),
            )

    asyncio.create_task(_run_server_loop())

    # ASGI → aiohttp 适配器
    async def mcp_handler(request):
        body = await request.read()
        scope = {
            "type": "http",
            "method": request.method,
            "path": request.path,
            "headers": [(k.encode(), v.encode()) for k, v in request.headers.items()],
            "query_string": request.query_string.encode(),
            "server": ("0.0.0.0", MCP_PORT),
            "client": (request.remote or "127.0.0.1", 0),
        }
        received = False

        async def receive():
            nonlocal received
            if received:
                return {"type": "http.disconnect"}
            received = True
            return {
                "type": "http.request",
                "body": body,
                "more_body": False,
            }

        response_status = [200]
        response_headers = []
        response_body = []

        async def send(event):
            if event["type"] == "http.response.start":
                response_status[0] = event["status"]
                response_headers[:] = [(k.decode(), v.decode()) for k, v in event.get("headers", [])]
            elif event["type"] == "http.response.body":
                response_body.append(event.get("body", b""))

        await transport.handle_request(scope, receive, send)

        return web.Response(
            status=response_status[0],
            headers=response_headers,
            body=b"".join(response_body),
        )

    mcp_app = web.Application()
    mcp_app.router.add_route("*", "/mcp{tail:.*}", mcp_handler)

    runner = web.AppRunner(mcp_app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=MCP_PORT, reuse_address=True)
    await site.start()
    log.info(f"MCP Server: http://0.0.0.0:{MCP_PORT}/mcp (streamable-http)")

    # 保持运行
    await asyncio.Event().wait()

async def _main_async():
    app = web.Application(middlewares=[auth_middleware])
    app.router.add_get("/", index)
    app.router.add_get("/admin", admin_page)
    app.router.add_get("/login", login_page)
    app.router.add_post("/api/login", api_login)
    app.router.add_post("/api/logout", api_logout)
    app.router.add_get("/api/admin/ip-config", api_admin_ip_config_get)
    app.router.add_post("/api/admin/ip-config", api_admin_ip_config_set)
    app.router.add_get("/api/admin/addr-config", api_admin_addr_config_get)
    app.router.add_post("/api/admin/addr-config", api_admin_addr_config_set)
    app.router.add_get("/api/admin/detect-addrs", api_admin_detect_addrs)
    app.router.add_get("/api/admin/ctx-usage", api_admin_ctx_usage)
    app.router.add_get("/api/admin/auth-lists", api_admin_auth_lists)
    app.router.add_get("/api/status", api_status)
    app.router.add_post("/api/speak", api_speak)
    app.router.add_post("/api/next-round", api_next_round)
    app.router.add_post("/api/clear", api_clear)
    app.router.add_post("/api/mcp/speak", api_speak_mcp)
    app.router.add_post("/api/camera/snapshot", api_camera_snapshot)
    app.router.add_post("/api/voice/transcribe", api_voice_transcribe)
    app.router.add_post("/api/her-speak", api_her_speak)
    app.router.add_get("/ws", ws_handler)
    # 静态文件
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(static_dir):
        app.router.add_static("/static", static_dir)

    runner = web.AppRunner(app)
    await runner.setup()

    ssl_ctx = _ssl_context()
    sites = []

    if ssl_ctx:
        sites.append(web.TCPSite(runner, host="0.0.0.0", port=HTTPS_PORT, ssl_context=ssl_ctx, reuse_address=True))
        sites.append(web.TCPSite(runner, host="::", port=HTTPS_PORT, ssl_context=ssl_ctx, reuse_address=True))
    sites.append(web.TCPSite(runner, host="0.0.0.0", port=PORT, reuse_address=True))
    sites.append(web.TCPSite(runner, host="::", port=PORT, reuse_address=True))

    for s in sites:
        await s.start()
        proto = "https" if (ssl_ctx and hasattr(s, '_ssl_context') and s._ssl_context) else "http"
        log.info(f"中台 Web: {proto}://{s._host}:{s._port}")

    log.info(f"中台 MCP: http://0.0.0.0:{MCP_PORT}/mcp (需安装 mcp 包)")

    # 同时启动 MCP
    if HAS_MCP:
        try:
            asyncio.create_task(_run_mcp_server())
        except Exception as e:
            log.warning(f"MCP 启动失败: {e}")

    await asyncio.Event().wait()

def main():
    log.info("启动中台作战室...")
    asyncio.run(_main_async())

if __name__ == "__main__":
    main()
