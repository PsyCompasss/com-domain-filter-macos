from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from .tlds import ascii_tld, unicode_tld


PATTERNS: tuple[str, ...] = (
    "不限", "AAA", "AAAA", "AAAAA", "AAAB", "ABC", "ABCD", "ABCDE",
    "AAAAB", "AAAAAB", "AAAABB", "AAAAABB", "AABB", "AABBCC", "AAABBB",
    "ABAB", "ABABAB", "ABCABC", "ABCDABCD", "AABBB", "AABBBB",
    "AAABBBB", "AABBCCCC", "ABABCCCC", "AABBCCDD", "AABAA",
)

ALLOWED_CHARACTERS = "abcdefghijklmnopqrstuvwxyz0123456789-"
CUSTOM_RE = re.compile(r"^[a-z0-9-]*$")
CUSTOM_PATTERN_RE = re.compile(r"^[A-Z]+$")
DOMAIN_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
DOMAIN_SUFFIX_RE = re.compile(r"^\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

BLOCK_FIXED = "fixed"
BLOCK_COMMON = "common"
BLOCK_CUSTOM = "custom"
BLOCK_UNLIMITED = "unlimited"
BLOCK_TYPES = (BLOCK_FIXED, BLOCK_COMMON, BLOCK_CUSTOM, BLOCK_UNLIMITED)

BIND_INDEPENDENT = "independent"
BIND_SHARED = "shared"
BIND_MODES = (BIND_INDEPENDENT, BIND_SHARED)


class PatternConfigurationError(ValueError):
    pass


def normalize_custom(value: str) -> str:
    normalized = value.strip().lower()
    if not CUSTOM_RE.fullmatch(normalized):
        raise PatternConfigurationError("固定内容只能包含英文字母、数字和半角连字符 -，不能包含 .com、空格或其他符号。")
    return normalized


def normalize_pattern(value: str) -> str:
    normalized = value.strip().upper()
    if not normalized or not CUSTOM_PATTERN_RE.fullmatch(normalized):
        raise PatternConfigurationError("自定义规律只能使用英文字母占位符，例如 ABCDDDD 或 ABCBA。")
    return normalized


def normalize_domain_suffixes(values: Iterable[str]) -> tuple[str, ...]:
    suffixes: list[str] = []
    for raw in values:
        value = str(raw).strip().lower()
        if not value:
            continue
        suffix = value if value.startswith(".") else f".{value}"
        ascii_suffix = ascii_tld(suffix)
        if not DOMAIN_SUFFIX_RE.fullmatch(ascii_suffix):
            raise PatternConfigurationError(f"域名后缀“{raw}”格式不正确。")
        suffixes.append(unicode_tld(suffix))
    normalized = tuple(dict.fromkeys(suffixes))
    if not normalized:
        raise PatternConfigurationError("请至少选择一个域名后缀。")
    return normalized


def unique_placeholders(pattern: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(pattern))


@dataclass(frozen=True)
class PatternBlock:
    kind: str
    value: str = ""
    length: int = 1
    random_position: bool = False

    @classmethod
    def from_dict(cls, payload: dict) -> "PatternBlock":
        return cls(
            kind=str(payload.get("kind", BLOCK_COMMON)),
            value=str(payload.get("value", "AAA")),
            length=int(payload.get("length", 1)),
            random_position=bool(payload.get("random_position", False)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "value": self.value,
            "length": self.length,
            "random_position": self.random_position,
        }

    def normalized(self) -> "PatternBlock":
        if self.kind not in BLOCK_TYPES:
            raise PatternConfigurationError(f"未知组合块类型：{self.kind}")
        if self.kind == BLOCK_FIXED:
            return PatternBlock(self.kind, normalize_custom(self.value), 1, self.random_position)
        if self.kind in (BLOCK_COMMON, BLOCK_CUSTOM):
            pattern = normalize_pattern(self.value)
            if self.kind == BLOCK_COMMON and pattern not in PATTERNS:
                raise PatternConfigurationError(f"未知常用规律：{pattern}")
            return PatternBlock(self.kind, pattern, 1, self.random_position)
        if not isinstance(self.length, int) or self.length < 1:
            raise PatternConfigurationError("不限随机块的长度必须是正整数。")
        return PatternBlock(self.kind, "", self.length, False)

    @property
    def output_length(self) -> int:
        normalized = self.normalized()
        if normalized.kind == BLOCK_FIXED:
            return len(normalized.value)
        if normalized.kind in (BLOCK_COMMON, BLOCK_CUSTOM):
            return len(normalized.value)
        return normalized.length

    @property
    def description(self) -> str:
        normalized = self.normalized()
        if normalized.kind == BLOCK_FIXED:
            return f"固定({normalized.value})"
        if normalized.kind == BLOCK_UNLIMITED:
            return f"不限{normalized.length}位"
        return normalized.value


@dataclass(frozen=True)
class GeneratedDomain:
    domain: str
    pattern: str
    random_part: str
    stem: str = ""
    domains: tuple[str, ...] = ()

    @property
    def query_stem(self) -> str:
        return self.stem or self.domain.rsplit(".", 1)[0]

    @property
    def query_domains(self) -> tuple[str, ...]:
        return self.domains or (self.domain,)


@dataclass(frozen=True)
class ContainmentRule:
    value: str
    minimum: int = 1

    @classmethod
    def from_dict(cls, payload: dict) -> "ContainmentRule":
        return cls(str(payload.get("value", "")), int(payload.get("minimum", 1)))

    def to_dict(self) -> dict[str, object]:
        return {"value": self.value, "minimum": self.minimum}

    def normalized(self) -> "ContainmentRule":
        value = normalize_custom(self.value)
        if not value:
            raise PatternConfigurationError("“至少包含”的字符不能为空。")
        if len(value) != 1:
            raise PatternConfigurationError("“至少包含”每条只能填写一个字母、数字或连字符。")
        if self.minimum < 1:
            raise PatternConfigurationError("“至少包含”的次数必须大于 0。")
        return ContainmentRule(value, self.minimum)


class BlockPatternGenerator:
    def __init__(
        self,
        characters: Iterable[str],
        blocks: Sequence[PatternBlock | dict],
        binding_mode: str = BIND_INDEPENDENT,
        containment_rules: Sequence[ContainmentRule | dict] = (),
        domain_suffixes: Sequence[str] = (".com",),
        rng: random.Random | None = None,
    ) -> None:
        self.characters = tuple(dict.fromkeys(c.lower() for c in characters))
        self.blocks = tuple(
            (item if isinstance(item, PatternBlock) else PatternBlock.from_dict(item)).normalized()
            for item in blocks
        )
        self.binding_mode = binding_mode
        self.containment_rules = tuple(
            (item if isinstance(item, ContainmentRule) else ContainmentRule.from_dict(item)).normalized()
            for item in containment_rules
        )
        self.domain_suffixes = normalize_domain_suffixes(domain_suffixes)
        self.rng = rng or random.SystemRandom()
        self.validate()

    def validate(self) -> None:
        if not self.characters:
            raise PatternConfigurationError("请至少选择一个字母、数字或连字符。")
        if any(not CUSTOM_RE.fullmatch(c) or len(c) != 1 for c in self.characters):
            raise PatternConfigurationError("字符池中包含无效字符。")
        if not self.blocks:
            raise PatternConfigurationError("请至少添加一个域名组合块。")
        if self.binding_mode not in BIND_MODES:
            raise PatternConfigurationError("请选择正确的规律字符关联方式。")
        total_length = sum(block.output_length for block in self.blocks)
        if total_length < 1:
            raise PatternConfigurationError("域名主体不能为空。")
        if total_length > 63:
            raise PatternConfigurationError(f"当前组合长度为 {total_length}，超过域名标签的63字符限制。")
        for rule in self.containment_rules:
            if len(rule.value) * rule.minimum > total_length:
                raise PatternConfigurationError(
                    f"当前域名只有 {total_length} 位，无法至少包含 {rule.minimum} 次“{rule.value}”。"
                )
            fixed_count = sum(block.value.count(rule.value) for block in self.blocks if block.kind == BLOCK_FIXED)
            if rule.value not in self.characters and fixed_count < rule.minimum:
                raise PatternConfigurationError(
                    f"字符池中没有“{rule.value}”，固定文字中也不足 {rule.minimum} 个。"
                )
        first = self.blocks[0]
        last = self.blocks[-1]
        if first.kind == BLOCK_FIXED and not first.random_position and first.value.startswith("-"):
            raise PatternConfigurationError("域名不能以连字符 - 开头。")
        if last.kind == BLOCK_FIXED and not last.random_position and last.value.endswith("-"):
            raise PatternConfigurationError("域名不能以连字符 - 结尾。")
        pattern_blocks = [block for block in self.blocks if block.kind in (BLOCK_COMMON, BLOCK_CUSTOM)]
        if self.binding_mode == BIND_SHARED:
            placeholders = unique_placeholders("".join(block.value for block in pattern_blocks))
            if len(placeholders) > len(self.characters):
                raise PatternConfigurationError(
                    f"全部规律块共需要 {len(placeholders)} 个不同字符，但当前只选择了 {len(self.characters)} 个。"
                )
        else:
            for block in pattern_blocks:
                needed = len(unique_placeholders(block.value))
                if needed > len(self.characters):
                    raise PatternConfigurationError(
                        f"规律 {block.value} 需要 {needed} 个不同字符，但当前只选择了 {len(self.characters)} 个。"
                    )

    def _mapping_for(self, pattern: str) -> dict[str, str]:
        placeholders = unique_placeholders(pattern)
        values = self.rng.sample(self.characters, len(placeholders))
        return dict(zip(placeholders, values))

    def generate(self) -> GeneratedDomain:
        for _ in range(2000):
            shared_mapping: dict[str, str] = {}
            if self.binding_mode == BIND_SHARED:
                combined = "".join(
                    block.value for block in self.blocks if block.kind in (BLOCK_COMMON, BLOCK_CUSTOM)
                )
                shared_mapping = self._mapping_for(combined)
            pieces: list[str] = []
            unlimited_piece_indexes: list[int] = []
            floating_pieces: list[str] = []
            random_pieces: list[str] = []
            for block in self.blocks:
                if block.kind == BLOCK_FIXED:
                    piece = block.value
                elif block.kind == BLOCK_UNLIMITED:
                    piece = "".join(self.rng.choice(self.characters) for _ in range(block.length))
                    random_pieces.append(piece)
                else:
                    mapping = shared_mapping if self.binding_mode == BIND_SHARED else self._mapping_for(block.value)
                    piece = "".join(mapping[item] for item in block.value)
                    random_pieces.append(piece)
                if block.kind != BLOCK_UNLIMITED and block.random_position:
                    floating_pieces.append(piece)
                else:
                    if block.kind == BLOCK_UNLIMITED:
                        unlimited_piece_indexes.append(len(pieces))
                    pieces.append(piece)
            for rule in self.containment_rules:
                current_count = sum(piece.count(rule.value) for piece in pieces + floating_pieces)
                deficit = rule.minimum - current_count
                if deficit <= 0:
                    continue
                candidates = [
                    (piece_index, character_index)
                    for piece_index in unlimited_piece_indexes
                    for character_index, character in enumerate(pieces[piece_index])
                    if character != rule.value
                ]
                if len(candidates) >= deficit:
                    for piece_index, character_index in self.rng.sample(candidates, deficit):
                        piece = pieces[piece_index]
                        pieces[piece_index] = f"{piece[:character_index]}{rule.value}{piece[character_index + 1:]}"
            label_tokens = list("".join(pieces))
            for piece in floating_pieces:
                position = self.rng.randrange(len(label_tokens) + 1)
                label_tokens.insert(position, piece)
            label = "".join(label_tokens)
            contains_required = all(
                label.count(rule.value) >= rule.minimum for rule in self.containment_rules
            )
            if DOMAIN_LABEL_RE.fullmatch(label) and contains_required:
                domains = tuple(f"{label}{suffix}" for suffix in self.domain_suffixes)
                return GeneratedDomain(
                    # ``domain`` 保留首个完整域名，兼容旧界面和旧测试；真正查询时
                    # 使用 ``query_stem`` 和 ``query_domains``，同一主体只搜索一次。
                    domain=domains[0],
                    pattern=" + ".join(block.description for block in self.blocks),
                    random_part="".join(random_pieces),
                    stem=label,
                    domains=domains,
                )
        raise PatternConfigurationError("当前字符和组合规则无法生成首尾合法的域名，请增加字母或数字。")

    def estimated_space(self) -> int:
        n = len(self.characters)
        size = 1
        if self.binding_mode == BIND_SHARED:
            combined = "".join(
                block.value for block in self.blocks if block.kind in (BLOCK_COMMON, BLOCK_CUSTOM)
            )
            size *= math.perm(n, len(unique_placeholders(combined))) if combined else 1
            for block in self.blocks:
                if block.kind == BLOCK_UNLIMITED:
                    size *= n**block.length
            return size
        for block in self.blocks:
            if block.kind == BLOCK_UNLIMITED:
                size *= n**block.length
            elif block.kind in (BLOCK_COMMON, BLOCK_CUSTOM):
                size *= math.perm(n, len(unique_placeholders(block.value)))
        return size


def normalize_imported_domains(
    values: Iterable[str],
    domain_suffixes: Sequence[str] = (".com",),
) -> tuple[str, ...]:
    selected_suffixes = normalize_domain_suffixes(domain_suffixes)
    domains: list[str] = []
    for raw in values:
        value = str(raw).strip().lower()
        if not value:
            continue
        value = re.sub(r"^https?://", "", value).split("/", 1)[0].strip(".")
        if "." in value:
            label, raw_suffix = value.rsplit(".", 1)
            suffixes = (unicode_tld(f".{raw_suffix}"),)
            if suffixes[0] not in selected_suffixes:
                raise PatternConfigurationError(f"导入项“{raw}”的后缀不在已选后缀池中。")
        else:
            label = value
            suffixes = selected_suffixes
        if not DOMAIN_LABEL_RE.fullmatch(label):
            raise PatternConfigurationError(f"导入项“{raw}”不是合法的域名主体。")
        domains.extend(f"{label}{suffix}" for suffix in suffixes)
    normalized = tuple(dict.fromkeys(domains))
    if not normalized:
        raise PatternConfigurationError("导入文件中没有可查询的域名。")
    return normalized


class ImportedDomainGenerator:
    def __init__(self, domains: Iterable[str], domain_suffixes: Sequence[str] = (".com",)) -> None:
        selected_suffixes = normalize_domain_suffixes(domain_suffixes)
        self.domains = normalize_imported_domains(domains, domain_suffixes)
        stems: dict[str, None] = {}
        for domain in self.domains:
            stem, _raw_suffix = domain.rsplit(".", 1)
            stems.setdefault(stem, None)

        # 导入文件决定“查询哪些名称”，后缀池决定“每个名称查询哪些后缀”。
        # 即使文件里写的是 alpha.com，只要后缀池选中了 .com/.net/.cc，
        # 也应当只搜索一次 alpha，并在同一结果页确认这三个完整域名。
        self.groups = tuple(
            (stem, tuple(f"{stem}{suffix}" for suffix in selected_suffixes))
            for stem in stems
        )
        self.index = 0

    def generate(self) -> GeneratedDomain:
        if self.index >= len(self.groups):
            raise StopIteration
        stem, domains = self.groups[self.index]
        self.index += 1
        return GeneratedDomain(
            domain=domains[0],
            pattern="导入域名",
            random_part=stem,
            stem=stem,
            domains=domains,
        )

    def estimated_space(self) -> int:
        return len(self.groups)


class PatternGenerator:
    """兼容旧设置和旧测试的单规律生成器。"""

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

    def _blocks_for(self, pattern: str) -> tuple[PatternBlock, ...]:
        blocks: list[PatternBlock] = []
        if self.prefix:
            blocks.append(PatternBlock(BLOCK_FIXED, self.prefix))
        if pattern == "不限":
            blocks.append(PatternBlock(BLOCK_UNLIMITED, length=self.unlimited_length))
        else:
            blocks.append(PatternBlock(BLOCK_COMMON, pattern))
        if self.suffix:
            blocks.append(PatternBlock(BLOCK_FIXED, self.suffix))
        return tuple(blocks)

    def validate(self) -> None:
        if not self.patterns:
            raise PatternConfigurationError("请至少选择一个规律。")
        unknown = [p for p in self.patterns if p not in PATTERNS]
        if unknown:
            raise PatternConfigurationError(f"未知规律：{', '.join(unknown)}")
        for pattern in self.patterns:
            BlockPatternGenerator(self.characters, self._blocks_for(pattern), rng=self.rng)

    def generate(self, pattern: str | None = None) -> GeneratedDomain:
        chosen = pattern or self.rng.choice(self.patterns)
        if chosen not in self.patterns:
            raise PatternConfigurationError(f"未选择规律：{chosen}")
        generated = BlockPatternGenerator(self.characters, self._blocks_for(chosen), rng=self.rng).generate()
        return GeneratedDomain(generated.domain, chosen, generated.random_part)

    def estimated_space(self) -> int:
        return sum(
            BlockPatternGenerator(self.characters, self._blocks_for(pattern), rng=self.rng).estimated_space()
            for pattern in self.patterns
        )
