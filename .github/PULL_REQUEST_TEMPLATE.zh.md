## 这个 PR 做了什么？

<!-- 清楚地描述更改。它解决了什么问题？为什么这种方法是正确的？ -->



## 相关 Issue

<!-- 链接此 PR 解决的 issue。如果没有 issue 存在，请考虑先创建一个。 -->

修复 #

## 变更类型

<!-- 勾选适用的一项。 -->

- [ ] 🐛 Bug 修复（不破坏现有功能的修复）
- [ ] ✨ 新功能（不破坏现有功能的新增功能）
- [ ] 🔒 安全修复
- [ ] 📝 文档更新
- [ ] ✅ 测试（添加或改进测试覆盖）
- [ ] ♻️ 重构（无行为变更）
- [ ] 🎯 新技能（内置或中心发布）

## 所做的更改

<!-- 列出具体更改。代码更改请包含文件路径。 -->

- 

## 如何测试

<!-- 验证此更改有效的步骤。对于 bug：复现步骤 + 修复有效的证明。 -->

1. 
2. 
3. 

## 检查清单

<!-- 在请求审查前完成这些。 -->

### 代码

- [ ] 我已阅读 [贡献指南](https://github.com/NousResearch/hermes-agent/blob/main/CONTRIBUTING.md)
- [ ] 我的提交消息遵循 [约定式提交](https://www.conventionalcommits.org/)（`fix(scope):`、`feat(scope):` 等）
- [ ] 我已搜索 [现有 PR](https://github.com/NousResearch/hermes-agent/pulls) 以确保这不是重复的
- [ ] 我的 PR **仅**包含与此修复/功能相关的更改（没有无关提交）
- [ ] 我已运行 `pytest tests/ -q` 且所有测试通过
- [ ] 我已为我的更改添加了测试（bug 修复必需，功能强烈建议）
- [ ] 我已在我的平台上测试：<!-- 例如 Ubuntu 24.04、macOS 15.2、Windows 11 -->

### 文档与整理

<!-- 勾选所有适用的。如果某个类别不适用于你的更改，勾选"不适用"也是可以的。 -->

- [ ] 我已更新相关文档（README、`docs/`、文档字符串）— 或不适用
- [ ] 如果添加/更改了配置键，我已更新 `cli-config.yaml.example` — 或不适用
- [ ] 如果更改了架构或工作流，我已更新 `CONTRIBUTING.md` 或 `AGENTS.md` — 或不适用
- [ ] 我已根据 [兼容性指南](https://github.com/NousResearch/hermes-agent/blob/main/CONTRIBUTING.md#cross-platform-compatibility) 考虑了跨平台影响（Windows、macOS）— 或不适用
- [ ] 如果更改了工具行为，我已更新工具描述/schema — 或不适用

## 新技能

<!-- 仅在添加技能时填写此部分。否则请删除此部分。 -->

- [ ] 此技能对大多数用户**广泛有用**（如果是内置的）— 参见 [贡献指南](https://github.com/NousResearch/hermes-agent/blob/main/CONTRIBUTING.md#should-the-skill-be-bundled)
- [ ] SKILL.md 遵循 [标准格式](https://github.com/NousResearch/hermes-agent/blob/main/CONTRIBUTING.md#skillmd-format)（frontmatter、触发条件、步骤、注意事项）
- [ ] 无外部依赖项不在已有范围内（优先使用标准库、curl、现有 Hermes 工具）
- [ ] 我已端到端测试该技能：`hermes --toolsets skills -q "Use the X skill to do Y"`

## 截图 / 日志

<!-- 如适用，添加截图或日志输出展示修复/功能的效果。 -->

