"""CLI 入口。

用法：
  python -m src.main --topic "新机发布" --duration 30
  python -m src.main --topic "新机发布" --duration 30 --audience "年轻用户" --platform douyin -v
  python -m src.main --topic "新机发布" --duration 30 --json
  python -m src.main --topic "新机发布" --duration 30 --trace out.json
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from .agent.llm import ModelClient
from .agent.react_loop import Result, run
from .agent.tools import Tools

STATUS_LABEL = {
    "generated": "已生成（通过全部检查）",
    "needs_more_info": "信息不足，待补充",
    "no_material": "无可用素材",
    "tool_error": "工具失败",
    "cannot_satisfy": "无法满足（给出原因）",
    "incomplete": "未完成",
}


def _to_dict(r: Result) -> dict:
    d = {
        "status": r.status,
        "status_label": STATUS_LABEL.get(r.status, r.status),
        "topic": r.topic,
        "gap": r.gap,
        "final": r.final,
        "script": r.script.to_dict() if r.script else None,
        "estimate": r.estimate,
        "checks": r.checks,
        "trace": [asdict(s) for s in r.trace],
    }
    return d


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="video-script")
    p.add_argument("--topic", default=None, help="视频主题")
    p.add_argument("--duration", type=int, default=None, help="目标时长（秒）")
    p.add_argument("--audience", default="年轻用户", help="目标受众")
    p.add_argument("--platform", default="douyin", help="发布平台")
    p.add_argument("--style", default="简洁", help="风格")
    p.add_argument("--json", action="store_true", help="以 JSON 输出完整结果")
    p.add_argument("--trace", default=None, help="将完整 trace 写入文件")
    p.add_argument("--offline", action="store_true", help="强制离线 Mock 模型")
    p.add_argument("--verbose", "-v", action="store_true", help="打印每步决策")
    args = p.parse_args(argv)

    model = ModelClient(offline=True if args.offline else None)
    tools = Tools()

    result = run(args.topic or "", args.duration, audience=args.audience,
                 platform=args.platform, style=args.style,
                 model=model, tools=tools)
    out = _to_dict(result)
    out["model_mode"] = model.mode

    if args.trace:
        with open(args.trace, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if result.status != "incomplete" else 2

    if args.verbose:
        print(f"[model] {model.mode}")
        for s in result.trace:
            print(f"  step {s.n}: {s.action}  ← {s.reason}")
            if s.observation:
                print(f"           obs={json.dumps(s.observation, ensure_ascii=False)[:160]}")

    print(f"\n主题: {result.topic} | 目标: {args.duration}s")
    print(f"状态: {STATUS_LABEL.get(result.status, result.status)}")
    print(f"决策依据: {result.final.get('reason', '')}")

    if result.script:
        print(f"\n标题: {result.script.title}")
        print("分镜:")
        for s in result.script.shots:
            print(f"  [{s.index}] {s.duration}s | {s.visual}")
            print(f"        旁白: {s.narration}")
            print(f"        素材来源: {', '.join(s.cites) if s.cites else '(无)'}")
        print(f"预计总时长: {result.script.total_duration}s")

    if result.estimate:
        print(f"估算核对: total={result.estimate.get('total_duration')}s, "
              f"target={result.estimate.get('target_duration')}s, "
              f"over={result.estimate.get('over_target')}")

    if result.checks:
        b = result.checks.get("block_count", 0)
        print(f"规则检查: {'通过' if not result.checks.get('blocked') else f'{b} 项阻断'}")
        for v in result.checks.get("violations", []):
            print(f"  - [{v['severity']}] {v['desc']}: {v['detail']}")

    if result.gap:
        print("缺口 / 待确认:")
        for g in result.gap:
            print(f"  - {g}")

    return 0 if result.status != "incomplete" else 2


if __name__ == "__main__":
    sys.exit(main())
