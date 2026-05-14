using System;
using System.Drawing;
using System.Windows.Forms;
using System.Threading.Tasks;
using System.Collections.Generic;
using System.IO;
using System.Media;
using System.Text;

namespace VoiceClient
{
    partial class MainForm : Form
    {
        // 语音消息记录（用于显示语音气泡）
        private class VoiceMessage
        {
            public DateTime Time { get; set; }
            public string Role { get; set; }
            public string Text { get; set; }
            public byte[] WavData { get; set; }  // 自己的录音
            public string TempFile { get; set; } // 临时 wav 文件
        }

        private const string BACKEND_URL = "http://192.168.1.21:12054";

        // 控件
        private RingControl _ringMic;
        private RingControl _ringAsr;
        private RingControl _ringHermes;
        private RingControl _ringTTS;
        private Button _btnRecord;
        private Button _btnMode;
        private FlowLayoutPanel _chatPanel;  // 聊天面板（气泡列表）
        private TextBox _txtInput;
        private Button _btnSend;
        private Button _btnClear;
        private Button _btnDebug;
        private Label _lblStatus;
        private Label _lblHint;
        private Label _lblTimer;
        private CheckBox _chkVoiceFilter;

        private ApiClient _api;
        private AudioRecorder _recorder;
        private bool _isHoldMode = true;
        private bool _isRecording = false;
        private bool _isProcessing = false;
        private List<VoiceMessage> _voiceMessages = new List<VoiceMessage>();
        private bool _debugMode = false;
        private Panel _debugPanel;  // 调试完成后出现的"发送日志"面板

        public MainForm()
        {
            InitializeComponent();
            Logger.Info("主窗体初始化");
        }

        private void InitializeComponent()
        {
            this.Text = "🎙️ 语音通话 - Hermes";
            this.Size = new Size(480, 760);
            this.MinimumSize = new Size(380, 540);
            this.StartPosition = FormStartPosition.CenterScreen;
            this.BackColor = Color.FromArgb(10, 10, 15);
            this.ForeColor = Color.FromArgb(224, 224, 224);
            this.FormBorderStyle = FormBorderStyle.Sizable;

            _api = new ApiClient(BACKEND_URL);

            // ═══════════════ 顶部标题栏 ═══════════════
            var topPanel = new Panel
            {
                Dock = DockStyle.Top, Height = 40,
                BackColor = Color.FromArgb(20, 20, 28),
            };
            var lblTitle = new Label
            {
                Text = "🎙️ 语音通话",
                Font = new Font("Microsoft YaHei", 12, FontStyle.Bold),
                ForeColor = Color.FromArgb(200, 200, 200),
                Location = new Point(10, 8), AutoSize = true,
            };
            _lblStatus = new Label
            {
                Text = "空闲", Font = new Font("Microsoft YaHei", 9),
                ForeColor = Color.FromArgb(100, 100, 100),
                Location = new Point(420, 10), AutoSize = true,
            };
            _lblTimer = new Label
            {
                Text = "", Font = new Font("Consolas", 9),
                ForeColor = Color.FromArgb(244, 67, 54),
                Location = new Point(360, 10), AutoSize = true,
            };
            topPanel.Controls.Add(lblTitle);
            topPanel.Controls.Add(_lblTimer);
            topPanel.Controls.Add(_lblStatus);

            // ═══════════════ 流水线状态栏 ═══════════════
            var pipelinePanel = new FlowLayoutPanel
            {
                Dock = DockStyle.Top, Height = 68,
                BackColor = Color.FromArgb(15, 15, 22),
                Padding = new Padding(8, 2, 8, 0),
                FlowDirection = FlowDirection.LeftToRight,
                WrapContents = false,
            };
            _ringMic = new RingControl { Size = new Size(46, 54) };
            _ringMic.SetLabel("麦克风"); _ringMic.SetIcon("🎤");
            pipelinePanel.Controls.Add(_ringMic);
            pipelinePanel.Controls.Add(MakeArrow());
            _ringAsr = new RingControl { Size = new Size(46, 54), ServiceName = "asr" };
            _ringAsr.SetLabel("ASR"); _ringAsr.SetIcon("⚡");
            _ringAsr.DiagCallback = DiagServiceAsync;
            pipelinePanel.Controls.Add(_ringAsr);
            pipelinePanel.Controls.Add(MakeArrow());
            _ringHermes = new RingControl { Size = new Size(46, 54), ServiceName = "hermes" };
            _ringHermes.SetLabel("Hermes"); _ringHermes.SetIcon("🤖");
            _ringHermes.DiagCallback = DiagServiceAsync;
            pipelinePanel.Controls.Add(_ringHermes);
            pipelinePanel.Controls.Add(MakeArrow());
            _ringTTS = new RingControl { Size = new Size(46, 54), ServiceName = "tts" };
            _ringTTS.SetLabel("TTS"); _ringTTS.SetIcon("🔊");
            _ringTTS.DiagCallback = DiagServiceAsync;
            pipelinePanel.Controls.Add(_ringTTS);

            // ═══════════════ 控制按钮区 ═══════════════
            var controlPanel = new Panel
            {
                Dock = DockStyle.Top, Height = 56,
                BackColor = Color.FromArgb(18, 18, 25),
            };

            _btnMode = new Button
            {
                Text = "按住说话", Font = new Font("Microsoft YaHei", 9),
                FlatStyle = FlatStyle.Flat,
                BackColor = Color.FromArgb(124, 92, 252), ForeColor = Color.White,
                Size = new Size(72, 30), Location = new Point(8, 13),
                FlatAppearance = { BorderSize = 0 }, Cursor = Cursors.Hand,
            };
            _btnMode.Click += (s, e) => ToggleMode();

            _btnRecord = new Button
            {
                Text = "🎤", Font = new Font("Segoe UI Emoji", 18),
                FlatStyle = FlatStyle.Flat,
                BackColor = Color.FromArgb(30, 30, 40), ForeColor = Color.FromArgb(200, 200, 200),
                Size = new Size(46, 46), Location = new Point(88, 5),
                FlatAppearance = { BorderSize = 0 }, Cursor = Cursors.Hand,
            };
            _btnRecord.MouseDown += (s, e) => { if (_isHoldMode) StartRecording(); };
            _btnRecord.MouseUp += (s, e) => { if (_isHoldMode) StopRecording(); };
            _btnRecord.Click += (s, e) => { if (!_isHoldMode) ToggleRecording(); };

            _lblHint = new Label
            {
                Text = "按住🎤说话，松开发送",
                Font = new Font("Microsoft YaHei", 9),
                ForeColor = Color.FromArgb(80, 80, 80),
                Location = new Point(145, 18), AutoSize = true,
            };

            _btnDebug = new Button
            {
                Text = "🔧调试", Font = new Font("Microsoft YaHei", 8),
                FlatStyle = FlatStyle.Flat,
                BackColor = Color.FromArgb(40, 40, 50), ForeColor = Color.FromArgb(200, 200, 200),
                Size = new Size(55, 24), Location = new Point(410, 16),
                FlatAppearance = { BorderSize = 0 }, Cursor = Cursors.Hand,
            };
            _btnDebug.Click += async (s, e) => await StartDebugAsync();

            controlPanel.Controls.Add(_btnMode);
            controlPanel.Controls.Add(_btnRecord);
            controlPanel.Controls.Add(_lblHint);
            controlPanel.Controls.Add(_btnDebug);

            // ═══════════════ 聊天面板（气泡列表） ═══════════════
            _chatPanel = new FlowLayoutPanel
            {
                Dock = DockStyle.Fill,
                BackColor = Color.FromArgb(10, 10, 15),
                AutoScroll = true,
                FlowDirection = FlowDirection.TopDown,
                WrapContents = false,
                Padding = new Padding(8, 4, 8, 4),
            };

            // ═══════════════ 调试结果面板（初始隐藏） ═══════════════
            _debugPanel = new Panel
            {
                Dock = DockStyle.Bottom, Height = 40,
                BackColor = Color.FromArgb(25, 20, 15),
                Visible = false,
            };
            var btnSendLog = new Button
            {
                Text = "📤 发送调试日志给 Hermes",
                Font = new Font("Microsoft YaHei", 9, FontStyle.Bold),
                FlatStyle = FlatStyle.Flat,
                BackColor = Color.FromArgb(244, 67, 54), ForeColor = Color.White,
                Size = new Size(260, 32),
                Location = new Point(110, 4),
                FlatAppearance = { BorderSize = 0 }, Cursor = Cursors.Hand,
            };
            btnSendLog.Click += async (s, e) => await SendDebugLogAsync();
            _debugPanel.Controls.Add(btnSendLog);

            // ═══════════════ 输入区 ═══════════════
            var inputPanel = new Panel
            {
                Dock = DockStyle.Bottom, Height = 38,
                BackColor = Color.FromArgb(18, 18, 25),
            };

            _txtInput = new TextBox
            {
                Font = new Font("Microsoft YaHei", 10),
                BackColor = Color.FromArgb(30, 30, 40), ForeColor = Color.FromArgb(200, 200, 200),
                BorderStyle = BorderStyle.FixedSingle,
                Location = new Point(8, 5), Size = new Size(310, 28),
            };
            _txtInput.KeyDown += (s, e) => { if (e.KeyCode == Keys.Enter) SendTextAsync(); };

            _btnSend = new Button
            {
                Text = "发送", Font = new Font("Microsoft YaHei", 9),
                FlatStyle = FlatStyle.Flat,
                BackColor = Color.FromArgb(124, 92, 252), ForeColor = Color.White,
                Size = new Size(56, 28), Location = new Point(326, 5),
                FlatAppearance = { BorderSize = 0 }, Cursor = Cursors.Hand,
            };
            _btnSend.Click += (s, e) => SendTextAsync();

            _btnClear = new Button
            {
                Text = "🗑️", Font = new Font("Segoe UI Emoji", 10),
                FlatStyle = FlatStyle.Flat,
                BackColor = Color.Transparent, ForeColor = Color.FromArgb(80, 80, 80),
                Size = new Size(28, 28), Location = new Point(390, 5),
                FlatAppearance = { BorderSize = 0 }, Cursor = Cursors.Hand,
            };
            _btnClear.Click += async (s, e) =>
            {
                if (MessageBox.Show("清空当前对话?", "确认", MessageBoxButtons.YesNo, MessageBoxIcon.Question) == DialogResult.Yes)
                {
                    await _api.ClearChatAsync();
                    _chatPanel.Controls.Clear();
                    _voiceMessages.Clear();
                    AddBubble("system", "🗑️ 对话已清空");
                }
            };

            inputPanel.Controls.Add(_txtInput);
            inputPanel.Controls.Add(_btnSend);
            inputPanel.Controls.Add(_btnClear);

            // ═══════════════ 布局组合 ═══════════════
            this.Controls.Add(_chatPanel);
            this.Controls.Add(controlPanel);
            this.Controls.Add(pipelinePanel);
            this.Controls.Add(topPanel);
            this.Controls.Add(_debugPanel);
            this.Controls.Add(inputPanel);

            // 初始提示
            AddBubble("system", "🎙️ 语音通话已连接，开始对话吧");

            // 启动服务检查
            Task.Run(async () => { await Task.Delay(800); await CheckAllServicesAsync(); });

            Logger.Info("主窗体加载完成");
        }

        // ═══════════════════════════════════════
        // 语音气泡 — 像微信一样显示聊天消息
        // ═══════════════════════════════════════
        private void AddBubble(string role, string text, byte[] wavData = null)
        {
            if (this.InvokeRequired)
            {
                this.Invoke(new Action(() => AddBubble(role, text, wavData)));
                return;
            }

            var bubble = new VoiceBubbleControl(role, text, wavData);
            bubble.Width = _chatPanel.ClientSize.Width - 16;
            bubble.Anchor = AnchorStyles.Left | AnchorStyles.Right;
            _chatPanel.Controls.Add(bubble);
            _chatPanel.ScrollControlIntoView(bubble);
        }

        private void AddUserVoiceBubble(string text, byte[] wavData)
        {
            if (this.InvokeRequired)
            {
                this.Invoke(new Action(() => AddUserVoiceBubble(text, wavData)));
                return;
            }

            var bubble = new VoiceBubbleControl("user", text, wavData, isRecordedByMe: true);
            bubble.Width = _chatPanel.ClientSize.Width - 16;
            _chatPanel.Controls.Add(bubble);
            _chatPanel.ScrollControlIntoView(bubble);
        }

        // ═══════════════════════════════════════
        // 调试功能
        // ═══════════════════════════════════════
        private async Task StartDebugAsync()
        {
            if (_debugMode) return;
            _debugMode = true;
            Logger.Info("========== 调试模式启动 ==========");
            _btnDebug.Enabled = false;
            _btnDebug.Text = "⏳调试中";

            AddBubble("system", "🔧 调试模式启动，正在通知 Hermes...");

            // 通知 Hermes
            try
            {
                await _api.ChatAsync("[内部诊断] 调试要开始了哟，你准备好监控。");
                AddBubble("system", "✅ Hermes 已就绪");
            }
            catch (Exception ex)
            {
                Logger.Warn($"通知 Hermes 失败: {ex.Message}");
            }

            // 开始录音测试
            AddBubble("system", "🎤 开始录音测试...");
            await Task.Delay(500);

            try
            {
                // 录音约 3 秒
                var recorder = new AudioRecorder(16000);
                recorder.Start();
                _ringMic.Start(10, false); // 10 秒倒计时
                await Task.Delay(3000);
                byte[] wav = recorder.Stop();
                recorder.Dispose();
                _ringMic.Done();

                if (wav != null && wav.Length > 44)
                {
                    Logger.Info($"调试录音完成: {wav.Length} bytes");
                    // 显示语音气泡（可点击播放）
                    AddUserVoiceBubble("调试录音 - 点击播放", wav);

                    // ASR 测试
                    _ringAsr.Start(30, true);
                    AddBubble("system", "🔍 正在识别语音...");
                    var asr = await _api.AsrAsync(wav);
                    _ringAsr.Done();

                    if (!string.IsNullOrEmpty(asr.Text))
                    {
                        string userName = Environment.UserName;
                        AddBubble("user", $"[调试] 我: {asr.Text}");
                        Logger.Info($"调试 ASR: {asr.Text}");

                        // LLM 测试
                        _ringHermes.Start(30);
                        AddBubble("system", "🤖 Hermes 正在回复...");
                        var chat = await _api.ChatAsync($"[调试模式] 用户 {userName} 说: {asr.Text}。这是一个语音调试测试，请给出简短确认。");
                        _ringHermes.Done();

                        if (!string.IsNullOrEmpty(chat.Text))
                        {
                            AddBubble("assistant", chat.Text);
                            Logger.Info($"调试 LLM: {chat.Text.Truncate(200)}");

                            // TTS 测试
                            _ringTTS.Start(30);
                            AddBubble("system", "🔊 正在合成语音...");
                            try
                            {
                                byte[] tts = await _api.TtsAsync(chat.Text.Length > 100 ? chat.Text.Substring(0, 100) : chat.Text);
                                _ringTTS.Done();
                                if (tts != null && tts.Length > 44)
                                {
                                    string tmp = Path.GetTempFileName() + ".wav";
                                    File.WriteAllBytes(tmp, tts);
                                    using (var player = new SoundPlayer(tmp)) { player.PlaySync(); }
                                    try { File.Delete(tmp); } catch { }
                                }
                                AddBubble("system", "✅ TTS 播放完成");
                            }
                            catch (Exception ex)
                            {
                                Logger.Warn($"调试 TTS 失败: {ex.Message}");
                                _ringTTS.Error();
                            }
                        }
                        else
                        {
                            AddBubble("system", "⚠️ LLM 返回为空");
                        }
                    }
                    else
                    {
                        AddBubble("system", "⚠️ ASR 未识别到语音");
                    }
                }
                else
                {
                    AddBubble("system", "⚠️ 录音数据为空");
                }
            }
            catch (Exception ex)
            {
                Logger.Error("调试异常", ex);
                AddBubble("system", $"⚠️ 调试异常: {ex.Message}");
            }

            // 调试完成，显示"发送日志"按钮
            _debugPanel.Visible = true;
            _btnDebug.Text = "✅调试完成";
            AddBubble("system", "✅ 调试完成，点击下方按钮发送日志给 Hermes 分析");
            Logger.Info("========== 调试完成 ==========");
        }

        private async Task SendDebugLogAsync()
        {
            _debugPanel.Visible = false;
            AddBubble("system", "📤 正在发送调试日志...");

            try
            {
                // 读取日志文件
                string logDir = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "VoiceClient", "logs");
                string today = $"voice_{DateTime.Now:yyyyMMdd}.log";
                string logPath = Path.Combine(logDir, today);
                string logContent = "";

                if (File.Exists(logPath))
                {
                    // 只取最后 200 行
                    var lines = File.ReadAllLines(logPath);
                    int start = Math.Max(0, lines.Length - 200);
                    logContent = string.Join("\n", lines, start, lines.Length - start);
                }

                // 生成诊断报告
                var sb = new StringBuilder();
                sb.AppendLine("[语音客户端调试报告]");
                sb.AppendLine($"时间: {DateTime.Now:yyyy-MM-dd HH:mm:ss}");
                sb.AppendLine($"用户: {Environment.UserName}");
                sb.AppendLine($"机器: {Environment.MachineName}");
                sb.AppendLine($"OS: {Environment.OSVersion}");
                sb.AppendLine($"后端: {BACKEND_URL}");
                sb.AppendLine($"总消息数: {_voiceMessages.Count}");
                sb.AppendLine("");
                sb.AppendLine("--- 最近日志 (200行) ---");
                sb.AppendLine(logContent);

                string report = sb.ToString();
                Logger.Info($"调试报告长度: {report.Length} chars");

                // 发给 Hermes
                _ringHermes.Start(60);
                var chat = await _api.ChatAsync(
                    "[诊断报告]\n" + report +
                    "\n\n请分析以上日志，找出所有异常和潜在问题。给出具体的修复建议。");
                _ringHermes.Done();

                if (!string.IsNullOrEmpty(chat.Text))
                {
                    AddBubble("assistant", chat.Text);
                    Logger.Info($"诊断报告已分析，回复长度: {chat.Text.Length}");
                }

                AddBubble("system", "✅ 调试日志已发送并分析完成");
            }
            catch (Exception ex)
            {
                Logger.Error("发送调试日志失败", ex);
                AddBubble("system", $"⚠️ 发送失败: {ex.Message}");
            }

            _debugMode = false;
            _btnDebug.Enabled = true;
            _btnDebug.Text = "🔧调试";
        }

        // ═══════════════════════════════════════
        // 通用方法
        // ═══════════════════════════════════════
        private Label MakeArrowLabel(string text) => new Label
        {
            Text = text, Font = new Font("Microsoft YaHei", 6),
            ForeColor = Color.FromArgb(80, 80, 80),
            AutoSize = false, Size = new Size(20, 54),
            TextAlign = ContentAlignment.MiddleCenter,
            BackColor = Color.Transparent,
        };

        private Label MakeArrow() => new Label
        {
            Text = "→", Font = new Font("Microsoft YaHei", 10),
            ForeColor = Color.FromArgb(60, 60, 60),
            AutoSize = false, Size = new Size(14, 54),
            TextAlign = ContentAlignment.MiddleCenter,
            BackColor = Color.Transparent,
        };

        private void ToggleMode()
        {
            _isHoldMode = !_isHoldMode;
            _btnMode.Text = _isHoldMode ? "按住说话" : "持续监听";
            _lblHint.Text = _isHoldMode ? "按住🎤说话，松开发送" : "单击🎤开始录音";
            Logger.Info($"模式切换: {(_isHoldMode ? "按住说话" : "持续监听")}");
        }

        private void ToggleRecording()
        {
            if (_isRecording) StopRecording();
            else StartRecording();
        }

        private async void StartRecording()
        {
            if (_isProcessing || _isRecording) return;
            _isRecording = true;

            try
            {
                _recorder = new AudioRecorder(16000);
                _recorder.Start();
                _btnRecord.BackColor = Color.FromArgb(50, 20, 20);
                _btnRecord.Text = "⏹";
                _lblStatus.Text = "录音中...";
                _lblStatus.ForeColor = Color.FromArgb(244, 67, 54);
                _ringMic.Start(90);
                Logger.Info("录音开始");
            }
            catch (Exception ex)
            {
                Logger.Error("录音失败", ex);
                AddBubble("system", $"⚠️ 录音失败: {ex.Message}");
                _isRecording = false;
                _btnRecord.BackColor = Color.FromArgb(30, 30, 40);
                _btnRecord.Text = "🎤";
            }
        }

        private async void StopRecording()
        {
            if (!_isRecording) return;
            _isRecording = false;
            _isProcessing = true;

            try
            {
                byte[] wavData = _recorder?.Stop();
                _recorder?.Dispose();
                _recorder = null;

                _ringMic.Done();
                _btnRecord.BackColor = Color.FromArgb(30, 30, 40);
                _btnRecord.Text = "🎤";
                _lblStatus.Text = "处理中...";
                _lblStatus.ForeColor = Color.FromArgb(255, 183, 77);

                if (wavData == null || wavData.Length <= 44)
                {
                    Logger.Warn("录音为空");
                    _isProcessing = false;
                    _lblStatus.Text = "空闲";
                    return;
                }

                Logger.Info($"录音完成: {wavData.Length} bytes");

                // 显示语音气泡（可播放）
                AddUserVoiceBubble("语音消息 - 点击播放", wavData);

                // ASR
                _ringAsr.Start(30, true);
                var asr = await _api.AsrAsync(wavData);
                _ringAsr.Done();
                _ringAsr.SetColor(RingControl.RingColor.Green);

                if (!string.IsNullOrEmpty(asr.Error) || string.IsNullOrEmpty(asr.Text))
                {
                    string err = asr.Error ?? "未识别到语音";
                    Logger.Warn($"ASR: {err}");
                    AddBubble("system", $"⚠️ {err}");
                    _isProcessing = false;
                    _lblStatus.Text = "空闲";
                    return;
                }

                AddBubble("user", asr.Text);
                Logger.Info($"ASR: {asr.Text}");
                await _api.SyncChatAsync("user", asr.Text);

                // LLM
                _lblHint.Text = "Hermes 正在思考...";
                _ringHermes.Start(30);
                var chat = await _api.ChatAsync(asr.Text);
                _ringHermes.Done();
                _ringHermes.SetColor(RingControl.RingColor.Green);

                if (!string.IsNullOrEmpty(chat.Error))
                {
                    AddBubble("system", $"⚠️ {chat.Error}");
                    _isProcessing = false;
                    _lblStatus.Text = "空闲";
                    return;
                }

                AddBubble("assistant", chat.Text);
                Logger.Info($"LLM: {chat.Text.Truncate(200)}");
                await _api.SyncChatAsync("assistant", chat.Text);

                // TTS
                _lblHint.Text = "正在播放语音...";
                _ringTTS.Start(30);
                try
                {
                    string ttsText = chat.Text.Length > 100 ? chat.Text.Substring(0, 100) : chat.Text;
                    byte[] ttsWav = await _api.TtsAsync(ttsText);
                    _ringTTS.Done();
                    _ringTTS.SetColor(RingControl.RingColor.Green);

                    if (ttsWav != null && ttsWav.Length > 44)
                    {
                        string tmp = Path.GetTempFileName() + ".wav";
                        File.WriteAllBytes(tmp, ttsWav);
                        using (var player = new SoundPlayer(tmp)) { player.PlaySync(); }
                        try { File.Delete(tmp); } catch { }
                    }
                }
                catch (Exception ex)
                {
                    Logger.Warn($"TTS: {ex.Message}");
                    _ringTTS.Error();
                }
            }
            catch (Exception ex)
            {
                Logger.Error("处理异常", ex);
                AddBubble("system", $"⚠️ 处理失败: {ex.Message}");
            }

            _isProcessing = false;
            _lblStatus.Text = "空闲";
            _lblStatus.ForeColor = Color.FromArgb(100, 100, 100);
            _lblHint.Text = _isHoldMode ? "按住🎤说话，松开发送" : "单击🎤开始录音";

            _ = CheckAllServicesAsync();
        }

        private async Task SendTextAsync()
        {
            string text = _txtInput.Text.Trim();
            if (string.IsNullOrEmpty(text) || _isProcessing) return;
            _isProcessing = true;

            _txtInput.Clear();
            AddBubble("user", text);
            await _api.SyncChatAsync("user", text);
            Logger.Info($"输入: {text}");

            try
            {
                _lblHint.Text = "Hermes 正在思考...";
                _ringHermes.Start(30);
                var chat = await _api.ChatAsync(text);
                _ringHermes.Done();
                _ringHermes.SetColor(RingControl.RingColor.Green);

                if (!string.IsNullOrEmpty(chat.Error))
                {
                    AddBubble("system", $"⚠️ {chat.Error}");
                }
                else
                {
                    AddBubble("assistant", chat.Text);
                    await _api.SyncChatAsync("assistant", chat.Text);

                    _lblHint.Text = "正在播放语音...";
                    _ringTTS.Start(30);
                    try
                    {
                        string ttsText = chat.Text.Length > 100 ? chat.Text.Substring(0, 100) : chat.Text;
                        byte[] tts = await _api.TtsAsync(ttsText);
                        _ringTTS.Done();
                        _ringTTS.SetColor(RingControl.RingColor.Green);
                        if (tts != null && tts.Length > 44)
                        {
                            string tmp = Path.GetTempFileName() + ".wav";
                            File.WriteAllBytes(tmp, tts);
                            using (var player = new SoundPlayer(tmp)) { player.PlaySync(); }
                            try { File.Delete(tmp); } catch { }
                        }
                    }
                    catch (Exception ex)
                    {
                        Logger.Warn($"TTS: {ex.Message}");
                        _ringTTS.Error();
                    }
                }
            }
            catch (Exception ex)
            {
                Logger.Error("发送异常", ex);
                AddBubble("system", $"⚠️ {ex.Message}");
            }

            _isProcessing = false;
            _lblHint.Text = _isHoldMode ? "按住🎤说话，松开发送" : "单击🎤开始录音";
        }

        private async Task<string> DiagServiceAsync(string service)
        {
            Logger.Info($"诊断: {service}");
            try
            {
                var result = await _api.DiagCheckAsync(service);
                string msg = result.Alive
                    ? $"✅ [{service.ToUpper()}] {result.Host}:{result.Port} 正常"
                    : $"❌ [{service.ToUpper()}] {result.Host}:{result.Port} 异常: {result.Error}";
                AddBubble("system", $"🔍 {msg}");
                Logger.Info(msg);
                var ring = GetRing(service);
                if (ring != null)
                {
                    if (result.Alive) { ring.SetColor(RingControl.RingColor.Green); ring.Idle(); }
                    else ring.Error();
                }
                return msg;
            }
            catch (Exception ex)
            {
                string msg = $"诊断 {service} 失败: {ex.Message}";
                AddBubble("system", $"⚠️ {msg}");
                Logger.Error(msg);
                return msg;
            }
        }

        private RingControl GetRing(string service)
        {
            switch (service)
            {
                case "asr": return _ringAsr;
                case "hermes": return _ringHermes;
                case "tts": return _ringTTS;
                default: return null;
            }
        }

        private async Task CheckAllServicesAsync()
        {
            Logger.Info("检查所有服务...");
            foreach (var svc in new[] { "asr", "hermes", "tts" })
            {
                try
                {
                    var result = await _api.DiagCheckAsync(svc);
                    var ring = GetRing(svc);
                    if (ring != null)
                    {
                        if (result.Alive) { ring.SetColor(RingControl.RingColor.Green); ring.Idle(); }
                        else ring.Error();
                    }
                }
                catch { GetRing(svc)?.Error(); }
            }
            Logger.Info("服务检查完成");
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            _recorder?.Stop();
            _recorder?.Dispose();
            Logger.Info("窗体关闭");
            base.OnFormClosing(e);
        }
    }
}
