# 可选技能

由 Nous Research 维护的官方技能，**默认未激活**。

这些技能随 hermes-agent 仓库一起发布，但在设置过程中不会被复制到 `~/.hermes/skills/`。它们可通过技能中心发现：

```bash
hermes skills browse               # 浏览所有技能，官方技能优先显示
hermes skills browse --source official  # 仅浏览官方可选技能
hermes skills search <query>       # 查找标记为 "official" 的可选技能
hermes skills install <identifier> # 复制到 ~/.hermes/skills/ 并激活
```

## 为什么是可选的？

某些技能有用但并非每个用户都广泛需要：

- **小众集成** — 特定的付费服务、专业工具
- **实验性功能** — 有前景但尚未验证
- **重量级依赖** — 需要大量设置（API 密钥、安装）

通过将它们设为可选，我们保持默认技能集精简，同时仍为需要的用户提供经过策划和测试的官方技能。
