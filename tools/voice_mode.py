"""语音模式 -- CLI 的按键说话音频录制和播放。

提供通过 sounddevice 进行音频捕获、通过标准库 wave 进行 WAV 编码、
通过 tools.transcription_tools 进行语音转文字调度，
以及通过 sounddevice 或系统音频播放器进行 TTS 播放。

依赖（可选）：
    pip install sounddevice numpy
    或: pip install hermes-agent[voice]
"""

import logging
import os
import platform
import re
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 延迟音频导入 -- 从不在模块级别导入，以避免在无头环境
# （SSH、Docker、WSL、无 PortAudio）中崩溃。
# ---------------------------------------------------------------------------

def _import_audio():
    """延迟导入 sounddevice 和 numpy。返回 (sd, np)。

    如果库不可用（例如无头服务器缺少 PortAudio），
    则抛出 ImportError 或 OSError。
    """
    import sounddevice as sd
    import numpy as np
    return sd, np


def _audio_available() -> bool:
    """如果音频库可以导入则返回 True。"""
    try:
        _import_audio()
        return True
    except (ImportError, OSError):
        return False


from hermes_constants import is_termux as _is_termux_environment


def _voice_capture_install_hint() -> str:
    if _is_termux_environment():
        return "pkg install python-numpy portaudio && python -m pip install sounddevice"
    return "pip install sounddevice numpy"


def _termux_microphone_command() -> Optional[str]:
    if not _is_termux_environment():
        return None
    return shutil.which("termux-microphone-record")



def _termux_api_app_installed() -> bool:
    if not _is_termux_environment():
        return False
    try:
        result = subprocess.run(
            ["pm", "list", "packages", "com.termux.api"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return "package:com.termux.api" in (result.stdout or "")
    except Exception:
        return False


def _termux_voice_capture_available() -> bool:
    return _termux_microphone_command() is not None and _termux_api_app_installed()


def detect_audio_environment() -> dict:
    """检测当前环境是否支持音频 I/O。

    返回包含 'available'（布尔值）、'warnings'（导致语音模式不可用的
    硬性失败原因列表）和 'notices'（不阻止语音模式的信息性消息列表）的字典。
    """
    warnings = []   # 硬性失败：这些会阻止语音模式
    notices = []     # 信息性：记录日志但不阻止
    termux_mic_cmd = _termux_microphone_command()
    termux_app_installed = _termux_api_app_installed()
    termux_capture = bool(termux_mic_cmd and termux_app_installed)

    # SSH 检测
    if any(os.environ.get(v) for v in ('SSH_CLIENT', 'SSH_TTY', 'SSH_CONNECTION')):
        warnings.append("Running over SSH -- no audio devices available")

    # Docker/Podman 容器检测
    from hermes_constants import is_container
    if is_container():
        warnings.append("Running inside Docker container -- no audio devices")

    # WSL 检测 — PulseAudio 桥接使音频在 WSL 中可用。
    # 仅在未配置 PULSE_SERVER 时阻止。
    try:
        with open('/proc/version', 'r') as f:
            if 'microsoft' in f.read().lower():
                if os.environ.get('PULSE_SERVER'):
                    notices.append("Running in WSL with PulseAudio bridge")
                else:
                    warnings.append(
                        "Running in WSL -- audio requires PulseAudio bridge.\n"
                        "  1. Set PULSE_SERVER=unix:/mnt/wslg/PulseServer\n"
                        "  2. Create ~/.asoundrc pointing ALSA at PulseAudio\n"
                        "  3. Verify with: arecord -d 3 /tmp/test.wav && aplay /tmp/test.wav"
                    )
    except (FileNotFoundError, PermissionError, OSError):
        pass

    # 检查音频库
    try:
        sd, _ = _import_audio()
        try:
            devices = sd.query_devices()
            if not devices:
                if termux_capture:
                    notices.append("No PortAudio devices detected, but Termux:API microphone capture is available")
                else:
                    warnings.append("No audio input/output devices detected")
        except Exception:
            # In WSL with PulseAudio, device queries can fail even though
            # recording/playback works fine. Don't block if PULSE_SERVER is set.
            if os.environ.get('PULSE_SERVER'):
                notices.append("Audio device query failed but PULSE_SERVER is set -- continuing")
            elif termux_capture:
                notices.append("PortAudio device query failed, but Termux:API microphone capture is available")
            else:
                warnings.append("Audio subsystem error (PortAudio cannot query devices)")
    except ImportError:
        if termux_capture:
            notices.append("Termux:API microphone recording available (sounddevice not required)")
        elif termux_mic_cmd and not termux_app_installed:
            warnings.append(
                "Termux:API Android app is not installed. Install/update the Termux:API app to use termux-microphone-record."
            )
        else:
            warnings.append(f"Audio libraries not installed ({_voice_capture_install_hint()})")
    except OSError:
        if termux_capture:
            notices.append("Termux:API microphone recording available (PortAudio not required)")
        elif termux_mic_cmd and not termux_app_installed:
            warnings.append(
                "Termux:API Android app is not installed. Install/update the Termux:API app to use termux-microphone-record."
            )
        elif _is_termux_environment():
            warnings.append(
                "PortAudio system library not found -- install it first:\n"
                "  Termux: pkg install portaudio\n"
                "Then retry /voice on."
            )
        else:
            warnings.append(
                "PortAudio system library not found -- install it first:\n"
                "  Linux:  sudo apt-get install libportaudio2\n"
                "  macOS:  brew install portaudio\n"
                "Then retry /voice on."
            )

    return {
        "available": not warnings,
        "warnings": warnings,
        "notices": notices,
    }

# ---------------------------------------------------------------------------
# 录音参数
# ---------------------------------------------------------------------------
SAMPLE_RATE = 16000  # Whisper 原生采样率
CHANNELS = 1  # 单声道
DTYPE = "int16"  # 16 位 PCM
SAMPLE_WIDTH = 2  # 每个采样的字节数（int16）

# 静音检测默认值
SILENCE_RMS_THRESHOLD = 200  # RMS 低于此值视为静音（int16 范围 0-32767）
SILENCE_DURATION_SECONDS = 3.0  # 连续静音多少秒后自动停止

# 语音录音的临时目录
_TEMP_DIR = os.path.join(tempfile.gettempdir(), "hermes_voice")


# ============================================================================
# 音频提示音（蜂鸣声）
# ============================================================================
def play_beep(frequency: int = 880, duration: float = 0.12, count: int = 1) -> None:
    """使用 numpy + sounddevice 播放短促的蜂鸣声。

    参数:
        frequency: 音调频率（Hz），默认 880 = A5。
        duration: 每次蜂鸣的持续时间（秒）。
        count: 播放蜂鸣的次数（中间有短暂间隔）。
    """
    try:
        sd, np = _import_audio()
    except (ImportError, OSError):
        return
    try:
        gap = 0.06  # seconds between beeps
        samples_per_beep = int(SAMPLE_RATE * duration)
        samples_per_gap = int(SAMPLE_RATE * gap)

        parts = []
        for i in range(count):
            t = np.linspace(0, duration, samples_per_beep, endpoint=False)
            # 应用淡入/淡出以避免点击伪影
            tone = np.sin(2 * np.pi * frequency * t)
            fade_len = min(int(SAMPLE_RATE * 0.01), samples_per_beep // 4)
            tone[:fade_len] *= np.linspace(0, 1, fade_len)
            tone[-fade_len:] *= np.linspace(1, 0, fade_len)
            parts.append((tone * 0.3 * 32767).astype(np.int16))
            if i < count - 1:
                parts.append(np.zeros(samples_per_gap, dtype=np.int16))

        audio = np.concatenate(parts)
        sd.play(audio, samplerate=SAMPLE_RATE)
        # sd.wait() 调用 Event.wait() 而无超时 — 如果音频设备
        # 停滞会永远挂起。使用 2 秒上限轮询并强制停止。
        deadline = time.monotonic() + 2.0
        while sd.get_stream() and sd.get_stream().active and time.monotonic() < deadline:
            time.sleep(0.01)
        sd.stop()
    except Exception as e:
        logger.debug("Beep playback failed: %s", e)


# ============================================================================
# Termux 音频录制器
# ============================================================================
class TermuxAudioRecorder:
    """使用 Termux:API 麦克风捕获命令的录制器后端。"""

    supports_silence_autostop = False

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._recording = False
        self._start_time = 0.0
        self._recording_path: Optional[str] = None
        self._current_rms = 0

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def elapsed_seconds(self) -> float:
        if not self._recording:
            return 0.0
        return time.monotonic() - self._start_time

    @property
    def current_rms(self) -> int:
        return self._current_rms

    def start(self, on_silence_stop=None) -> None:
        del on_silence_stop  # Termux:API 不支持实时静音回调。
        mic_cmd = _termux_microphone_command()
        if not mic_cmd:
            raise RuntimeError(
                "Termux voice capture requires the termux-api package and app.\n"
                "Install with: pkg install termux-api\n"
                "Then install/update the Termux:API Android app."
            )
        if not _termux_api_app_installed():
            raise RuntimeError(
                "Termux voice capture requires the Termux:API Android app.\n"
                "Install/update the Termux:API app, then retry /voice on."
            )

        with self._lock:
            if self._recording:
                return
            os.makedirs(_TEMP_DIR, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            self._recording_path = os.path.join(_TEMP_DIR, f"recording_{timestamp}.aac")

        command = [
            mic_cmd,
            "-f", self._recording_path,
            "-l", "0",
            "-e", "aac",
            "-r", str(SAMPLE_RATE),
            "-c", str(CHANNELS),
        ]
        try:
            subprocess.run(command, capture_output=True, text=True, timeout=15, check=True)
        except subprocess.CalledProcessError as e:
            details = (e.stderr or e.stdout or str(e)).strip()
            raise RuntimeError(f"Termux microphone start failed: {details}") from e
        except Exception as e:
            raise RuntimeError(f"Termux microphone start failed: {e}") from e

        with self._lock:
            self._start_time = time.monotonic()
            self._recording = True
            self._current_rms = 0
        logger.info("Termux voice recording started")

    def _stop_termux_recording(self) -> None:
        mic_cmd = _termux_microphone_command()
        if not mic_cmd:
            return
        subprocess.run([mic_cmd, "-q"], capture_output=True, text=True, timeout=15, check=False)

    def stop(self) -> Optional[str]:
        with self._lock:
            if not self._recording:
                return None
            self._recording = False
            path = self._recording_path
            self._recording_path = None
            started_at = self._start_time
            self._current_rms = 0

        self._stop_termux_recording()
        if not path or not os.path.isfile(path):
            return None
        if time.monotonic() - started_at < 0.3:
            try:
                os.unlink(path)
            except OSError:
                pass
            return None
        if os.path.getsize(path) <= 0:
            try:
                os.unlink(path)
            except OSError:
                pass
            return None
        logger.info("Termux voice recording stopped: %s", path)
        return path

    def cancel(self) -> None:
        with self._lock:
            path = self._recording_path
            self._recording = False
            self._recording_path = None
            self._current_rms = 0
        try:
            self._stop_termux_recording()
        except Exception:
            pass
        if path and os.path.isfile(path):
            try:
                os.unlink(path)
            except OSError:
                pass
        logger.info("Termux voice recording cancelled")

    def shutdown(self) -> None:
        self.cancel()


# ============================================================================
# 音频录制器
# ============================================================================
class AudioRecorder:
    """使用 sounddevice.InputStream 的线程安全音频录制器。

    使用方法::

        recorder = AudioRecorder()
        recorder.start(on_silence_stop=my_callback)
        # ... 用户说话 ...
        wav_path = recorder.stop()   # 返回 WAV 文件路径
        # 或
        recorder.cancel()            # 丢弃而不保存

    如果提供了 ``on_silence_stop``，当用户静音 ``silence_duration`` 秒后
    录制会自动停止并调用回调。
    """

    supports_silence_autostop = True

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stream: Any = None
        self._frames: List[Any] = []
        self._recording = False
        self._start_time: float = 0.0
        # 静音检测状态
        self._has_spoken = False
        self._speech_start: float = 0.0  # 说话尝试开始的时间
        self._dip_start: float = 0.0  # 当前低于阈值下降开始的时间
        self._min_speech_duration: float = 0.3  # 确认说话所需的秒数
        self._max_dip_tolerance: float = 0.3  # 重置说话前最大允许的下降持续时间
        self._silence_start: float = 0.0
        self._resume_start: float = 0.0  # 跟踪静音开始后持续说话的情况
        self._resume_dip_start: float = 0.0  # 恢复检测的下降容忍度跟踪器
        self._on_silence_stop = None
        self._silence_threshold: int = SILENCE_RMS_THRESHOLD
        self._silence_duration: float = SILENCE_DURATION_SECONDS
        self._max_wait: float = 15.0  # 等待说话的最大秒数，超时后自动停止
        # 录制期间观察到的峰值 RMS（用于 stop() 中的语音存在检查）
        self._peak_rms: int = 0
        # 实时音频电平（由 UI 读取以提供视觉反馈）
        self._current_rms: int = 0

    # -- 公共属性 ---------------------------------------------------

    @property
    def elapsed_seconds(self) -> float:
        if not self._recording:
            return 0.0
        return time.monotonic() - self._start_time

    @property
    def current_rms(self) -> int:
        """当前音频输入 RMS 电平（0-32767）。每个音频数据块更新一次。"""
        return self._current_rms

    @property
    def is_recording(self) -> bool:
        """音频录制是否正在进行中。"""
        return self._recording

    # -- 公共方法 ------------------------------------------------------

    def _ensure_stream(self) -> None:
        """创建一次音频 InputStream 并保持活跃。

        流在录制器的整个生命周期内保持打开。在录制之间，回调会简单地
        丢弃音频数据块（``_recording`` 为 ``False``）。这避免了在 macOS 上
        关闭和重新打开 ``InputStream`` 时 CoreAudio 无限期挂起的 bug。
        """
        if self._stream is not None:
            return  # already alive

        sd, np = _import_audio()

        def _callback(indata, frames, time_info, status):  # noqa: ARG001
            if status:
                logger.debug("sounddevice status: %s", status)
            # 不录制时流处于空闲状态 — 丢弃音频。
            if not self._recording:
                return
            self._frames.append(indata.copy())

            # 计算 RMS 用于电平显示和静音检测
            rms = int(np.sqrt(np.mean(indata.astype(np.float64) ** 2)))
            self._current_rms = rms
            if rms > self._peak_rms:
                self._peak_rms = rms

            # 静音检测
            if self._on_silence_stop is not None:
                now = time.monotonic()
                elapsed = now - self._start_time

                if rms > self._silence_threshold:
                    # 音频高于阈值 -- 这是说话（或噪音）。
                    self._dip_start = 0.0  # 重置下降跟踪器
                    if self._speech_start == 0.0:
                        self._speech_start = now
                    elif not self._has_spoken and now - self._speech_start >= self._min_speech_duration:
                        self._has_spoken = True
                        logger.debug("Speech confirmed (%.2fs above threshold)",
                                     now - self._speech_start)
                    # 语音确认后，只有在说话持续（超过阈值 >0.3 秒）时
                    # 才重置静音计时器。来自环境噪音的短暂尖峰不应重置计时器。
                    if not self._has_spoken:
                        self._silence_start = 0.0
                    else:
                        # 使用带下降容忍的恢复跟踪。
                        # 低于阈值的短暂下降在说话中是正常的，
                        # 所以我们镜像初始语音检测模式：
                        # 开始跟踪、容忍短暂下降、0.3 秒后确认。
                        self._resume_dip_start = 0.0  # 高于阈值 — 无下降
                        if self._resume_start == 0.0:
                            self._resume_start = now
                        elif now - self._resume_start >= self._min_speech_duration:
                            self._silence_start = 0.0
                            self._resume_start = 0.0
                elif self._has_spoken:
                    # 语音确认后低于阈值。
                    # 在重置恢复跟踪器之前使用下降容忍 —
                    # 自然语音中低于阈值的短暂下降是正常的。
                    if self._resume_start > 0:
                        if self._resume_dip_start == 0.0:
                            self._resume_dip_start = now
                        elif now - self._resume_dip_start >= self._max_dip_tolerance:
                            # 持续下降 — 用户确实停止说话了
                            self._resume_start = 0.0
                            self._resume_dip_start = 0.0
                elif self._speech_start > 0:
                    # 我们正在说话尝试中但 RMS 下降了。
                    # 容忍短暂下降（音节间的微停顿）。
                    if self._dip_start == 0.0:
                        self._dip_start = now
                    elif now - self._dip_start >= self._max_dip_tolerance:
                        # 下降持续太长 -- 真正的静音，重置
                        logger.debug("Speech attempt reset (dip lasted %.2fs)",
                                     now - self._dip_start)
                        self._speech_start = 0.0
                        self._dip_start = 0.0

                # 触发静音回调的条件：
                # 1. 用户说话后静音了 silence_duration 秒，或
                # 2. 在 max_wait 秒内完全没有检测到说话
                should_fire = False
                if self._has_spoken and rms <= self._silence_threshold:
                    # 用户之前在说话，现在静默了
                    if self._silence_start == 0.0:
                        self._silence_start = now
                    elif now - self._silence_start >= self._silence_duration:
                        logger.info("Silence detected (%.1fs), auto-stopping",
                                    self._silence_duration)
                        should_fire = True
                elif not self._has_spoken and elapsed >= self._max_wait:
                    logger.info("No speech within %.0fs, auto-stopping",
                                self._max_wait)
                    should_fire = True

                if should_fire:
                    with self._lock:
                        cb = self._on_silence_stop
                        self._on_silence_stop = None  # fire only once
                    if cb:
                        def _safe_cb():
                            try:
                                cb()
                            except Exception as e:
                                logger.error("静音回调失败: %s", e, exc_info=True)
                        threading.Thread(target=_safe_cb, daemon=True).start()

        # 创建流 — 可能在 CoreAudio 上阻塞（仅首次调用）。
        stream = None
        try:
            stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                callback=_callback,
            )
            stream.start()
        except Exception as e:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            raise RuntimeError(
                f"Failed to open audio input stream: {e}. "
                "Check that a microphone is connected and accessible."
            ) from e
        self._stream = stream

    def start(self, on_silence_stop=None) -> None:
        """从默认输入设备开始捕获音频。

        底层 InputStream 仅创建一次并在多次录制间保持活跃。
        后续调用只重置检测状态并通过 ``_recording`` 切换帧收集。

        参数:
            on_silence_stop: 可选回调，在语音后检测到静音时
                在守护线程中调用。回调不接收任何参数。
                用于自动停止录制并触发转录。

        如果 sounddevice/numpy 未安装或录制正在进行中，
        则抛出 ``RuntimeError``。
        """
        try:
            _import_audio()
        except (ImportError, OSError) as e:
            raise RuntimeError(
                "Voice mode requires sounddevice and numpy.\n"
                "Install with: pip install sounddevice numpy\n"
                "Or: pip install hermes-agent[voice]"
            ) from e

        with self._lock:
            if self._recording:
                return  # already recording

            self._frames = []
            self._start_time = time.monotonic()
            self._has_spoken = False
            self._speech_start = 0.0
            self._dip_start = 0.0
            self._silence_start = 0.0
            self._resume_start = 0.0
            self._resume_dip_start = 0.0
            self._peak_rms = 0
            self._current_rms = 0
            self._on_silence_stop = on_silence_stop

        # 确保持久流处于活跃状态（首次调用后为空操作）。
        self._ensure_stream()

        with self._lock:
            self._recording = True
        logger.info("Voice recording started (rate=%d, channels=%d)", SAMPLE_RATE, CHANNELS)

    def _close_stream_with_timeout(self, timeout: float = 3.0) -> None:
        """带超时关闭音频流以防止 CoreAudio 挂起。"""
        if self._stream is None:
            return

        stream = self._stream
        self._stream = None

        def _do_close():
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

        t = threading.Thread(target=_do_close, daemon=True)
        t.start()
        # 以短间隔轮询以避免 Ctrl+C 被阻塞
        deadline = __import__("time").monotonic() + timeout
        while t.is_alive() and __import__("time").monotonic() < deadline:
            t.join(timeout=0.1)
        if t.is_alive():
            logger.warning("Audio stream close timed out after %.1fs — forcing ahead", timeout)

    def stop(self) -> Optional[str]:
        """停止录制并将捕获的音频写入 WAV 文件。

        底层流保持活跃以便重用 — 只停止帧收集。

        返回:
            WAV 文件的路径，如果没有捕获到音频则返回 ``None``。
        """
        with self._lock:
            if not self._recording:
                return None

            self._recording = False
            self._current_rms = 0
            # Stream stays alive — no close needed.

            if not self._frames:
                return None

            # Concatenate frames and write WAV
            _, np = _import_audio()
            audio_data = np.concatenate(self._frames, axis=0)
            self._frames = []

            elapsed = time.monotonic() - self._start_time
            logger.info("Voice recording stopped (%.1fs, %d samples)", elapsed, len(audio_data))

            # Skip very short recordings (< 0.3s of audio)
            min_samples = int(SAMPLE_RATE * 0.3)
            if len(audio_data) < min_samples:
                logger.debug("Recording too short (%d samples), discarding", len(audio_data))
                return None

            # Skip silent recordings using peak RMS (not overall average, which
            # gets diluted by silence at the end of the recording).
            if self._peak_rms < SILENCE_RMS_THRESHOLD:
                logger.info("Recording too quiet (peak RMS=%d < %d), discarding",
                            self._peak_rms, SILENCE_RMS_THRESHOLD)
                return None

            return self._write_wav(audio_data)

    def cancel(self) -> None:
        """Stop recording and discard all captured audio.

        The underlying stream is kept alive for reuse.
        """
        with self._lock:
            self._recording = False
            self._frames = []
            self._on_silence_stop = None
            self._current_rms = 0
        logger.info("Voice recording cancelled")

    def shutdown(self) -> None:
        """Release the audio stream.  Call when voice mode is disabled."""
        with self._lock:
            self._recording = False
            self._frames = []
            self._on_silence_stop = None
        # Close stream OUTSIDE the lock to avoid deadlock with audio callback
        self._close_stream_with_timeout()
        logger.info("AudioRecorder shut down")

    # -- private helpers -----------------------------------------------------

    @staticmethod
    def _write_wav(audio_data) -> str:
        """Write numpy int16 audio data to a WAV file.

        Returns the file path.
        """
        os.makedirs(_TEMP_DIR, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        wav_path = os.path.join(_TEMP_DIR, f"recording_{timestamp}.wav")

        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(SAMPLE_WIDTH)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio_data.tobytes())

        file_size = os.path.getsize(wav_path)
        logger.info("WAV written: %s (%d bytes)", wav_path, file_size)
        return wav_path


def create_audio_recorder() -> AudioRecorder | TermuxAudioRecorder:
    """Return the best recorder backend for the current environment."""
    if _termux_voice_capture_available():
        return TermuxAudioRecorder()
    return AudioRecorder()


# ============================================================================
# Whisper hallucination filter
# ============================================================================
# Whisper commonly hallucinates these phrases on silent/near-silent audio.
WHISPER_HALLUCINATIONS = {
    "thank you.",
    "thank you",
    "thanks for watching.",
    "thanks for watching",
    "subscribe to my channel.",
    "subscribe to my channel",
    "like and subscribe.",
    "like and subscribe",
    "please subscribe.",
    "please subscribe",
    "thank you for watching.",
    "thank you for watching",
    "bye.",
    "bye",
    "you",
    "the end.",
    "the end",
    # Non-English hallucinations (common on silence)
    "продолжение следует",
    "продолжение следует...",
    "sous-titres",
    "sous-titres réalisés par la communauté d'amara.org",
    "sottotitoli creati dalla comunità amara.org",
    "untertitel von stephanie geiges",
    "amara.org",
    "www.mooji.org",
    "ご視聴ありがとうございました",
}

# Regex patterns for repetitive hallucinations (e.g. "Thank you. Thank you. Thank you.")
_HALLUCINATION_REPEAT_RE = re.compile(
    r'^(?:thank you|thanks|bye|you|ok|okay|the end|\.|\s|,|!)+$',
    flags=re.IGNORECASE,
)


def is_whisper_hallucination(transcript: str) -> bool:
    """Check if a transcript is a known Whisper hallucination on silence."""
    cleaned = transcript.strip().lower()
    if not cleaned:
        return True
    # Exact match against known phrases
    if cleaned.rstrip('.!') in WHISPER_HALLUCINATIONS or cleaned in WHISPER_HALLUCINATIONS:
        return True
    # Repetitive patterns (e.g. "Thank you. Thank you. Thank you. you")
    if _HALLUCINATION_REPEAT_RE.match(cleaned):
        return True
    return False


# ============================================================================
# STT dispatch
# ============================================================================
def transcribe_recording(wav_path: str, model: Optional[str] = None) -> Dict[str, Any]:
    """Transcribe a WAV recording using the existing Whisper pipeline.

    Delegates to ``tools.transcription_tools.transcribe_audio()``.
    Filters out known Whisper hallucinations on silent audio.

    Args:
        wav_path: Path to the WAV file.
        model: Whisper model name (default: from config or ``whisper-1``).

    Returns:
        Dict with ``success``, ``transcript``, and optionally ``error``.
    """
    from tools.transcription_tools import transcribe_audio

    result = transcribe_audio(wav_path, model=model)

    # Filter out Whisper hallucinations (common on silent/near-silent audio)
    if result.get("success") and is_whisper_hallucination(result.get("transcript", "")):
        logger.info("Filtered Whisper hallucination: %r", result["transcript"])
        return {"success": True, "transcript": "", "filtered": True}

    return result


# ============================================================================
# Audio playback (interruptable)
# ============================================================================

# Global reference to the active playback process so it can be interrupted.
_active_playback: Optional[subprocess.Popen] = None
_playback_lock = threading.Lock()


def stop_playback() -> None:
    """Interrupt the currently playing audio (if any)."""
    global _active_playback
    with _playback_lock:
        proc = _active_playback
        _active_playback = None
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            logger.info("Audio playback interrupted")
        except Exception:
            pass
    # Also stop sounddevice playback if active
    try:
        sd, _ = _import_audio()
        sd.stop()
    except Exception:
        pass


def play_audio_file(file_path: str) -> bool:
    """Play an audio file through the default output device.

    Strategy:
    1. WAV files via ``sounddevice.play()`` when available.
    2. System commands: ``afplay`` (macOS), ``ffplay`` (cross-platform),
       ``aplay`` (Linux ALSA).

    Playback can be interrupted by calling ``stop_playback()``.

    Returns:
        ``True`` if playback succeeded, ``False`` otherwise.
    """
    global _active_playback

    if not os.path.isfile(file_path):
        logger.warning("Audio file not found: %s", file_path)
        return False

    # Try sounddevice for WAV files
    if file_path.endswith(".wav"):
        try:
            sd, np = _import_audio()
            with wave.open(file_path, "rb") as wf:
                frames = wf.readframes(wf.getnframes())
                audio_data = np.frombuffer(frames, dtype=np.int16)
                sample_rate = wf.getframerate()

            sd.play(audio_data, samplerate=sample_rate)
            # sd.wait() calls Event.wait() without timeout — hangs forever if
            # the audio device stalls.  Poll with a ceiling and force-stop.
            duration_secs = len(audio_data) / sample_rate
            deadline = time.monotonic() + duration_secs + 2.0
            while sd.get_stream() and sd.get_stream().active and time.monotonic() < deadline:
                time.sleep(0.01)
            sd.stop()
            return True
        except (ImportError, OSError):
            pass  # audio libs not available, fall through to system players
        except Exception as e:
            logger.debug("sounddevice playback failed: %s", e)

    # Fall back to system audio players (using Popen for interruptability)
    system = platform.system()
    players = []

    if system == "Darwin":
        players.append(["afplay", file_path])
    players.append(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", file_path])
    if system == "Linux":
        players.append(["aplay", "-q", file_path])

    for cmd in players:
        exe = shutil.which(cmd[0])
        if exe:
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                with _playback_lock:
                    _active_playback = proc
                proc.wait(timeout=300)
                with _playback_lock:
                    _active_playback = None
                return True
            except subprocess.TimeoutExpired:
                logger.warning("System player %s timed out, killing process", cmd[0])
                proc.kill()
                proc.wait()
                with _playback_lock:
                    _active_playback = None
            except Exception as e:
                logger.debug("System player %s failed: %s", cmd[0], e)
                with _playback_lock:
                    _active_playback = None

    logger.warning("No audio player available for %s", file_path)
    return False


# ============================================================================
# Requirements check
# ============================================================================
def check_voice_requirements() -> Dict[str, Any]:
    """Check if all voice mode requirements are met.

    Returns:
        Dict with ``available``, ``audio_available``, ``stt_available``,
        ``missing_packages``, and ``details``.
    """
    # Determine STT provider availability
    from tools.transcription_tools import _get_provider, _load_stt_config, is_stt_enabled
    stt_config = _load_stt_config()
    stt_enabled = is_stt_enabled(stt_config)
    stt_provider = _get_provider(stt_config)
    stt_available = stt_enabled and stt_provider != "none"

    missing: List[str] = []
    termux_capture = _termux_voice_capture_available()
    has_audio = _audio_available() or termux_capture

    if not has_audio:
        missing.extend(["sounddevice", "numpy"])

    # Environment detection
    env_check = detect_audio_environment()

    available = has_audio and stt_available and env_check["available"]
    details_parts = []

    if termux_capture:
        details_parts.append("Audio capture: OK (Termux:API microphone)")
    elif has_audio:
        details_parts.append("Audio capture: OK")
    else:
        details_parts.append(f"Audio capture: MISSING ({_voice_capture_install_hint()})")

    if not stt_enabled:
        details_parts.append("STT provider: DISABLED in config (stt.enabled: false)")
    elif stt_provider == "local":
        details_parts.append("STT provider: OK (local faster-whisper)")
    elif stt_provider == "groq":
        details_parts.append("STT provider: OK (Groq)")
    elif stt_provider == "openai":
        details_parts.append("STT provider: OK (OpenAI)")
    else:
        details_parts.append(
            "STT provider: MISSING (pip install faster-whisper, "
            "or set GROQ_API_KEY / VOICE_TOOLS_OPENAI_KEY)"
        )

    for warning in env_check["warnings"]:
        details_parts.append(f"Environment: {warning}")
    for notice in env_check.get("notices", []):
        details_parts.append(f"Environment: {notice}")

    return {
        "available": available,
        "audio_available": has_audio,
        "stt_available": stt_available,
        "missing_packages": missing,
        "details": "\n".join(details_parts),
        "environment": env_check,
    }


# ============================================================================
# Temp file cleanup
# ============================================================================
def cleanup_temp_recordings(max_age_seconds: int = 3600) -> int:
    """Remove old temporary voice recording files.

    Args:
        max_age_seconds: Delete files older than this (default: 1 hour).

    Returns:
        Number of files deleted.
    """
    if not os.path.isdir(_TEMP_DIR):
        return 0

    deleted = 0
    now = time.time()

    for entry in os.scandir(_TEMP_DIR):
        if entry.is_file() and entry.name.startswith("recording_") and entry.name.endswith(".wav"):
            try:
                age = now - entry.stat().st_mtime
                if age > max_age_seconds:
                    os.unlink(entry.path)
                    deleted += 1
            except OSError:
                pass

    if deleted:
        logger.debug("Cleaned up %d old voice recordings", deleted)
    return deleted
