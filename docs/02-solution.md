# 02 · 方案

## 调用流程

```
输入(主题/受众/平台/目标时长/风格)
        │
        ▼
┌──────────────────────────────────────────────┐
│ ReAct 循环 (src/agent/react_loop.py)          │
│  模型 decide() ──► 动作                        │
│      │                                         │
│      ├─ search_materials ──► 命中素材事实       │
│      ├─ draft_script     ──► 事实派生起草       │
│      ├─ estimate_duration──► 时长/超限事实      │
│      ├─ revise_script    ──► 压缩/修正（作废旧检查）│
│      ├─ check_rules      ──► 违规事实           │
│      └─ finish           ──► generated 需守卫   │
│      ▲                     │                   │
│      └── 观察结果喂回 ◄─────┘                    │
│  步数上限 12；无检查/违规未修不得 generated      │
└──────────────────────────────────────────────┘
```

## 动态决策（ReAct 的核心）

**工具只返回事实，决策权 100% 归模型。** 代码里没有 `if 超时长 then 压缩`——
压缩由"观察到 `over_target=true` 事实"触发。

两条典型路径：

- **路径 A（超时长修正）**：`检索 → 起草38s → 估算(38>30) → 压缩 → 重估28s → 检查 → finish`
- **路径 B（达标不修正）**：`检索 → 起草38s → 估算(38≤45) → 检查 → finish`（无 revise）

见 `examples/trace_normal.json` 与 `trace_within_target.json`。

## 关键设计取舍

### 1. 事实派生起草（评审修复点）
`_draft_from_hits`：起草的旁白**逐字取自命中素材的 `facts`**、分镜围绕命中事实生成。
素材替换测试证明脚本随数据变化（`TestFactDerivation::test_swap_material_changes_script`）。

### 2. 修正后作废旧检查（评审修复点）
`revise_script` 后历史里旧估算/旧检查作废，强制重新估算、重新检查；
`generated` 守卫要求"最后一次检查干净"。

### 3. 整词匹配（评审修复点）
检索按标点切分的整词包含匹配（无滑窗），修掉"支持字母"经滑窗误命中"支持字幕"；
剩余字面近似局限已在限制中声明。

### 4. 修正收敛保证
压缩系数 0.74（单调递减）、最低 2s；`MAX_REVISIONS=2` 轮仍超 → `cannot_satisfy` + 缺口。

### 5. 防编造双层防线
- 规则层：数值/效果词逐字核对（"效率提升 80%"被 block）
- 循环层：`finish(generated)` 守卫（结构保证，不信任模型自律）

### 6. 零依赖 + Mock 与真实模型同构
标准库 `urllib` 直连 OpenAI 兼容端点；`ModelClient.decide` 唯一接口。

## 技术栈

- Python 3.12，标准库 only
- 模型：OpenAI 兼容 `/chat/completions`（`response_format=json_object`）
