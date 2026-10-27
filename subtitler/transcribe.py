"""Whisper 语音识别（faster-whisper，GPU 优先）。"""

import threading
from pathlib import Path
from typing import Callable

from .media import Cue, decode_audio
from .runtime import prepare

MODELS = ("tiny", "base", "small", "medium", "large-v3", "large-v3-turbo")
DEFAULT_MODEL = "small"

SAMPLE_RATE = 16000
# 每段长度。太短会切断句子，太长则提示词又会在段内被稀释。
# Whisper 的窗口是 30 秒，取 60 秒让每段还能递进一次上下文。
CHUNK_SECONDS = 60
# 块间重叠秒数，防止句子被切分点截断
BLOCK_OVERLAP = 3

_cache: dict[str, object] = {}
_lock = threading.Lock()


def _device() -> tuple[str, str]:
    prepare()
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


def load_model(name: str):
    """加载并缓存模型（首次会自动下载到项目 runtime/models）。"""
    prepare()
    with _lock:
        if name not in _cache:
            from faster_whisper import WhisperModel

            device, compute = _device()
            _cache[name] = WhisperModel(name, device=device, compute_type=compute)
        return _cache[name]


def transcribe(
    path: str | Path,
    model: str = DEFAULT_MODEL,
    language: str | None = None,
    start: float | None = None,
    end: float | None = None,
    prompt: str | None = None,
    on_segment: Callable[[Cue], None] | None = None,
    on_progress: Callable[[float, float], None] | None = None,
    cancel: threading.Event | None = None,
) -> tuple[list[Cue], str]:
    """转写媒体文件，返回 (字幕条目, 检测到的语言)。

    prompt 是给模型的上下文提示，能改善专有名词、术语的识别准确率。

    分块处理：Whisper 的 initial_prompt 只对开头有效，后续窗口会被前一段的
    转录结果挤出去（condition_on_previous_text）。把音频切成段、每段重新注入
    提示词，才能让提示贯穿全程。
    """
    audio = decode_audio(path, start=start)
    offset = start or 0.0
    if end:
        audio = audio[: int((end - offset) * 16000)]

    total = len(audio) / SAMPLE_RATE
    model_obj = load_model(model)

    cues: list[Cue] = []
    detected = ""
    chunk_samples = int(CHUNK_SECONDS * SAMPLE_RATE)
    # 块之间留一小段重叠，避免句子的开头或结尾正好落在切分点上被截断。
    # 重叠部分产生的结果会和上一块的尾部重复，用起点阈值丢掉。
    overlap_samples = int(BLOCK_OVERLAP * SAMPLE_RATE)
    step = max(1, chunk_samples - overlap_samples)

    for base in range(0, max(len(audio), 1), step):
        if cancel is not None and cancel.is_set():
            raise InterruptedError("已取消")
        piece = audio[base:base + chunk_samples]
        if len(piece) < SAMPLE_RATE * 0.3:  # 太短的尾块跳过
            continue
        chunk_offset = offset + base / SAMPLE_RATE
        # seg.start 是块内相对时间。上一块已经覆盖到本块开头 overlap 秒处，
        # 所以只接受这里之后的条目，避免重叠区重复。
        accept_from = 0.0 if base == 0 else overlap_samples / SAMPLE_RATE

        segments, info = model_obj.transcribe(
            piece,
            language=language or None,
            vad_filter=True,
            beam_size=5,
            initial_prompt=prompt or None,
            # 关掉跨窗口的上下文传递，否则提示词会逐段被稀释
            condition_on_previous_text=False,
        )
        if not detected:
            detected = getattr(info, "language", "") or ""

        for seg in segments:
            if cancel is not None and cancel.is_set():
                raise InterruptedError("已取消")
            if seg.start < accept_from:
                continue
            text = (seg.text or "").strip()
            if not text:
                continue
            cue = Cue(seg.start + chunk_offset, seg.end + chunk_offset, text)
            cues.append(cue)
            if on_segment:
                on_segment(cue)
            if on_progress and total:
                on_progress(min(chunk_offset + seg.end - offset, total), total)

    if on_progress and total:
        on_progress(total, total)
    return cues, detected
