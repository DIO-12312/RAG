"""识别目录/导航类纯索引文本，避免把目录条目当作可引用证据。"""

from __future__ import annotations

import re

# 目录条目的稳定特征：文字后用点导引（dot leader）连到页码，
# 例如「第 10 章 QoS 策略....... 119」或「| 1.1 | 分布式系统...... 1 |」。
# 中文省略号「……」只有两个字符，正常正文里的「...」也更短，
# 因此要求至少 4 个点类字符（或 6 个 ASCII 点）才认定为导引点。
_DOT_LEADER = re.compile(r"(?:[.．·・…]{4,}|\.{6,})\s*\|?\s*(?:[ivxlcdm]{1,6}|\d{1,4})\s*\|?\s*$")
_DOT_RUN = re.compile(r"(?:[.．·・…]{4,}|\.{6,})")
_LONG_LINE = 40


# 判断一个段落是否只是目录/索引行；只要有正文行就不再视为导航。
def is_navigation_index(text: str) -> bool:
    """Return True when every non-empty line is a table-of-contents style entry."""

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    leaders = 0
    for line in lines:
        if _DOT_LEADER.search(line):
            leaders += 1
        elif len(line) > _LONG_LINE:
            return False
    return leaders > 0


# 保留原始点导引检测能力，供需要单独判断一行的调用方复用。
def has_dot_leader(text: str) -> bool:
    """Return True when the text contains a table-of-contents dot leader."""

    return bool(_DOT_RUN.search(text))
