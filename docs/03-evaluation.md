# 03 · 评估

## 测试预期 vs 实际结果

全部测试：`python -m unittest discover -s tests -v` → **41 passed**（原始输出见 `examples/test-results.txt`）。

| 用例 | 预期 | 实际 | 证据 |
|---|---|---|---|
| 正常生成（38→28s 修正） | `generated`，总时长 28s | ✅ | `examples/trace_normal.json` |
| Trace 规范序列 | 检索→起草→估算→压缩→重估→检查→finish | ✅ | 同上（`test_canonical_trace_sequence`）|
| 修正由事实触发 | 首估 observation：38s/over=True/overflow=8 | ✅ | 同上 |
| 修正后重新估算 | 两次估算 = [38, 28] | ✅ | 同上 |
| 展示时长=估算值 | total_duration 与 estimate 一致 | ✅ | 同上 |
| 目标 45s 不修正 | 无 revise 动作，38s 达标 | ✅ | `examples/trace_within_target.json` |
| 改写命中 | 0 直击→改写「画质」→P02 | ✅ | `examples/trace_rewrite_hit.json` |
| 无素材 | `no_material` | ✅ | `examples/trace_no_material.json` |
| 缺主题/时长 | `needs_more_info` | ✅ | `trace_missing_topic/duration.json` |
| 工具失败 | 重试 1 次后 `tool_error` | ✅ | `examples/trace_tool_failure.json` |
| 修正失败明确 | 2 轮压缩后 `cannot_satisfy`+缺口 | ✅ | `examples/trace_cannot_satisfy.json` |
| 素材替换脚本变化 | 旁白随数据变，无旧内容 | ✅ | `tests::TestFactDerivation` |
| 匹配不过宽 | "支持字母" 0 命中 | ✅ | `tests::TestMatchingPrecision` |
| 规则违规→修正→重检 | P05 违禁词素材：拦截→修正标题/旁白→重检通过 | ✅ | `examples/trace_rule_violation.json` |
| 生成守卫 ×2 | 无检查/违规未修 → 拦截 incomplete | ✅ | `tests::TestGuards` |
| 畸形输出 ×2 | 不崩溃 | ✅ | `tests::TestMalformedOutput` |

## ReAct 决策证据（两条不同路径）

**路径 A · 超时长修正**（`trace_normal.json`，7 步）：

```
step1 search_materials("产品特色 支持字幕") → 命中 P01(支持字幕)
step2 draft_script   → 旁白「支持字幕」逐字取自 P01 facts，首稿 38s
step3 estimate_duration → total=38 > target=30, over_target=true, overflow=8  ← 观察到超时长事实
step4 revise_script  → 压缩系数 0.74 → 新稿 28s                              ← 由事实触发的修正
step5 estimate_duration → total=28 ≤ 30 ✓                                    ← 修正后重新估算
step6 check_rules    → 0 违规（旁白均有素材依据）
step7 finish(generated) → 总时长 28s
```

**路径 B · 达标不修正**（`trace_within_target.json`）：step3 估算 38s ≤ 45s →
直接进入检查，**无 revise 动作**。同一段循环代码因估算事实不同走出不同决策。

**路径 C · 规则违规修正**（`trace_rule_violation.json`，10 步）：

```
step1-5  检索P05(世界级音质) → 起草 → 估算38s超时长 → 压缩 → 重估28s
step6  check_rules → blocked: 标题/第1镜违禁词「世界级」  ← 观察到规则违规事实
step7  revise_script → 修正：标题与画面/旁白移除违禁词
step8  estimate_duration → 28s 仍达标（修正不破坏时长约束）
step9  check_rules → 0 违规 ✓                            ← 修正后重新检查
step10 finish(generated)
```

三条路径（超时长压缩 / 达标直通 / 规则修正）由不同工具事实驱动，
同一段循环代码走出三种决策序列。

## 评审三点修复的验证

| 评审点 | 验证方式 | 结果 |
|---|---|---|
| 1 检索结果未进入生成逻辑 | 换素材测试：P01 facts 改为"支持手势控制" | 脚本旁白随之变化、无"字幕"残留 ✅ |
| 2 修正循环失效风险 | `revise_script` 后作废旧检查/估算；守卫要求最后检查干净 | `test_generated_with_blocked_check_blocked` 通过、canonical trace 5 步含重新估算 ✅ |
| 3 检索匹配过宽 | "支持字母"（原经滑窗误命中"支持字幕"） | 0 命中 ✅ |

## 真实模型接入验证

`python examples/verify_live_path.py` 验证 `_call_live` 的 HTTP 契约
（payload / Bearer 鉴权 / JSON 解析）→ **通过**。

## 失败与限制

1. **Mock 非真实模型决策**：默认离线 Mock 是确定性生成器，只证明**流程、边界与修正逻辑**，
   不证明真实 LLM 决策质量。真实能力需配 `MODEL_*` 后实测。
2. **检索为字面近似**：整词包含匹配（无滑窗），但无分词/语义能力；短片段（"支持"）会命中
   所有含该词素材；改写依赖 `data/synonyms.json` 知识表。
3. **脚本模板结构固定**："开场+功能镜+收尾"四镜结构由 Mock 确定性生成；
   时长/旁白由素材事实与压缩系数驱动。真实模型接入后由模型自由生成。
4. **压缩系数固定 0.74**：真实场景应由模型决策压缩哪一镜、压多少。
5. **时长按整秒计**：无片头/转场毫秒级开销建模。

## 越权/虚报检查

- 修正失败给出 `cannot_satisfy` + 缺口，不硬凑（`test_impossible_target_explicit_failure`）。
- 工具失败重试后明确 `tool_error`，不虚报（`test_tool_error_after_retry`）。
- 编造宣称被规则拦截 + generated 守卫兜底（`TestGuards`）。
