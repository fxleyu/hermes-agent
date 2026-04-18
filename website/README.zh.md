# 网站

本网站使用 [Docusaurus](https://docusaurus.io/) 构建，一个现代静态网站生成器。

## 安装

```bash
yarn
```

## 本地开发

```bash
yarn start
```

此命令启动本地开发服务器并打开浏览器窗口。大多数更改无需重启服务器即可实时反映。

## 构建

```bash
yarn build
```

此命令将静态内容生成到 `build` 目录中，可以使用任何静态内容托管服务来提供服务。

## 部署

使用 SSH：

```bash
USE_SSH=true yarn deploy
```

不使用 SSH：

```bash
GIT_USER=<你的 GitHub 用户名> yarn deploy
```

如果你使用 GitHub Pages 进行托管，此命令可以方便地构建网站并推送到 `gh-pages` 分支。

## 图表检查

CI 运行 `ascii-guard` 来检查文档中的 ASCII 框图。使用 Mermaid（````mermaid`）或纯列表/表格代替 ASCII 框图以避免 CI 失败。
