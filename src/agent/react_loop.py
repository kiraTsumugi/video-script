"""ReAct 决策循环。

循环骨架：模型输出动作 → 执行工具（或记录脚本状态）→ 观察结果喂回模型 → 模型决定下一步。
硬约束：
- 步数上限 MAX_STEPS，达到即返回 incomplete。
- status=generated 必须通过最后一次规则检查——循环层守卫，防编造事实漏出。
- draft_script / revise_script 是模型的"决策动作"（更新脚本状态），不是工具。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .llm import ModelClient
from .models import Script, Shot
from .tools import Tools

MAX_STEPS = 12
TOOL_ACTIONS = {"search_materials", "estimate_duration", "check_rules"}
STATE_ACTIONS = {"draft_script", "revise_script"}

SYSTEM_PROMPT = """你是"短视频脚本 Agent"。职责：生成有事实依据、满足时长限制的可编辑脚本。

只能通过 JSON 动作交互：
- {"action":"search_materials","args":{"query":"..."},"reason":"..."}
- {"action":"draft_script","args":{"title":"...","shots":[{"index":1,"duration":4,"visual":"...","narration":"...","cites":["P01"]}]},"reason":"..."}
- {"action":"revise_script","args":{"title":"...","shots":[...]},"reason":"..."}
- {"action":"estimate_duration","args":{"shots":[...],"target":30},"reason":"..."}
- {"action":"check_rules","args":{"script":{"shots":[...]}},"reason":"..."}
结束用：{"action":"finish","status":"generated|needs_more_info|no_material|tool_error|cannot_satisfy|incomplete","reason":"...","gap":["..."]}

铁律：
1. 旁白中的数值与效果宣称必须能在引用素材的事实中找到依据，禁止编造。
2. 总时长超出目标时必须压缩并重新估算；修正后必须重新检查。
3. 每次只输出一个 JSON 动作。"""


@dataclass
class TraceStep:
    n: int
    action: str
    args: dict
    reason: str
    observation: dict | None = None


@dataclass
class Result:
    status: str
    topic: str
    script: Script | None = None
    checks: dict | None = None
    estimate: dict | None = None
    gap: list[str] = field(default_factory=list)
    trace: list[TraceStep] = field(default_factory=list)
    final: dict = field(default_factory=dict)


def run(topic: str, target_duration: int | None, audience: str = "general",
        platform: str = "douyin", style: str = "简洁",
        model: ModelClient | None = None, tools: Tools | None = None) -> Result:
    model = model or ModelClient()
    tools = tools or Tools()

    missing = []
    if not topic:
        missing.append("主题")
    if target_duration is None:
        missing.append("目标时长")

    intent = json.dumps({
        "task": f"为「{topic}」生成 {target_duration} 秒短视频脚本",
        "topic": topic, "target_duration": target_duration,
        "audience": audience, "platform": platform, "style": style,
        "missing": missing,
    }, ensure_ascii=False)

    messages: list[dict] = [{"role": "user", "content": intent}]
    trace: list[TraceStep] = []
    current_script: Script | None = None
    last_check: dict | None = None
    last_estimate: dict | None = None

    for step_no in range(1, MAX_STEPS + 1):
        action = model.decide(SYSTEM_PROMPT, messages)

        if not isinstance(action, dict):
            trace.append(TraceStep(step_no, "<invalid>", {}, "模型返回非结构化输出",
                                   {"error": "invalid_action"}))
            messages.append({"role": "user", "content": json.dumps(
                {"error": "invalid_action", "hint": "只输出一个 JSON 动作"})})
            continue

        name = action.get("action", "")
        args = action.get("args", {}) or {}
        reason = action.get("reason", "")
        ts = TraceStep(step_no, name, args, reason)

        # 结束：generated 需通过守卫
        if name == "finish":
            status = action.get("status", "incomplete")
            if status == "generated":
                if current_script is None or last_check is None:
                    ts.observation = {"error": "guard_violation",
                                      "detail": "未产出脚本或未经检查"}
                    trace.append(ts)
                    return Result(status="incomplete", topic=topic,
                                  script=current_script, gap=["生成脚本前未完成检查，已拦截"],
                                  trace=trace, final={"reason": "generated 守卫拦截"})
                if last_check.get("blocked"):
                    ts.observation = {"error": "guard_violation",
                                      "detail": "最后一次检查有 block 违规"}
                    trace.append(ts)
                    return Result(status="incomplete", topic=topic,
                                  script=current_script, checks=last_check,
                                  gap=["存在未修正的规则违规，已拦截"],
                                  trace=trace, final={"reason": "generated 守卫拦截"})
            ts.observation = {"finished": True, "status": status}
            trace.append(ts)
            return Result(status=status, topic=topic, script=current_script,
                          checks=last_check, estimate=last_estimate,
                          gap=action.get("gap", []), trace=trace, final=action)

        # 工具动作
        if name in TOOL_ACTIONS:
            obs = _dispatch_tool(tools, name, args, current_script, target_duration)
            if name == "estimate_duration" and obs.get("ok"):
                last_estimate = obs
            if name == "check_rules" and obs.get("ok"):
                last_check = obs
            ts.observation = obs
            trace.append(ts)
            messages.append({"role": "assistant", "content": json.dumps(action)})
            messages.append({"role": "user", "content": json.dumps(obs, ensure_ascii=False)})
            continue

        # 状态动作：起草/修正脚本
        if name in STATE_ACTIONS:
            script, err = _build_script(args, topic, target_duration, current_script)
            if err:
                ts.observation = {"ok": False, "error": err}
            else:
                current_script = script
                # 修正后上一次检查/估算作废（修正必须重新检查）
                last_check = None
                ts.observation = {"ok": True, "script_saved": True,
                                  "total_duration": script.total_duration,
                                  "shot_count": len(script.shots)}
            trace.append(ts)
            messages.append({"role": "assistant", "content": json.dumps(action)})
            messages.append({"role": "user", "content": json.dumps(
                ts.observation, ensure_ascii=False)})
            continue

        # 未知动作
        ts.observation = {"error": "unknown_action", "action": name}
        trace.append(ts)
        messages.append({"role": "user", "content": json.dumps(
            {"error": "unknown_action", "action": name, "hint": "重选合法动作"})})

    trace.append(TraceStep(MAX_STEPS + 1, "abort", {}, "达到步数上限",
                           {"error": "max_steps_exceeded"}))
    return Result(status="incomplete", topic=topic, script=current_script,
                  trace=trace, gap=["执行步数超限，未完成任务"],
                  final={"reason": f"达到最大步数 {MAX_STEPS} 仍未完成"})


def _dispatch_tool(tools: Tools, name: str, args: dict,
                   current_script: Script | None,
                   target_duration: int | None) -> dict:
    if name == "search_materials":
        return tools.search_materials(args.get("query", ""))
    if name == "estimate_duration":
        # 循环层强制使用当前脚本——不接受模型传入的任意 shots（防绕过）
        if current_script is None:
            return {"ok": False, "error": "no_script",
                    "message": "尚未产出脚本，无法估算"}
        shots = [s.to_dict() for s in current_script.shots]
        return tools.estimate_duration(shots, target_duration or 0)
    if name == "check_rules":
        # 同上：检查对象必须是当前脚本（含标题），确保"检查通过"与"输出内容"是同一份
        if current_script is None:
            return {"ok": False, "error": "no_script",
                    "message": "尚未产出脚本，无法检查"}
        return tools.check_rules({
            "title": current_script.title,
            "shots": [s.to_dict() for s in current_script.shots]})
    return {"ok": False, "error": "unknown_action", "action": name}


def _build_script(args: dict, topic: str, target: int | None,
                  prev: Script | None) -> tuple[Script | None, str | None]:
    shots_raw = args.get("shots")
    if not isinstance(shots_raw, list) or not shots_raw:
        return None, "empty_shots"
    shots = []
    for i, s in enumerate(shots_raw, 1):
        if not isinstance(s, dict):
            return None, "bad_shot"
        try:
            duration = int(s.get("duration", 0))
        except (TypeError, ValueError):
            return None, "bad_duration"
        shots.append(Shot(index=int(s.get("index", i)), duration=duration,
                          visual=str(s.get("visual", "")),
                          narration=str(s.get("narration", "")),
                          cites=list(s.get("cites", []) or [])))
    title = args.get("title") or (prev.title if prev else f"{topic}脚本")
    script = Script(title=title, topic=topic,
                    target_duration=target if target is not None else 0,
                    shots=shots)
    return script, None
