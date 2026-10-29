# 运行记录索引

| 用例 | 场景 | 最终状态 | Trace | CLI 输出 |
|---|---|---|---|---|
| normal | 正常生成：首估38s超时长→压缩→28s（参考实例） | `generated` | [trace_normal.json](trace_normal.json) | [run_normal.txt](run_normal.txt) |
| within_target | 目标宽松：38s达标，无修正动作 | `generated` | [trace_within_target.json](trace_within_target.json) | [run_within_target.txt](run_within_target.txt) |
| rewrite_hit | 无直击→改写查询「画质」→命中P02→生成 | `generated` | [trace_rewrite_hit.json](trace_rewrite_hit.json) | [run_rewrite_hit.txt](run_rewrite_hit.txt) |
| missing_topic | 缺主题 | `needs_more_info` | [trace_missing_topic.json](trace_missing_topic.json) | [run_missing_topic.txt](run_missing_topic.txt) |
| missing_duration | 缺目标时长 | `needs_more_info` | [trace_missing_duration.json](trace_missing_duration.json) | [run_missing_duration.txt](run_missing_duration.txt) |
| no_material | 无素材：改写后仍无命中 | `no_material` | [trace_no_material.json](trace_no_material.json) | [run_no_material.txt](run_no_material.txt) |
| tool_failure | 工具失败：重试一次后明确失败 | `tool_error` | [trace_tool_failure.json](trace_tool_failure.json) | [run_tool_failure.txt](run_tool_failure.txt) |
| cannot_satisfy | 目标5s过短：压缩2轮仍21s，明确失败+缺口 | `cannot_satisfy` | [trace_cannot_satisfy.json](trace_cannot_satisfy.json) | [run_cannot_satisfy.txt](run_cannot_satisfy.txt) |
