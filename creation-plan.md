# Skill 标签管理与检索增强器创作计划

## 1. 背景与目标

当 agent 或 harness 可用的 Skills 越来越多时，全量加载全部 Skill 元数据并直接匹配会增加上下文占用和选择延迟。

本项目实现两项紧密相关的能力：

1. 持续维护 Skill 标签库，支持用户手工标签和 agent 自动标签。
2. 仅在 Skill 数量超过阈值时，通过标签向量召回候选，再由 TypeSafe Jev 对候选 Skill 元数据进行语义决策。

agent/harness 自身的 Skill 注册表始终是 Skill 元数据的唯一数据源。本项目不接管 Skill 的安装、存储和版本管理。

## 2. 总体行为

### 创建时

1. harness 提供当前已有的全部 Skill 元数据。
2. 读取已有原生标签。
3. 对缺少标签的 Skills，根据名称、描述和路由示例生成标签。
4. 建立初始标签库和标签到 Skill ID 的映射。
5. 同时构建可持久复用的标签向量与 Skill 元数据向量缓存。
6. 相同完整快照再次初始化时直接复用；快照或标签体系变化时要求显式重建。

### 日常同步

- 新增 Skill：自动添加标签记录，并增量增加倒排项与元数据向量。
- 更新 Skill：重新计算自动标签，保留用户手工标签，并只更新受影响索引项。
- 删除 Skill：同步删除标签记录、倒排项和相关向量缓存。
- 用户可以随时为指定 Skill 增加、删除或锁定标签。

### 对话检索

```text
Skill 数量未超过阈值
→ 不触发标签检索
→ harness 使用原有全量元数据匹配

Skill 数量超过阈值
→ harness 只传 query + registry_version + enabled_skill_ids
→ 对话匹配标签向量
→ 标签筛选候选 Skill ID
→ harness 只加载候选 Skill 元数据
→ Jev Choice + Noul fit 精排
→ 返回一个 Skill 或 none
```

标签库始终可以维护；只有标签检索受数量阈值控制。

## 3. 能力边界

本项目负责：

- 初始化并维护 Skill 标签分配；
- 用户手工标签、锁定和覆盖；
- agent 根据 Skill 元数据自动生成标签；
- 根据 harness 正常安装、修改和删除产生的变更事件同步标签；
- 大规模 Skill 集合下的标签向量召回和候选精排。

本项目不负责：

- Skill 安装、下载、卸载或升级；
- 独立 Skill 数据库或 Skill 注册中心；
- 复制或托管完整 `SKILL.md` 正文；
- 替换 agent/harness 原有 Skill 存储格式；
- 同名 Skill 的原生优先级和版本冲突处理；
- 小规模 Skill 集合的全量匹配算法。

第一版不监听文件系统，也不执行周期扫描或运行时快照对账。绕过 harness 直接修改或删除 Skill 时，标签库可能暂时不一致；该场景不在 v1 支持范围内。

## 4. 启用阈值

检索启用判断由 harness 完成：

```text
if active_skill_count <= activation_threshold:
    sync_tags_if_needed()
    use_native_skill_matching()
else:
    sync_tags_if_needed()
    use_tag_retrieval_enhancer()
```

首版建议：

- `activation_threshold: 80`
- `tag_top_k: 5`
- `candidate_skill_limit: 32`
- `minimum_tag_score: 0.28`
- `jev_model: jev-latest`
- `jev_fit_threshold: 0.30`
- `jev_timeout_seconds: 5.0`
- `jev_max_attempts: 2`

阈值由不同 harness 独立配置。增强器收到未超过阈值的路由请求时返回 `bypass`，但初始化、同步和人工标签操作不受该阈值限制。

## 5. Harness 输入协议

harness 在普通对话的第一阶段只提供：

```json
{
  "query": "把明天下午的会议改到三点",
  "registry_version": "harness-defined-version",
  "enabled_skill_ids": ["lark-calendar", "lark-vc", "pdf"]
}
```

标签召回返回候选 ID 后，harness 只加载候选 Skill 的元数据，并在第二阶段增加
`candidate_skills` 字段。增强器重新计算候选集合，要求候选元数据与召回 ID 完全一致，
校验元数据指纹后再交给 Jev 精排。完整 Skill 快照只用于首次 `initialize_tags`、显式
`rebuild_tags`，不进入普通对话路由。

支持的操作：

- `initialize_tags`：创建时为全部已有 Skills 建立标签。
- `sync_tags`：处理 harness 正常安装、修改和删除产生的事件。
- `set_tags`：用户增加、删除、锁定或解锁标签。
- `reclassify_tags`：让 agent 重新分析指定 Skill。
- `route`：仅在超过阈值时执行标签召回和精排。
- `inspect_tags`：查看 Skill 的标签、来源和生成依据。
- `compare_routing`：对比同一查询的增强结果与 harness 原生结果，并自动累计修正。
- `comparison_status`：查看自动对比次数、差异归因和修正统计。
- `benchmark`：对比标签增强与全量元数据匹配的查询吞吐量和延迟。

检索增强器只引用 `skill_id` 和 harness 提供的元数据，不依赖 Skill 的实际存储路径。

## 6. 标签数据模型

每个 Skill 对应一条标签记录：

```json
{
  "skill_id": "lark-calendar",
  "metadata_fingerprint": "sha256:...",
  "manual_tags": ["productivity.calendar"],
  "native_tags": ["lark"],
  "agent_tags": ["meeting-room", "availability"],
  "locked_tags": ["productivity.calendar"],
  "effective_tags": [
    "productivity.calendar",
    "lark",
    "meeting-room",
    "availability"
  ],
  "classification_reason": "Calendar scheduling and meeting-room management"
}
```

标签优先级：

1. 用户手工指定并锁定的标签；
2. harness 或 Skill 元数据明确提供的原生标签；
3. 确定性规则从名称、平台、文件类型和动作中提取的标签；
4. agent 根据 Skill 元数据补充生成的标签。

元数据更新时只重新计算 `native_tags` 和 `agent_tags`，不能覆盖 `manual_tags` 与 `locked_tags`。

每个 Skill 建议包含：

- 1～3 个主标签；
- 0～5 个辅助标签；
- 平台、产物和动作特征；
- 可选正向示例与排除场景。

## 7. 标签体系

主标签采用受控 ID，例如：

- `content.documents`
- `content.spreadsheets`
- `productivity.calendar`
- `communication.email`
- `engineering.version-control`

模型生成的新标签不能静默扩展主标签体系：

1. 优先映射到已有受控标签。
2. 无法映射时作为辅助标签保存。
3. 只有用户确认后才能加入主标签体系。

用户可以：

- 给某个 Skill 增加或删除指定标签；
- 锁定标签，防止自动同步覆盖；
- 解锁后重新执行 agent 分类；
- 查看标签来源和生成依据；
- 批量调整同类 Skills 的标签。

## 8. 标签库不是 Skill 数据库

标签库持久保存：

```text
skill_id
metadata_fingerprint
manual_tags
native_tags
agent_tags
locked_tags
effective_tags
classification_reason
```

标签库不保存：

- 完整 Skill 描述副本；
- `SKILL.md` 正文；
- Skill 安装路径；
- scripts、references 或 assets；
- Skill 版本和安装状态的权威信息。

Skill 是否存在、是否启用及其最新元数据始终由 harness 注册表决定。标签库只管理标签分配，可以用一个小型、可审阅的 YAML/JSON 文件实现。

## 9. 标签库生命周期

### 首次初始化

1. 接收 harness 当前完整 Skill 快照。
2. 为全部 Skills 建立标签记录。
3. 规范化原生标签并运行 agent 分类。
4. 生成 `effective_tags`。
5. 建立标签到 Skill ID 的倒排映射。
6. 生成标签向量与 Skill 元数据向量缓存，使以后超过阈值时无需全量构建。
7. 对相同快照的重复初始化返回成功但不重新分类；不同快照必须显式执行重建。

### 新增 Skill

1. 接收 harness 安装完成后产生的 `skill_added` 事件。
2. 读取原生标签并生成 agent 标签。
3. 添加标签记录。
4. 增量更新相关倒排映射和元数据向量；是否启用标签检索不影响索引维护。

### 更新 Skill

1. `metadata_fingerprint` 变化时重新分类。
2. 保留人工标签和锁定状态。
3. 更新变化的标签倒排映射和向量。

### 删除 Skill

1. 接收 harness 删除完成后产生的 `skill_removed` 事件。
2. 从标签库和倒排映射删除该 `skill_id`。
3. 清理元数据向量缓存。
4. 数量降到阈值以下时停止标签检索，但保留剩余标签库。

### v1 同步边界

第一版只响应 harness 的 `skill_added`、`skill_updated`、`skill_removed` 事件。除首次创建标签库外，不进行完整快照差异对账，不监听本地目录，也不处理绕过 harness 的外部变更。

## 10. 向量缓存

向量缓存是从标签库和 harness 元数据派生的可丢弃数据：

```text
registry_version
taxonomy_version
embedding_model_version
tag vectors
tag -> skill_id postings
candidate skill metadata vectors
```

规则：

- 标签库是标签分配来源，harness 是 Skill 元数据来源；
- 不缓存完整 `SKILL.md` 正文；
- 使用注册表版本和元数据指纹判断失效；
- 缓存缺失、损坏或版本不兼容时失败关闭，并通过完整快照显式重建；
- Skill 数量未超过阈值时仍维护派生索引，但路由直接绕过标签检索；
- 首版不引入独立数据库或常驻索引服务。

## 11. 大规模检索流程

```text
用户对话
   │
   ├─ harness 判断 Skill 数量超过阈值
   ├─ 同步标签库
   ├─ 对话向量匹配标签向量
   ├─ Top-K 标签映射为候选 Skill ID
   ├─ harness 提供候选 Skill 元数据
   ├─ Jev Choice 选择与逐候选 Noul fit 判断
   └─ 返回一个 Skill 或 none
```

候选 Skill 取命中标签下 Skill ID 的并集。超过上限时，标签得分只负责裁剪到高召回候选集，不决定最终 Skill。

最终决策：

```text
Jev Choice(candidate metadata) → winner + probability distribution
Jev Noul(each candidate)      → absolute fit probability
```

所有候选 fit 概率都低于门槛时返回 `none`，否则返回 Choice winner。召回分数、历史第一名和原有候选顺序不传给 Jev；模型输出必须通过候选集合、概率范围、注册表版本和元数据指纹校验。

## 12. 输出协议

允许的路由决策：

- `invoke`：返回一个 `selected_skill_id`。
- `none`：没有足够可靠的匹配。
- `bypass`：Skill 数量未超过阈值，应使用 harness 原生匹配。
- `error`：同步、标签、embedding 或 Jev 失败，不擅自调用 Skill。

示例：

```json
{
  "decision": "invoke",
  "selected_skill_id": "lark-calendar",
  "confidence": 0.84,
  "winner_probability": 0.82,
  "matched_tags": ["productivity.calendar"],
  "candidate_skill_ids": ["lark-calendar", "lark-vc"],
  "candidate_choice_scores": {"lark-calendar": 0.82, "lark-vc": 0.18},
  "candidate_fit_scores": {"lark-calendar": 0.91, "lark-vc": 0.24},
  "reranker_model": "jev-1.x",
  "reason_codes": ["jev_choice"],
  "registry_version": "harness-defined-version"
}
```

## 13. Skill 包结构

建议 Skill 名称为 `enhance-skill-retrieval`：

```text
enhance-skill-retrieval/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── scripts/
│   ├── initialize_tags.py
│   ├── sync_tags.py
│   ├── set_tags.py
│   ├── classify_tags.py
│   ├── build_vectors.py
│   ├── jev_reranker.py
│   ├── route_query.py
│   └── evaluate_retrieval.py
└── references/
    ├── tag-taxonomy.yaml
    ├── skill-tags.yaml
    ├── routing-policy.yaml
    ├── input.schema.json
    └── output.schema.json
```

`SKILL.md` 只保留标签管理和检索流程。确定性的同步、标签编辑、索引和路由逻辑放入 scripts；标签体系和协议放入 references。

## 14. Harness 接入

```text
on enhancer_install:
    initialize_tags(full_skill_snapshot)

on skill_registry_changed:
    sync_tags(harness_change_event)

on user_query:
    if skills.count > configured_threshold:
        candidates = recall(query, registry_version, enabled_skill_ids)
        metadata = registry.load_metadata(candidates.skill_ids)
        jev = JevReranker.from_policy(policy, api_key=secret_store.resolve("typesafe/jev"))
        rerank(query, registry_version, enabled_skill_ids, metadata, jev_reranker=jev)
    else:
        continue native routing
```

密钥由 harness 的安全存储解析并注入 Jev 客户端，不进入 Skill 配置、路由 JSON、环境变量或持久化状态。子进程集成使用 harness 持有的秘密文件或继承文件描述符作为注入边界。

`SKILL.md` 的 description 应明确两种用途：根据 harness 变更维护 Skill 标签，以及在 Skill 数量超过阈值时进行检索增强。

## 15. 实施阶段

### 阶段 A：调整协议

- 删除独立 Skill 数据库、安装管理和目录发现设计。
- 定义标签库、人工覆盖和标签来源。
- 定义 `initialize_tags`、`sync_tags`、`set_tags` 和 `route` 输入输出。
- 将路由决策增加 `bypass`。

### 阶段 B：标签初始化与编辑

- 为已有全部 Skills 建立初始标签库。
- 实现原生标签规范化和 agent 自动分类。
- 实现人工标签、标签锁定和来源追踪。

### 阶段 C：标签同步

- 实现新增、更新和删除事件处理。
- 更新 Skill 时保留人工标签和锁定状态。
- 标签同步始终可用，不受检索阈值限制。
- 不实现文件监听、周期扫描和快照对账。

### 阶段 D：检索增强

- 仅在超过阈值时构建或加载标签向量。
- 实现标签 Top-K 召回、候选裁剪和 Jev 语义精排。
- 实现 `invoke`、`none`、`bypass` 和 `error`。

### 阶段 E：Harness 适配

- 为不同 harness 编写薄适配器。
- 适配器只传递元数据、注册表变化和路由结果。
- 不复制或托管完整 Skill 数据。

### 阶段 F：评测与调参

- 在 20、50、80、100、200、500、1000 和 5000 个 Skills 下评测。
- 验证阈值以下不触发标签检索。
- 对比阈值以上标签召回与全量匹配的准确率和延迟。
- 使用同一批查询分别测量标签增强检索和全量元数据检索的吞吐量、平均延迟、P50 与 P95。

### 阶段 G：原生路由影子对比闭环

- harness 对同一查询独立执行增强检索与原生 Skill 匹配，不向用户询问准确性。
- 将原生匹配命中的 Skill 或原生 `none` 作为伪正确结果，自动与增强结果比较。
- 从原生命中 Skill 的受控有效标签中推断最相关主题，判断查询标签错误、候选漏召回、精排错误或过度召回。
- 普通检索仍只接收 `query + registry_version + enabled_skill_ids`；原生对比由 harness 在检索 seam 外执行，不向增强路由传递完整目录。
- 只保存聚合计数、派生查询向量和有数量上限的 comparison 哈希；不保存原始 ID、用户身份、查询文本、响应或任务产物。
- 少于 5 条同类对比时不影响路由；达到门槛后才应用对应修正，且显式指定 Skill 时不受修正影响。
- 通过 harness 删除 Skill 时同步删除对应自动对比统计。

### 阶段 H：封装与验证

- 使用 `init_skill.py` 初始化最终 Skill。
- 使用 `quick_validate.py` 验证结构。
- 在具备注册表变更事件的大规模隔离 harness 中进行前向测试。

## 16. 验收标准

- 创建时能够为 harness 当前全部 Skills 建立标签记录。
- 用户手工标签优先于原生和 agent 标签，Skill 更新时不会被覆盖。
- 通过 harness 正常新增、更新和删除 Skill 后，标签库能够根据事件正确同步。
- Skill 数量未超过阈值时，路由请求 100% 返回 `bypass`；标签维护仍然可用。
- 不创建 Skill 数据库，不改变 harness 注册表，也不保存完整 Skill 元数据。
- 标签候选 Recall@32 不低于 98%。
- 有明确目标的请求 Top-1 准确率不低于 92%。
- 错误调用率不高于 2%；内部错误时错误调用率为 0。
- 在 500 个 Skills 下，相对全量匹配的路由计算 P95 延迟至少降低 30%。
- 删除向量缓存后，能够根据标签库和 harness 元数据恢复检索能力。
- 性能报告同时提供增强检索与全量元数据检索的 QPS、平均延迟、P50、P95、准确率和结果一致率。
- 自动对比未达到最小样本数时路由结果不发生变化，调整绝对值不超过配置上限。
- 自动对比状态不包含原始查询、用户身份、任务响应或任务产物。

## 17. 开工前需要确认

1. harness 能否提供全部 Skill 的 `skill_id`、`name` 和 `description`。
2. harness 能否提供稳定的注册表版本或元数据指纹。
3. 标签库保存为 YAML 还是 JSON。
4. embedding 使用本地模型还是远程 API。
5. 是否接受 80 作为首版标签检索启用阈值。

推荐默认值：创建时全量初始化标签、之后只处理 harness 变更事件、标签库使用 YAML、本地可替换 embedding provider、80 个 Skills 以上启用标签检索。
