# Hermes Agent — Web UI

基于浏览器的仪表盘，用于管理 Hermes Agent 配置、API 密钥和监控活跃会话。

## 技术栈

- **Vite** + **React 19** + **TypeScript**
- **Tailwind CSS v4** 配合自定义深色主题
- **shadcn/ui** 风格组件（手动实现，不依赖 CLI）

## 开发

```bash
# 启动后端 API 服务器
cd ../
python -m hermes_cli.main web --no-open

# 在另一个终端中，启动 Vite 开发服务器（带 HMR + API 代理）
cd web/
npm run dev
```

Vite 开发服务器将 `/api` 请求代理到 `http://127.0.0.1:9119`（FastAPI 后端）。

## 构建

```bash
npm run build
```

输出到 `../hermes_cli/web_dist/`，FastAPI 服务器将其作为静态 SPA 提供服务。构建产物通过 `pyproject.toml` package-data 包含在 Python 包中。

## 结构

```
src/
├── components/ui/   # 可复用 UI 基础组件（Card、Badge、Button、Input 等）
├── lib/
│   ├── api.ts       # API 客户端——所有后端端点的类型化 fetch 包装器
│   └── utils.ts     # cn() 辅助函数，用于 Tailwind 类名合并
├── pages/
│   ├── StatusPage   # 代理状态、活跃/近期会话
│   ├── ConfigPage   # 动态配置编辑器（从后端读取架构）
│   └── EnvPage      # API 密钥管理，支持保存/清除
├── App.tsx          # 主布局和导航
├── main.tsx         # React 入口点
└── index.css        # Tailwind 导入和主题变量
```
