# -*- coding: utf-8 -*-
"""v3(babeldoc)翻译参数与任务记录。

从 no_ocr/v3/service/models.py 解耦而来:去掉 TaskStatus 枚举与 tasks_store 耦合,
run_translate 直接操作 V3Record 对象,状态由调用方(orchestrate)管理。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TranslateParams:
    """v3 翻译参数。LLM 配置由服务端 config 兜底,客户端可不传。"""

    lang_in: str = "en"
    lang_out: str = "zh"
    qps: int = 4

    # LLM 配置(可选,缺省读 config.settings)
    openai_model: str | None = None
    openai_base_url: str | None = None
    openai_api_key: str | None = None

    # 翻译行为
    no_mono: bool = False
    no_watermark: bool = True
    enable_json_mode_if_requested: bool = True
    send_dashscope_header: bool = False
    no_send_temperature: bool = False
    openai_reasoning: str | None = None
    openai_thinking: str | None = None

    # 自定义译者系统提示(留空用引擎默认)
    custom_system_prompt: str | None = None

    # 术语硬约束
    glossary_hard: bool = False

    def resolved_model(self, default: str) -> str:
        return self.openai_model or default

    def resolved_base_url(self, default: str) -> str | None:
        return self.openai_base_url or default

    def resolved_api_key(self, default: str) -> str:
        return self.openai_api_key or default


@dataclass
class V3Record:
    """v3 翻译任务记录(进程内,run_translate 操作它)。

    input_pdf:输入 PDF 路径
    work_dir:工作目录(产物写 work_dir/output/,tracking 写 work_dir/working/)
    glossary_path:可选术语表路径(CSV/XLSX),无则 None
    result_files:产物文件名列表(跑完回填)
    progress / stage:进度回调回填
    error:失败时回填
    """

    input_pdf: Path
    work_dir: Path
    params: TranslateParams
    glossary_path: Path | None = None
    result_files: list[str] = field(default_factory=list)
    progress: float = 0.0
    stage: str = ""
    error: str | None = None
