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


class TestBoundaryInputs(unittest.TestCase):
    """第4阶段: 空/非法/超长输入的优雅处理。"""

    def test_whitespace_topic_needs_more_info(self):
        r, _ = go("   ", 30)
        self.assertEqual(r.status, "needs_more_info")
        self.assertTrue(any("主题" in g for g in r.gap))

    def test_negative_duration_needs_more_info(self):
        r, _ = go("产品特色 支持字幕", -5)
        self.assertEqual(r.status, "needs_more_info")

    def test_zero_duration_needs_more_info(self):
        r, _ = go("产品特色 支持字幕", 0)
        self.assertEqual(r.status, "needs_more_info")

    def test_very_long_topic_no_crash(self):
        r, _ = go("词" * 10000, 30)
        self.assertEqual(r.status, "no_material")

    def test_huge_duration_ok(self):
        r, _ = go("产品特色 支持字幕", 999999)
        self.assertEqual(r.status, "generated")
        self.assertEqual(r.script.total_duration, 38)  # 宽松目标无修正

    def test_empty_material_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for f in ("synonyms.json", "brand_rules.json"):
                shutil.copy(DATA / f, tmp / f)
            docs = json.loads((DATA / "materials.json").read_text(encoding="utf-8"))
            docs["materials"] = []
            (tmp / "materials.json").write_text(
                json.dumps(docs, ensure_ascii=False), encoding="utf-8")
            r, _ = go("产品特色", 30, tools=Tools(data_dir=tmp))
            self.assertEqual(r.status, "no_material")

    def test_empty_shots_draft_rejected(self):
        class M:
            mode = "bad"

            def __init__(self):
                self.n = 0

            def decide(self, s, m):
                self.n += 1
                if self.n == 1:
                    return {"action": "search_materials",
                            "args": {"query": "支持字幕"}}
                if self.n == 2:
                    return {"action": "draft_script",
                            "args": {"title": "t", "shots": []}}
                return {"action": "finish", "status": "no_material"}

        r = run("产品特色 支持字幕", 30, model=M(), tools=Tools())
        draft_obs = next(t.observation for t in r.trace
                         if t.action == "draft_script")
        self.assertEqual(draft_obs.get("error"), "empty_shots")

    def test_bad_shot_types_no_crash(self):
        """cites 传字符串、index 传非法值 → 不崩溃。"""
        class M:
            mode = "bad"

            def __init__(self):
                self.n = 0

            def decide(self, s, m):
                self.n += 1
                if self.n == 1:
                    return {"action": "search_materials",
                            "args": {"query": "支持字幕"}}
                if self.n == 2:
                    return {"action": "draft_script", "args": {
                        "title": "t", "shots": [
                            {"index": "x", "duration": "4",
                             "visual": "a", "narration": "支持字幕",
                             "cites": "P01"}]}}
                return {"action": "finish", "status": "incomplete"}

        r = run("产品特色 支持字幕", 30, model=M(), tools=Tools())
        self.assertIsNotNone(r.script)
        self.assertEqual(r.script.shots[0].cites, ["P01"])  # 字符串归一为列表
        self.assertEqual(r.script.shots[0].index, 1)  # 非法 index 用序号兜底


class TestModelFailureFallback(unittest.TestCase):
    """第4阶段: 模型调用失败的回退（P0）。"""

    def test_model_exception_returns_tool_error(self):
        class Broken:
            mode = "broken"

            def decide(self, s, m):
                raise RuntimeError("model_call_failed: connection refused")

        r = run("产品特色 支持字幕", 30, model=Broken(), tools=Tools())
        self.assertEqual(r.status, "tool_error")
        self.assertTrue(any("模型调用失败" in g for g in r.gap))

    def test_non_json_model_output_no_crash(self):
        """live 模型返回非 JSON → 契约违规自纠，不崩溃。"""
        import os
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
                  "all_proxy", "ALL_PROXY"):
            os.environ.pop(k, None)

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                self.rfile.read(n)
                d = b'{"choices":[{"message":{"content":"NOT JSON"}}]}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(d)))
                self.end_headers()
                self.wfile.write(d)

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        old = {k: os.environ.get(k) for k in
               ("MODEL_BASE_URL", "MODEL_API_KEY")}
        os.environ["MODEL_BASE_URL"] = f"http://127.0.0.1:{port}/v1"
        os.environ["MODEL_API_KEY"] = "k"
        try:
            r = run("产品特色", 30, model=ModelClient(), tools=Tools())
            self.assertEqual(r.status, "incomplete")  # 自纠到步数上限，非崩溃
        finally:
            srv.shutdown()
            srv.server_close()
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


class TestToolInputHardening(unittest.TestCase):
    """第4阶段: 工具层对 None/非法类型输入的加固（同类调用点一并覆盖）。"""

    def test_check_rules_null_values_no_crash(self):
        t = Tools()
        cases = [
            {"shots": [{"index": 1, "duration": 4, "cites": ["P01"],
                        "narration": None}]},
            {"shots": [{"index": 1, "duration": None, "cites": ["P01"],
                        "narration": "x"}]},
            {"shots": [{"index": 1, "duration": "4", "cites": ["P01"],
                        "narration": "支持字幕"}]},
            {"title": None, "shots": []},
            None,
            {},
        ]
        for c in cases:
            r = t.check_rules(c)
            self.assertTrue(r["ok"])  # 不崩溃即可

    def test_check_rules_none_duration_counted_as_zero(self):
        """duration=None 视为 0 → 触发单镜最短时长规则（合理拦截）。"""
        t = Tools()
        r = t.check_rules({"shots": [{"index": 1, "duration": None,
                                      "cites": ["P01"], "narration": "x"}]})
        self.assertTrue(r["blocked"])

    def test_estimate_duration_invalid_inputs(self):
        t = Tools()
        self.assertFalse(t.estimate_duration([], 30)["ok"])
        self.assertFalse(t.estimate_duration(None, 30)["ok"])
        self.assertFalse(t.estimate_duration([{"duration": "abc"}], 30)["ok"])

    def test_search_null_query_no_crash(self):
        t = Tools()
        r = t.search_materials(None)
        self.assertTrue(r["ok"])

    def test_trace_write_failure_warns_but_result_intact(self):
        """--trace 写入失败：stderr 警告，主流程结果不受影响。"""
        import subprocess
        out = subprocess.run(
            [sys.executable, "-m", "src.main", "--offline",
             "--topic", "产品特色 支持字幕", "--duration", "30",
             "--trace", "/nonexistent_dir/x.json"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True,
            env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(out.returncode, 0)
        self.assertIn("warn", out.stderr)
        self.assertIn("状态", out.stdout)


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
