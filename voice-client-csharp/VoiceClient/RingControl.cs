using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Windows.Forms;

namespace VoiceClient
{
    /// <summary>
    /// 流水线状态圆圈控件 — GDI+ 绘制 SVG 风格的计时外圈
    /// 支持顺时针/逆时针倒计时、绿/黄/红状态色、红叉故障指示
    /// </summary>
    public class RingControl : UserControl
    {
        // 状态枚举
        public enum RingState { Idle, Running, Done, Error }

        // 颜色
        public enum RingColor { Gray, Green, Yellow, Red }

        private RingState _state = RingState.Idle;
        private RingColor _color = RingColor.Gray;
        private string _label = "";
        private string _icon = "";
        private float _progress = 0f; // 0~1
        private bool _counterClockwise = false;
        private Timer _timer;
        private int _totalMs;
        private int _elapsedMs;
        private bool _hover = false;

        public string ServiceName { get; set; } // 用于诊断
        public Func<string, Task<string>> DiagCallback { get; set; }

        // 颜色映射
        private static readonly Color[] RingColors = {
            Color.FromArgb(80, 80, 80),     // Gray
            Color.FromArgb(76, 175, 80),    // Green
            Color.FromArgb(255, 183, 77),   // Yellow
            Color.FromArgb(244, 67, 54),    // Red
        };

        public RingControl()
        {
            this.Size = new Size(48, 54);
            this.ResizeRedraw = true;
            this.DoubleBuffered = true;
            this.Cursor = Cursors.Hand;

            _timer = new Timer { Interval = 100 };
            _timer.Tick += (s, e) =>
            {
                _elapsedMs += 100;
                _progress = Math.Min(1f, (float)_elapsedMs / _totalMs);
                if (_progress >= 1f)
                {
                    _timer.Stop();
                    _state = RingState.Done;
                }
                Invalidate();
            };

            this.Click += OnClick;
            this.MouseEnter += (s, e) => { _hover = true; Invalidate(); };
            this.MouseLeave += (s, e) => { _hover = false; Invalidate(); };
        }

        public void SetLabel(string label) { _label = label; Invalidate(); }
        public void SetIcon(string icon) { _icon = icon; Invalidate(); }

        public void SetColor(RingColor c)
        {
            _color = c;
            Invalidate();
            Logger.Debug($"Ring {_label} 颜色: {c}");
        }

        public void Start(int seconds, bool counterClockwise = false)
        {
            _totalMs = seconds * 1000;
            _elapsedMs = 0;
            _progress = 0f;
            _counterClockwise = counterClockwise;
            _state = RingState.Running;
            _timer.Start();
            Invalidate();
            Logger.Debug($"Ring {_label} 开始: {seconds}s {(counterClockwise ? "逆时针" : "顺时针")}");
        }

        public void Done()
        {
            _timer.Stop();
            _state = RingState.Done;
            _progress = 1f;
            Invalidate();
            Logger.Debug($"Ring {_label} 完成");
        }

        public void Idle()
        {
            _timer.Stop();
            _state = RingState.Idle;
            _progress = 0f;
            Invalidate();
        }

        public void Error()
        {
            _timer.Stop();
            _state = RingState.Error;
            Invalidate();
            Logger.Debug($"Ring {_label} 故障");
        }

        public void SetProgress(float p) { _progress = p; Invalidate(); }

        private async void OnClick(object sender, EventArgs e)
        {
            if (_state != RingState.Error || string.IsNullOrEmpty(ServiceName) || DiagCallback == null)
                return;

            Logger.Info($"Ring {_label} 点击诊断: {ServiceName}");
            var result = await DiagCallback(ServiceName);
            Logger.Info($"Ring 诊断结果: {result}");
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            var g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;

            int cx = this.Width / 2;
            int cy = this.Height / 2 - 2;
            int outerR = Math.Min(cx, cy) - 2;
            int innerR = outerR - 8;

            // 外圈（计时环）
            if (_state == RingState.Running)
            {
                using (var pen = new Pen(RingColors[(int)_color], 3))
                {
                    float startAngle = _counterClockwise ? 90f : -90f;
                    float sweep = _counterClockwise ? -(_progress * 360f) : (_progress * 360f);
                    g.DrawArc(pen, cx - outerR, cy - outerR, outerR * 2, outerR * 2, startAngle, sweep);
                }
            }

            // 内圈（状态圆）
            int d = innerR * 2;
            int ix = cx - innerR;
            int iy = cy - innerR;

            using (var bgBrush = new SolidBrush(Color.FromArgb(30, 30, 40)))
            {
                g.FillEllipse(bgBrush, ix, iy, d, d);
            }
            using (var borderPen = new Pen(Color.FromArgb(60, 60, 70), 1))
            {
                g.DrawEllipse(borderPen, ix, iy, d, d);
            }

            // 红叉状态
            if (_state == RingState.Error)
            {
                using (var crossPen = new Pen(Color.FromArgb(244, 67, 54), 2))
                {
                    int pad = innerR / 3;
                    g.DrawLine(crossPen, ix + pad, iy + pad, ix + d - pad, iy + d - pad);
                    g.DrawLine(crossPen, ix + d - pad, iy + pad, ix + pad, iy + d - pad);
                }
            }
            // 空闲状态 - 灰色圆点
            else if (_state == RingState.Idle)
            {
                using (var dotBrush = new SolidBrush(Color.FromArgb(80, 80, 80)))
                {
                    g.FillEllipse(dotBrush, ix + innerR / 2, iy + innerR / 2, innerR, innerR);
                }
            }
            // 正常运行 - 填充对应颜色
            else if (_state == RingState.Done || _state == RingState.Running)
            {
                int dotSize = innerR - 4;
                int dotX = cx - dotSize / 2;
                int dotY = cy - dotSize / 2;
                using (var fillBrush = new SolidBrush(Color.FromArgb(80, RingColors[(int)_color])))
                {
                    g.FillEllipse(fillBrush, dotX, dotY, dotSize, dotSize);
                }
            }

            // 图标文字
            if (!string.IsNullOrEmpty(_icon))
            {
                using (var font = new Font("Segoe UI Emoji", 12))
                using (var brush = new SolidBrush(Color.FromArgb(200, 200, 200)))
                {
                    var sz = g.MeasureString(_icon, font);
                    g.DrawString(_icon, font, brush, cx - sz.Width / 2, cy - sz.Height / 2);
                }
            }

            // 标签
            if (!string.IsNullOrEmpty(_label))
            {
                using (var font = new Font("Microsoft YaHei", 8))
                using (var brush = new SolidBrush(Color.FromArgb(140, 140, 140)))
                {
                    var sz = g.MeasureString(_label, font);
                    g.DrawString(_label, font, brush, cx - sz.Width / 2, this.Height - sz.Height - 2);
                }
            }

            // hover 高亮
            if (_hover && _state == RingState.Error)
            {
                using (var hlPen = new Pen(Color.FromArgb(100, 244, 67, 54), 2))
                {
                    g.DrawRectangle(hlPen, 0, 0, this.Width - 1, this.Height - 1);
                }
            }
        }
    }
}
