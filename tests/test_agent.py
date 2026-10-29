"""可执行测试：主流程修正闭环、事实派生、匹配精度、异常输入与安全守卫。

运行：python -m unittest discover -s tests -v
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent.llm import MAX_REVISIONS, ModelClient
from src.agent.react_loop import MAX_STEPS, run
from src.agent.tools import Tools

DATA = Path(__file__).resolve().parents[1] / "data"


def go(topic, duration, tools=None, model=None):
    tools = tools or Tools()
    model = model or ModelClient(offline=True)
    return run(topic, duration, model=model, tools=tools), tools


class TestMainFlow(unittest.TestCase):
    """正常生成 + 超时长修正闭环（参考实例：38s → 修正 → 28s）。"""

    def test_normal_generation_with_revision(self):
        r, _ = go("产品特色 支持字幕", 30)
        self.assertEqual(r.status, "generated")
        self.assertEqual(r.script.total_duration, 28)

    def test_canonical_trace_sequence(self):
        r, _ = go("产品特色 支持字幕", 30)
        acts = [s.action for s in r.trace]
        self.assertEqual(acts, [
            "search_materials", "draft_script", "estimate_duration",
            "revise_script", "estimate_duration", "check_rules", "finish"])

    def test_revision_triggered_by_observed_fact(self):
        """压缩必须由「观察到超时长事实」触发：首估 38s > 30s。"""
        r, _ = go("产品特色 支持字幕", 30)
        first_est = next(s.observation for s in r.trace
                         if s.action == "estimate_duration")
        self.assertEqual(first_est["total_duration"], 38)
        self.assertTrue(first_est["over_target"])
        self.assertEqual(first_est["overflow"], 8)

    def test_reestimate_after_revision(self):
        """修正后必须重新估算：两次估算分别为 38 / 28。"""
        r, _ = go("产品特色 支持字幕", 30)
        ests = [s.observation["total_duration"] for s in r.trace
                if s.action == "estimate_duration"]
        self.assertEqual(ests, [38, 28])

    def test_display_total_matches_estimate(self):
        """展示的总时长必须等于最终估算值（不一致视为未完成）。"""
        r, _ = go("产品特色 支持字幕", 30)
        self.assertEqual(r.script.total_duration, r.estimate["total_duration"])

    def test_within_target_no_revision(self):
        """目标 45s 时首稿 38s 已达标，不得出现修正动作。"""
        r, _ = go("产品特色 支持字幕", 45)
        self.assertEqual(r.status, "generated")
        self.assertEqual(r.script.total_duration, 38)
        self.assertNotIn("revise_script", [s.action for s in r.trace])

    def test_checks_clean_when_generated(self):
        r, _ = go("产品特色 支持字幕", 30)
        self.assertEqual(r.checks.get("blocked"), False)


class TestFactDerivation(unittest.TestCase):
    """评审点：检索结果必须进入生成逻辑；脚本随素材变化。"""

    def test_narration_derived_from_material_facts(self):
        r, _ = go("产品特色 支持字幕", 30)
        self.assertTrue(any("支持字幕" in s.narration for s in r.script.shots))
        # 旁白逐字来自素材事实（可追溯）
        facts = Tools().get_material("P01").facts
        for s in r.script.shots:
            for cite in s.cites:
                self.assertIn(cite, ["P01", "P02", "P03", "P04"])

    def test_every_shot_cited(self):
        r, _ = go("产品特色 支持字幕", 30)
        self.assertTrue(all(s.cites for s in r.script.shots))

    def test_swap_material_changes_script(self):
        """替换素材内容：脚本旁白随之改变，无旧内容残留。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for f in ("synonyms.json", "brand_rules.json"):
                shutil.copy(DATA / f, tmp / f)
            docs = json.loads((DATA / "materials.json").read_text(encoding="utf-8"))
            for m in docs["materials"]:
                if m["id"] == "P01":
                    m["facts"] = ["支持手势控制"]
            (tmp / "materials.json").write_text(
                json.dumps(docs, ensure_ascii=False), encoding="utf-8")
            r, _ = go("产品特色 支持手势控制", 30, tools=Tools(data_dir=tmp))
            self.assertEqual(r.status, "generated")
            self.assertTrue(any("手势控制" in s.narration for s in r.script.shots))
            self.assertFalse(any("字幕" in s.narration for s in r.script.shots))


class TestMatchingPrecision(unittest.TestCase):
    """评审点：检索匹配不得过宽（滑窗导致的跨词误命中）。"""

    def test_no_sliding_window_cross_hit(self):
        t = Tools()
        # "支持字母" 曾在 2 字滑窗下误命中 "支持字幕"，整词匹配后应为 0 命中
        self.assertEqual(t.search_materials("支持字母")["hits"], [])

    def test_exact_fact_hit(self):
        t = Tools()
        self.assertEqual([h["id"] for h in t.search_materials("支持字幕")["hits"]],
                         ["P01"])
        self.assertEqual([h["id"] for h in t.search_materials("续航")["hits"]],
                         ["P04"])

    def test_no_hit_returns_empty_and_all_ids(self):
        t = Tools()
        r = t.search_materials("完全无关主题xyz")
        self.assertEqual(r["hits"], [])
        self.assertEqual(r["all_material_ids"],
                         ["P01", "P02", "P03", "P04", "P05"])

    def test_illegal_material_blocks_and_fixes(self):
        """P05 自带违禁词素材：命中 → 起草违规 → 修正 → 重检通过。"""
        r, _ = go("世界级音质", 30)
        self.assertEqual(r.status, "generated")
        acts = [s.action for s in r.trace]
        # 规则违规链：check(blocked) → revise → estimate → check(clean) → finish
        self.assertEqual(acts.count("check_rules"), 2)
        self.assertEqual(acts.count("revise_script"), 2)  # 1 压缩 + 1 规则修正
        # 违禁词不得残留在标题/画面/旁白任何位置
        self.assertNotIn("世界级", r.script.title)
        for s in r.script.shots:
            self.assertNotIn("世界级", s.narration)
            self.assertNotIn("世界级", s.visual)

    def test_illegal_material_fix_respects_budget(self):
        """规则修正不得破坏时长约束：修正后总时长仍在目标内。"""
        r, _ = go("世界级音质", 30)
        self.assertLessEqual(r.script.total_duration, 30)


class TestRewritePath(unittest.TestCase):
    def test_rewrite_then_hit(self):
        """无直击 → 模型改写查询「画面质量→画质」 → 命中 P02。"""
        r, _ = go("画面质量", 30)
        searches = [s for s in r.trace if s.action == "search_materials"]
        self.assertEqual(len(searches), 2)
        self.assertEqual(searches[1].args["query"], "画质")
        self.assertEqual(r.status, "generated")


class TestMissingInfo(unittest.TestCase):
    def test_missing_topic(self):
        r, _ = go("", 30)
        self.assertEqual(r.status, "needs_more_info")
        self.assertTrue(any("主题" in g for g in r.gap))

    def test_missing_duration(self):
        r, _ = go("产品特色", None)
        self.assertEqual(r.status, "needs_more_info")
        self.assertTrue(any("时长" in g for g in r.gap))


class TestNoMaterial(unittest.TestCase):
    def test_no_material_after_rewrite(self):
        r, _ = go("完全无关主题xyz", 30)
        self.assertEqual(r.status, "no_material")
        self.assertEqual(r.script, None)


class TestToolFailure(unittest.TestCase):
    def test_tool_error_after_retry(self):
        r, _ = go("触发故障的查询", 30)
        self.assertEqual(r.status, "tool_error")
        searches = [s for s in r.trace if s.action == "search_materials"]
        self.assertEqual(len(searches), 2)  # 1 次初始 + 1 次重试


class TestCannotSatisfy(unittest.TestCase):
    def test_impossible_target_explicit_failure(self):
        """目标 5s 过短：压缩 MAX_REVISIONS 轮后仍超 → 明确失败 + 缺口。"""
        r, _ = go("产品特色 支持字幕", 5)
        self.assertEqual(r.status, "cannot_satisfy")
        revisions = [s for s in r.trace if s.action == "revise_script"]
        self.assertEqual(len(revisions), MAX_REVISIONS)
        self.assertIn("无法满足", r.final.get("reason", ""))
        self.assertTrue(r.gap)


class TestGuards(unittest.TestCase):
    """安全：无检查/未修正违规不得声称 generated（防编造产品事实）。"""

    def test_generated_without_check_blocked(self):
        class Bad:
            mode = "bad"

            def decide(self, s, m):
                return {"action": "finish", "status": "generated", "reason": "编造"}

        r = run("x", 30, model=Bad(), tools=Tools())
        self.assertEqual(r.status, "incomplete")
        self.assertTrue(any("拦截" in g for g in r.gap))

    def test_generated_with_blocked_check_blocked(self):
        shots = [{"index": 1, "duration": 4, "visual": "a",
                  "narration": "效率提升 80%", "cites": ["P01"]}]

        class Bad:
            mode = "bad"

            def __init__(self):
                self.step = 0

            def decide(self, s, m):
                self.step += 1
                if self.step == 1:
                    return {"action": "draft_script",
                            "args": {"title": "t", "shots": shots}}
                if self.step == 2:
                    return {"action": "check_rules",
                            "args": {"script": {"shots": shots}}}
                return {"action": "finish", "status": "generated", "reason": "强行成功"}

        r = run("x", 30, model=Bad(), tools=Tools())
        self.assertEqual(r.status, "incomplete")
        self.assertTrue(any("违规" in g for g in r.gap))


class TestMalformedOutput(unittest.TestCase):
    def test_unknown_action_no_crash(self):
        class Bad:
            mode = "bad"

            def decide(self, s, m):
                return {"action": "fly_to_moon", "args": {}}

        r = run("x", 30, model=Bad(), tools=Tools())
        self.assertEqual(r.status, "incomplete")

    def test_non_dict_no_crash(self):
        class Bad:
            mode = "bad"

            def decide(self, s, m):
                return "not a dict"

        r = run("x", 30, model=Bad(), tools=Tools())
        self.assertEqual(r.status, "incomplete")


class TestStepLimit(unittest.TestCase):
    def test_max_steps_incomplete(self):
        class Loop:
            mode = "loop"

            def decide(self, s, m):
                return {"action": "search_materials", "args": {"query": "x"}}

        r = run("x", 30, model=Loop(), tools=Tools())
        self.assertEqual(r.status, "incomplete")
        self.assertLessEqual(
            len([s for s in r.trace if s.action != "abort"]), MAX_STEPS)


if __name__ == "__main__":
    unittest.main()
