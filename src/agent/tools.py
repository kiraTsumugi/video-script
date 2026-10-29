"""业务工具层。

铁律：工具只返回"事实"，绝不返回"决策"。
- search_materials 返回命中的素材事实（或 0 结果 / 故障，均为事实）。
- estimate_duration 返回总时长与是否超出目标（事实），不改脚本。
- check_rules 返回违规列表（事实），不判断"该怎么办"。

工具不判断"该不该压缩""要不要改写查询"——那是模型的活。
"""
from __future__ import annotations

import re
from pathlib import Path

from .models import Material, Violation, load_json


class Tools:
    def __init__(self, data_dir: Path | None = None):
        raw = load_json("materials.json", data_dir)
        self._materials: dict[str, Material] = {
            m["id"]: Material(id=m["id"], name=m["name"], facts=m["facts"])
            for m in raw["materials"]
        }
        self._rules: list[dict] = load_json("brand_rules.json", data_dir)["rules"]
        self._rewrites: dict[str, str] = load_json("synonyms.json", data_dir)["rewrites"]
        self._fail = raw.get("_fail_injection", {})

    # --- 1. 素材检索：返回命中素材 ----------------------------------------
    def search_materials(self, query: str) -> dict:
        if self._fail.get("tool") == "search_materials" and any(
                k in query for k in self._fail.get("keywords", [])):
            return {"ok": False, "error": "tool_error", "tool": "search_materials",
                    "message": "素材检索服务暂时不可用（故障注入）"}

        # 事实：查询片段与素材 facts 的包含匹配（不自动改写——改写是模型的决策）
        segments = self._segments(query)
        hits = []
        for m in self._materials.values():
            matched = [fact for fact in m.facts
                       if any(seg in fact or fact in seg for seg in segments)]
            if matched:
                hits.append({"id": m.id, "name": m.name, "facts": m.facts,
                             "matched_facts": matched})
        return {"ok": True, "query": query, "segments": segments,
                "hits": hits,
                "all_material_ids": [m.id for m in self._materials.values()]}

    def _segments(self, query: str) -> list[str]:
        """按标点/空白切分的查询片段（≥2 字整词匹配，不做滑窗）。

        整词匹配可避免「支持字母」经 2 字滑窗误命中「支持字幕」；
        但仍为字面近似：短片段（如"支持"）会命中所有含该词的素材。
        """
        parts = re.split(r"[，。！？、；：,.;:!?\s]+", query)
        return [p.strip() for p in parts if len(p.strip()) >= 2]

    def get_material(self, mid: str) -> Material | None:
        return self._materials.get(mid)

    # --- 2. 时长估算：返回时长事实 ----------------------------------------
    def estimate_duration(self, shots: list[dict], target: int) -> dict:
        """shots: [{"index":1,"duration":4,...}, ...]（脚本草稿由模型给出）"""
        if not isinstance(shots, list) or not shots:
            return {"ok": False, "error": "empty_shots"}
        try:
            durations = [int(s.get("duration", 0)) for s in shots]
        except (TypeError, ValueError):
            return {"ok": False, "error": "bad_duration"}
        total = sum(durations)
        return {"ok": True, "total_duration": total, "target_duration": target,
                "over_target": total > target, "overflow": max(0, total - target),
                "shot_durations": durations}

    # --- 3. 规则检查：返回违规事实 ----------------------------------------
    def check_rules(self, script: dict) -> dict:
        """script: {"shots":[{index,duration,narration,cites},...]}
        逐条规则核对；grounded 规则校验旁白宣称是否有素材事实支撑。"""
        shots = script.get("shots", [])
        violations: list[Violation] = []
        for r in self._rules:
            t = r["type"]
            if t == "shot_min_duration":
                for s in shots:
                    if int(s.get("duration", 0)) < r["value"]:
                        violations.append(Violation(
                            r["id"], r["desc"], r["severity"],
                            f"第{s.get('index')}镜 {s.get('duration')}s < {r['value']}s",
                            s.get("index")))
            elif t == "shot_max_duration":
                for s in shots:
                    if int(s.get("duration", 0)) > r["value"]:
                        violations.append(Violation(
                            r["id"], r["desc"], r["severity"],
                            f"第{s.get('index')}镜 {s.get('duration')}s > {r['value']}s",
                            s.get("index")))
            elif t == "require_cite":
                for s in shots:
                    cites = s.get("cites") or []
                    if not cites:
                        violations.append(Violation(
                            r["id"], r["desc"], r["severity"],
                            f"第{s.get('index')}镜未引用素材", s.get("index")))
            elif t == "grounded_number":
                for s in shots:
                    nums = re.findall(r"\d+(?:\.\d+)?", s.get("narration", ""))
                    if not nums:
                        continue
                    facts_text = self._facts_text(s.get("cites", []))
                    for n in nums:
                        if n not in facts_text:
                            violations.append(Violation(
                                r["id"], r["desc"], r["severity"],
                                f"第{s.get('index')}镜旁白数值「{n}」无素材依据",
                                s.get("index")))
            elif t == "grounded_effect":
                for s in shots:
                    facts_text = self._facts_text(s.get("cites", []))
                    for w in r["words"]:
                        if w in s.get("narration", "") and w not in facts_text:
                            violations.append(Violation(
                                r["id"], r["desc"], r["severity"],
                                f"第{s.get('index')}镜效果宣称「{w}」无素材依据",
                                s.get("index")))
            elif t == "banned_words":
                # 输出任何位置都不得残留违禁词：逐镜（旁白+画面）与标题
                for w in r["words"]:
                    if w in script.get("title", ""):
                        violations.append(Violation(
                            r["id"], r["desc"], r["severity"],
                            f"标题出现违禁词「{w}」"))
                for s in shots:
                    for w in r["words"]:
                        if w in s.get("narration", "") or w in s.get("visual", ""):
                            violations.append(Violation(
                                r["id"], r["desc"], r["severity"],
                                f"第{s.get('index')}镜出现违禁词「{w}」", s.get("index")))
            elif t == "max_shots":
                if len(shots) > r["value"]:
                    violations.append(Violation(
                        r["id"], r["desc"], r["severity"],
                        f"分镜数 {len(shots)} > {r['value']}"))
        blocked = [v for v in violations if v.severity == "block"]
        return {"ok": True,
                "violations": [v.to_dict() for v in violations],
                "blocked": len(blocked) > 0,
                "block_count": len(blocked)}

    def _facts_text(self, cites: list[str]) -> str:
        parts = []
        for cid in cites or []:
            m = self._materials.get(cid)
            if m:
                parts.extend(m.facts)
        return " ".join(parts)
