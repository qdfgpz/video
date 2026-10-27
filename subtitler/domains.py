"""领域提示词：给 Whisper 提供上下文，改善专有名词和术语的识别。

Whisper 会把音近的专有名词听错（Anthony → Ansony、爱德华兹 → 艾德华茲）。
给它一段描述视频主题的文字，模型会倾向于输出这个语境里常见的词。

**关键经验**（实测得出）：

1. 提示词要短、要像人说的话。长串术语清单反而更差——模型被清单结构带偏，
   输出多余的顿号，人名也被拼成奇怪的词。
2. 预设里**不要放具体人名**。放了之后模型会把不相干的声音也硬套成人名。
   人名应该由用户按视频填在「补充词」里。
3. **外文人名要写英文原文**。提示词里写「安东尼·爱德华兹」，模型输出
   `安森尼·爱德华兹`；写 `Anthony Edwards`，模型输出 `Anthony Edwards`。
   所以补充词里外文人名请写英文，中文名可以附在括号里。
4. 提示词默认只对开头生效，所以要分块处理才能贯穿全程（见 `transcribe.py`）。

所以预设只描述**领域和通用术语**，人名交给用户补充。
"""

DOMAINS: dict[str, tuple[str, str]] = {
    "sports": (
        "体育",
        "这是一段体育赛事解说，谈到得分、篮板、助攻、MVP和季后赛。",
    ),
    "tech": (
        "科技数码",
        "这是一段科技数码内容，谈到人工智能、大模型、芯片、显卡和编程技术。",
    ),
    "finance": (
        "财经投资",
        "这是一段财经内容，谈到股票、基金、大盘走势、财报和经济政策。",
    ),
    "game": (
        "游戏",
        "这是一段游戏实况解说，谈到角色、装备、技能、版本更新和团战。",
    ),
    "education": (
        "教育科普",
        "这是一段课程讲解，谈到定义、定理、公式、实验和结论。",
    ),
    "entertainment": (
        "影视娱乐",
        "这是一段影视娱乐内容，谈到导演、演员、剧情、票房和综艺节目。",
    ),
    "medical": (
        "医疗健康",
        "这是一段医学科普，谈到症状、诊断、治疗、药物和检查指标。",
    ),
    "law": (
        "法律",
        "这是一段法律解读，谈到原告、被告、起诉、证据和判决。",
    ),
    "food": (
        "美食烹饪",
        "这是一段美食烹饪教程，谈到食材、火候、调味和具体做法。",
    ),
    "auto": (
        "汽车",
        "这是一段汽车评测，谈到发动机、续航、油耗、底盘和驾驶体验。",
    ),
    "music": (
        "音乐",
        "这是一段音乐内容，谈到旋律、节奏、和弦、编曲和演唱。",
    ),
    "news": (
        "新闻时事",
        "这是一段新闻播报，谈到政策、发布会、调查和相关部门。",
    ),
}

# 领域中文名 → key，方便 CLI 和界面用中文
ALIASES = {
    "体育": "sports", "运动": "sports", "篮球": "sports", "足球": "sports",
    "科技": "tech", "数码": "tech", "编程": "tech", "计算机": "tech",
    "财经": "finance", "投资": "finance", "股票": "finance", "金融": "finance",
    "游戏": "game", "电竞": "game",
    "教育": "education", "科普": "education", "学术": "education", "课程": "education",
    "娱乐": "entertainment", "影视": "entertainment", "综艺": "entertainment",
    "医疗": "medical", "健康": "medical", "医学": "medical",
    "法律": "law", "法规": "law",
    "美食": "food", "烹饪": "food", "做饭": "food",
    "汽车": "auto", "车": "auto",
    "音乐": "music", "歌曲": "music",
    "新闻": "news", "时事": "news",
}


def resolve(name: str) -> str | None:
    """把用户写的领域名（英文 key 或中文）转成标准 key。"""
    name = name.strip().lower()
    if name in DOMAINS:
        return name
    return ALIASES.get(name.strip())


def build(domains: list[str] | str | None, extra: str | None = None) -> str | None:
    """把若干领域和一个自定义补充拼成最终的提示词。

    domains 可以是 key 列表、中文名列表，或逗号分隔的字符串。
    """
    if not domains and not extra:
        return None
    if isinstance(domains, str):
        domains = [d for d in domains.replace("，", ",").split(",") if d.strip()]

    parts: list[str] = []
    for raw in domains or []:
        key = resolve(raw)
        if key and key in DOMAINS:
            text = DOMAINS[key][1]
            if text not in parts:
                parts.append(text)
    if extra:
        parts.append(extra.strip())

    return " ".join(parts) or None


def describe() -> str:
    """列出所有可用领域，用于命令行帮助。"""
    return "、".join(f"{cn}({key})" for key, (cn, _) in DOMAINS.items())
