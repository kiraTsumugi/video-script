# data/ 说明

全部为**合成数据**，不含任何真实隐私或密钥。

| 文件 | 内容 |
|---|---|
| `materials.json` | 合成产品素材库：每条素材只含可被引用的产品事实（`facts`）。含故障注入配置。 |
| `brand_rules.json` | 品牌规则（数据驱动）：时长边界、引用要求、**事实依据核对**（数值/效果词）、违禁词、分镜数。 |
| `synonyms.json` | 查询改写知识表：检索无结果时供模型改写查询（如 `画面质量→画质`）。 |

## 素材与验收场景映射

| 素材 | 事实 | 用途 |
|---|---|---|
| P01 | 支持字幕 | 参考实例主角：唯一事实"支持字幕"，**无效果数据**（防编造验证） |
| P02 | 支持 4K 画质 | 改写查询命中目标（画面质量→画质） |
| P03 | 防水设计 IP68 | 干扰项 |
| P04 | 续航 18 小时 | 干扰项 |

**关键**：P01 只含"支持字幕"——若脚本出现"效率提升 80%"等宣称，即为编造，必须被
`R_GROUNDED_NUMBER`/`R_GROUNDED_EFFECT` 拦截（见 `tests/test_agent.py::TestGuards`）。

## 品牌规则（brand_rules.json）

| 规则 | 类型 | 级别 | 说明 |
|---|---|---|---|
| R_MIN_SHOT / R_MAX_SHOT | 时长边界 | block | 单镜 2~15s |
| R_REQUIRE_CITE | 引用要求 | block | 每镜必须引用素材 |
| R_GROUNDED_NUMBER | 事实核对 | block | 旁白数值必须能在引用素材 facts 找到 |
| R_GROUNDED_EFFECT | 事实核对 | block | 效果词（提升/提高/…）必须有依据 |
| R_BANNED_WORDS | 违禁词 | block | 广告法违禁词 |
| R_MAX_SHOTS | 分镜数 | warn | 建议 ≤ 8 镜 |

## 故障注入

`materials.json._fail_injection.keywords`（默认 `触发故障`）出现在查询中时，
`search_materials` 返回失败，用于验证工具失败路径（见 `examples/trace_tool_failure.json`）。

## 验收场景 ↔ 输入映射

| 场景 | 输入 |
|---|---|
| 正常生成（修正闭环） | 主题=产品特色 支持字幕, 时长=30 |
| 目标宽松不修正 | 主题=产品特色 支持字幕, 时长=45 |
| 改写命中 | 主题=画面质量, 时长=30 |
| 无素材 | 主题=完全无关主题xyz |
| 缺主题 / 缺时长 | 省略对应参数 |
| 工具失败 | 主题=触发故障的查询 |
| 超时长修正失败 | 主题=产品特色 支持字幕, 时长=5 |
| 素材替换验证 | `tests/…::TestFactDerivation::test_swap_material_changes_script` |
