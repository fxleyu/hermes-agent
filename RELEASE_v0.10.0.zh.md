# Hermes Agent v0.10.0 (v2026.4.16)

**发布日期：** 2026年4月16日

> 工具网关发布版 — Nous Portal 付费订阅用户现在可以通过现有订阅使用网络搜索、图像生成、文本转语音和浏览器自动化，无需额外的 API 密钥。

---

## ✨ 亮点

- **Nous 工具网关** — [Nous Portal](https://portal.nousresearch.com) 付费订阅用户现在可以自动获得 **网络搜索**（Firecrawl）、**图像生成**（FAL / FLUX 2 Pro）、**文本转语音**（OpenAI TTS）和 **浏览器自动化**（Browser Use）的访问权限。无需单独的 API 密钥 — 只需运行 `hermes model`，选择 Nous Portal，然后选择要启用的工具即可。通过 `use_gateway` 配置进行按工具选择，与 `hermes tools` 和 `hermes status` 完全集成，即使存在直接 API 密钥，运行时也能正确优先使用网关。用清晰的基于订阅的检测替代了旧的隐藏 `HERMES_ENABLE_NOUS_MANAGED_TOOLS` 环境变量。([#11206](https://github.com/NousResearch/hermes-agent/pull/11206)，基于 @jquesnelle 的工作；文档：[#11208](https://github.com/NousResearch/hermes-agent/pull/11208))

---

## 🐛 Bug 修复与改进

本次发布包含 180+ 个提交，涵盖代理核心、网关、CLI 和工具系统的大量 Bug 修复、平台改进和可靠性增强。完整详情将在 v0.11.0 变更日志中发布。

---

## 👥 贡献者

- **@jquesnelle**（emozilla）— 原始工具网关实现 ([#10799](https://github.com/NousResearch/hermes-agent/pull/10799))，在本次发布中整合并发布

---

**完整变更日志**：[v2026.4.13...v2026.4.16](https://github.com/NousResearch/hermes-agent/compare/v2026.4.13...v2026.4.16)
