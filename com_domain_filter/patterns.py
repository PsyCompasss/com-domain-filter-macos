from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass
from typing import Iterable, Sequence


PATTERNS: tuple[str, ...] = (
    "不限",
    "AAA",
    "AAAA",
    "AAAAA",
    "AAAB",
    "ABC",
    "ABCD",
    "ABCDE",
    "AAAAB",
    "AAAAAB",
    "AAAABB",
    "AAAAABB",
    "AABB",
    "AABBCC",
    "AAABBB",
    "ABAB",
    "ABABAB",
    "ABCABC",
    "ABCDABCD",
    "AABBB",
    "AABBBB",
    "AAABBBB",
    "AABBCCCC",
    "ABABCCCC",
    "AABBCCDD",
    "AABAA",
)

CUSTOM_RE = re.compile(r"^[a-z0-9]*$")


class PatternConfigurationError(ValueError):
    pass


def normalize_custom(value: str) -> str:
    normalized = value.strip().lower()
    if not CUSTOM_RE.fullmatch(normalized):
        raise PatternConfigurationError("固定内容只能包含英文字母和数字，不能包含 .com、空格或符号。")
    return normalized


def unique_placeholders(pattern: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(pattern))


@dataclass(frozen=True)
class GeneratedDomain:
    domain: str
    pattern: str
    random_part: str


class PatternGenerator:
    def __init__(
        self,
        characters: Iterable[str],
        patterns: Sequence[str],
        prefix: str = "",
        suffix: str = "",
        unlimited_length: int = 6,
        rng: random.Random | None = None,
    ) -> None:
        self.characters = tuple(dict.fromkeys(c.lower() for c in characters))
        self.patterns = tuple(dict.fromkeys(patterns))
        self.prefix = normalize_custom(prefix)
        self.suffix = normalize_custom(suffix)
        self.unlimited_length = unlimited_length
        self.rng = rng or random.SystemRandom()
        self.validate()

    def validate(self) -> None:
        if not self.characters:
            raise PatternConfigurationError("请至少选择一个字母或数字。")
        if any(not CUSTOM_RE.fullmatch(c) or len(c) != 1 for c in self.characters):
            raise PatternConfigurationError("字符池中包含无效字符。")
        if not self.patterns:
            raise PatternConfigurationError("请至少选择一个规律。")
        unknown = [p for p in self.patterns if p not in PATTERNS]
        if unknown:
            raise PatternConfigurationError(f"未知规律：{', '.join(unknown)}")
        if not isinstance(self.unlimited_length, int) or self.unlimited_length < 1:
            raise PatternConfigurationError("“不限”的随机部分长度必须是正整数。")
        for pattern in self.patterns:
            if pattern != "不限" and len(unique_placeholders(pattern)) > len(self.characters):
                raise PatternConfigurationError(
                    f"规律 {pattern} 需要 {len(unique_placeholders(pattern))} 个不同字符，"
                    f"但当前只选择了 {len(self.characters)} 个。"
                )
            random_length = self.unlimited_length if pattern == "不限" else len(pattern)
            total_length = len(self.prefix) + random_length + len(self.suffix)
            if total_length > 63:
                raise PatternConfigurationError(
                    f"规律 {pattern} 生成的名称长度为 {total_length}，超过域名标签的63字符限制。"
                )

    def generate(self, pattern: str | None = None) -> GeneratedDomain:
        chosen = pattern or self.rng.choice(self.patterns)
        if chosen not in self.patterns:
            raise PatternConfigurationError(f"未选择规律：{chosen}")
        if chosen == "不限":
            random_part = "".join(self.rng.choice(self.characters) for _ in range(self.unlimited_length))
        else:
            placeholders = unique_placeholders(chosen)
            values = self.rng.sample(self.characters, len(placeholders))
            mapping = dict(zip(placeholders, values))
            random_part = "".join(mapping[item] for item in chosen)
        label = f"{self.prefix}{random_part}{self.suffix}"
        return GeneratedDomain(domain=f"{label}.com", pattern=chosen, random_part=random_part)

    def estimated_space(self) -> int:
        size = 0
        n = len(self.characters)
        for pattern in self.patterns:
            if pattern == "不限":
                size += n**self.unlimited_length
            else:
                size += math.perm(n, len(unique_placeholders(pattern)))
        return size
