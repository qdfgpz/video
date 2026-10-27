# 视频字幕提取

从**视频网址**或**本地文件**提取字幕，输出 SRT / VTT / TXT。

按优先级自动选择来源：

1. **网站自带字幕** —— 粘贴链接直接拿，零误差、秒级
2. **视频内嵌字幕轨** —— 本地 mkv/mp4 的软字幕
3. **Whisper 语音识别** —— 前两者都没有时，只下载音轨转写

## 使用

**双击桌面上的「视频字幕提取」图标**即可打开界面（开始菜单里也有）。

打开后：点「添加文件」选本地视频，或点「添加网址」粘贴链接，两者能混在一起批量处理，然后点「开始提取」。

其他启动方式：

| 方式 | 说明 |
|---|---|
| 桌面图标 | 推荐，无黑窗口 |
| `启动.bat` | 同上，项目目录内的备份入口 |
| `命令行.bat` | 命令行模式 |

命令行：

```bash
# 网址
.venv\Scripts\python.exe -m subtitler.cli "https://www.bilibili.com/video/BVxxxx"

# 本地文件
.venv\Scripts\python.exe -m subtitler.cli 视频.mkv

# 先看看有什么字幕
.venv\Scripts\python.exe -m subtitler.cli --probe "https://..."
```

### B 站字幕需要登录

B 站的字幕接口必须登录才返回数据。扫码授权一次即可，Cookie 存在项目里，之后自动使用：

```bash
.venv\Scripts\python.exe -m subtitler.cli --login
```

终端会显示二维码，用手机 B 站 App 扫码确认。不想用终端可以加 `--png` 存成图片，
或者在图形界面点「B 站登录」。

不登录也能用，只是 B 站视频会走语音识别（慢一些，但结果一样）。

```bash
# 清除保存的登录状态
.venv\Scripts\python.exe -m subtitler.cli --logout
```

### 网络

国内站点（B 站、抖音、小红书等）直连；国外站点（YouTube 等）默认跟随系统代理。
需要强制指定时：

```bash
.venv\Scripts\python.exe -m subtitler.cli --proxy http://127.0.0.1:7897 "https://..."
.venv\Scripts\python.exe -m subtitler.cli --no-proxy "https://..."
```

## 模式

| `--mode` | 行为 |
|---|---|
| `auto`（默认） | 网站字幕 → 内嵌字幕轨 → 语音识别 |
| `site` | 只用网站自带字幕 |
| `track` | 只导出内嵌字幕轨 |
| `whisper` | 强制语音识别 |

## 模型档位

默认 `small`。追求质量可用 `large-v3-turbo`。

| 模型 | 磁盘 | 相对速度 |
|---|---|---|
| tiny / base | 75 / 142 MB | 8× / 4× |
| **small** | 464 MB | 1× |
| medium | 1.5 GB | 0.4× |
| large-v3-turbo | 1.6 GB | 0.35× |
| large-v3 | 3.1 GB | 0.25× |

模型首次使用自动下载到 `runtime/models/`，走 hf-mirror 镜像。

## 提高识别准确率

Whisper 会把音近的专有名词听错（Anthony Edwards → Ansony Edwards、爱德华兹 → 艾德华茲）。
给它一点上下文能明显改善。

**界面**：选项区有「领域」下拉框和「补充词」输入框。

**命令行**：

```bash
# 指定领域
.venv\Scripts\python.exe -m subtitler.cli --domain sports "https://..."

# 补充人名、术语（效果最明显）
.venv\Scripts\python.exe -m subtitler.cli --domain sports --prompt "安东尼·爱德华兹，森林狼" "https://..."

# 只填补充词也行
.venv\Scripts\python.exe -m subtitler.cli --prompt "雷军，小米，SU7" "https://..."
```

可用领域：体育、科技数码、财经投资、游戏、教育科普、影视娱乐、医疗健康、
法律、美食烹饪、汽车、音乐、新闻时事。中文名或英文 key（如 `sports`）都行。

### 实测经验

| 提示词 | 效果 |
|---|---|
| 不加 | `Ansony Edwards`、`艾德华茲` |
| 中文名「安东尼·爱德华兹」 | `安森尼·爱德华兹`（音译别扭） |
| **英文名「Anthony Edwards」** | **`Anthony Edwards`（正确）** |

**要点一：外文人名写英文原文。** 写中文音译，模型会输出更别扭的中文音译。

**要点二：提示词现在贯穿全程。** Whisper 的提示默认只对开头 30 秒有效，之后会被
前一段的转录结果挤出去。程序把音频切成 60 秒的块、**每块重新注入提示词**，
块间留 3 秒重叠防止句子被截断，所以长视频后面几十分钟也能生效。

另外：提示词要短、像人说的话。堆砌术语清单反而会让模型输出多余的顿号。
领域预设里刻意**不放具体人名**，因为放了之后模型会把不相干的声音也硬套成人名。

## 输出

**默认只输出纯文字 `.txt`**：只有文字和标点，没有时间轴，适合阅读或交给其他工具。

需要字幕文件时再勾选：

| 文件 | 内容 |
|---|---|
| `视频名.txt` | **默认**。只有文字和标点，不带时间轴 |
| `视频名.srt` | 带时间轴的字幕，可挂播放器或导入剪辑软件 |
| `视频名.vtt` | 带时间轴，网页播放器用 |

界面里勾选对应选项，或命令行 `-f txt,srt`、`-f srt` 等。

两者都会做中文标点规范化（半角 `,` → 全角 `，`）。

SRT / VTT 会按标点把过长的句子切成多条（默认每行 42 字、每条 7 秒），符合字幕阅读习惯；
TXT **不切分**，保持完整句子。

### 存在哪里

- **网址**：`D:\AI\提取字幕\runtime\output\`，用视频标题命名
- **本地文件**：与视频同目录，同文件名

想换位置，界面里填「输出目录」，或命令行加 `-o`：

```bash
.venv\Scripts\python.exe -m subtitler.cli -o "D:\字幕" "https://..."
```

## 说明

- **不需要安装 ffmpeg**：解码和字幕提取全部由 PyAV 完成。
- 处理网址时**只下载音轨**（几百 MB 的视频通常音轨只有几 MB）。
- 图形字幕（PGS / VobSub）需要 OCR，不支持；画面里烧进去的硬字幕也不支持。
- 所有数据都在本目录：`.venv/`（依赖）、`runtime/`（模型、下载、登录状态）。
- 本工具供个人学习使用，请遵守各平台的服务条款。
