"""单文件流水线：有内嵌文本字幕就导出，否则语音识别。"""

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import domains, formats, media
from .runtime import RUNTIME
from .transcribe import DEFAULT_MODEL, transcribe

Event = Callable[[str, dict], None]


@dataclass
class Options:
    mode: str = "auto"              # auto / track / whisper
    track: int | None = None        # 指定字幕轨序号
    prefer_lang: str | None = None  # 自动选轨时偏好语言，如 zh
    model: str = DEFAULT_MODEL
    language: str | None = None     # 语音识别语言，None=自动
    formats: tuple[str, ...] = ("txt",)
    max_chars: int = 42
    max_seconds: float = 7.0
    output_dir: Path | None = None
    overwrite: bool = False
    proxy: str | None = None        # None=按站点自动，""=直连，其他=显式代理
    domains: list[str] = field(default_factory=list)  # 领域预设，如 ["sports"]
    prompt: str | None = None       # 自定义提示词，追加到领域词之后


@dataclass
class Result:
    source: Path
    outputs: list[Path] = field(default_factory=list)
    method: str = ""        # embedded / whisper
    language: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.outputs)


def _choose(info: media.Media, opts: Options) -> media.Track | None:
    usable = [t for t in info.tracks if t.exportable]
    if opts.track is not None:
        match = next((t for t in usable if t.rel_index == opts.track), None)
        if match is None:
            raise RuntimeError(f"找不到第 {opts.track} 条可导出的字幕轨")
        return match
    if opts.prefer_lang:
        want = opts.prefer_lang.lower()
        for t in usable:
            if (t.language or "").lower() == want:
                return t
    return usable[0] if usable else None


def _base(path: Path, opts: Options) -> Path:
    folder = Path(opts.output_dir) if opts.output_dir else path.parent
    return folder / path.stem


def _safe_name(title: str, fallback: str) -> str:
    """把视频标题变成合法的文件名。"""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", title).strip(" .")
    name = re.sub(r"\s+", " ", name)[:120]
    return name or fallback


def _process_url(url: str, opts: Options, emit: Event | None,
                 cancel: threading.Event | None) -> Result:
    """处理一个视频网址。"""
    from . import net

    result = Result(source=Path(url))

    def notify(kind: str, **data) -> None:
        if emit:
            emit(kind, {"source": url, **data})

    try:
        notify("fetching", url=url)
        cookie = None
        if net.is_bilibili(url):
            from .auth import cookie_is_valid, load_cookie

            saved = load_cookie()
            if saved and cookie_is_valid(saved):
                cookie = saved
                notify("auth", message="使用已保存的 B 站登录状态")
            else:
                notify("auth", message="B 站字幕需要登录：运行 subextract --login 扫码授权", missing=True)

        meta = net.info(url, opts.proxy, cookie)
        title = meta.get("title") or "video"
        notify("probed", title=title, duration=meta.get("duration") or 0,
               tracks=list((meta.get("subtitles") or {}).keys()))

        cues = None
        if opts.mode in ("auto", "site"):
            found = net.site_subtitles(url, opts.prefer_lang, opts.proxy, cookie)
            if found:
                cues, language = found
                result.method = "site"
                result.language = language
                notify("site_subtitles", language=language, count=len(cues))

        if cues is None:
            if opts.mode == "site":
                hint = "（B 站需登录：subextract --login）" if net.is_bilibili(url) and not cookie else ""
                raise RuntimeError(f"该视频没有网站自带字幕{hint}")
            notify("downloading")
            audio = net.download_audio(
                url, opts.proxy,
                progress=lambda d, t: notify("download", done=d, total=t),
                cancel=cancel, cookie=cookie,
            )
            notify("transcribing", model=opts.model)
            cues, language = transcribe(
                audio, model=opts.model, language=opts.language,
                prompt=domains.build(opts.domains, opts.prompt),
                on_segment=lambda c: notify("segment", cue=c),
                on_progress=lambda d, t: notify("progress", done=d, total=t),
                cancel=cancel,
            )
            result.method = "whisper"
            result.language = language

        if not cues:
            raise RuntimeError("没有得到任何字幕内容")

        folder = Path(opts.output_dir) if opts.output_dir else Path(RUNTIME) / "output"
        base = folder / _safe_name(title, "video")
        existing = [base.with_suffix(f".{f}") for f in opts.formats]
        if not opts.overwrite and any(p.exists() for p in existing):
            names = "、".join(p.name for p in existing if p.exists())
            raise RuntimeError(f"输出文件已存在：{names}（加 --overwrite 覆盖）")

        result.outputs = formats.write(cues, base, opts.formats, opts.max_chars, opts.max_seconds)
        notify("done", method=result.method, language=result.language,
               outputs=[str(p) for p in result.outputs])
        return result

    except InterruptedError:
        result.error = "已取消"
        notify("cancelled")
    except Exception as exc:
        result.error = str(exc)
        notify("error", message=str(exc))
    return result


def process(path: str | Path, opts: Options, emit: Event | None = None,
            cancel: threading.Event | None = None) -> Result:
    """处理一个本地文件，或一个视频网址。"""
    from . import net

    if isinstance(path, str) and net.is_url(path):
        return _process_url(path.strip(), opts, emit, cancel)

    src = Path(path)
    result = Result(source=src)

    def notify(kind: str, **data) -> None:
        if emit:
            emit(kind, {"source": str(src), **data})

    try:
        notify("start")
        info = media.probe(src)
        notify("probed", duration=info.duration, tracks=info.tracks)
        base = _base(src, opts)

        track = _choose(info, opts) if opts.mode in ("auto", "track") else None
        if opts.mode == "track" and track is None:
            raise RuntimeError("文件里没有可导出的文本字幕轨")
        if opts.mode == "auto" and track is None and info.audio_index is None:
            raise RuntimeError("既没有文本字幕轨也没有音频轨，无法提取字幕")

        if track is not None:
            notify("extracting", track=str(track))
            cues = media.extract_subtitles(src, track)
            method, language = "embedded", track.language or ""
        else:
            notify("transcribing", model=opts.model)
            cues, language = transcribe(
                src, model=opts.model, language=opts.language,
                prompt=domains.build(opts.domains, opts.prompt),
                on_segment=lambda c: notify("segment", cue=c),
                on_progress=lambda d, t: notify("progress", done=d, total=t),
                cancel=cancel,
            )
            method = "whisper"

        if not cues:
            raise RuntimeError("没有得到任何字幕内容")

        existing = [base.with_suffix(f".{f}") for f in opts.formats]
        if not opts.overwrite and any(p.exists() for p in existing):
            names = "、".join(p.name for p in existing if p.exists())
            raise RuntimeError(f"输出文件已存在：{names}（加 --overwrite 覆盖）")

        result.outputs = formats.write(cues, base, opts.formats, opts.max_chars, opts.max_seconds)
        result.method, result.language = method, language
        notify("done", method=method, language=language,
               outputs=[str(p) for p in result.outputs])
        return result

    except InterruptedError:
        result.error = "已取消"
        notify("cancelled")
    except Exception as exc:
        result.error = str(exc)
        notify("error", message=str(exc))
    return result
