using System;
using System.Net.Http;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;

namespace VoiceClient
{
    /// <summary>
    /// 后端 API 调用封装 — 所有 HTTP 请求走这里
    /// </summary>
    public class ApiClient
    {
        private readonly HttpClient _http;
        private readonly string _baseUrl;
        private readonly JavaScriptSerializer _json;

        public ApiClient(string baseUrl)
        {
            _baseUrl = baseUrl.TrimEnd('/');
            _http = new HttpClient { Timeout = TimeSpan.FromSeconds(30) };
            _json = new JavaScriptSerializer();
            Logger.Info($"ApiClient 初始化, 后端地址: {_baseUrl}");
        }

        /// <summary>ASR：发送音频 → 返回文字</summary>
        public async Task<AsrResult> AsrAsync(byte[] wavData)
        {
            Logger.Info($"ASR 请求: {wavData.Length} bytes");
            using (var content = new ByteArrayContent(wavData))
            {
                content.Headers.ContentType = new System.Net.Http.Headers.MediaTypeHeaderValue("audio/wav");
                var resp = await _http.PostAsync($"{_baseUrl}/api/asr", content);
                string body = await resp.Content.ReadAsStringAsync();
                Logger.Info($"ASR 响应: {resp.StatusCode} {body.Truncate(200)}");
                if (!resp.IsSuccessStatusCode)
                    return new AsrResult { Error = $"HTTP {resp.StatusCode}: {body.Truncate(100)}" };
                try { return _json.Deserialize<AsrResult>(body); }
                catch (Exception ex) { return new AsrResult { Error = $"解析失败: {ex.Message}" }; }
            }
        }

        /// <summary>Chat：发送文字 → 返回回答</summary>
        public async Task<ChatResult> ChatAsync(string text)
        {
            Logger.Info($"Chat 请求: {text.Truncate(100)}");
            var payload = _json.Serialize(new { text });
            var resp = await _http.PostAsync($"{_baseUrl}/api/chat",
                new StringContent(payload, Encoding.UTF8, "application/json"));
            string body = await resp.Content.ReadAsStringAsync();
            Logger.Info($"Chat 响应: {resp.StatusCode} {body.Truncate(200)}");
            if (!resp.IsSuccessStatusCode)
                return new ChatResult { Error = $"HTTP {resp.StatusCode}: {body.Truncate(100)}" };
            try { return _json.Deserialize<ChatResult>(body); }
            catch (Exception ex) { return new ChatResult { Error = $"解析失败: {ex.Message}" }; }
        }

        /// <summary>TTS：发送文字 → 返回 wav 音频</summary>
        public async Task<byte[]> TtsAsync(string text)
        {
            Logger.Info($"TTS 请求: {text.Truncate(100)}");
            var payload = _json.Serialize(new { text });
            var resp = await _http.PostAsync($"{_baseUrl}/api/tts",
                new StringContent(payload, Encoding.UTF8, "application/json"));
            if (!resp.IsSuccessStatusCode)
            {
                Logger.Warn($"TTS 失败: {resp.StatusCode}, 尝试 fallback");
                var fb = await _http.PostAsync($"{_baseUrl}/api/tts-fallback",
                    new StringContent(payload, Encoding.UTF8, "application/json"));
                if (!fb.IsSuccessStatusCode)
                    throw new Exception($"TTS 全部失败, HTTP {resp.StatusCode}/{fb.StatusCode}");
                var data = await fb.Content.ReadAsByteArrayAsync();
                Logger.Info($"TTS fallback 成功: {data.Length} bytes");
                return data;
            }
            var body = await resp.Content.ReadAsByteArrayAsync();
            Logger.Info($"TTS 成功: {body.Length} bytes");
            return body;
        }

        /// <summary>同步聊天记录</summary>
        public async Task SyncChatAsync(string role, string content)
        {
            var payload = _json.Serialize(new { role, content });
            await _http.PostAsync($"{_baseUrl}/api/sync_chat",
                new StringContent(payload, Encoding.UTF8, "application/json"));
        }

        /// <summary>清空聊天记录</summary>
        public async Task ClearChatAsync()
        {
            await _http.PostAsync($"{_baseUrl}/api/clear_current_chat",
                new StringContent("{}", Encoding.UTF8, "application/json"));
            Logger.Info("聊天记录已清空");
        }

        /// <summary>诊断：检测服务端口</summary>
        public async Task<DiagResult> DiagCheckAsync(string service)
        {
            Logger.Info($"诊断检测: {service}");
            var resp = await _http.GetAsync($"{_baseUrl}/api/diag/check?service={service}");
            string body = await resp.Content.ReadAsStringAsync();
            try { return _json.Deserialize<DiagResult>(body); }
            catch { return new DiagResult { Alive = false, Error = body.Truncate(100) }; }
        }
    }

    public class AsrResult
    {
        public string Text { get; set; }
        public string Error { get; set; }
    }

    public class ChatResult
    {
        public string Text { get; set; }
        public string Error { get; set; }
    }

    public class DiagResult
    {
        public string Service { get; set; }
        public bool Alive { get; set; }
        public string Host { get; set; }
        public int Port { get; set; }
        public string Error { get; set; }
    }

    /// <summary>字符串截断扩展</summary>
    public static class StringExt
    {
        public static string Truncate(this string s, int max)
        {
            if (string.IsNullOrEmpty(s) || s.Length <= max) return s;
            return s.Substring(0, max) + "...";
        }
    }
}
