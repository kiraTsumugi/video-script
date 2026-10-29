"""核心数据结构。

设计原则：
- 事实可追溯由结构保证：每个 Shot 必须携带引用素材 ID（cites），
  规则检查据此逐条核对旁白宣称与素材事实。
- 状态正交：generated / needs_more_info / no_material / tool_error / cannot_satisfy / incomplete。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def load_json(name: str, data_dir: Path | None = None) -> Any:
    with open((data_dir or DATA_DIR) / name, "r", encoding="utf-8") as f:
        return json.load(f)


@dataclass
class Material:
    id: str
    name: str
    facts: list[str]


@dataclass
class Shot:
    """一个分镜。cites 为引用的素材 ID 列表——事实可追溯的最小单元。"""
    index: int
    duration: int
    visual: str          # 画面描述
    narration: str       # 旁白/字幕
    cites: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Script:
    title: str
    topic: str
    target_duration: int
    shots: list[Shot] = field(default_factory=list)

    @property
    def total_duration(self) -> int:
        return sum(s.duration for s in self.shots)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["total_duration"] = self.total_duration
        return d


@dataclass
class Violation:
    rule_id: str
    desc: str
    severity: str
    detail: str = ""
    shot_index: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)
