"""Tkinter 图形界面。"""

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__, domains, media, net
from .core import Options, process
from .transcribe import MODELS

LANG_CHOICES = [
    ("自动检测", None), ("中文", "zh"), ("英语", "en"), ("日语", "ja"),
    ("韩语", "ko"), ("法语", "fr"), ("德语", "de"), ("西班牙语", "es"), ("俄语", "ru"),
]
VIDEO_FILETYPES = [("视频文件", " ".join(f"*{e}" for e in sorted(media.VIDEO_EXTS))),
                   ("所有文件", "*.*")]


def _hms(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"视频字幕提取 v{__version__}")
        self.geometry("900x680")
        self.minsize(760, 560)

        icon = Path(__file__).resolve().parent.parent / "icon.ico"
        if icon.exists():
            try:
                self.iconbitmap(default=str(icon))
            except Exception:
                pass

        self.files: list[str] = []
        self.events: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_flag = threading.Event()

        self._build()
        self.after(80, self._pump)

    # ---------- 布局 ----------
    def _build(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        # 文件列表
        box = ttk.LabelFrame(root, text="视频文件")
        box.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        box.columnconfigure(0, weight=1)

        wrap = ttk.Frame(box)
        wrap.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        wrap.columnconfigure(0, weight=1)
        self.listbox = tk.Listbox(wrap, height=5, selectmode="extended")
        self.listbox.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(wrap, orient="vertical", command=self.listbox.yview)
        bar.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=bar.set)

        buttons = ttk.Frame(box)
        buttons.grid(row=1, column=0, sticky="w", padx=6, pady=(0, 6))
        ttk.Button(buttons, text="添加文件", command=self.add_files).pack(side="left")
        ttk.Button(buttons, text="添加文件夹", command=self.add_folder).pack(side="left", padx=6)
        ttk.Button(buttons, text="添加网址", command=self.add_url).pack(side="left")
        ttk.Button(buttons, text="移除选中", command=self.remove_selected).pack(side="left", padx=6)
        ttk.Button(buttons, text="清空", command=self.clear).pack(side="left")
        ttk.Button(buttons, text="查看信息", command=self.show_tracks).pack(side="left", padx=6)
        ttk.Button(buttons, text="B 站登录", command=self.bilibili_login).pack(side="left")

        # 选项
        opt = ttk.LabelFrame(root, text="选项")
        opt.grid(row=1, column=0, sticky="nsew")
        opt.columnconfigure(0, weight=1)
        opt.rowconfigure(5, weight=1)

        row1 = ttk.Frame(opt)
        row1.grid(row=0, column=0, sticky="w", padx=8, pady=4)
        self.mode = tk.StringVar(value="auto")
        for text, value in [
            ("自动（网站字幕→内嵌→识别）", "auto"),
            ("仅网站字幕", "site"),
            ("仅内嵌字幕", "track"),
            ("仅语音识别", "whisper"),
        ]:
            ttk.Radiobutton(row1, text=text, value=value, variable=self.mode).pack(side="left", padx=4)

        row2 = ttk.Frame(opt)
        row2.grid(row=1, column=0, sticky="ew", padx=8, pady=4)
        ttk.Label(row2, text="模型").pack(side="left")
        self.model = tk.StringVar(value="small")
        ttk.Combobox(row2, textvariable=self.model, values=list(MODELS),
                     state="readonly", width=14).pack(side="left", padx=(4, 12))
        ttk.Label(row2, text="语言").pack(side="left")
        self.language = tk.StringVar(value="自动检测")
        ttk.Combobox(row2, textvariable=self.language, values=[c[0] for c in LANG_CHOICES],
                     state="readonly", width=10).pack(side="left", padx=(4, 12))
        ttk.Label(row2, text="字幕轨").pack(side="left")
        self.track = tk.StringVar(value="自动")
        self.track_combo = ttk.Combobox(row2, textvariable=self.track, values=["自动"],
                                        state="readonly", width=22)
        self.track_combo.pack(side="left", padx=4)

        row3 = ttk.Frame(opt)
        row3.grid(row=2, column=0, sticky="ew", padx=8, pady=4)
        ttk.Label(row3, text="输出").pack(side="left")
        self.f_srt = tk.BooleanVar(value=False)
        self.f_vtt = tk.BooleanVar(value=False)
        self.f_txt = tk.BooleanVar(value=True)
        for text, var, hint in [
            ("纯文字 TXT", self.f_txt, "只有文字和标点"),
            ("字幕 SRT", self.f_srt, "带时间轴"),
            ("网页 VTT", self.f_vtt, "带时间轴"),
        ]:
            ttk.Checkbutton(row3, text=text, variable=var).pack(side="left", padx=(4, 0))
            ttk.Label(row3, text=hint, foreground="#888").pack(side="left", padx=(0, 8))
        self.overwrite = tk.BooleanVar(value=False)
        ttk.Checkbutton(row3, text="覆盖已有文件", variable=self.overwrite).pack(side="left", padx=12)
        ttk.Label(row3, text="输出目录").pack(side="left", padx=(12, 4))
        self.out_dir = tk.StringVar()
        ttk.Entry(row3, textvariable=self.out_dir, width=26).pack(side="left")
        ttk.Button(row3, text="…", width=3, command=self.pick_dir).pack(side="left", padx=2)

        row4 = ttk.Frame(opt)
        row4.grid(row=3, column=0, sticky="ew", padx=8, pady=4)
        ttk.Label(row4, text="网络").pack(side="left")
        self.proxy_mode = tk.StringVar(value="自动")
        ttk.Combobox(row4, textvariable=self.proxy_mode, state="readonly", width=10,
                     values=["自动", "直连", "自定义"]).pack(side="left", padx=(4, 8))
        ttk.Label(row4, text="代理地址").pack(side="left")
        self.proxy_addr = tk.StringVar(value="http://127.0.0.1:7897")
        ttk.Entry(row4, textvariable=self.proxy_addr, width=22).pack(side="left", padx=4)

        row5 = ttk.Frame(opt)
        row5.grid(row=4, column=0, sticky="ew", padx=8, pady=4)
        ttk.Label(row5, text="领域").pack(side="left")
        self.domain = tk.StringVar(value="通用")
        self.domain_combo = ttk.Combobox(
            row5, textvariable=self.domain, state="readonly", width=12,
            values=["通用"] + [cn for cn, _ in domains.DOMAINS.values()],
        )
        self.domain_combo.pack(side="left", padx=(4, 8))
        ttk.Label(row5, text="补充词").pack(side="left")
        self.prompt = tk.StringVar()
        ttk.Entry(row5, textvariable=self.prompt, width=34).pack(side="left", padx=4)
        ttk.Label(row5, text="（人名、术语，用顿号分隔）", foreground="#888").pack(side="left")

        self.log = tk.Text(opt, height=9, wrap="word", state="disabled",
                           font=("Consolas", 9))
        self.log.grid(row=5, column=0, sticky="nsew", padx=8, pady=(4, 8))

        # 底部
        bottom = ttk.Frame(root)
        bottom.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        bottom.columnconfigure(2, weight=1)
        self.start_btn = ttk.Button(bottom, text="开始提取", command=self.start)
        self.start_btn.grid(row=0, column=0)
        self.cancel_btn = ttk.Button(bottom, text="取消", command=self.cancel, state="disabled")
        self.cancel_btn.grid(row=0, column=1, padx=6)
        self.progress = ttk.Progressbar(bottom, maximum=100)
        self.progress.grid(row=0, column=2, sticky="ew", padx=8)
        self.status = ttk.Label(bottom, text="就绪")
        self.status.grid(row=0, column=3, sticky="e")

    # ---------- 文件 ----------
    def add_url(self) -> None:
        window = tk.Toplevel(self)
        window.title("添加视频网址")
        window.transient(self)
        window.grab_set()
        ttk.Label(window, text="粘贴视频链接，支持 B 站、YouTube 等：").pack(padx=12, pady=(12, 4), anchor="w")
        entry = ttk.Entry(window, width=72)
        entry.pack(padx=12, fill="x")
        entry.focus_set()

        def confirm(event=None) -> None:
            url = entry.get().strip()
            if not url:
                return
            if net.is_url(url):
                self._add([url])
                window.destroy()
            else:
                messagebox.showwarning("提示", "请填写以 http:// 或 https:// 开头的网址", parent=window)

        entry.bind("<Return>", confirm)
        buttons = ttk.Frame(window)
        buttons.pack(padx=12, pady=10, fill="x")
        ttk.Button(buttons, text="添加", command=confirm).pack(side="right")
        ttk.Button(buttons, text="取消", command=window.destroy).pack(side="right", padx=6)

    def bilibili_login(self) -> None:
        """显示二维码，扫码后保存登录状态。"""
        from . import auth

        saved = auth.load_cookie()
        if saved and auth.cookie_is_valid(saved):
            if not messagebox.askyesno("B 站登录", "已有有效的登录状态。\n要重新登录吗？"):
                return

        try:
            url, key = auth.start_login()
            png = auth.render_qr_png(url)
        except Exception as exc:
            messagebox.showerror("登录失败", str(exc))
            return

        window = tk.Toplevel(self)
        window.title("B 站扫码登录")
        window.transient(self)
        window.grab_set()
        ttk.Label(window, text="用 B 站手机 App 扫码并确认").pack(padx=16, pady=(14, 6))

        image = tk.PhotoImage(file=str(png))
        label = ttk.Label(window, image=image)
        label.image = image  # 防止被垃圾回收
        label.pack(padx=16)

        status = ttk.Label(window, text="等待扫码…")
        status.pack(padx=16, pady=8)
        cancel_flag = threading.Event()

        def close() -> None:
            cancel_flag.set()
            window.destroy()

        ttk.Button(window, text="关闭", command=close).pack(pady=(0, 14))

        def poll() -> None:
            def on_status(code, text):
                self.after(0, status.configure, {"text": text})

            cookie = auth.poll_until(key, on_status=on_status, cancel=cancel_flag)
            if cookie:
                self.after(0, lambda: (status.configure(text="登录成功"), self.after(900, window.destroy)))
            elif not cancel_flag.is_set():
                self.after(0, status.configure, {"text": "已过期，请重新登录"})

        threading.Thread(target=poll, daemon=True).start()

    def add_files(self) -> None:
        paths = filedialog.askopenfilenames(title="选择视频", filetypes=VIDEO_FILETYPES)
        self._add([str(p) for p in paths])

    def add_folder(self) -> None:
        folder = filedialog.askdirectory(title="选择文件夹")
        if not folder:
            return
        found = sorted(str(f) for f in Path(folder).iterdir()
                       if f.is_file() and f.suffix.lower() in media.VIDEO_EXTS)
        if not found:
            messagebox.showinfo("提示", "文件夹内没有视频文件")
        self._add(found)

    def _add(self, items: list[str]) -> None:
        for item in items:
            if item not in self.files:
                self.files.append(item)
        self.listbox.delete(0, "end")
        for item in self.files:
            self.listbox.insert("end", item)

    def remove_selected(self) -> None:
        for i in sorted(self.listbox.curselection(), reverse=True):
            del self.files[i]
        self._add([])

    def clear(self) -> None:
        self.files = []
        self._add([])

    def pick_dir(self) -> None:
        folder = filedialog.askdirectory(title="输出目录")
        if folder:
            self.out_dir.set(folder)

    def show_tracks(self) -> None:
        if not self.files:
            messagebox.showinfo("提示", "先添加视频文件")
            return
        target = self.files[0]
        try:
            if net.is_url(target):
                meta = net.info(target)
                lines = [meta.get("title") or target,
                         f"时长 {_hms(meta.get('duration') or 0)}", ""]
                subs = meta.get("subtitles") or {}
                auto = meta.get("automatic_captions") or {}
                lines += [f"人工字幕：{', '.join(subs)}"] if subs else []
                lines += [f"自动字幕：{', '.join(auto)}"] if auto else []
                if not subs and not auto:
                    lines.append("没有网站字幕（会下载音轨做语音识别）")
                messagebox.showinfo("视频信息", "\n".join(lines))
                return
            info = media.probe(target)
        except Exception as exc:
            messagebox.showerror("读取失败", str(exc))
            return
        lines = [info.path.name, f"时长 {_hms(info.duration)}", ""]
        lines += [str(t) for t in info.tracks] or ["没有内嵌字幕轨"]
        messagebox.showinfo("媒体信息", "\n".join(lines))
        usable = [t for t in info.tracks if t.exportable]
        self.track_combo.configure(values=["自动"] + [str(t) for t in usable])

    # ---------- 运行 ----------
    def _options(self) -> Options | None:
        fmts = tuple(f for f, on in
                     (("srt", self.f_srt.get()), ("vtt", self.f_vtt.get()), ("txt", self.f_txt.get()))
                     if on)
        if not fmts:
            messagebox.showwarning("提示", "至少选择一种输出格式")
            return None
        lang = dict((k, v) for k, v in LANG_CHOICES)[self.language.get()]
        track = None
        if self.track.get() != "自动":
            try:
                track = int(self.track.get().split()[0].lstrip("#"))
            except ValueError:
                track = None
        out = self.out_dir.get().strip()

        # 自动 = None（按域名分流）；直连 = ""；自定义 = 填入的地址
        proxy_mode = self.proxy_mode.get()
        if proxy_mode == "直连":
            proxy = ""
        elif proxy_mode == "自定义":
            proxy = self.proxy_addr.get().strip() or None
        else:
            proxy = None

        # "通用" 表示不加领域提示
        domain_cn = self.domain.get()
        domain_key = next(
            (k for k, (cn, _) in domains.DOMAINS.items() if cn == domain_cn), None
        )
        extra = self.prompt.get().strip() or None

        return Options(
            mode=self.mode.get(),
            track=track,
            model=self.model.get(),
            language=lang,
            formats=fmts,
            output_dir=Path(out) if out else None,
            overwrite=self.overwrite.get(),
            proxy=proxy,
            domains=[domain_key] if domain_key else [],
            prompt=extra,
        )

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.files:
            messagebox.showinfo("提示", "先添加视频文件")
            return
        opts = self._options()
        if opts is None:
            return

        self.cancel_flag.clear()
        self.start_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.progress["value"] = 0
        self._clear_log()
        self._log(f"开始处理 {len(self.files)} 个文件\n")
        self.worker = threading.Thread(target=self._run, args=(list(self.files), opts), daemon=True)
        self.worker.start()

    def _run(self, files: list[str], opts: Options) -> None:
        ok = 0
        for i, target in enumerate(files, 1):
            if self.cancel_flag.is_set():
                break
            label = target if net.is_url(target) else Path(target).name
            self.events.put(("status", f"[{i}/{len(files)}] {label}"))
            self.events.put(("log", f"\n[{i}/{len(files)}] {label}\n"))
            result = process(target, opts, emit=lambda k, d: self.events.put((k, d)),
                             cancel=self.cancel_flag)
            if result.ok:
                ok += 1
                how = {"site": f"网站字幕 {result.language}",
                       "embedded": "内嵌字幕",
                       "whisper": f"语音识别 {result.language}"}[result.method]
                self.events.put(("log", f"  ✓ {how} → {'、'.join(p.name for p in result.outputs)}\n"))
            else:
                self.events.put(("log", f"  ✗ {result.error}\n"))
            self.events.put(("progress", {"done": i, "total": len(files)}))
        self.events.put(("finished", {"ok": ok, "total": len(files)}))

    def cancel(self) -> None:
        self.cancel_flag.set()
        self.status.configure(text="取消中…")

    # ---------- 事件 ----------
    def _pump(self) -> None:
        try:
            while True:
                kind, data = self.events.get_nowait()
                self._handle(kind, data)
        except queue.Empty:
            pass
        self.after(80, self._pump)

    def _handle(self, kind: str, data: dict | str) -> None:
        if kind == "log":
            self._log(data)
        elif kind == "status":
            self.status.configure(text=data)
        elif kind == "segment":
            cue = data["cue"]
            self._log(f"  {_hms(cue.start)} {cue.text}\n")
        elif kind == "progress":
            done, total = data["done"], data["total"]
            self.progress["value"] = min(100, done / total * 100) if total else 0
        elif kind == "download":
            done, total = data["done"], data["total"]
            if total:
                self.progress["value"] = min(100, done / total * 100)
                self.status.configure(text=f"下载音轨 {done / 1048576:.1f}/{total / 1048576:.1f} MB")
        elif kind == "fetching":
            self._log("  读取视频信息…\n")
        elif kind == "auth":
            self._log(f"  {data['message']}\n")
        elif kind == "site_subtitles":
            self._log(f"  网站字幕 {data['language']}，{data['count']} 条\n")
        elif kind == "downloading":
            self._log("  网站没有字幕，下载音轨做语音识别…\n")
        elif kind == "extracting":
            self._log(f"  字幕轨 {data['track']}\n")
        elif kind == "finished":
            ok, total = data["ok"], data["total"]
            self.start_btn.configure(state="normal")
            self.cancel_btn.configure(state="disabled")
            self.status.configure(text=f"完成：成功 {ok}/{total}")
            self.progress["value"] = 100 if ok else 0
            self._log(f"\n全部完成：成功 {ok}，失败 {total - ok}\n")

    def _log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
