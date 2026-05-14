using System;
using System.Drawing;
using System.Windows.Forms;
using System.Threading.Tasks;
using System.Collections.Generic;

namespace VoiceClient
{
    partial class MainForm
    {
        private const string BACKEND_URL = "http://192.168.1.21:12054";

        // 控件
        private RingControl _ringMic;
        private RingControl _ringAsr;
        private RingControl _ringHermes;
        private RingControl _ringTTS;

        private Button _btnRecord;
        private Button _btnMode;    // 模式切换
        private RichTextBox _txtChat;
        private TextBox _txtInput;
        private Button _btnSend;
        private Button _btnClear;
        private Button _btnDebug;   // 调试按钮
        private Label _lblStatus;
        private Label _lblHint;
        private Label _lblTimer;    // 录音计时
        private CheckBox _chkVoiceFilter;

        private ApiClient _api;
        private AudioRecorder _recorder;
        private bool _isHoldMode = true; // true=按住说话 false=持续监听
        private bool _isRecording = false;
        private bool _isProcessing = false;
        private List<ChatMessage> _messages = new List<ChatMessage>();
    }

    public class ChatMessage
    {
        public string Role { get; set; } // "user" or "assistant"
        public string Content { get; set; }
        public DateTime Time { get; set; }
    }
}
