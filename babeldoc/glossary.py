import csv
import io
import itertools
import logging
import re
import time
from pathlib import Path

import chardet
import hyperscan
import regex

logger = logging.getLogger(__name__)


class GlossaryEntry:
    def __init__(self, source: str, target: str, target_language: str | None = None):
        self.source = source
        self.target = target
        self.target_language = target_language

    def __repr__(self):
        return f"GlossaryEntry(source='{self.source}', target='{self.target}', target_language='{self.target_language}')"


def batched(iterable, n, *, strict=False):
    # batched('ABCDEFG', 3) → ABC DEF G
    if n < 1:
        raise ValueError("n must be at least one")
    iterator = iter(iterable)
    while batch := tuple(itertools.islice(iterator, n)):
        if strict and len(batch) != n:
            raise ValueError("batched(): incomplete batch")
        yield batch


TERM_NORM_PATTERN = re.compile(r"\s+", regex.UNICODE)


class Glossary:
    def __init__(self, name: str, entries: list[GlossaryEntry]):
        self.name = name

        # Deduplicate entries based on normalized source
        unique_entries = []
        seen_normalized_sources = set()
        for entry in entries:
            normalized_source = self.normalize_source(entry.source)
            if normalized_source not in seen_normalized_sources:
                unique_entries.append(entry)
                seen_normalized_sources.add(normalized_source)
        self.entries = unique_entries

        self.normalized_lookup: dict[str, tuple[str, str]] = {}
        self.id_lookup: list[tuple[str, str]] = []
        self.hs_dbs: list[hyperscan.Database] | None = None
        self._build_regex_and_lookup()

    @staticmethod
    def normalize_source(source_term: str) -> str:
        """Normalizes a source term by lowercasing and standardizing whitespace."""
        term = source_term.lower()
        term = TERM_NORM_PATTERN.sub(
            " ", term
        )  # Replace multiple whitespace with single space
        return term.strip()

    def _build_regex_and_lookup(self):
        logger.debug(
            f"start build regex for glossary {self.name} with {len(self.entries)} entries"
        )
        """
        Builds a combined regex for all source terms and a lookup dictionary
        from normalized source terms to (original_source, original_target).
        Regex patterns are sorted by length in descending order to prioritize longer matches.
        """
        self.normalized_lookup = {}

        if not self.entries:
            self.source_terms_regex = None
            return

        self.hs_dbs = []
        hs_pattern = []
        start = time.time()
        for idx, entry in enumerate(self.entries):
            normalized_key = self.normalize_source(entry.source)
            self.normalized_lookup[normalized_key] = (entry.source, entry.target)
            self.id_lookup.append((entry.source, entry.target))

            hs_pattern.append((re.escape(entry.source).encode("utf-8"), idx))

        chunk_size = 20000
        for i, pattern_chunk in enumerate(
            batched(hs_pattern, chunk_size, strict=False)
        ):
            logger.debug(
                f"building hs_db chunk {i + 1} / {len(self.entries) // chunk_size + 1}"
            )
            expressions, ids = zip(*pattern_chunk, strict=False)

            hs_db = hyperscan.Database()
            hs_db.compile(
                expressions=expressions,
                ids=ids,
                elements=len(pattern_chunk),
                flags=hyperscan.HS_FLAG_CASELESS | hyperscan.HS_FLAG_SINGLEMATCH,
                # | hyperscan.HS_FLAG_UTF8
                # | hyperscan.HS_FLAG_UCP,
            )
            self.hs_dbs.append(hs_db)

        end = time.time()
        logger.debug(
            f"finished building regex for glossary {self.name} in {end - start:.2f} seconds"
        )
        logger.debug(
            f"build hs database for glossary {self.name} with {len(self.entries)} entries, hs_info: {self.hs_dbs[0].info()}"
        )
        if not self.hs_dbs:
            self.hs_dbs = None

    @classmethod
    def from_xlsx(cls, file_path: Path, target_lang_out: str) -> "Glossary":
        """从 xlsx 加载术语表。

        首行须为表头，至少含 source、target 两列（tgt_lng 可选，与 CSV 一致）。
        需要安装 openpyxl；未装则抛 ImportError 并提示。
        """
        try:
            import openpyxl
        except ImportError as e:  # noqa: BLE001
            raise ImportError(
                "加载 xlsx 术语表需要 openpyxl，请执行: pip install openpyxl"
            ) from e

        glossary_name = file_path.stem
        normalized_target_lang_out = target_lang_out.lower().replace("-", "_")
        loaded_entries: list[GlossaryEntry] = []

        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        ws = wb.active
        rows = ws.iter_rows(values_only=True)
        try:
            header = next(rows)
        except StopIteration:
            raise ValueError(f"xlsx 文件 {file_path} 为空") from None
        header = [str(c).strip().lower() if c is not None else "" for c in header]
        if "source" not in header or "target" not in header:
            raise ValueError(
                f"xlsx 文件 {file_path} 表头须含 source、target 列，实际: {header}"
            )
        col = {name: i for i, name in enumerate(header)}
        tgt_lng_idx = col.get("tgt_lng")

        for row in rows:
            source = row[col["source"]]
            target = row[col["target"]]
            if source is None or target is None:
                continue
            source = str(source).strip()
            target = str(target).strip()
            if not source or not target:
                continue
            tgt_lng = None
            if tgt_lng_idx is not None:
                raw = row[tgt_lng_idx]
                tgt_lng = str(raw).strip() if raw is not None else ""
                if tgt_lng:
                    normalized_entry_tgt_lng = tgt_lng.lower().replace("-", "_")
                    if normalized_entry_tgt_lng != normalized_target_lang_out:
                        continue
            loaded_entries.append(GlossaryEntry(source, target, tgt_lng))
        wb.close()
        return cls(name=glossary_name, entries=loaded_entries)

    @classmethod
    def from_file(cls, file_path: Path, target_lang_out: str) -> "Glossary":
        """按扩展名自动分发：.xlsx 走 from_xlsx，其余（csv/txt）走 from_csv。"""
        suffix = file_path.suffix.lower()
        if suffix == ".xlsx":
            return cls.from_xlsx(file_path, target_lang_out)
        return cls.from_csv(file_path, target_lang_out)

    @classmethod
    def from_csv(cls, file_path: Path, target_lang_out: str) -> "Glossary":
        """
        Loads glossary entries from a CSV file.
        CSV format: source,target,tgt_lng (tgt_lng is optional)
        Filters entries based on tgt_lng matching target_lang_out.
        The glossary name is derived from the CSV filename.
        """
        glossary_name = file_path.stem
        loaded_entries: list[GlossaryEntry] = []

        # Normalize target_lang_out once for comparison
        normalized_target_lang_out = target_lang_out.lower().replace("-", "_")

        try:
            with file_path.open("rb") as f:
                content = f.read()
                encoding = chardet.detect(content)["encoding"]
                buffer = io.StringIO(content.decode(encoding))
                reader = csv.DictReader(buffer, doublequote=True)
                if not all(col in reader.fieldnames for col in ["source", "target"]):
                    raise ValueError(
                        f"CSV file {file_path} must contain 'source' and 'target' columns."
                    )

                for row in reader:
                    source = row["source"]
                    target = row["target"]
                    tgt_lng = row.get("tgt_lng", None)  # Handle optional tgt_lng

                    if tgt_lng and tgt_lng.strip():
                        normalized_entry_tgt_lng = (
                            tgt_lng.strip().lower().replace("-", "_")
                        )
                        if normalized_entry_tgt_lng != normalized_target_lang_out:
                            continue  # Skip if language doesn't match

                    loaded_entries.append(GlossaryEntry(source, target, tgt_lng))
        except FileNotFoundError:
            # Or handle as per your project's error strategy, e.g., log and return empty Glossary
            raise
        except Exception as e:
            # Or handle as per your project's error strategy
            raise ValueError(
                f"Error reading or parsing CSV file {file_path}: {e}"
            ) from e

        return cls(name=glossary_name, entries=loaded_entries)

    def to_csv(self) -> str:
        """Exports the glossary entries to a CSV formatted string."""
        dict_data = [
            {
                "source": x.source,
                "target": x.target,
                "tgt_lng": x.target_language if x.target_language else "",
            }
            for x in self.entries
        ]
        buffer = io.StringIO()
        dict_writer = csv.DictWriter(
            buffer, fieldnames=["source", "target", "tgt_lng"], doublequote=True
        )
        dict_writer.writeheader()
        dict_writer.writerows(dict_data)
        return buffer.getvalue()

    def __repr__(self):
        return f"Glossary(name='{self.name}', num_entries={len(self.entries)})"

    def get_active_entries_for_text(self, text: str) -> list[tuple[str, str]]:
        """Returns a list of (original_source, target_text) tuples for terms found in the given text."""
        if not self.hs_dbs or not text:
            return []

        text = TERM_NORM_PATTERN.sub(" ", text)  # Normalize whitespace in the text
        if not text:
            return []

        active_entries = []

        def on_match(
            idx: int, _from: int, _to: int, _flags: int, _context=None
        ) -> bool | None:
            active_entries.append(self.id_lookup[idx])
            return False

        for hs_db in self.hs_dbs:
            # Scan the text with the hyperscan database
            scratch = hyperscan.Scratch(hs_db)
            hs_db.scan(text.encode("utf-8"), on_match, scratch=scratch)
        return active_entries

    def get_active_spans_for_text(
        self, text: str
    ) -> list[tuple[str, str, int, int]]:
        """返回 (source, target, start, end),偏移相对原文 text,含所有出现位置。

        供术语硬约束(glossary_hard)复用:把命中的 source 段落替换成占位符前,
        需要在原文上精确定位每一次出现的位置。

        与 get_active_entries_for_text 的差异:
        - 后者只返回"是否出现"(SINGLEMATCH,每条至多一次),丢弃位置;
        - 本方法返回**全部**出现位置(finditer),同一术语多处出现都返回,
          否则只保护首处、其余漏译。
        - 偏移相对原文 text(与硬替换操作的文本一致),否则原文含换行/多空格时
          会错位替换。

        实现:hyperscan 只做候选过滤(哪些条目可能出现,O(文本长度)与条目数无关);
        精确定位用 re.escape(source) 在原文上 finditer,只对 hyperscan 命中的
        少量候选做,不对全表逐条 finditer。跨空白变体(原文含换行/多空格,归一化
        后才能匹配的条目)无法在原文精确定位 → 自动跳过(反正也无法安全替换,
        交由软引导兜底)。返回未做去重/重叠裁剪(交给调用方处理)。
        """
        if not self.hs_dbs or not text:
            return []

        # 复用 hyperscan 命中做候选过滤(不逐条 finditer 全表)
        candidates = self.get_active_entries_for_text(text)
        if not candidates:
            return []

        spans: list[tuple[str, str, int, int]] = []
        for original_source, target_text in candidates:
            # 大小写无关,与 glossary 的 HS_FLAG_CASELESS 语义一致
            pattern = re.compile(re.escape(original_source), re.IGNORECASE)
            for m in pattern.finditer(text):
                spans.append((original_source, target_text, m.start(), m.end()))
        return spans
