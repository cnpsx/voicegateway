using System;
using System.IO;

namespace VoiceClient
{
    /// <summary>
    /// 日志记录器 — 所有重要动作都记录到文件和控制台
    /// </summary>
    public static class Logger
    {
        private static string _logPath;
        private static readonly object _lock = new object();

        public static void Init()
        {
            string dir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "VoiceClient", "logs");
            Directory.CreateDirectory(dir);
            _logPath = Path.Combine(dir, $"voice_{DateTime.Now:yyyyMMdd}.log");

            File.AppendAllText(_logPath, $"=== 日志开始 {DateTime.Now:yyyy-MM-dd HH:mm:ss} ===\r\n");
        }

        private static void Write(string level, string msg)
        {
            string line = $"{DateTime.Now:HH:mm:ss.fff} [{level}] {msg}";
            Console.WriteLine(line);
            if (_logPath != null)
            {
                lock (_lock)
                {
                    try { File.AppendAllText(_logPath, line + "\r\n"); }
                    catch { /* 日志写失败不阻塞主流程 */ }
                }
            }
        }

        public static void Debug(string msg) => Write("DEBUG", msg);
        public static void Info(string msg) => Write("INFO", msg);
        public static void Warn(string msg) => Write("WARN", msg);
        public static void Error(string msg) => Write("ERROR", msg);
        public static void Error(string msg, Exception ex) => Write("ERROR", $"{msg} | {ex.Message}\r\n{ex.StackTrace}");
    }
}
