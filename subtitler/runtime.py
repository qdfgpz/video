"""运行环境准备。

必须在 import faster_whisper / ctranslate2 之前调用 prepare()，因为
CTranslate2 在加载模块时就要能解析 cublas64_12.dll。

所有可变数据（模型）都保存在项目内的 runtime/ 下，不写用户目录。
"""

import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME = os.path.join(ROOT, "runtime")
MODELS = os.path.join(RUNTIME, "models")

MIRROR = "https://hf-mirror.com"

_ready = False


def _find_cuda_dlls() -> list[str]:
    """pip 安装的 NVIDIA 库目录。

    不能用 site.getsitepackages()：中文安装路径在部分 Windows 编码下会
    返回损坏的字符串。sys.prefix 是启动时确定的内存值，不受影响。
    """
    root = os.path.join(sys.prefix, "Lib", "site-packages", "nvidia")
    return sorted(
        d for d in glob.glob(os.path.join(root, "*", "bin")) if os.path.isdir(d)
    )


def _setup_cuda() -> None:
    dirs = _find_cuda_dlls()
    if not dirs:
        return
    os.environ["PATH"] = ";".join(dirs) + ";" + os.environ.get("PATH", "")
    if sys.platform == "win32":
        for d in dirs:
            try:
                os.add_dll_directory(d)
            except OSError:
                pass


def _setup_models() -> None:
    os.makedirs(MODELS, exist_ok=True)
    os.environ["HF_HOME"] = RUNTIME
    os.environ["HF_HUB_CACHE"] = MODELS
    # Xet 传输在镜像上会返回 401，关掉走普通 HTTP
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    # 国内直连 huggingface.co 常超时；显式设置过的值优先
    os.environ.setdefault("HF_ENDPOINT", MIRROR)


def prepare() -> None:
    global _ready
    if _ready:
        return
    _setup_cuda()
    _setup_models()
    _ready = True
