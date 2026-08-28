"""Hyperscan-backed glossary matcher for large-scale terminology tables.

对标 no_ocr/v3 的 babeldoc/glossary.py:把上万条术语编译成 hyperscan
Database(任务级"DB"),单次 scan 返回所有命中,O(文本长度)与条目数无关。

与 v3 的差异:
- 保留 RetainPDF 的富语义:3 级 level、exact/case_insensitive/regex 三种
  match_mode、context 窗口(±160)。literal 条目走 hyperscan,regex 条目
  量少,仍走 stdlib re。
- 保留 exact 模式的词边界语义:字节级检查 (?<![A-Za-z0-9_])...(?![...])
  (v3 是子串匹配,无边界;这里更严格,与原有 matched_glossary_entries 一致)。
- 优雅降级:hyperscan 不可用(Windows 本地 / 无 wheel)时退化为逐条 re finditer,
  语义与原实现完全一致,不报错。
"""

from __future__ import annotations

import logging
import re
from typing import Literal

from services.translation.core.terms.glossary import GlossaryEntry
from services.translation.core.terms.glossary import context_matches
from services.translation.core.terms.glossary import normalize_glossary_entries
from services.translation.core.terms.glossary import term_pattern

try:
    import hyperscan  # type: ignore[import-untyped]

    _HAS_HYPERSCAN = True
except ImportError:  # pragma: no cover - 环境相关
    hyperscan = None  # type: ignore[assignment]
    _HAS_HYPERSCAN = False


logger = logging.getLogger(__name__)

# 同 v3:每 20000 条术语编译成一个 hyperscan Database,避免单库过大。
_CHUNK_SIZE = 20000

# 空白归一化(同 v3 TERM_NORM_PATTERN):把连续空白折成单空格,避免空白差异导致漏配。
_WHITESPACE_RE = re.compile(r"\s+")


class GlossaryMatcher:
    """编译一组 GlossaryEntry 成 hyperscan DB,提供 O(文本长度) 的命中查询。

    构造代价高(上万条编译十几秒到两分钟),应通过模块级 LRU 缓存复用(见
    glossary.py 的 _MATCHER_CACHE)。hyperscan 不可用时退化为逐条 re。
    """

    def __init__(self, entries: list[GlossaryEntry]) -> None:
        self._use_hyperscan = _HAS_HYPERSCAN
        # literal 条目(exact / case_insensitive)走 hyperscan;regex 条目走 re。
        self._literal_entries: list[GlossaryEntry] = []
        self._regex_entries: list[GlossaryEntry] = []
        for entry in entries:
            if entry.match_mode == "regex":
                self._regex_entries.append(entry)
            else:
                self._literal_entries.append(entry)

        self._hs_dbs: list = []  # list[hyperscan.Database]
        # idx -> entry;与 hyperscan 编译时的 ids 一一对应。
        self._id_lookup: list[GlossaryEntry] = []

        if self._use_hyperscan:
            try:
                self._build_hyperscan_dbs()
            except Exception as exc:  # pragma: no cover - 编译失败降级
                logger.warning(
                    "hyperscan DB 编译失败,降级为逐条 re 匹配: %s: %s",
                    type(exc).__name__,
                    exc,
                )
                self._use_hyperscan = False
                self._hs_dbs = []
                self._id_lookup = []

    # ------------------------------------------------------------------ build

    def _build_hyperscan_dbs(self) -> None:
        if not self._literal_entries:
            return
        import itertools

        def _batched(seq, n):
            it = iter(seq)
            while batch := tuple(itertools.islice(it, n)):
                yield batch

        hs_pattern: list[tuple[bytes, int]] = []
        for idx, entry in enumerate(self._literal_entries):
            self._id_lookup.append(entry)
            # re.escape 后编码:literal 匹配,大小写由 HS_FLAG_CASELESS 统一处理。
            hs_pattern.append((re.escape(entry.source).encode("utf-8"), idx))

        for chunk in _batched(hs_pattern, _CHUNK_SIZE):
            expressions, ids = zip(*chunk, strict=True)
            hs_db = hyperscan.Database()
            hs_db.compile(
                expressions=expressions,
                ids=ids,
                elements=len(chunk),
                flags=hyperscan.HS_FLAG_CASELESS | hyperscan.HS_FLAG_SINGLEMATCH,
            )
            self._hs_dbs.append(hs_db)

    # ------------------------------------------------------------------- match

    def match(
        self,
        text: str,
        *,
        include_levels: set[str] | None = None,
    ) -> list[GlossaryEntry]:
        allowed_levels = include_levels or {"preserve", "canonical", "preferred"}
        return self._finalize(
            self._detect_candidates(text, allowed_levels),
            allowed_levels,
        )

    def _detect_candidates(
        self,
        text: str,
        allowed_levels: set[str],
    ) -> list[tuple[GlossaryEntry, int, int]]:
        """仅用于 match():在归一化文本上检测哪些条目出现(覆盖跨空白变体)。

        位置无意义(_finalize 会丢弃),全部相对归一化文本。只需知道"是否出现",
        故用 search(首个命中即可),不必 finditer 全部位置。
        """
        if not text or not (self._literal_entries or self._regex_entries):
            return []
        normalized_text = _WHITESPACE_RE.sub(" ", text)
        if not normalized_text:
            return []
        candidates: list[tuple[GlossaryEntry, int, int]] = []
        if self._use_hyperscan and self._hs_dbs:
            candidate_ids = self._hyperscan_candidate_ids(normalized_text)
            for idx in candidate_ids:
                entry = self._id_lookup[idx]
                if entry.level not in allowed_levels:
                    continue
                match = term_pattern(entry).search(normalized_text)
                if match and context_matches(
                    normalized_text, entry, start=match.start(), end=match.end()
                ):
                    candidates.append((entry, match.start(), match.end()))
        else:
            # 降级路径:逐条 re search(语义同原 matched_glossary_entries)
            for entry in self._literal_entries:
                if entry.level not in allowed_levels:
                    continue
                match = term_pattern(entry).search(normalized_text)
                if match and context_matches(
                    normalized_text, entry, start=match.start(), end=match.end()
                ):
                    candidates.append((entry, match.start(), match.end()))
        # regex 模式条目:始终走 stdlib re(量少)
        for entry in self._regex_entries:
            if entry.level not in allowed_levels:
                continue
            match = term_pattern(entry).search(normalized_text)
            if match and context_matches(
                normalized_text, entry, start=match.start(), end=match.end()
            ):
                candidates.append((entry, match.start(), match.end()))
        return candidates

    def match_with_spans(
        self,
        text: str,
        *,
        include_levels: set[str] | None = None,
    ) -> list[tuple[GlossaryEntry, int, int]]:
        """返回 (entry, start, end) 候选(去重前),偏移相对原文 text。

        供硬替换复用:protect_glossary_terms 直接拿这些位置建占位符 span。
        关键约束——偏移必须相对原文(与 protect_glossary_terms 操作的文本一致),
        否则原文含换行/多空格时会错位替换;且同一术语多处出现都要返回(用 finditer),
        否则只保护首处、其余漏译。

        hyperscan 只做候选过滤(哪些条目可能出现,O(文本长度)与条目数无关);
        精确定位用 term_pattern 在原文上 finditer,只对 hyperscan 命中的少量候选做,
        不对全表逐条 finditer。跨空白变体无法在原文精确定位的条目自动跳过
        (反正也无法安全替换)。返回 context_matches 通过的候选,未做去重/重叠裁剪
        (交由 protect_glossary_terms 的 span 选择逻辑处理)。
        """
        if not text or not (self._literal_entries or self._regex_entries):
            return []
        allowed_levels = include_levels or {"preserve", "canonical", "preferred"}
        normalized_text = _WHITESPACE_RE.sub(" ", text)
        if not normalized_text:
            return []
        candidates: list[tuple[GlossaryEntry, int, int]] = []
        if self._use_hyperscan and self._hs_dbs:
            candidate_ids = self._hyperscan_candidate_ids(normalized_text)
            for idx in candidate_ids:
                entry = self._id_lookup[idx]
                if entry.level not in allowed_levels:
                    continue
                # exact 模式大小写敏感:DB 是 CASELESS,可能命中大小写不同的串,
                # 用 term_pattern(exact 模式无 IGNORECASE flag)在原文上再确认一次。
                pattern = term_pattern(entry)
                for match in pattern.finditer(text):
                    if context_matches(text, entry, start=match.start(), end=match.end()):
                        candidates.append((entry, match.start(), match.end()))
        else:
            # 降级路径:逐条 re finditer(无 hyperscan 时)
            for entry in self._literal_entries:
                if entry.level not in allowed_levels:
                    continue
                pattern = term_pattern(entry)
                for match in pattern.finditer(text):
                    if context_matches(text, entry, start=match.start(), end=match.end()):
                        candidates.append((entry, match.start(), match.end()))
        # regex 模式条目:始终走 stdlib re(量少)
        for entry in self._regex_entries:
            if entry.level not in allowed_levels:
                continue
            pattern = term_pattern(entry)
            for match in pattern.finditer(text):
                if context_matches(text, entry, start=match.start(), end=match.end()):
                    candidates.append((entry, match.start(), match.end()))
        return candidates

    def _hyperscan_candidate_ids(self, normalized_text: str) -> set[int]:
        """hyperscan 单次 scan 返回出现的条目 idx 集合(每条至多一次,SINGLEMATCH)。

        hyperscan 默认不保证返回正确起始偏移(除非加 HS_FLAG_SOM_LEFTMOST,代价高),
        且无法表达词边界 / context 窗口语义,故只取 idx 集合做候选过滤,
        精确位置交给 term_pattern 在原文上 finditer。
        """
        buf = normalized_text.encode("utf-8")
        candidate_ids: set[int] = set()

        def on_match(idx: int, _frm: int, _to: int, _flags: int, _ctx=None) -> bool | None:
            candidate_ids.add(idx)
            return False

        for hs_db in self._hs_dbs:
            scratch = hyperscan.Scratch(hs_db)
            hs_db.scan(buf, on_match, scratch=scratch)
        return candidate_ids

    @staticmethod
    def _finalize(
        candidates: list[tuple[GlossaryEntry, int, int]],
        allowed_levels: set[str],
    ) -> list[GlossaryEntry]:
        seen: set[tuple[str, str, str, str | None]] = set()
        matched: list[GlossaryEntry] = []
        # 按 source 长度降序、casefold 升序,与原 matched_glossary_entries 一致
        for entry, _, _ in sorted(
            candidates, key=lambda item: (-len(item[0].source), item[0].source.casefold())
        ):
            if entry.level not in allowed_levels:
                continue
            key = (
                entry.source.casefold(),
                entry.target.casefold(),
                entry.level,
                entry.context.casefold() if entry.context else None,
            )
            if key in seen:
                continue
            seen.add(key)
            matched.append(entry)
        return matched


__all__ = ["GlossaryMatcher"]
