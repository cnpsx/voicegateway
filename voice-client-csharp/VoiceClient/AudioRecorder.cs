using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;

namespace VoiceClient
{
    /// <summary>
    /// 音频录制器 — 用 Windows API (waveIn) 录音，不依赖第三方库
    /// 输出 16kHz 16-bit mono PCM WAV
    /// </summary>
    public class AudioRecorder : IDisposable
    {
        // Windows MM WAVE API
        private const int MM_WOM_OPEN = 0x3BB;
        private const int MM_WOM_CLOSE = 0x3BC;
        private const int MM_WOM_DONE = 0x3BD;
        private const int MM_WIM_OPEN = 0x3BE;
        private const int MM_WIM_CLOSE = 0x3BF;
        private const int MM_WIM_DATA = 0x3C0;
        private const int CALLBACK_FUNCTION = 0x30000;
        private const int WAVE_FORMAT_PCM = 1;

        private IntPtr _hWaveIn = IntPtr.Zero;
        private IntPtr[] _headers;
        private byte[][] _buffers;
        private bool _isRecording;
        private int _sampleRate = 16000;
        private int _bitsPerSample = 16;
        private int _channels = 1;
        private int _bufferMs = 200; // 每200ms一个buffer
        private int _bufferSize;
        private MemoryStream _stream;
        private GCHandle _callbackHandle;
        private WaveInDelegate _callbackDelegate;

        public event Action<byte[], int> DataAvailable; // data, sampleRate
        public event Action RecordingStopped;

        public AudioRecorder(int sampleRate = 16000)
        {
            _sampleRate = sampleRate;
            _bufferSize = _sampleRate * _bitsPerSample / 8 * _channels * _bufferMs / 1000;
            Logger.Info($"AudioRecorder 初始化: {_sampleRate}Hz, {_bitsPerSample}bit, {_channels}ch, buffer={_bufferSize}");
        }

        #region Native waveIn API
        [DllImport("winmm.dll")]
        private static extern int waveInOpen(out IntPtr hWaveIn, int uDeviceID,
            ref WaveFormat lpFormat, WaveInDelegate dwCallback, IntPtr dwInstance, int fdwOpen);

        [DllImport("winmm.dll")]
        private static extern int waveInClose(IntPtr hWaveIn);

        [DllImport("winmm.dll")]
        private static extern int waveInPrepareHeader(IntPtr hWaveIn, ref WaveHeader lpHeader, int uSize);

        [DllImport("winmm.dll")]
        private static extern int waveInUnprepareHeader(IntPtr hWaveIn, ref WaveHeader lpHeader, int uSize);

        [DllImport("winmm.dll")]
        private static extern int waveInAddBuffer(IntPtr hWaveIn, ref WaveHeader lpHeader, int uSize);

        [DllImport("winmm.dll")]
        private static extern int waveInStart(IntPtr hWaveIn);

        [DllImport("winmm.dll")]
        private static extern int waveInStop(IntPtr hWaveIn);

        [DllImport("winmm.dll")]
        private static extern int waveInReset(IntPtr hWaveIn);

        private delegate void WaveInDelegate(IntPtr hWaveIn, int uMsg, IntPtr dwInstance, ref WaveHeader dwParam1);

        [StructLayout(LayoutKind.Sequential)]
        private struct WaveFormat
        {
            public short wFormatTag;
            public short nChannels;
            public int nSamplesPerSec;
            public int nAvgBytesPerSec;
            public short nBlockAlign;
            public short wBitsPerSample;
            public short cbSize;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct WaveHeader
        {
            public IntPtr lpData;
            public int dwBufferLength;
            public int dwBytesRecorded;
            public IntPtr dwUser;
            public int dwFlags;
            public int dwLoops;
            public IntPtr lpNext;
            public IntPtr reserved;
        }
        #endregion

        private void WaveCallback(IntPtr hWaveIn, int uMsg, IntPtr dwInstance, ref WaveHeader dwParam1)
        {
            if (uMsg == MM_WIM_DATA && _isRecording)
            {
                int bytesRecorded = dwParam1.dwBytesRecorded;
                if (bytesRecorded > 0 && _stream != null)
                {
                    byte[] data = new byte[bytesRecorded];
                    Marshal.Copy(dwParam1.lpData, data, 0, bytesRecorded);
                    _stream.Write(data, 0, bytesRecorded);
                    DataAvailable?.Invoke(data, _sampleRate);
                }
                // 重新加入 buffer
                waveInAddBuffer(_hWaveIn, ref dwParam1, Marshal.SizeOf(typeof(WaveHeader)));
            }
            else if (uMsg == MM_WIM_CLOSE)
            {
                RecordingStopped?.Invoke();
            }
        }

        public void Start()
        {
            if (_isRecording) return;
            Logger.Info("开始录音...");

            _stream = new MemoryStream();
            _bufferSize = _sampleRate * 2 * _bufferMs / 1000; // 16-bit = 2 bytes

            var fmt = new WaveFormat
            {
                wFormatTag = WAVE_FORMAT_PCM,
                nChannels = (short)_channels,
                nSamplesPerSec = _sampleRate,
                wBitsPerSample = (short)_bitsPerSample,
                nBlockAlign = (short)(_channels * _bitsPerSample / 8),
                nAvgBytesPerSec = _sampleRate * _channels * _bitsPerSample / 8,
                cbSize = 0
            };

            _callbackDelegate = WaveCallback;
            _callbackHandle = GCHandle.Alloc(_callbackDelegate);

            int result = waveInOpen(out _hWaveIn, 0, ref fmt, _callbackDelegate, IntPtr.Zero, CALLBACK_FUNCTION);
            if (result != 0)
            {
                Logger.Error($"waveInOpen 失败: {result}");
                throw new Exception($"打开麦克风失败 (error {result})");
            }

            // 准备 4 个 buffer
            int headerSize = Marshal.SizeOf(typeof(WaveHeader));
            _buffers = new byte[4][];
            _headers = new IntPtr[4];
            for (int i = 0; i < 4; i++)
            {
                _buffers[i] = new byte[_bufferSize];
                _headers[i] = Marshal.AllocHGlobal(headerSize);
                var header = new WaveHeader
                {
                    lpData = Marshal.AllocHGlobal(_bufferSize),
                    dwBufferLength = _bufferSize,
                    dwFlags = 0
                };
                Marshal.StructureToPtr(header, _headers[i], false);
                waveInPrepareHeader(_hWaveIn, ref header, headerSize);
                waveInAddBuffer(_hWaveIn, ref header, headerSize);
            }

            waveInStart(_hWaveIn);
            _isRecording = true;
            Logger.Info("录音已开始");
        }

        public byte[] Stop()
        {
            if (!_isRecording) return null;
            Logger.Info("停止录音...");

            _isRecording = false;
            waveInStop(_hWaveIn);
            waveInReset(_hWaveIn);

            // 清理 header
            int headerSize = Marshal.SizeOf(typeof(WaveHeader));
            for (int i = 0; i < 4; i++)
            {
                if (_headers[i] != IntPtr.Zero)
                {
                    var header = (WaveHeader)Marshal.PtrToStructure(_headers[i], typeof(WaveHeader));
                    waveInUnprepareHeader(_hWaveIn, ref header, headerSize);
                    Marshal.FreeHGlobal(header.lpData);
                    Marshal.FreeHGlobal(_headers[i]);
                }
            }

            waveInClose(_hWaveIn);
            _hWaveIn = IntPtr.Zero;

            if (_callbackHandle.IsAllocated)
                _callbackHandle.Free();

            byte[] rawData = _stream?.ToArray() ?? new byte[0];
            _stream?.Dispose();
            _stream = null;

            Logger.Info($"录音结束: {rawData.Length} bytes");

            // 包装成 WAV 格式
            return BuildWav(rawData);
        }

        private byte[] BuildWav(byte[] pcmData)
        {
            if (pcmData.Length == 0) return pcmData;

            int sampleRate = _sampleRate;
            short channels = (short)_channels;
            short bitsPerSample = (short)_bitsPerSample;
            int byteRate = sampleRate * channels * bitsPerSample / 8;
            short blockAlign = (short)(channels * bitsPerSample / 8);
            int dataSize = pcmData.Length;

            using (var ms = new MemoryStream(44 + dataSize))
            {
                var writer = new BinaryWriter(ms);
                writer.Write(new char[] { 'R', 'I', 'F', 'F' });
                writer.Write(36 + dataSize);
                writer.Write(new char[] { 'W', 'A', 'V', 'E' });
                writer.Write(new char[] { 'f', 'm', 't', ' ' });
                writer.Write(16);
                writer.Write((short)1); // PCM
                writer.Write(channels);
                writer.Write(sampleRate);
                writer.Write(byteRate);
                writer.Write(blockAlign);
                writer.Write(bitsPerSample);
                writer.Write(new char[] { 'd', 'a', 't', 'a' });
                writer.Write(dataSize);
                writer.Write(pcmData);

                Logger.Info($"WAV 打包完成: {ms.Length} bytes");
                return ms.ToArray();
            }
        }

        public void Dispose()
        {
            Stop();
        }
    }
}
