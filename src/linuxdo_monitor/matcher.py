"""关键词匹配。

支持两种关键词写法：

* 普通关键词：大小写不敏感的子串匹配（默认）
* 正则关键词：``/pattern/flags`` 或 ``re:pattern`` 形式，按正则匹配

示例::

    m = KeywordMatcher()
    m.match("Docker 容器求助", ["docker", "/求助|提问/"])  # ['docker', '/求助|提问/']
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Iterable, List, Optional

_REGEX_WRAPPED = re.compile(r"^/(?P<pattern>.+)/(?P<flags>[aimsux]*)$", re.DOTALL)
_REGEX_PREFIX = "re:"


def is_regex_keyword(keyword: str) -> bool:
    """判断关键词是否为正则写法。"""
    keyword = keyword or ""
    return keyword.startswith(_REGEX_PREFIX) or bool(_REGEX_WRAPPED.match(keyword))


@lru_cache(maxsize=1024)
def _compile(keyword: str, case_sensitive: bool) -> Optional[re.Pattern]:
    pattern = keyword
    flags = 0 if case_sensitive else re.IGNORECASE

    if pattern.startswith(_REGEX_PREFIX):
        pattern = pattern[len(_REGEX_PREFIX) :]
    else:
        m = _REGEX_WRAPPED.match(pattern)
        if m:
            pattern = m.group("pattern")
            for ch in m.group("flags"):
                flags |= getattr(re, ch.upper(), 0)
    if not pattern:
        return None
    try:
        if is_regex_keyword(keyword):
            return re.compile(pattern, flags)
        return re.compile(re.escape(pattern), flags)
    except re.error:
        return None


class KeywordMatcher:
    """帖子标题 / 摘要与关键词的匹配器。"""

    def __init__(self, case_sensitive: bool = False, match_summary: bool = False):
        self.case_sensitive = case_sensitive
        self.match_summary = match_summary

    def match(self, text: str, keywords: Iterable[str]) -> List[str]:
        """返回所有命中的关键词（保持传入顺序，去重）。"""
        text = text or ""
        haystack = text
        matched: List[str] = []
        seen = set()
        for keyword in keywords or []:
            if not keyword:
                continue
            compiled = _compile(keyword, self.case_sensitive)
            if compiled is None:
                continue
            if compiled.search(haystack) and keyword not in seen:
                seen.add(keyword)
                matched.append(keyword)
        return matched

    def match_post(self, post, keywords: Iterable[str]) -> List[str]:
        """对帖子做匹配，可按配置把摘要一起纳入。"""
        text = post.title or ""
        if self.match_summary and getattr(post, "summary", None):
            text = f"{text}\n{post.summary}"
        return self.match(text, keywords)
