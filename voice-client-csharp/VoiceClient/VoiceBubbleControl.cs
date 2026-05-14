using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Media;
using System.Windows.Forms;

namespace VoiceClient
{
    /// <summary>
    /// 语音气泡控件 — 像微信语音消息一样显示
    /// 绿色的泡泡 = 我的消息（右侧）
    /// 灰色的泡泡 = Hermes 消息（左侧）
    /// 带喇叭图标的 = 语音消息，点击播放
    /// </summary>
    public class VoiceBubbleControl : UserControl
    {
        private string _role;       // "user" / "assistant" / "system"
        private string _text;
        private byte[] _wavData;
        private bool _isVoiceMsg;   // 是否是语音消息（带波形图标）
        private bool _isPlaying;
        private bool _hover;

        private static readonly Font _timeFont = new Font("Microsoft YaHei", 7);
        private static readonly Font _textFont = new Font("Microsoft YaHei", 9);
        private static readonly Font _voiceFont = new Font("Segoe UI Emoji", 10);

        // 颜色定义
        private static readonly Color ColorUserBg = Color.FromArgb(124, 92, 252);
        private static readonly Color ColorUserText = Color.White;
        private static readonly Color ColorAssistantBg = Color.FromArgb(30, 30, 40);
        private static readonly Color ColorAssistantBorder = Color.FromArgb(50, 50, 60);
        private static readonly Color ColorAssistantText = Color.FromArgb(224, 224, 224);
        private static readonly Color ColorSystemBg = Color.FromArgb(25, 25, 30);
        private static readonly Color ColorSystemText = Color.FromArgb(100, 100, 100);
        private static readonly Color ColorTime = Color.FromArgb(80, 80, 80);

        public VoiceBubbleControl(string role, string text, byte[] wavData = null, bool isRecordedByMe = false)
        {
            _role = role;
            _text = text;
            _wavData = wavData;
            _isVoiceMsg = wavData != null;

            this.Height = _isVoiceMsg ? 48 : 40;
            this.Padding = new Padding(0);
            this.Margin = new Padding(2, 3, 2, 1);
            this.DoubleBuffered = true;
            this.Cursor = _isVoiceMsg ? Cursors.Hand : Cursors.Default;

            if (_isVoiceMsg)
            {
                this.Click += (s, e) => PlayVoice();
                this.MouseEnter += (s, e) => { _hover = true; Invalidate(); };
                this.MouseLeave += (s, e) => { _hover = false; Invalidate(); };
            }
        }

        private void PlayVoice()
        {
            if (_wavData == null || _wavData.Length <= 44) return;
            if (_isPlaying) return;
            _isPlaying = true;
            Invalidate();

            try
            {
                string tmp = Path.GetTempFileName() + ".wav";
                File.WriteAllBytes(tmp, _wavData);
                using (var player = new SoundPlayer(tmp))
                {
                    player.PlaySync();
                }
                try { File.Delete(tmp); } catch { }
            }
            catch (Exception ex)
            {
                Logger.Warn($"播放语音失败: {ex.Message}");
            }

            _isPlaying = false;
            Invalidate();
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            var g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;

            string time = DateTime.Now.ToString("HH:mm");
            int rightMargin = 10;
            int leftMargin = 10;

            if (_role == "system")
            {
                // 系统消息居中
                var sz = g.MeasureString(_text, _textFont);
                float x = (this.Width - sz.Width) / 2;
                using (var brush = new SolidBrush(ColorSystemText))
                {
                    g.DrawString(_text, _textFont, brush, x, 4);
                }
                return;
            }

            bool isUser = _role == "user";
            int textX, textY = 6;
            int maxTextWidth = this.Width - 120;

            if (_isVoiceMsg)
            {
                // 语音消息 — 显示波形图标 + 时长
                string icon = _isPlaying ? "🔊▶" : "🔊";
                string label = $"{icon}  {_text}";

                var sz = g.MeasureString(label, _voiceFont);
                int bubbleW = (int)sz.Width + 24;
                int bubbleH = 34;
                int bubbleX = isUser ? this.Width - bubbleW - rightMargin : leftMargin;
                int bubbleY = 2;

                using (var bgBrush = new SolidBrush(isUser ? ColorUserBg : ColorAssistantBg))
                using (var borderPen = new Pen(isUser ? ColorUserBg : ColorAssistantBorder, 1))
                {
                    // 圆角矩形
                    var rect = new Rectangle(bubbleX, bubbleY, bubbleW, bubbleH);
                    int r = 10;
                    using (var path = RoundedRect(rect, r))
                    {
                        g.FillPath(bgBrush, path);
                        if (!isUser) g.DrawPath(borderPen, path);
                    }
                }

                using (var brush = new SolidBrush(isUser ? ColorUserText : ColorAssistantText))
                {
                    g.DrawString(label, _voiceFont, brush, bubbleX + 12, bubbleY + 8);
                }

                // hover 高亮
                if (_hover)
                {
                    using (var hlPen = new Pen(Color.FromArgb(80, 255, 255, 255), 1))
                    {
                        var rect = new Rectangle(bubbleX, bubbleY, bubbleW, bubbleH);
                        int r = 10;
                        using (var path = RoundedRect(rect, r))
                        {
                            g.DrawPath(hlPen, path);
                        }
                    }
                }

                textY = bubbleY + bubbleH + 2;
                textX = isUser ? bubbleX : bubbleX;
            }
            else
            {
                // 文字消息
                var sz = g.MeasureString(_text, _textFont);
                float bubbleW = Math.Min(sz.Width + 24, maxTextWidth);
                float bubbleH = Math.Max(sz.Height + 16, 28);
                float bubbleX = isUser ? this.Width - bubbleW - rightMargin : leftMargin;
                float bubbleY = 2;

                using (var bgBrush = new SolidBrush(isUser ? ColorUserBg : ColorAssistantBg))
                using (var borderPen = new Pen(isUser ? ColorUserBg : ColorAssistantBorder, 1))
                {
                    var rect = new Rectangle((int)bubbleX, (int)bubbleY, (int)bubbleW, (int)bubbleH);
                    int r = 10;
                    using (var path = RoundedRect(rect, r))
                    {
                        g.FillPath(bgBrush, path);
                        if (!isUser) g.DrawPath(borderPen, path);
                    }
                }

                // 文字换行
                using (var brush = new SolidBrush(isUser ? ColorUserText : ColorAssistantText))
                {
                    var textRect = new RectangleF(bubbleX + 12, bubbleY + 6, bubbleW - 24, bubbleH - 12);
                    g.DrawString(_text, _textFont, brush, textRect);
                }

                textY = (int)(bubbleY + bubbleH + 2);
                textX = isUser ? (int)bubbleX : (int)bubbleX;
            }

            // 时间
            using (var brush = new SolidBrush(ColorTime))
            {
                var sz = g.MeasureString(time, _timeFont);
                float tx = isUser ? textX + 4 : textX + 4;
                g.DrawString(time, _timeFont, brush, tx, textY);
            }
        }

        private static GraphicsPath RoundedRect(Rectangle rect, int r)
        {
            var path = new GraphicsPath();
            path.AddArc(rect.X, rect.Y, r * 2, r * 2, 180, 90);
            path.AddArc(rect.Right - r * 2, rect.Y, r * 2, r * 2, 270, 90);
            path.AddArc(rect.Right - r * 2, rect.Bottom - r * 2, r * 2, r * 2, 0, 90);
            path.AddArc(rect.X, rect.Bottom - r * 2, r * 2, r * 2, 90, 90);
            path.CloseFigure();
            return path;
        }
    }
}
