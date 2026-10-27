"""B 站登录：扫码授权，Cookie 保存在项目内。

不读取浏览器或客户端里的登录凭证——那是别的程序的私有数据。程序自己
走一遍官方扫码授权，拿到属于本程序的 Cookie。
"""

import json
import threading
import time
from pathlib import Path

import requests

from .runtime import RUNTIME

COOKIE_FILE = Path(RUNTIME) / "cookies.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}

GENERATE = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
POLL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"

# 轮询返回的业务码
NOT_SCANNED = 86101
SCANNED = 86090
EXPIRED = 86038
SUCCESS = 0

STATUS_TEXT = {
    NOT_SCANNED: "等待扫码",
    SCANNED: "已扫码，请在手机上确认",
    EXPIRED: "二维码已过期",
    SUCCESS: "登录成功",
}


def _session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False  # B 站直连即可，不要套系统代理
    return s


def start_login() -> tuple[str, str]:
    """申请二维码。返回 (二维码内容, 轮询用的 key)。"""
    s = _session()
    r = s.get(GENERATE, headers=HEADERS, timeout=20).json()
    if r.get("code") != 0:
        raise RuntimeError(f"申请二维码失败：{r.get('message', r.get('code'))}")
    data = r["data"]
    return data["url"], data["qrcode_key"]


def poll_once(key: str) -> tuple[int, str | None]:
    """查一次扫码状态。返回 (业务码, 登录成功时的 Cookie 串)。"""
    s = _session()
    r = s.get(POLL, params={"qrcode_key": key}, headers=HEADERS, timeout=20)
    payload = r.json()
    data = payload.get("data", {})
    code = data.get("code")
    if code != SUCCESS:
        return code, None

    # 成功时 Cookie 在 data.url 的查询参数里，也在响应的 Set-Cookie 中
    from urllib.parse import parse_qs, urlparse

    params = parse_qs(urlparse(data.get("url") or "").query)
    pairs = {k: v[0] for k, v in params.items() if k in ("SESSDATA", "bili_jct", "DedeUserID")}
    if not pairs:
        pairs = {c.name: c.value for c in r.cookies}
    if not pairs.get("SESSDATA"):
        raise RuntimeError("登录成功但没拿到 SESSDATA，请重试")
    return SUCCESS, "; ".join(f"{k}={v}" for k, v in pairs.items())


def poll_until(key: str, timeout: float = 180, on_status=None,
               cancel: threading.Event | None = None) -> str | None:
    """持续轮询直到登录成功、过期或超时。返回 Cookie 串或 None。"""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        if cancel is not None and cancel.is_set():
            return None
        code, cookie = poll_once(key)
        if code != last:
            last = code
            if on_status:
                on_status(code, STATUS_TEXT.get(code, f"状态 {code}"))
        if code == SUCCESS:
            save_cookie(cookie)
            return cookie
        if code == EXPIRED:
            return None
        time.sleep(1.5)
    return None


def save_cookie(cookie: str) -> None:
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    COOKIE_FILE.write_text(json.dumps({"cookie": cookie, "saved_at": time.time()},
                                      ensure_ascii=False), encoding="utf-8")


def load_cookie() -> str | None:
    if not COOKIE_FILE.exists():
        return None
    try:
        data = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
        return data.get("cookie") or None
    except Exception:
        return None


def clear_cookie() -> None:
    COOKIE_FILE.unlink(missing_ok=True)


def cookie_is_valid(cookie: str) -> bool:
    """用 /x/web-interface/nav 验证 Cookie 是否还有效。"""
    s = _session()
    try:
        r = s.get("https://api.bilibili.com/x/web-interface/nav",
                  headers={**HEADERS, "Cookie": cookie}, timeout=15).json()
        return bool(r.get("data", {}).get("isLogin"))
    except Exception:
        return False


def render_qr_ascii(text: str) -> str:
    """在终端里用半角块字符画二维码。"""
    import io

    import qrcode

    qr = qrcode.QRCode(border=1)
    qr.add_data(text)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    # 两个竖排像素合成一个字符，黑白用实心/空白块
    lines = []
    for row in range(0, len(matrix), 2):
        upper = matrix[row]
        lower = matrix[row + 1] if row + 1 < len(matrix) else [False] * len(upper)
        line = ""
        for top, bottom in zip(upper, lower):
            line += {(True, True): "█", (True, False): "▀",
                     (False, True): "▄", (False, False): " "}[(top, bottom)]
        lines.append(line)
    return "\n".join(lines)


def render_qr_png(text: str) -> Path:
    """把二维码保存成 PNG，路径返回给调用方。"""
    import qrcode

    out = Path(RUNTIME) / "bilibili_login_qr.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    qrcode.make(text).save(out)
    return out
