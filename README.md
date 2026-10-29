# 短视频脚本 Agent

面向创作者的脚本生成助手：产出**有事实依据、满足时长限制**的可编辑脚本。旁白逐字取自素材事实、逐镜挂素材来源 ID；时长超限时**观察到超时长事实后自动压缩并重新估算**；规则不通过自动修正，仍不通过则**明确失败并给出缺口**。编造产品事实由两层防线拦截（规则检查 + 循环层硬守卫）。

模型驱动 ReAct 循环：**模型选动作 → 执行工具 → 观察结果 → 模型调整下一步或结束**。

## 快速开始

无需任何第三方依赖（纯标准库），Python ≥ 3.10。

```bash
# 1. 正常生成：首稿 38s 超 30s 目标 → 压缩 → 重估 28s → 检查通过（参考实例）
python -m src.main --topic "产品特色 支持字幕" --duration 30 -v

# 2. 目标宽松：38s ≤ 45s 达标，无修正动作
python -m src.main --topic "产品特色 支持字幕" --duration 45

# 3. 无直击 → 改写查询「画面质量→画质」→ 命中 P02
python -m src.main --topic "画面质量" --duration 30 -v

# 4. 缺主题 / 缺时长 → needs_more_info
python -m src.main --duration 30
python -m src.main --topic "产品特色"

# 5. 无素材（改写后仍无命中）
python -m src.main --topic "完全无关主题xyz" --duration 30

# 6. 工具失败（故障注入）
python -m src.main --topic "触发故障的查询" --duration 30

# 7. 超时长修正失败：目标 5s 过短 → 明确失败 + 缺口
python -m src.main --topic "产品特色 支持字幕" --duration 5
```

**评审入口**：`python -m src.main --topic "产品特色 支持字幕" --duration 30 -v`
打印每步 `动作 → 参数 → 决策依据`，即完整 ReAct Trace。

## 运行模式

- **离线 Mock（默认，无 key 时自动）**：确定性决策，消费工具事实。`--offline` 强制。
  边界：Mock 只验证**流程、边界与修正逻辑**，不证明真实模型决策效果。
- **真实模型**：任意 OpenAI 兼容端点：
  ```bash
  export MODEL_BASE_URL=https://api.openai.com/v1
  export MODEL_API_KEY=sk-...
  export MODEL_NAME=gpt-4o-mini
  ```

## 测试

```bash
python -m unittest discover -s tests -v        # 24 项测试
python examples/generate_traces.py             # 重新生成 examples/ 运行记录
python examples/verify_live_path.py            # 验证真实模型 HTTP 接入契约
```

## 决策模型

| 工具事实 | 模型动作 | 最终状态 |
|---|---|---|
| 检索命中 → 起草 → 超时长 | 压缩并重新估算 | （继续）|
| 压缩 2 轮仍超 | finish | `cannot_satisfy` + 缺口 |
| 无直击 | 改写查询再检索 | （继续）|
| 改写后仍无命中 | finish | `no_material` |
| 缺主题/时长 | finish | `needs_more_info` |
| 检索连续失败 | finish | `tool_error` |
| 检查干净 | finish(generated) | `generated` |
| 步数超限 | abort | `incomplete` |

## 安全红线

- **旁白事实可追溯**：旁白逐字取自素材 facts，逐镜挂 `cites` 素材来源 ID。
- **数值/效果宣称核对**：旁白中数值与效果词必须能在引用素材 facts 中找到依据，否则 block。
- **generated 守卫**：循环层要求"有脚本 + 最后一次检查干净"，防编造事实漏出。

## 目录

```
src/agent/        核心：models / tools / llm / react_loop
src/main.py       CLI 入口
tests/            可执行测试（24 项）
examples/         运行记录（8 组 Trace + CLI 输出 + 测试输出 + INDEX）
data/             合成产品素材（P01-P04）+ 品牌规则 + 改写知识表
docs/             01~05 设计文档
SUBMISSION.md     交付说明（自包含）
BRIEF.md          实现前说明
```

详见 [docs/01-requirements.md](docs/01-requirements.md) 起的设计文档。
