"""在线视频：抓取网站提供的字幕，或下载纯音频做语音识别。

只依赖 yt-dlp，不需要 ffmpeg：音频用 "bestaudio/best" 单文件格式下载，
拿到的就是可直接解码的 m4a / webm / mp3。
"""

import html
import re
import threading
import urllib.parse
from pathlib import Path

from .media import CJK, Cue, parse_timestamp
from .runtime import RUNTIME

# 国内站点直连即可；其他站点默认走系统代理。按域名判断，避免一刀切。
DOMESTIC = (
    "bilibili.com", "b23.tv", "douyin.com", "iesdouyin.com", "kuaishou.com",
    "xiaohongshu.com", "xhslink.com", "zhihu.com", "iqiyi.com", "youku.com",
    "qq.com", "weibo.com", "acfun.cn", "toutiao.com", "ixigua.com",
)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

SUBTITLE_LANGS = {
    "zh-Hans": "zh", "zh-Hant": "zh", "zh-CN": "zh", "zh-TW": "zh", "zh": "zh",
    "ai-zh": "zh", "en": "en", "en-US": "en", "ja": "ja", "ko": "ko",
}


def is_url(text: str) -> bool:
    return re.match(r"^https?://", text.strip(), re.I) is not None


def _host(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()


def _base_opts(proxy: str | None = None, cookie: str | None = None) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "noplaylist": True,
        "http_headers": {"User-Agent": UA},
        # yt-dlp 在 Windows 上会自动读取注册表代理；显式传空字符串才是"不用代理"
        "proxy": proxy if proxy is not None else "",
    }
    if cookie:
        opts["http_headers"]["Cookie"] = cookie
    return opts


def _opts(url: str, proxy: str | None, cookie: str | None = None) -> dict:
    if proxy is not None:
        return _base_opts(proxy, cookie)
    # 国内站点直连，国外站点跟随系统代理（None = 用系统设置）
    return _base_opts("" if any(d in _host(url) for d in DOMESTIC) else None, cookie)


def info(url: str, proxy: str | None = None, cookie: str | None = None) -> dict:
    """读取视频元信息。"""
    import yt_dlp

    with yt_dlp.YoutubeDL(_opts(url, proxy, cookie)) as y:
        return y.extract_info(url, download=False)


def is_bilibili(url: str) -> bool:
    return any(d in _host(url) for d in ("bilibili.com", "b23.tv"))


def _vtt_to_cues(text: str) -> list[Cue]:
    cues: list[Cue] = []
    for block in re.split(r"\r?\n\s*\r?\n", text):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines or "-->" not in block:
            continue
        idx = next(i for i, ln in enumerate(lines) if "-->" in ln)
        left, _, right = lines[idx].partition("-->")
        start = parse_timestamp(left)
        end = parse_timestamp(right)
        if start is None or end is None:
            continue
        body = html.unescape(re.sub(r"<[^>]+>", "", "\n".join(lines[idx + 1:]))).strip()
        if body:
            cues.append(Cue(start, end, body))
    return cues


# 自动字幕的滚动窗口内，相邻条目间隔通常不超过几秒
ROLLING_GAP = 5.0


def _tail_head_overlap(prev: str, cur: str) -> int:
    """prev 的结尾与 cur 的开头重合多少字符。

    中文没有空格，按字符比较；有空格的语言要求重合部分落在词边界上，
    避免 "…好的" + "的确…" 这种只重合一个字的误判。
    """
    if not CJK.search(prev) and " " in prev:
        pw, cw = prev.split(), cur.split()
        for k in range(min(len(pw), len(cw)), 0, -1):
            if pw[-k:] == cw[:k]:
                return sum(len(w) + 1 for w in cw[:k]) - 1
        return 0
    for k in range(min(len(prev), len(cur)), 0, -1):
        if prev.endswith(cur[:k]):
            return k if k >= 2 else 0
    return 0


def dedup_rolling(cues: list[Cue]) -> list[Cue]:
    """合并自动字幕的滚动重复。

    平台的自动字幕会不断刷新：有的是"增长式"（后一条包含前一条），有的是
    "滑动窗口"（后一条开头重复前一条结尾）。直接转换会得到大量重复句。
    """
    out: list[Cue] = []
    for cue in cues:
        text = cue.text.strip()
        if not text:
            continue
        if not out:
            out.append(Cue(cue.start, cue.end, text))
            continue

        prev = out[-1]
        # 间隔太远说明是两句独立的话，不处理
        if cue.start - prev.end > ROLLING_GAP:
            out.append(Cue(cue.start, cue.end, text))
            continue

        # 增长式：前一条是本条的前缀，用本条替换
        if len(text) > len(prev.text) and text.startswith(prev.text):
            out[-1] = Cue(prev.start, cue.end, text)
            continue

        overlap = _tail_head_overlap(prev.text, text)
        if overlap:
            tail = text[overlap:].strip()
            if tail:
                out.append(Cue(cue.start, cue.end, tail))
            else:
                # 本条完全被前一条覆盖，只延长结束时间
                out[-1] = Cue(prev.start, cue.end, prev.text)
            continue

        out.append(Cue(cue.start, cue.end, text))
    return out


def _fetch_text(url: str, proxy: str | None, cookie: str | None = None) -> str:
    import urllib.request

    headers = {"User-Agent": UA}
    if cookie:
        headers["Cookie"] = cookie
    handler = urllib.request.ProxyHandler(
        {} if proxy == "" else ({"http": proxy, "https": proxy} if proxy else {})
    )
    opener = urllib.request.build_opener(handler)
    req = urllib.request.Request(url, headers=headers)
    with opener.open(req, timeout=30) as resp:
        raw = resp.read()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def site_subtitles(url: str, prefer: str | None = None, proxy: str | None = None,
                   cookie: str | None = None) -> tuple[list[Cue], str] | None:
    """尝试抓取网站自带字幕（人工优先，其次自动生成）。

    返回 (字幕, 语言码)，没有可用字幕时返回 None。
    """
    meta = info(url, proxy, cookie)
    manual = meta.get("subtitles") or {}
    auto = meta.get("automatic_captions") or {}
    use_proxy = _opts(url, proxy, cookie)["proxy"]

    for group, is_auto in ((manual, False), (auto, True)):
        if not group:
            continue
        langs = list(group)
        if prefer:
            hit = [k for k in langs if SUBTITLE_LANGS.get(k, k).startswith(prefer)]
            langs = hit or langs
        for code in langs:
            for entry in group[code]:
                # B 站返回的已经是 SRT；YouTube 等是 vtt
                if entry.get("ext") not in ("vtt", "srt"):
                    continue
                try:
                    text = _fetch_text(entry["url"], use_proxy, cookie)
                except Exception:
                    continue
                cues = _vtt_to_cues(text)
                # 只有自动生成的字幕才有滚动重复，人工字幕原样保留
                if cues and is_auto:
                    cues = dedup_rolling(cues)
                if cues:
                    return cues, SUBTITLE_LANGS.get(code, code)
    return None


def download_audio(url: str, proxy: str | None = None,
                   progress: callable = None, cancel: threading.Event | None = None,
                   cookie: str | None = None) -> Path:
    """只下载音轨，保存到 runtime/downloads/，返回文件路径。"""
    import yt_dlp

    out_dir = Path(RUNTIME) / "downloads"
    out_dir.mkdir(parents=True, exist_ok=True)
    template = str(out_dir / "%(id)s.%(ext)s")

    def hook(d):
        if cancel is not None and cancel.is_set():
            raise InterruptedError("已取消")
        if progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            progress(d.get("downloaded_bytes", 0), total)

    opts = _opts(url, proxy, cookie)
    opts.update({
        "format": "bestaudio/best",
        "outtmpl": template,
        "progress_hooks": [hook],
        "overwrites": True,
        # 关掉 yt-dlp 自带的控制台进度，避免和调用方的进度显示打架
        "noprogress": True,
        "console_title": False,
    })
    with yt_dlp.YoutubeDL(opts) as y:
        data = y.extract_info(url, download=True)
        path = Path(y.prepare_filename(data))
    if not path.exists():
        candidates = sorted(out_dir.glob(Path(path).stem + ".*"))
        if not candidates:
            raise RuntimeError("音频下载完成但找不到文件")
        path = candidates[0]
    return path
