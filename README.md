# enhance-skill-retrieval

为大型 Skill 集合提供标签管理和两阶段检索增强。

- 第一次接入时从 harness 的完整 Skill 快照一次性初始化标签库、倒排索引和派生向量；相同快照重复初始化不会重算。
- 支持用户手工标签、锁定标签和 agent 自动分类。
- 通过 harness 的正常安装、修改、删除事件增量同步标签、倒排索引和元数据向量。
- Skill 数量不超过阈值时返回 `bypass`，继续使用 harness 原生匹配。
- 超过阈值后，普通对话只提交 `query + registry_version + enabled_skill_ids`；标签召回候选 ID 后由 harness 仅加载候选 Skill 元数据，再进行精排。

可安装的 Skill 位于 [`enhance-skill-retrieval/`](enhance-skill-retrieval/)，设计依据见 [`creation-plan.md`](creation-plan.md)，测试位于 [`tests/`](tests/)。

## 本地验证

```bash
python3 -m unittest discover -s tests -v
python3 ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py enhance-skill-retrieval
```

官方 `quick_validate.py` 需要运行环境提供 PyYAML；Skill 本身只使用 Python 标准库。

动态标签状态由 harness 指定 `state_dir`，不会写入已安装 Skill，也不会修改其他 Skills。
完整快照发生变化但缺少正常生命周期事件时，初始化会拒绝覆盖并提示显式 `rebuild`；重建会保留仍存在 Skill 的人工和锁定标签。
普通路由不会接受完整 `skills` 快照。标签库、向量缓存或注册表版本不一致时会失败关闭，由 harness 决定是否回退原生选择。

## 固定标签命令

将 `enhance-skill-retrieval/scripts` 加入 `PATH`，并由 harness 设置标签状态目录：

```bash
export ENHANCE_SKILL_STATE_DIR=/path/to/harness/state
enhance-skill status
enhance-skill list-tags
enhance-skill show-tags lark-calendar
enhance-skill lock-tags lark-calendar productivity.calendar
enhance-skill feedback-status
```

顶级命令 `enhance-skill` 已检查未与当前环境中的命令重复。完整子命令以 `enhance-skill --help` 为准。

## 性能与反馈

对比标签增强检索与全量元数据检索：

```bash
python3 enhance-skill-retrieval/scripts/benchmark_retrieval.py \
  --catalog tests/fixtures/catalog.json \
  --cases tests/performance-cases.jsonl \
  --synthetic-size 500 \
  --iterations 20
```

任务成功完成后，harness 可调用 `enhance-skill request-feedback` 进行默认 10% 抽样，再使用同一 interaction ID 调用 `record-feedback`。负反馈必须提供正确 Skill 与需求标签，系统分别判断查询标签错误、Skill 标签标注错误、候选漏召回和精排错误，并进入独立修正通道。系统不保存原始查询，累计至少 5 次同类反馈后才生效。
