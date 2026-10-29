"""模型接入层。

两种后端：
1. 真实模型：任意 OpenAI 兼容端点，仅用标准库 urllib。
2. 离线 Mock：无 API key 时自动降级。消费工具返回的事实做确定性决策。

边界声明：Mock 只验证流程、边界与修正逻辑，不证明真实模型决策质量。

Mock 决策链（全部由"观察到的工具事实"驱动，无预判跳过）：
  检索(0命中→改写一次→仍0→请求补充；失败→重试一次)
  → 起草(首稿，时长偏长属正常)
  → 估算 → [观察到 over_target] → 压缩(系数 0.74, 最低 2s) → 重估
  → 检查 → [观察到 block] → 修正旁白 → 再检查
  → 收尾(generated，守卫要求检查干净)
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

MAX_REVISIONS = 2  # 压缩/修正的最大轮数，超出则明确失败

COMPRESS_FACTOR = 0.74
MIN_SHOT = 2


class ModelClient:
    def __init__(self, offline: bool | None = None):
        self.base_url = os.environ.get("MODEL_BASE_URL", "").rstrip("/")
        self.api_key = os.environ.get("MODEL_API_KEY", "")
        self.model = os.environ.get("MODEL_NAME", "gpt-4o-mini")
        if offline is None:
            offline = not (self.base_url and self.api_key)
        self.offline = offline

    @property
    def mode(self) -> str:
        return "offline-mock" if self.offline else f"live:{self.model}"

    def decide(self, system: str, messages: list[dict]) -> dict:
        if self.offline:
            return OfflineMock.decide(messages)
        return self._call_live(system, messages)

    def _call_live(self, system: str, messages: list[dict]) -> dict:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.loads(resp.read().decode())
            content = body["choices"][0]["message"]["content"]
        except urllib.error.URLError as e:
            raise RuntimeError(f"model_call_failed: {e}") from e
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            raise RuntimeError(f"model_response_malformed: {e}") from e
        try:
            return _parse_action(content)
        except (json.JSONDecodeError, ValueError) as e:
            # 非 JSON 输出（真实模型常见）：作为契约违规返回，让循环层观察并自纠
            return {"action": "<invalid>", "args": {},
                    "reason": f"模型返回非 JSON：{str(content)[:80]}"}


def _parse_action(content: str) -> dict:
    s = content.strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.endswith("```"):
            s = s[:-3]
    start, end = s.find("{"), s.rfind("}")
    if start >= 0 and end > start:
        s = s[start:end + 1]
    return json.loads(s)


class OfflineMock:
    @staticmethod
    def decide(messages: list[dict]) -> dict:
        f = OfflineMock._collect(messages)

        # 缺关键信息 → 请求补充（不调工具）
        if f["missing"]:
            return {"action": "finish", "status": "needs_more_info",
                    "reason": "关键信息缺失：" + "、".join(f["missing"]),
                    "gap": ["请补充：" + "、".join(f["missing"])]}

        # 步骤1：检索（失败重试一次；无命中改写一次）
        if not f["searches"]:
            return {"action": "search_materials",
                    "args": {"query": f["topic"]},
                    "reason": "检索产品素材"}
        last = f["searches"][-1]
        if not last.get("ok"):
            if len(f["searches"]) <= 1:
                return {"action": "search_materials",
                        "args": {"query": f["topic"]},
                        "reason": "素材检索失败，重试一次"}
            return {"action": "finish", "status": "tool_error",
                    "reason": "素材检索连续失败，无法生成有依据的脚本",
                    "gap": ["检索工具不可用，请稍后重试"]}

        if not last.get("hits"):
            # 无命中 → 模型决定：改写查询（依据 rewrite 知识表）再试一次
            if len(f["searches"]) == 1:
                rq = OfflineMock._rewrite(f["topic"])
                if rq != f["topic"]:
                    return {"action": "search_materials", "args": {"query": rq},
                            "reason": f"检索无结果，改写查询「{rq}」再试"}
            return {"action": "finish", "status": "no_material",
                    "reason": "检索无结果且改写后仍无命中",
                    "gap": ["未找到相关素材，请补充素材或调整主题"]}

        # 步骤2：起草——脚本内容由检索到的素材事实派生（非硬编码）
        if not f["drafts"] and not f["estimates"]:
            shots = OfflineMock._draft_from_hits(last["hits"], f["topic"])
            return {"action": "draft_script",
                    "args": {"title": f"{f['topic']}｜{f['target']}秒短片",
                             "shots": shots},
                    "reason": f"依据 {len(last['hits'])} 条命中素材起草脚本"
                              "（旁白取自素材事实，首稿待估算）"}

        script = f["script"]  # draft_script 已在循环里落为事实

        # 步骤3：估算（草稿后、或压缩/修正后都要重估）
        if not f["estimates"] or f["need_reestimate"]:
            return {"action": "estimate_duration",
                    "args": {"shots": script["shots"], "target": f["target"]},
                    "reason": "估算总时长并检查是否超目标"}

        est = f["estimates"][-1]
        # 观察到超时长事实 → 压缩后重估（修正闭环的核心）
        if est.get("over_target"):
            if f["compressions"] >= MAX_REVISIONS:
                return {"action": "finish", "status": "cannot_satisfy",
                        "reason": f"压缩 {MAX_REVISIONS} 轮后仍为 "
                                  f"{est['total_duration']}s > 目标 {f['target']}s，无法满足",
                        "gap": [f"目标时长 {f['target']}s 过短，"
                                f"当前最短可行 {est['total_duration']}s，请放宽时长"]}
            new_shots = OfflineMock._compress(script["shots"])
            return {"action": "revise_script",
                    "args": {"title": script["title"], "shots": new_shots,
                             "reason": f"总时长 {est['total_duration']}s 超目标 "
                                       f"{f['target']}s（超出 {est['overflow']}s），压缩分镜"},
                    "reason": f"观察到超时长 {est['total_duration']}s > {f['target']}s，"
                              f"压缩各镜时长后重新估算"}

        # 步骤4：规则检查
        if not f["checks"]:
            return {"action": "check_rules",
                    "args": {"script": {"shots": script["shots"]}},
                    "reason": "检查品牌规则与事实依据"}

        check = f["checks"][-1]
        if check.get("blocked"):
            if f["fixes"] >= MAX_REVISIONS:
                return {"action": "finish", "status": "cannot_satisfy",
                        "reason": "规则违规修正后仍不通过，无法满足",
                        "gap": ["存在无法自动修正的规则违规，请人工调整"]}
            title = OfflineMock._fix_title(script.get("title", ""),
                                           check["violations"])
            fixed = OfflineMock._fix_violations(script["shots"],
                                                check["violations"])
            return {"action": "revise_script",
                    "args": {"title": title, "shots": fixed,
                             "reason": "修正规则违规"},
                    "reason": "观察到规则违规，修正标题与旁白后重新检查"}

        # 步骤5：收尾（检查干净才允许 generated——由循环层守卫强制）
        return {"action": "finish", "status": "generated",
                "reason": f"脚本通过全部检查，总时长 {est['total_duration']}s",
                "gap": []}

    # --- 起草：由命中素材事实派生（旁白逐字取自 matched_facts，可追溯）---
    @staticmethod
    def _draft_from_hits(hits: list[dict], topic: str) -> list[dict]:
        primary = hits[0]
        feature_hits = hits[:2]
        shots = [{
            "index": 1, "duration": 4,
            "visual": f"开场：{topic}产品亮相",
            "narration": "先看产品亮点",
            "cites": [primary["id"]],
        }]
        for h in feature_hits:
            fact = (h.get("matched_facts") or h.get("facts") or [""])[0]
            shots.append({
                "index": len(shots) + 1, "duration": 12,
                "visual": f"特写：{fact}功能演示",
                "narration": fact,  # 逐字取自素材事实，保证可追溯
                "cites": [h["id"]],
            })
        if len(feature_hits) == 1:
            # 单一命中时补一个演示镜（引用同一素材，旁白不含新宣称）
            fact = (primary.get("matched_facts") or primary.get("facts") or [""])[0]
            shots.append({
                "index": len(shots) + 1, "duration": 12,
                "visual": f"演示：{fact}使用场景",
                "narration": "功能细节演示",
                "cites": [primary["id"]],
            })
        shots.append({
            "index": len(shots) + 1, "duration": 10,
            "visual": "收尾：产品 LOGO 与引导关注",
            "narration": "更多内容请关注后续",
            "cites": [primary["id"]],
        })
        return shots

    # --- 压缩：单调递减，保证收敛 ---
    @staticmethod
    def _compress(shots: list[dict]) -> list[dict]:
        out = []
        for s in shots:
            d = max(MIN_SHOT, round(int(s["duration"]) * COMPRESS_FACTOR))
            ns = dict(s)
            ns["duration"] = d
            out.append(ns)
        return out

    @staticmethod
    def _fix_violations(shots, violations) -> list[dict]:
        """按违规事实修正分镜：无依据的数值/效果词从旁白移除，
        违禁词从旁白与画面描述一并移除——否则复检仍会命中。"""
        import re as _re
        out = []
        for s in shots:
            ns = dict(s)
            n = str(ns.get("narration", ""))
            vis = str(ns.get("visual", ""))
            for v in violations:
                detail = v.get("detail", "")
                m = _re.search(r"「([^」]+)」", detail)
                if not m:
                    continue
                bad = m.group(1)
                if v["rule_id"] == "R_GROUNDED_NUMBER":
                    # 删除该数值（含可能的百分号）
                    n = _re.sub(_re.escape(bad) + r"%?", "", n)
                elif v["rule_id"] == "R_GROUNDED_EFFECT":
                    # 删除"效果词"到下一个标点为止的短语
                    n = _re.sub(_re.escape(bad) + r"[^，。！？]*", "", n)
                elif v["rule_id"] == "R_BANNED_WORDS":
                    n = n.replace(bad, "")
                    vis = vis.replace(bad, "")
                elif v["rule_id"] == "R_REQUIRE_CITE":
                    pass  # 补引用由起草阶段保证
            n = n.strip("，。！？ ").strip()
            ns["narration"] = n or "产品功能展示"
            ns["visual"] = vis.strip() or "产品展示画面"
            out.append(ns)
        return out

    @staticmethod
    def _fix_title(title: str, violations) -> str:
        """按违规事实修正标题：移除违禁词。"""
        out = title
        for v in violations:
            if v["rule_id"] != "R_BANNED_WORDS":
                continue
            import re as _re
            m = _re.search(r"「([^」]+)」", v.get("detail", ""))
            if m:
                out = out.replace(m.group(1), "")
        return out.strip("｜| 　") or title

    @staticmethod
    def _rewrite(topic: str) -> str:
        from .models import load_json
        try:
            rw = load_json("synonyms.json")["rewrites"]
        except Exception:
            return topic
        q = topic
        for src, dst in rw.items():
            q = q.replace(src, dst)
        return q

    # --- 事实收集 ---
    @staticmethod
    def _collect(messages: list[dict]) -> dict:
        topic, target, missing = "", 0, []
        searches, estimates, checks = [], [], []
        drafts, revisions = [], []
        script = {"title": "", "shots": []}
        need_reestimate = False
        compress_count = 0
        fix_count = 0
        for m in messages:
            if m.get("role") == "assistant":
                try:
                    a = _parse_action(m.get("content", ""))
                except Exception:
                    continue
                name = a.get("action")
                if name == "draft_script":
                    drafts.append(a.get("args", {}))
                    script = dict(a.get("args", {}))
                    need_reestimate = False
                elif name == "revise_script":
                    args = a.get("args", {})
                    revisions.append(args)
                    script = dict(args)
                    need_reestimate = True
                    # 修正后旧检查/旧估算作废——必须基于新脚本重新执行
                    checks = []
                    estimates = []
                    if "压缩" in str(args.get("reason", "")):
                        compress_count += 1
                    else:
                        fix_count += 1
                elif name == "estimate_duration":
                    need_reestimate = False
            if m.get("role") in ("tool", "user"):
                try:
                    obs = json.loads(m.get("content", ""))
                except Exception:
                    continue
                if not isinstance(obs, dict):
                    continue
                if "topic" in obs and "missing" in obs:
                    topic = obs["topic"] or ""
                    target = obs.get("target_duration") or 0
                    missing = obs["missing"]
                elif "hits" in obs or obs.get("tool") == "search_materials":
                    searches.append(obs)
                elif "over_target" in obs:
                    estimates.append(obs)
                elif "blocked" in obs:
                    checks.append(obs)
        return {"topic": topic, "target": target, "missing": missing,
                "searches": searches, "estimates": estimates, "checks": checks,
                "drafts": drafts, "revisions": revisions, "script": script,
                "need_reestimate": need_reestimate,
                "compressions": compress_count,
                "fixes": fix_count}
