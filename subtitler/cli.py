"""命令行：subextract 文件或目录 [选项]。"""

import argparse
import sys
from pathlib import Path

from . import __version__, domains, media
from .core import Options, process
from .media import lang_name
from .transcribe import MODELS

LANG_ALIASES = {
    "zh": "zh", "chinese": "zh", "中文": "zh",
    "en": "en", "english": "en", "英文": "en", "英语": "en",
    "ja": "ja", "japanese": "ja", "日文": "ja", "日语": "ja",
    "ko": "ko", "korean": "ko", "韩语": "ko",
}


def _hms(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def cmd_login(args) -> int:
    """B 站扫码登录，保存 Cookie 供之后抓字幕使用。"""
    import sys

    from . import auth

    saved = auth.load_cookie()
    if saved and auth.cookie_is_valid(saved):
        print("已有可用的 B 站登录状态。")
        if not args.force:
            print("如需重新登录，加 --force。")
            return 0

    url, key = auth.start_login()
    qr = auth.render_qr_ascii(url) if not args.png else auth.render_qr_png(url)
    print("\n请用 B 站手机 App 扫描二维码并确认：\n")
    if args.png:
        print(f"二维码已保存到：{qr}\n")
    else:
        print(qr)

    def on_status(code, text):
        print(f"  {text}")

    cookie = auth.poll_until(key, timeout=args.timeout, on_status=on_status)
    if cookie is None:
        print("\n登录未完成（超时或二维码过期）。", file=sys.stderr)
        return 1
    print("登录成功，已保存。之后处理 B 站视频会自动使用这个状态。")
    return 0


def cmd_logout(args) -> int:
    from . import auth

    auth.clear_cookie()
    print("已清除保存的登录状态。")
    return 0


def cmd_formats(targets: list[str]) -> int:
    """列出网址提供的字幕和可下载的音轨格式。"""
    import sys

    from . import net

    for raw in targets:
        if not net.is_url(raw):
            print(f"跳过（不是网址）：{raw}", file=sys.stderr)
            continue
        try:
            meta = net.info(raw)
        except Exception as exc:
            print(f"{raw}\n  读取失败：{exc}", file=sys.stderr)
            continue

        print(f"\n{meta.get('title') or raw}")
        print(f"  {meta.get('extractor_key') or '未知站点'} · "
              f"时长 {_hms(meta.get('duration') or 0)}")

        manual = meta.get("subtitles") or {}
        auto = meta.get("automatic_captions") or {}
        if manual:
            print("  人工字幕：")
            for lang, entries in manual.items():
                exts = sorted({e.get("ext") for e in entries if e.get("ext")})
                print(f"    {lang:<12} {lang_name(lang):<8} {', '.join(exts)}")
        if auto:
            print("  自动字幕：")
            for lang, entries in list(auto.items())[:15]:
                exts = sorted({e.get("ext") for e in entries if e.get("ext")})
                print(f"    {lang:<12} {lang_name(lang):<8} {', '.join(exts)}")
        if not manual and not auto:
            print("  字幕：无（会下载音轨做语音识别）")

        audio = [f for f in meta.get("formats", [])
                 if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")]
        if audio:
            print("  可用音轨：")
            for f in sorted(audio, key=lambda x: x.get("abr") or 0, reverse=True)[:5]:
                size = f.get("filesize") or f.get("filesize_approx")
                size_text = f"{size / 1048576:.1f} MB" if size else "未知大小"
                abr = f"{f['abr']:.0f} kbps" if f.get("abr") else "码率未知"
                print(f"    {f.get('format_id'):<8} {f.get('ext'):<6} {abr:<12} {size_text}")
    return 0


def cmd_probe(targets: list[str]) -> int:
    from . import net

    for raw in targets:
        if net.is_url(raw):
            meta = net.info(raw)
            print(f"\n{meta.get('title')}")
            print(f"  时长 {_hms(meta.get('duration') or 0)}  "
                  f"[{meta.get('extractor_key') or '网址'}]")
            subs = meta.get("subtitles") or {}
            auto = meta.get("automatic_captions") or {}
            if not subs and not auto:
                print("  网站字幕：无")
            if subs:
                print(f"  人工字幕：{', '.join(subs)}")
            if auto:
                print(f"  自动字幕：{', '.join(list(auto)[:8])}")
            continue

        info = media.probe(raw)
        print(f"\n{info.path.name}")
        print(f"  时长 {_hms(info.duration)}  "
              f"音频 {'有' if info.audio_index is not None else '无'}")
        if not info.tracks:
            print("  字幕轨：无")
        for t in info.tracks:
            print(f"  字幕轨 {t}")
    return 0


def _collect(targets: list[str], recursive: bool) -> list[str]:
    """把文件、文件夹和网址整理成一个待处理列表。"""
    from . import net

    found: list[str] = []
    for raw in targets:
        if net.is_url(raw):
            found.append(raw.strip())
            continue
        p = Path(raw)
        if p.is_dir():
            pattern = "**/*" if recursive else "*"
            found += [str(f) for f in sorted(p.glob(pattern))
                      if f.is_file() and f.suffix.lower() in media.VIDEO_EXTS]
        elif p.is_file():
            found.append(str(p))
        else:
            print(f"跳过不存在的路径：{p}", file=sys.stderr)
    return found


def _label(target: str) -> str:
    from . import net

    if net.is_url(target):
        return target
    return Path(target).name


def cmd_extract(args) -> int:
    targets = _collect(args.inputs, args.recursive)
    if not targets:
        print("没有找到可处理的视频。", file=sys.stderr)
        return 2

    language = None
    if args.language:
        language = LANG_ALIASES.get(args.language.lower(), args.language)

    proxy = "" if args.no_proxy else args.proxy
    opts = Options(
        mode=args.mode,
        track=args.track,
        prefer_lang=args.prefer_lang,
        model=args.model,
        language=language,
        formats=tuple(f.strip().lower() for f in args.format.split(",") if f.strip()),
        max_chars=args.max_chars,
        max_seconds=args.max_seconds,
        output_dir=Path(args.output_dir) if args.output_dir else None,
        overwrite=args.overwrite,
        proxy=proxy,
        domains=[d for d in (args.domain or "").replace("，", ",").split(",") if d.strip()],
        prompt=args.prompt,
    )

    print(f"{len(targets)} 个目标 · 模式 {args.mode} · 输出 {','.join(opts.formats)}")
    failed = 0
    for i, target in enumerate(targets, 1):
        print(f"\n[{i}/{len(targets)}] {_label(target)}")
        state = {"live": False}

        def clear_live() -> None:
            if state["live"]:
                print()
                state["live"] = False

        def emit(kind: str, data: dict) -> None:
            if kind == "segment":
                clear_live()
                print(f"  {_hms(data['cue'].start)} {data['cue'].text}")
            elif kind == "progress" and not args.quiet:
                done, total = data["done"], data["total"]
                print(f"\r  识别 {done / total * 100:5.1f}%  {_hms(done)}/{_hms(total)}",
                      end="", flush=True)
                state["live"] = True
            elif kind == "download" and not args.quiet:
                done, total = data["done"], data["total"]
                if total:
                    print(f"\r  下载 {done / total * 100:5.1f}%  "
                          f"{done / 1048576:.1f}/{total / 1048576:.1f} MB",
                          end="", flush=True)
                    state["live"] = True
            elif kind == "fetching":
                print("  读取视频信息…")
            elif kind == "auth":
                print(f"  {data['message']}")
            elif kind == "site_subtitles":
                print(f"  网站字幕 {data['language']}，{data['count']} 条")
            elif kind == "downloading":
                print("  网站没有字幕，下载音轨做语音识别…")
            elif kind == "extracting":
                print(f"  导出字幕轨 {data['track']}")
            elif kind == "error":
                clear_live()
                print(f"  ✗ {data['message']}")

        result = process(target, opts, emit=emit)
        clear_live()
        if result.ok:
            how = {"site": f"网站字幕 {result.language}",
                   "embedded": "内嵌字幕",
                   "whisper": f"语音识别 {result.language}"}[result.method]
            print(f"  ✓ {how} → {'、'.join(p.name for p in result.outputs)}")
        else:
            failed += 1

    print(f"\n完成：成功 {len(targets) - failed}，失败 {failed}")
    return 0 if failed == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="subextract",
        description="从视频提取字幕：优先用网站自带字幕，其次内嵌字幕轨，最后 Whisper 识别。",
    )
    p.add_argument("inputs", nargs="*", help="视频文件、文件夹或视频网址")
    p.add_argument("--mode", choices=["auto", "site", "track", "whisper"], default="auto",
                   help="auto=网站字幕>内嵌轨>语音识别（默认）")
    p.add_argument("--track", type=int, help="指定内嵌字幕轨序号（用 --probe 查看）")
    p.add_argument("--prefer-lang", help="自动选轨时偏好语言，如 zh / en")
    p.add_argument("--probe", action="store_true", help="只看媒体信息，不提取")
    p.add_argument("--list-formats", action="store_true",
                   help="列出网址提供的字幕语言和可下载音轨")
    p.add_argument("--language", "-l", help="识别语言 zh/en/ja…，默认自动检测")
    p.add_argument("--domain", "-d", default=None,
                   help=f"领域预设，改善专有名词识别。可用：{domains.describe()}")
    p.add_argument("--prompt", help="自定义提示词，追加在领域词之后")
    p.add_argument("--model", default="small", choices=MODELS, help="Whisper 模型（默认 small）")
    p.add_argument("--format", "-f", default="txt",
                   help="输出格式，逗号分隔：txt,srt,vtt（默认 txt：只有文字）。要字幕加 srt")
    p.add_argument("--output-dir", "-o", help="输出目录（本地文件默认同目录，网址默认 runtime/output）")
    p.add_argument("--max-chars", type=int, default=42, help="单行最大字符数，0=不限")
    p.add_argument("--max-seconds", type=float, default=7.0, help="单条最长秒数，0=不限")
    p.add_argument("--recursive", "-r", action="store_true", help="递归子目录")
    p.add_argument("--overwrite", action="store_true", help="覆盖已有输出")
    p.add_argument("--quiet", "-q", action="store_true", help="不显示进度")
    p.add_argument("--proxy", help="强制使用代理，如 http://127.0.0.1:7897")
    p.add_argument("--no-proxy", action="store_true", help="强制直连（默认国内站点已直连）")

    net_group = p.add_argument_group("B 站")
    net_group.add_argument("--login", action="store_true", help="扫码登录 B 站，抓取需要登录的字幕")
    net_group.add_argument("--force", action="store_true", help="配合 --login 强制重新登录")
    net_group.add_argument("--png", action="store_true", help="登录时把二维码存成 PNG（默认终端显示）")
    net_group.add_argument("--timeout", type=float, default=180, help="扫码等待秒数，默认 180")
    net_group.add_argument("--logout", action="store_true", help="清除已保存的登录状态")

    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.login:
        return cmd_login(args)
    if args.logout:
        return cmd_logout(args)
    if not args.inputs:
        build_parser().error("需要提供视频文件、文件夹或网址（或用 --login 登录 B 站）")
    if args.probe:
        return cmd_probe(args.inputs)
    if args.list_formats:
        return cmd_formats(args.inputs)
    return cmd_extract(args)


if __name__ == "__main__":
    raise SystemExit(main())
