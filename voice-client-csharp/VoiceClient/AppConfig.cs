using System;
using System.IO;
using System.Web.Script.Serialization;

namespace VoiceClient
{
    /// <summary>
    /// 应用配置 — 从 config.json 读取 voice_server 地址
    /// 配置文件路径: %LOCALAPPDATA%\VoiceClient\config.json
    /// 首次运行自动生成默认配置
    /// </summary>
    public class AppConfig
    {
        /// <summary>语音后端服务器地址 (bt2)</summary>
        public string Host { get; set; } = "192.168.1.21";

        /// <summary>语音后端服务器端口</summary>
        public int Port { get; set; } = 12054;

        /// <summary>是否使用 HTTPS</summary>
        public bool UseHttps { get; set; } = false;

        /// <summary>获取 voice_server 的完整 URL</summary>
        public string VoiceServerUrl
        {
            get
            {
                string proto = UseHttps ? "https" : "http";
                return $"{proto}://{Host}:{Port}";
            }
        }

        /// <summary>从文件加载配置，不存在则创建默认</summary>
        public static AppConfig Load(string path = null)
        {
            if (path == null)
            {
                path = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "VoiceClient", "config.json");
            }

            if (File.Exists(path))
            {
                try
                {
                    string json = File.ReadAllText(path);
                    var cfg = new JavaScriptSerializer().Deserialize<AppConfig>(json);
                    Logger.Info($"配置已加载: {path}");
                    return cfg;
                }
                catch (Exception ex)
                {
                    Logger.Warn($"配置加载失败，使用默认: {ex.Message}");
                }
            }

            // 创建默认配置
            var def = new AppConfig();
            try
            {
                string dir = Path.GetDirectoryName(path);
                Directory.CreateDirectory(dir);
                string json = new JavaScriptSerializer().Serialize(new { def.Host, def.Port, def.UseHttps });
                File.WriteAllText(path, json);
                Logger.Info($"已创建默认配置文件: {path}");
            }
            catch (Exception ex)
            {
                Logger.Warn($"创建默认配置失败: {ex.Message}");
            }
            return def;
        }
    }
}
