using System;
using System.IO;
using System.Windows.Forms;

namespace VoiceClient
{
    static class Program
    {
        [STAThread]
        static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            Logger.Init();
            Logger.Info("=== 语音客户端启动 ===");

            try
            {
                Application.Run(new MainForm());
            }
            catch (Exception ex)
            {
                Logger.Error("主线程异常", ex);
                MessageBox.Show($"程序异常退出: {ex.Message}", "错误", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }

            Logger.Info("=== 语音客户端退出 ===");
        }
    }
}
