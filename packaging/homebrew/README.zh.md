Hermes Agent 的 Homebrew 打包说明。

使用 `packaging/homebrew/hermes-agent.rb` 作为 tap 或 `homebrew-core` 的起点。

关键选择：
- 稳定版构建应以附加在每个 GitHub release 上的 semver 命名的 sdist 资源为目标，而非 CalVer 标签 tarball。
- `faster-whisper` 现在位于 `voice` extra 中，这使得仅 wheel 的传递依赖项不会进入基础 Homebrew formula。
- 包装器导出 `HERMES_BUNDLED_SKILLS`、`HERMES_OPTIONAL_SKILLS` 和 `HERMES_MANAGED=homebrew`，使打包安装保留运行时资源并将升级交给 Homebrew。

典型的更新流程：
1. 更新 formula 的 `url`、`version` 和 `sha256`。
2. 使用 `brew update-python-resources --print-only hermes-agent` 刷新 Python 资源。
3. 保持 `ignore_packages: %w[certifi cryptography pydantic]`。
4. 验证 `brew audit --new --strict hermes-agent` 和 `brew test hermes-agent`。
