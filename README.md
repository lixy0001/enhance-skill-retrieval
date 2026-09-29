# enhance-skill-retrieval

为大型 Skill 集合提供标签管理和两阶段检索增强。

- 第一次接入时从 harness 的完整 Skill 快照一次性初始化标签库、倒排索引和派生向量；相同快照重复初始化不会重算。
- 支持用户手工标签、锁定标签和 agent 自动分类。
- 通过 harness 的正常安装、修改、删除事件增量同步标签、倒排索引和元数据向量。
- Skill 数量不超过阈值时返回 `bypass`，继续使用 harness 原生匹配。
- 超过阈值后，普通对话只提交 `query + registry_version + enabled_skill_ids`；标签召回候选 ID 后由 harness 仅加载候选 Skill 元数据，再由 TypeSafe Jev 进行语义决策。

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

第二阶段由 harness 构造并注入 Jev 客户端；子进程接入通过 `--jev-api-key-file <受保护路径或 /dev/fd/N>` 传递一次性密钥来源。密钥不会进入路由 JSON、环境变量、状态文件、输出或日志。默认请求 `https://api.typesafe.ai/v1/systemone` 和 `jev-latest`。Jev 不接收召回分数或原有候选顺序，缺少注入、返回越界 Skill、无效概率、超时或服务错误时路由返回 `error`。

## 固定标签命令

将 `enhance-skill-retrieval/scripts` 加入 `PATH`，并由 harness 设置标签状态目录：

```bash
export ENHANCE_SKILL_STATE_DIR=/path/to/harness/state
enhance-skill status
enhance-skill list-tags
enhance-skill show-tags lark-calendar
enhance-skill lock-tags lark-calendar productivity.calendar
enhance-skill comparison-status
```

顶级命令 `enhance-skill` 已检查未与当前环境中的命令重复。完整子命令以 `enhance-skill --help` 为准。

## 性能与自动对比修正

对比标签增强检索与全量元数据检索：

```bash
python3 enhance-skill-retrieval/scripts/benchmark_retrieval.py \
  --catalog tests/fixtures/catalog.json \
  --cases tests/performance-cases.jsonl \
  --jev-api-key-file /path/to/harness-owned/jev-key \
  --synthetic-size 500 \
  --iterations 20
```

该命令会真实调用 Jev，延迟指标包含外部请求时间并产生相应 API 用量；无凭据时应使用单元测试中的模拟 Jev 做本地回归，而不是把模拟结果当作线上性能。

harness 对同一查询分别执行增强检索和原生 Skill 匹配，然后调用 `enhance-skill compare-routing`。系统把原生匹配命中的 Skill（或 `none`）作为伪正确结果，自动判断查询标签错误、候选漏召回、精排错误或过度召回，并进入独立修正通道；不再询问用户准确与否。系统不保存原始查询，累计至少 5 次同类对比后才应用有上限的修正。
