"""媒体探测与解码，全部基于 PyAV，不需要 ffmpeg 可执行文件。"""

import os
import re
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

import av
import numpy as np

VIDEO_EXTS = {
    ".mp4", ".mkv", ".mov", ".avi", ".wmv", ".flv", ".webm", ".m4v", ".mpg",
    ".mpeg", ".ts", ".mts", ".m2ts", ".3gp", ".rmvb", ".rm", ".ogv", ".vob",
}

# 能直接读出纯文本的字幕编码；pgssub/dvdsub/xsub 是图形字幕，需要 OCR，不处理
TEXT_SUB_CODECS = {
    "srt", "subrip", "ass", "ssa", "mov_text", "webvtt", "text", "subviewer",
    "subviewer1", "sami", "realtext", "microdvd", "jacosub", "eia_608",
}

LANG_NAMES = {
    "zh": "中文", "chi": "中文", "zho": "中文", "cmn": "中文(普通话)",
    "yue": "中文(粤语)", "en": "英语", "eng": "英语", "ja": "日语", "jpn": "日语",
    "ko": "韩语", "kor": "韩语", "fr": "法语", "fre": "法语", "fra": "法语",
    "de": "德语", "ger": "德语", "deu": "德语", "es": "西班牙语", "spa": "西班牙语",
    "ru": "俄语", "rus": "俄语", "pt": "葡萄牙语", "por": "葡萄牙语",
    "it": "意大利语", "ita": "意大利语", "ar": "阿拉伯语", "ara": "阿拉伯语",
    "th": "泰语", "tha": "泰语", "vi": "越南语", "vie": "越南语",
}

SAMPLE_RATE = 16000

CJK = re.compile(r"[㐀-鿿぀-ヿ가-힯]")


def lang_name(code: str | None) -> str:
    if not code:
        return "未标注"
    return LANG_NAMES.get(code.lower(), code)


@dataclass
class Track:
    rel_index: int                # 第几条字幕轨（0 起）
    codec: str
    language: str | None = None
    title: str | None = None

    @property
    def exportable(self) -> bool:
        return self.codec in TEXT_SUB_CODECS

    def __str__(self) -> str:
        bits = [f"#{self.rel_index}", lang_name(self.language)]
        if self.title:
            bits.append(self.title)
        bits.append(self.codec)
        if not self.exportable:
            bits.append("图形字幕，不支持")
        return " · ".join(bits)


@dataclass
class Media:
    path: Path
    duration: float = 0.0
    audio_index: int | None = None   # 第几条音频轨
    tracks: list[Track] = field(default_factory=list)


@dataclass
class Cue:
    start: float
    end: float
    text: str


_TS = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{1,2})[.,](\d{1,3})")


def parse_timestamp(text: str) -> float | None:
    """解析 SRT/VTT 时间戳为秒，如 00:01:02.500 / 01:02,500。"""
    m = _TS.search(text)
    if not m:
        return None
    h, mnt, sec, ms = m.groups()
    return int(h or 0) * 3600 + int(mnt) * 60 + int(sec) + int(ms.ljust(3, "0")) / 1000


def probe(path: str | Path) -> Media:
    """读取时长、音频流、字幕流信息。"""
    p = Path(path)
    with av.open(str(p), metadata_errors="ignore") as c:
        info = Media(path=p)
        if c.duration:
            info.duration = c.duration / av.time_base
        sub_rel = 0
        for s in c.streams:
            if s.type == "audio" and info.audio_index is None:
                info.audio_index = s.index
            elif s.type == "subtitle":
                info.tracks.append(
                    Track(
                        rel_index=sub_rel,
                        codec=(s.codec_context.name or "").lower(),
                        language=s.metadata.get("language"),
                        title=s.metadata.get("title"),
                    )
                )
                sub_rel += 1
    return info


def _text_of(packet) -> str:
    """从字幕包里取出纯文本。

    裸文本包（srt/webvtt/mov_text 在 PyAV 里）直接就是 UTF-8；
    ASS 包可能带样式前缀，去掉 {\\...} 控制段和 \\N 换行。
    """
    try:
        raw = bytes(packet)
    except Exception:
        return ""
    text = raw.decode("utf-8", "replace")
    if "\\" in text or "{" in text:
        text = text.replace("\\N", "\n").replace("\\n", "\n")
        text = re.sub(r"\{[^}]*\}", "", text)
    return text.strip()


def extract_subtitles(path: str | Path, track: Track) -> list[Cue]:
    """导出指定字幕轨为 Cue 列表（时间单位：秒）。"""
    if not track.exportable:
        raise RuntimeError(
            f"第 {track.rel_index} 条字幕是 {track.codec} 图形字幕，需要 OCR，暂不支持"
        )
    cues: list[Cue] = []
    with av.open(str(path), metadata_errors="ignore") as c:
        streams = [s for s in c.streams if s.type == "subtitle"]
        if track.rel_index >= len(streams):
            raise RuntimeError(f"找不到第 {track.rel_index} 条字幕轨")
        stream = streams[track.rel_index]
        tb = float(stream.time_base) if stream.time_base else 0.001
        for pkt in c.demux(stream):
            if not pkt.size:
                continue
            text = _text_of(pkt)
            if not text:
                continue
            start = (pkt.pts or 0) * tb
            dur = (pkt.duration or 0) * tb
            end = start + dur if dur > 0 else start + 2.0
            cues.append(Cue(start, end, text))
    return cues


def _resample(c, stream) -> np.ndarray:
    resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    chunks: list[np.ndarray] = []
    for frame in c.decode(stream):
        for out in resampler.resample(frame):
            chunks.append(out.to_ndarray())
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    pcm = np.concatenate(chunks, axis=1).reshape(-1)
    return pcm.astype(np.float32) / 32768.0


def decode_audio(path: str | Path, start: float | None = None,
                 end: float | None = None) -> np.ndarray:
    """解码为 16kHz 单声道 float32（-1..1），可直接喂给 faster-whisper。

    start/end 为秒；解码后按采样率裁剪，精度足够并且省去 seek 的兼容性问题。
    """
    with av.open(str(path), metadata_errors="ignore") as c:
        audio = [s for s in c.streams if s.type == "audio"]
        if not audio:
            raise RuntimeError("文件没有音频轨，无法语音识别")
        samples = _resample(c, audio[0])
    if start or end:
        lo = int((start or 0) * SAMPLE_RATE)
        hi = int(end * SAMPLE_RATE) if end else len(samples)
        samples = samples[max(0, lo):max(0, hi)]
    return samples
