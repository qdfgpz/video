"""字幕输出：SRT / VTT / TXT，含中文标点规范化与长句切分。"""

import re
from pathlib import Path

from .media import Cue

CJK = re.compile(r"[㐀-鿿぀-ヿ가-힯]")
_SENT_END = re.compile(r"[。！？!?；;…]|(?<=[.])\s")
_HALF_TO_FULL = str.maketrans(",;:?!", "，；：？！")


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").strip()
    # Whisper 对中文常输出半角标点，统一成全角更符合字幕习惯
    return text.translate(_HALF_TO_FULL) if CJK.search(text) else text


def _fmt_time(seconds: float, sep: str = ",") -> str:
    ms = max(0, round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _wrap(text: str, limit: int) -> str:
    """把超长文本折行；中文按字折，其他按词折。"""
    if limit <= 0 or len(text) <= limit:
        return text
    if CJK.search(text):
        return "\n".join(text[i:i + limit] for i in range(0, len(text), limit))
    lines, cur = [], ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > limit:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def _split_long(cues: list[Cue], max_chars: int, max_seconds: float) -> list[Cue]:
    """按标点切分过长或过久的条目，时长按字数比例分配。"""
    if max_chars <= 0 and max_seconds <= 0:
        return cues
    out: list[Cue] = []
    for cue in cues:
        text = _clean(cue.text)
        duration = max(cue.end - cue.start, 0.001)
        too_long = (max_chars > 0 and len(text) > max_chars) or (
            max_seconds > 0 and duration > max_seconds
        )
        if not too_long:
            out.append(Cue(cue.start, cue.end, _wrap(text, max_chars)))
            continue

        pieces = [p.strip() for p in _SENT_END.split(text) if p and p.strip()]
        if len(pieces) <= 1:
            step = max_chars if max_chars > 0 else max(1, int(len(text) * duration / max_seconds))
            pieces = [text[i:i + step] for i in range(0, len(text), step)]

        chars = sum(len(p) for p in pieces) or 1
        cursor = cue.start
        for piece in pieces:
            stop = min(cue.end, cursor + duration * len(piece) / chars)
            out.append(Cue(cursor, stop, _wrap(piece, max_chars)))
            cursor = stop
    return out


def to_srt(cues: list[Cue]) -> str:
    return "\n\n".join(
        f"{i}\n{_fmt_time(c.start)} --> {_fmt_time(c.end)}\n{c.text}"
        for i, c in enumerate(cues, 1)
    ) + "\n"


def to_vtt(cues: list[Cue]) -> str:
    body = "\n\n".join(
        f"{_fmt_time(c.start, '.')} --> {_fmt_time(c.end, '.')}\n{c.text}" for c in cues
    )
    return f"WEBVTT\n\n{body}\n"


def to_txt(cues: list[Cue]) -> str:
    """纯文字版：每句一行，保留标点，不带时间轴。"""
    lines = [_clean(c.text).replace("\n", " ") for c in cues]
    return "\n".join(line for line in lines if line) + "\n"


_WRITERS = {"srt": to_srt, "vtt": to_vtt, "txt": to_txt}


def write(cues: list[Cue], base: str | Path, formats: tuple[str, ...] = ("txt",),
          max_chars: int = 42, max_seconds: float = 7.0) -> list[Path]:
    """按指定格式写出，返回写出的文件路径。

    srt / vtt 会做长句切分和折行，适合当字幕用；txt 保持原句不切分，
    只做标点规范化，适合阅读或交给其他工具处理。
    """
    base = Path(base)
    base.parent.mkdir(parents=True, exist_ok=True)

    written = []
    for fmt in formats:
        fmt = fmt.lower()
        if fmt not in _WRITERS:
            raise ValueError(f"不支持的输出格式：{fmt}（可选 srt/vtt/txt）")
        if fmt == "txt":
            content = to_txt(cues)
        else:
            content = _WRITERS[fmt](_split_long(cues, max_chars, max_seconds))
        out = base.with_suffix(f".{fmt}")
        out.write_text(content, encoding="utf-8")
        written.append(out)
    return written
