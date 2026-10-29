#!/usr/bin/env python3
"""生成 examples/ 下的运行记录（Trace + CLI 实际输出）。

运行：python examples/generate_traces.py
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agent.llm import ModelClient
from src.agent.react_loop import run
from src.agent.tools import Tools

EX = ROOT / "examples"

CASES = [
    ("normal", "产品特色 支持字幕", 30, "正常生成：首估38s超时长→压缩→28s（参考实例）"),
    ("within_target", "产品特色 支持字幕", 45, "目标宽松：38s达标，无修正动作"),
    ("rewrite_hit", "画面质量", 30, "无直击→改写查询「画质」→命中P02→生成"),
    ("missing_topic", "", 30, "缺主题"),
    ("missing_duration", "产品特色", None, "缺目标时长"),
    ("no_material", "完全无关主题xyz", 30, "无素材：改写后仍无命中"),
    ("tool_failure", "触发故障的查询", 30, "工具失败：重试一次后明确失败"),
    ("cannot_satisfy", "产品特色 支持字幕", 5, "目标5s过短：压缩2轮仍21s，明确失败+缺口"),
]


def main():
    EX.mkdir(exist_ok=True)
    index = []
    for name, topic, dur, desc in CASES:
        model = ModelClient(offline=True)
        r = run(topic, dur, model=model, tools=Tools())
        record = {
            "case": name,
            "description": desc,
            "topic": topic,
            "target_duration": dur,
            "model_mode": model.mode,
            "status": r.status,
            "gap": r.gap,
            "final": r.final,
            "script": r.script.to_dict() if r.script else None,
            "estimate": r.estimate,
            "checks": r.checks,
            "trace": [vars(s) for s in r.trace],
        }
        (EX / f"trace_{name}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

        cmd = [sys.executable, "-m", "src.main", "--offline"]
        if topic:
            cmd += ["--topic", topic]
        if dur is not None:
            cmd += ["--duration", str(dur)]
        out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                             env={**__import__("os").environ,
                                  "PYTHONDONTWRITEBYTECODE": "1"})
        (EX / f"run_{name}.txt").write_text(out.stdout, encoding="utf-8")
        index.append((name, desc, r.status))
        print(f"[{name}] status={r.status}")

    lines = ["# 运行记录索引", "",
             "| 用例 | 场景 | 最终状态 | Trace | CLI 输出 |",
             "|---|---|---|---|---|"]
    for name, desc, status in index:
        lines.append(f"| {name} | {desc} | `{status}` | "
                     f"[trace_{name}.json](trace_{name}.json) | [run_{name}.txt](run_{name}.txt) |")
    (EX / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("written to", EX)


if __name__ == "__main__":
    main()
