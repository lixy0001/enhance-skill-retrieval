# enhance-skill-retrieval

为大型 Skill 集合提供标签管理和两阶段检索增强。

- 创建时从 harness 的完整 Skill 快照初始化标签库。
- 支持用户手工标签、锁定标签和 agent 自动分类。
- 通过 harness 的正常安装、修改、删除事件同步标签。
- Skill 数量不超过阈值时返回 `bypass`，继续使用 harness 原生匹配。
- 超过阈值后先进行标签向量召回，再对候选 Skill 元数据精排。

可安装的 Skill 位于 [`enhance-skill-retrieval/`](enhance-skill-retrieval/)，设计依据见 [`creation-plan.md`](creation-plan.md)，测试位于 [`tests/`](tests/)。

## 本地验证

```bash
python3 -m unittest discover -s tests -v
python3 ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py enhance-skill-retrieval
```

官方 `quick_validate.py` 需要运行环境提供 PyYAML；Skill 本身只使用 Python 标准库。

动态标签状态由 harness 指定 `state_dir`，不会写入已安装 Skill，也不会修改其他 Skills。
