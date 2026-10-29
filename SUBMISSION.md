# 交付说明 · 短视频脚本 Agent

> 本文自包含全部评审所需信息（背景、运行、验证、取舍、限制），评审无需其他文档即可上手。

## 提交信息

| 项 | 内容 |
|---|---|
| 题号 | 短视频脚本 Agent（项目题四选一） |
| GitHub URL | https://github.com/kiraTsumugi/video-script |
| 分支 | `main` |
| 交付 tag | `submission-v1` |
| 运行方式 | CLI |
| 依赖 | 零第三方依赖，Python ≥ 3.10（无需 pip install） |

> **关于 commit SHA**：本文件被写入最终提交，无法在文件内记录自身 SHA（自引用）。
> 请以 tag 指向的提交为交付版本：
> ```bash
> git rev-list -n1 submission-v1
> ```

## 一句话概述

面向创作者的短视频脚本 Agent：生成**有事实依据**（旁白逐字取自素材事实、逐镜挂素材来源 ID）、
**满足时长限制**（估算超限→压缩→重估的修正闭环）的可编辑脚本；规则不通过自动修正，修正后仍
不通过则**明确失败并给出缺口**；**编造产品事实由两层防线拦截**（规则检查 + 循环层硬守卫）。

## 评审入口（先看这两条）

```bash
cd video-script

# ① 核心：正常生成 + 超时长修正闭环（每步 动作→观察→决策依据）
python -m src.main --topic "产品特色 支持字幕" --duration 30 -v

# ② 全量测试（24 项）
python -m unittest discover -s tests -v
```

预期 ①：`检索P01(支持字幕) → 起草38s → 估算(38s>30s) → 压缩 → 重估28s → 检查通过 → 输出`。

## 评审反馈 → 修改说明（本轮）

| # | 评审反馈 | 修改内容 | 依据文件 |
|---|---|---|---|
| 1 | **检索结果未进入生成逻辑**：脚本内容硬编码，仅让 `cites` 挂素材 ID；素材变化时脚本不变，失去事实可追溯 | 重写起草逻辑为 `_draft_from_hits`：**旁白逐字取自命中素材的 `facts`**、分镜围绕命中事实生成。素材替换测试证明：把 P01 改为"支持手势控制"后脚本旁白随之变化且无旧内容残留 | `src/agent/llm.py`、`tests/test_agent.py::TestFactDerivation` |
| 2 | **修正循环失效风险**：修正后历史里旧检查/旧估算残留，模型可能基于过期结果决策 | `revise_script` 后**作废旧估算与旧检查**，强制重新估算、重新检查；守卫要求"最后一次检查干净"才允许 `generated` | `src/agent/llm.py::_collect`、`src/agent/react_loop.py` |
| 3 | **检索匹配过宽**：2-4 字滑窗导致跨词误命中（"支持字母"命中"支持字幕"） | 改为**按标点切分的整词包含匹配**（不做滑窗）；回归测试固化。剩余局限见"已知限制" | `src/agent/tools.py::_segments`、`tests/test_agent.py::TestMatchingPrecision` |

## 需求映射（验收点 → 命令 → 证据）

| 验收点 | 命令 | 状态 | 证据文件 |
|---|---|---|---|
| 正常生成（含修正闭环） | `--topic "产品特色 支持字幕" --duration 30` | `generated`，38→28s | `examples/trace_normal.json` |
| 目标宽松不修正 | `--duration 45` | `generated` 38s，无 revise | `examples/trace_within_target.json` |
| 无素材（改写命中路径） | `--topic "画面质量"` | 改写→命中P02→`generated` | `examples/trace_rewrite_hit.json` |
| 无素材（彻底无果） | `--topic "完全无关主题xyz"` | `no_material` | `examples/trace_no_material.json` |
| 缺关键信息（主题/时长） | 省略对应参数 | `needs_more_info` | `trace_missing_topic/duration.json` |
| 工具失败 | `--topic "触发故障的查询"` | `tool_error`（重试一次后明确失败） | `examples/trace_tool_failure.json` |
| 超时长修正失败 | `--duration 5` | `cannot_satisfy` + 缺口 | `examples/trace_cannot_satisfy.json` |

完整测试：**24 项**，`python -m unittest discover -s tests -v` 全绿（输出见 `examples/test-results.txt`）。

## ReAct 决策证据（两条不同路径）

**路径 A · 超时长修正**（`trace_normal.json`，7 步）：

```
step1 search_materials("产品特色 支持字幕") → 命中 P01(支持字幕)
step2 draft_script   → 依据命中素材起草（旁白「支持字幕」逐字取自 P01 facts），首稿 38s
step3 estimate_duration → total=38 > target=30, over_target=true, overflow=8   ← 观察到超时长事实
step4 revise_script  → 压缩（0.74 系数，最低 2s）→ 新稿 28s                    ← 由事实触发的修正
step5 estimate_duration → total=28 ≤ 30 ✓                                     ← 修正后重新估算
step6 check_rules    → 0 违规（旁白均有素材依据）
step7 finish(generated) → 总时长 28s
```

**路径 B · 目标宽松不修正**（`trace_within_target.json`）：step3 估算 38s ≤ 45s 达标 →
**直接进入检查**，无 revise 动作。同一段循环代码因估算事实不同走出不同决策。

## 安全设计（防编造产品事实：关键缺陷防线）

1. **数据层**：P01 facts 仅"支持字幕"，无任何效果数据。
2. **规则层**：`R_GROUNDED_NUMBER`/`R_GROUNDED_EFFECT` 逐字核对——旁白中的数值与效果词
   （提升/提高/降低…）必须能在引用素材 facts 中找到依据，否则 block。测试：
   "效率提升 80%" 被拦截（`tests::TestGuards`）。
3. **循环层守卫**：`finish(generated)` 要求"有脚本 + 最后一次检查干净"，否则拦截为
   `incomplete`——不信任模型自律，结构保证。

## 已知限制（诚实声明）

1. **默认离线 Mock 不代表真实 LLM 决策质量**：只验证流程、边界与修正逻辑。真实能力需配
   `MODEL_BASE_URL / MODEL_API_KEY / MODEL_NAME`（OpenAI 兼容端点）后实测。
   live HTTP 契约已用本地 mock server 验证通过（`examples/verify_live_path.py`）。
2. **检索为字面近似**：整词包含匹配（无滑窗）修掉了跨词误命中，但仍无分词/语义能力——
   短片段（如"支持"）会命中所有含该词的素材；改写依赖 `data/synonyms.json` 知识表。
3. **脚本模板结构固定**："开场+功能镜+收尾"四镜结构由 Mock 确定性生成；分镜时长/旁白由
   素材事实与压缩系数驱动（真实模型接入后该结构由模型自由生成）。
4. **时长估算为帧精度取整**：按整秒计算，无片头/转场等毫秒级开销建模。
5. **压缩系数固定**（0.74）：真实场景下应由模型决策压缩哪一镜、压多少。

## 目录导览

```
README.md              环境/运行/评审入口
SUBMISSION.md          本文件（自包含交付说明）
src/agent/models.py    数据结构（Shot.cites 事实可追溯；状态正交）
src/agent/tools.py     工具层（检索/估算/检查；只返回事实）
src/agent/llm.py       模型接入（OpenAI 兼容 + 确定性离线 Mock；事实派生起草）
src/agent/react_loop.py ReAct 循环（步数上限 12；generated 守卫）
src/main.py            CLI 入口
tests/test_agent.py    24 项可执行测试
examples/              运行记录（8 组 Trace + CLI 输出 + 测试输出 + INDEX）+ 生成/验证脚本
data/                  合成产品素材（P01-P04）+ 品牌规则 + 改写知识表
```
