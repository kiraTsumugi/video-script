# 实现前说明（Linus 视角）

1. **必须交付的功能**
   ① ReAct 循环（模型选动作→跑工具→观察→调整/结束，步数上限 12，超限 incomplete）
   ② 3 个工具：素材检索 / 时长估算 / 规则检查（只返回事实，不返回决策）
   ③ 修正闭环：观察到超时长事实→压缩→**重新估算**；规则违规→修正→**重新检查**；仍不通过→明确失败+缺口
   ④ 事实可追溯：旁白逐字取自素材 facts、逐镜挂 cites 来源 ID；数值/效果宣称必须可核对
   ⑤ 防编造守卫：generated 需"有脚本+检查干净"（循环层硬校验，不信任模型自律）

2. **最短可运行主流程**
   `python -m src.main --topic "产品特色 支持字幕" --duration 30`
   → 检索P01 → 起草38s → 估算(超时长) → 压缩 → 重估28s → 检查 → 输出分镜+来源+总时长

3. **关键文件与阻塞**
   关键：`src/agent/{models,tools,llm,react_loop}.py`、`src/main.py`、`data/{materials,brand_rules,synonyms}.json`
   阻塞：无（零依赖，纯标准库）

4. **可以暂时不做的内容**
   界面、线上部署、真实视频渲染、素材上传、多语言、真实付费模型接入（保留 live 路径与契约验证）

---

## 设计思路（文件里放不下的）

**核心数据结构**：`Shot{index,duration,visual,narration,cites}`。cites 是事实可追溯的最小单元；
`Script.total_duration` 由分镜求和——展示总时长与估算值不一致视为未完成。

**消除特殊情况**：工具不判断"该不该压缩"——`estimate_duration` 返回 `over_target/overflow`
事实，压缩决策由模型做；`check_rules` 返回违规列表事实，修正决策由模型做。

**修正收敛保证**：压缩系数 0.74（单调递减）、最低 2s；MAX_REVISIONS=2 轮，
仍不通过 → `cannot_satisfy` + 缺口（不硬凑）。

**防编造双层防线**：
- 规则层：`R_GROUNDED_NUMBER`/`R_GROUNDED_EFFECT` 逐字核对（"效率提升 80%"被 block）
- 循环层：`finish(generated)` 要求最后一次检查干净，否则拦截为 incomplete

**Mock 不预判**：起草的 38s 是真实首稿；压缩必须由"观察到 over_target=true"触发——
Trace 逐行可验（第2题踩过"预判跳过"的坑，此处防复发）。
