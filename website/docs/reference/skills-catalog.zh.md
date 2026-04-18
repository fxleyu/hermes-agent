---
sidebar_position: 5
title: "内置技能目录"
description: "Hermes Agent 附带的内置技能目录"
---

# 内置技能目录

Hermes 附带大量内置技能库，安装时会复制到 `~/.hermes/skills/`。本页记录了仓库 `skills/` 目录下的内置技能。

## apple

Apple/macOS 专属技能 — iMessage、提醒事项、备忘录、FindMy 和 macOS 自动化。这些技能仅在 macOS 系统上加载。

| 技能 | 描述 | 路径 |
|------|------|------|
| `apple-notes` | 通过 macOS 上的 memo CLI 管理 Apple 备忘录（创建、查看、搜索、编辑）。 | `apple/apple-notes` |
| `apple-reminders` | 通过 remindctl CLI 管理 Apple 提醒事项（列出、添加、完成、删除）。 | `apple/apple-reminders` |
| `findmy` | 通过 macOS 上的 FindMy.app 使用 AppleScript 和屏幕截图追踪 Apple 设备和 AirTag。 | `apple/findmy` |
| `imessage` | 通过 macOS 上的 imsg CLI 发送和接收 iMessage/SMS。 | `apple/imessage` |

## autonomous-ai-agents

用于生成和编排自主 AI 编码代理和多代理工作流的技能 — 运行独立代理进程、委派任务和协调并行工作流。

| 技能 | 描述 | 路径 |
|------|------|------|
| `claude-code` | 将编码任务委派给 Claude Code（Anthropic 的 CLI 代理）。用于构建功能、重构、PR 审查和迭代编码。需要安装 claude CLI。 | `autonomous-ai-agents/claude-code` |
| `codex` | 将编码任务委派给 OpenAI Codex CLI 代理。用于构建功能、重构、PR 审查和批量问题修复。需要 codex CLI 和 git 仓库。 | `autonomous-ai-agents/codex` |
| `hermes-agent-spawning` | 生成额外的 Hermes Agent 实例作为自主子进程执行独立的长时间运行任务。支持非交互式一次性模式（-q）和交互式 PTY 模式进行多轮协作。与 delegate_task 不同 — 这会运行一个完整的独立 hermes 进程。 | `autonomous-ai-agents/hermes-agent` |
| `opencode` | 将编码任务委派给 OpenCode CLI 代理，用于功能实现、重构、PR 审查和长时间自主会话。需要安装并认证 opencode CLI。 | `autonomous-ai-agents/opencode` |

## data-science

数据科学工作流技能 — 交互式探索、Jupyter 笔记本、数据分析和可视化。

| 技能 | 描述 | 路径 |
|------|------|------|
| `jupyter-live-kernel` | 通过 hamelnb 使用实时 Jupyter 内核进行有状态的迭代 Python 执行。当任务涉及探索、迭代或检查中间结果时加载此技能。 | `data-science/jupyter-live-kernel` |

## creative

创意内容生成 — ASCII 艺术、手绘风格图表和视觉设计工具。

| 技能 | 描述 | 路径 |
|------|------|------|
| `ascii-art` | 使用 pyfiglet（571 种字体）、cowsay、boxes、toilet、image-to-ascii、远程 API（asciified、ascii.co.uk）和 LLM 后备生成 ASCII 艺术。无需 API 密钥。 | `creative/ascii-art` |
| `ascii-video` | ASCII 艺术视频的制作管线 — 任何格式。将视频/音频/图像/生成输入转换为彩色 ASCII 字符视频输出（MP4、GIF、图像序列）。 | `creative/ascii-video` |
| `excalidraw` | 使用 Excalidraw JSON 格式创建手绘风格图表。生成 .excalidraw 文件用于架构图、流程图、序列图、概念图等。文件可在 excalidraw.com 打开或上传获取可分享链接。 | `creative/excalidraw` |
| `p5js` | 使用 p5.js 创建交互式和生成式视觉艺术的制作管线。创建草图，通过无头浏览器渲染为图像/视频，并提供实时预览。 | `creative/p5js` |

## devops

DevOps 和基础设施自动化技能。

| 技能 | 描述 | 路径 |
|------|------|------|
| `webhook-subscriptions` | 创建和管理 webhook 订阅以实现事件驱动的代理激活。外部服务（GitHub、Stripe、CI/CD、IoT）通过 POST 事件触发代理运行。需要启用 webhook 平台。 | `devops/webhook-subscriptions` |

## dogfood

| 技能 | 描述 | 路径 |
|------|------|------|
| `dogfood` | 系统化的 Web 应用探索性 QA 测试 — 发现 bug、捕获证据并生成结构化报告。 | `dogfood/dogfood` |
| `hermes-agent-setup` | 帮助用户配置 Hermes Agent — CLI 使用、设置向导、模型/提供商选择、工具、技能、语音/STT/TTS、网关和故障排除。 | `dogfood/hermes-agent-setup` |

## email

从终端发送、接收、搜索和管理电子邮件的技能。

| 技能 | 描述 | 路径 |
|------|------|------|
| `himalaya` | 通过 IMAP/SMTP 管理电子邮件的 CLI。使用 himalaya 从终端列出、阅读、撰写、回复、转发、搜索和整理邮件。支持多账户和使用 MML（MIME Meta Language）撰写消息。 | `email/himalaya` |

## gaming

设置、配置和管理游戏服务器、模组包和游戏相关基础设施的技能。

| 技能 | 描述 | 路径 |
|------|------|------|
| `minecraft-modpack-server` | 从 CurseForge/Modrinth 服务器包 zip 设置模组化 Minecraft 服务器。涵盖 NeoForge/Forge 安装、Java 版本、JVM 调优、防火墙、局域网配置、备份和启动脚本。 | `gaming/minecraft-modpack-server` |
| `pokemon-player` | 通过无头模拟器自主玩 Pokemon 游戏。启动游戏服务器，从 RAM 读取结构化游戏状态，做出战略决策，并发送按键输入 — 全部从终端操作。 | `gaming/pokemon-player` |

## github

GitHub 工作流技能，用于通过 gh CLI 和 git 在终端中管理仓库、Pull Request、代码审查、Issue 和 CI/CD 管线。

| 技能 | 描述 | 路径 |
|------|------|------|
| `codebase-inspection` | 使用 pygount 检查和分析代码库，用于代码行数统计、语言分布和代码/注释比例。 | `github/codebase-inspection` |
| `github-auth` | 使用 git（通用可用）或 gh CLI 为代理设置 GitHub 认证。涵盖 HTTPS 令牌、SSH 密钥、凭证助手和 gh auth。 | `github/github-auth` |
| `github-code-review` | 通过分析 git diff、在 PR 上留下行内注释以及执行彻底的推送前审查来审查代码变更。 | `github/github-code-review` |
| `github-issues` | 创建、管理、分类和关闭 GitHub Issue。搜索现有 Issue，添加标签，分配人员并链接到 PR。 | `github/github-issues` |
| `github-pr-workflow` | 完整的 Pull Request 生命周期 — 创建分支、提交变更、开启 PR、监控 CI 状态、自动修复失败并合并。 | `github/github-pr-workflow` |
| `github-repo-management` | 克隆、创建、Fork、配置和管理 GitHub 仓库。管理远程仓库、密钥、发布和工作流。 | `github/github-repo-management` |

## inference-sh

通过 inference.sh 云平台执行 AI 应用的技能。

| 技能 | 描述 | 路径 |
|------|------|------|
| `inference-sh-cli` | 通过 inference.sh CLI（infsh）运行 150+ AI 应用 — 图像生成、视频创建、LLM、搜索、3D、社交自动化。 | `inference-sh/cli` |

## leisure

| 技能 | 描述 | 路径 |
|------|------|------|
| `find-nearby` | 使用 OpenStreetMap 查找附近场所（餐厅、咖啡馆、酒吧、药店等）。支持坐标、地址、城市、邮政编码或 Telegram 位置标记。无需 API 密钥。 | `leisure/find-nearby` |

## mcp

用于与 MCP（模型上下文协议）服务器、工具和集成交互的技能。包括内置原生 MCP 客户端（在 config.yaml 中配置服务器以自动工具发现）和用于临时服务器交互的 mcporter CLI 桥接器。

| 技能 | 描述 | 路径 |
|------|------|------|
| `mcporter` | 使用 mcporter CLI 列出、配置、认证和直接调用 MCP 服务器/工具（HTTP 或 stdio），包括临时服务器、配置编辑和 CLI/类型生成。 | `mcp/mcporter` |
| `native-mcp` | 内置 MCP（模型上下文协议）客户端，连接外部 MCP 服务器，发现其工具并将其注册为原生 Hermes Agent 工具。支持 stdio 和 HTTP 传输，具有自动重连、安全过滤和零配置工具注入。 | `mcp/native-mcp` |

## media

用于处理媒体内容的技能 — YouTube 字幕、GIF 搜索、音乐生成和音频可视化。

| 技能 | 描述 | 路径 |
|------|------|------|
| `gif-search` | 使用 curl 从 Tenor 搜索和下载 GIF。除 curl 和 jq 外无其他依赖。 | `media/gif-search` |
| `heartmula` | 设置和运行 HeartMuLa，开源音乐生成模型系列（类 Suno）。从歌词和标签生成完整歌曲，支持多语言。 | `media/heartmula` |
| `songsee` | 通过 CLI 从音频文件生成频谱图和音频特征可视化（mel、chroma、MFCC、节奏图等）。 | `media/songsee` |
| `youtube-content` | 获取 YouTube 视频字幕并将其转换为结构化内容（章节、摘要、话题、博客文章）。 | `media/youtube-content` |

## mlops

通用 ML 运维工具 — 模型中心管理、数据集操作和工作流编排。

| 技能 | 描述 | 路径 |
|------|------|------|
| `huggingface-hub` | Hugging Face Hub CLI（hf）— 搜索、下载和上传模型及数据集，管理仓库，部署推理端点。 | `mlops/huggingface-hub` |

## mlops/cloud

用于 ML 工作负载的 GPU 云提供商和无服务器计算平台。

| 技能 | 描述 | 路径 |
|------|------|------|
| `lambda-labs-gpu-cloud` | 用于 ML 训练和推理的预留和按需 GPU 云实例。简单的 SSH 访问、持久文件系统或高性能多节点集群。 | `mlops/cloud/lambda-labs` |
| `modal-serverless-gpu` | 用于运行 ML 工作负载的无服务器 GPU 云平台。无需基础设施管理即可获得按需 GPU 访问、部署 ML 模型作为 API 或运行自动扩展的批处理作业。 | `mlops/cloud/modal` |

## mlops/evaluation

模型评估基准、实验跟踪、数据整理、分词器和可解释性工具。

| 技能 | 描述 | 路径 |
|------|------|------|
| `evaluating-llms-harness` | 跨 60+ 学术基准（MMLU、HumanEval、GSM8K、TruthfulQA、HellaSwag）评估 LLM。行业标准，被 EleutherAI、HuggingFace 和主要实验室使用。 | `mlops/evaluation/lm-evaluation-harness` |
| `huggingface-tokenizers` | 为研究和生产优化的快速分词器。基于 Rust 的实现，20 秒内分词 1GB。支持 BPE、WordPiece 和 Unigram 算法。 | `mlops/evaluation/huggingface-tokenizers` |
| `nemo-curator` | GPU 加速的 LLM 训练数据整理。支持文本/图像/视频/音频。模糊去重（16 倍加速）、质量过滤（30+ 启发式）、语义去重、PII 编辑、NSFW 检测。使用 RAPIDS 跨 GPU 扩展。 | `mlops/evaluation/nemo-curator` |
| `sparse-autoencoder-training` | 使用 SAELens 训练和分析稀疏自编码器（SAE），将神经网络激活分解为可解释的特征。 | `mlops/evaluation/saelens` |
| `weights-and-biases` | 使用 W&B 跟踪 ML 实验、实时可视化训练、通过 sweep 优化超参数和管理模型注册表。 | `mlops/evaluation/weights-and-biases` |

## mlops/inference

模型服务、量化（GGUF/GPTQ）、结构化输出、推理优化和模型手术工具，用于部署和运行 LLM。

| 技能 | 描述 | 路径 |
|------|------|------|
| `gguf-quantization` | GGUF 格式和 llama.cpp 量化，用于高效的 CPU/GPU 推理。适合在消费级硬件、Apple Silicon 上部署模型。 | `mlops/inference/gguf` |
| `guidance` | 使用正则表达式和语法控制 LLM 输出，保证有效的 JSON/XML/代码生成，强制结构化格式。 | `mlops/inference/guidance` |
| `instructor` | 使用 Pydantic 验证从 LLM 响应中提取结构化数据，自动重试失败的提取，流式传输部分结果。 | `mlops/inference/instructor` |
| `llama-cpp` | 在 CPU、Apple Silicon 和消费级 GPU 上运行 LLM 推理，无需 NVIDIA 硬件。支持 GGUF 量化（1.5-8 位）。 | `mlops/inference/llama-cpp` |
| `obliteratus` | 使用 OBLITERATUS 的机制可解释性技术移除开放权重 LLM 的拒绝行为。9 种 CLI 方法、28 个分析模块、116 种模型预设。 | `mlops/inference/obliteratus` |
| `outlines` | 在生成过程中保证有效的 JSON/XML/代码结构，使用 Pydantic 模型实现类型安全输出。 | `mlops/inference/outlines` |
| `serving-llms-vllm` | 使用 vLLM 的 PagedAttention 和连续批处理以高吞吐量服务 LLM。支持 OpenAI 兼容端点、量化等。 | `mlops/inference/vllm` |
| `tensorrt-llm` | 使用 NVIDIA TensorRT 优化 LLM 推理以获得最大吞吐量。在 A100/H100 上比 PyTorch 快 10-100 倍。 | `mlops/inference/tensorrt-llm` |

## mlops/models

特定模型架构和工具 — 计算机视觉（CLIP、SAM、Stable Diffusion）、语音（Whisper）、音频生成（AudioCraft）和多模态模型（LLaVA）。

| 技能 | 描述 | 路径 |
|------|------|------|
| `audiocraft-audio-generation` | 用于音频生成的 PyTorch 库，包括文本到音乐（MusicGen）和文本到音效（AudioGen）。 | `mlops/models/audiocraft` |
| `clip` | OpenAI 连接视觉和语言的模型。支持零样本图像分类、图文匹配和跨模态检索。在 4 亿图文对上训练。 | `mlops/models/clip` |
| `llava` | 大型语言和视觉助手。支持视觉指令调优和基于图像的对话。结合 CLIP 视觉编码器和 Vicuna/LLaMA 语言模型。 | `mlops/models/llava` |
| `segment-anything-model` | 用于图像分割的基础模型，具有零样本迁移能力。使用点、框或掩码作为提示分割图像中的任何对象。 | `mlops/models/segment-anything` |
| `stable-diffusion-image-generation` | 使用 HuggingFace Diffusers 的 Stable Diffusion 模型进行先进的文本到图像生成。 | `mlops/models/stable-diffusion` |
| `whisper` | OpenAI 的通用语音识别模型。支持 99 种语言、转录、翻译为英语和语言识别。六种模型大小。 | `mlops/models/whisper` |

## mlops/research

用于构建和优化 AI 系统的 ML 研究框架，采用声明式编程。

| 技能 | 描述 | 路径 |
|------|------|------|
| `dspy` | 使用声明式编程构建复杂的 AI 系统，自动优化提示，使用 DSPy 创建模块化 RAG 系统和代理。 | `mlops/research/dspy` |

## mlops/training

微调、RLHF/DPO/GRPO 训练、分布式训练框架和优化工具，用于训练 LLM 和其他模型。

| 技能 | 描述 | 路径 |
|------|------|------|
| `axolotl` | 使用 Axolotl 微调 LLM 的专家指导 — YAML 配置、100+ 模型、LoRA/QLoRA、DPO/KTO/ORPO/GRPO、多模态支持。 | `mlops/training/axolotl` |
| `distributed-llm-pretraining-torchtitan` | 使用 torchtitan 的 4D 并行性（FSDP2、TP、PP、CP）进行 PyTorch 原生分布式 LLM 预训练。从 8 到 512+ GPU 扩展。 | `mlops/training/torchtitan` |
| `fine-tuning-with-trl` | 使用 TRL 通过强化学习微调 LLM — SFT 用于指令调优，DPO 用于偏好对齐，PPO/GRPO 用于奖励优化。 | `mlops/training/trl-fine-tuning` |
| `grpo-rl-training` | GRPO/RL 微调的专家指导，使用 TRL 进行推理和任务特定的模型训练。 | `mlops/training/grpo-rl-training` |
| `hermes-atropos-environments` | 为 Atropos 训练构建、测试和调试 Hermes Agent RL 环境。涵盖 HermesAgentBaseEnv 接口、奖励函数、代理循环集成和评估。 | `mlops/training/hermes-atropos-environments` |
| `huggingface-accelerate` | 最简单的分布式训练 API。4 行代码为任何 PyTorch 脚本添加分布式支持。统一的 DeepSpeed/FSDP/Megatron/DDP API。 | `mlops/training/accelerate` |
| `optimizing-attention-flash` | 使用 Flash Attention 优化 Transformer 注意力，实现 2-4 倍加速和 10-20 倍内存减少。 | `mlops/training/flash-attention` |
| `peft-fine-tuning` | 使用 LoRA、QLoRA 和 25+ 方法的 LLM 参数高效微调。在有限 GPU 内存下微调大模型（7B-70B）。 | `mlops/training/peft` |
| `pytorch-fsdp` | 使用 PyTorch FSDP 进行完全分片数据并行训练的专家指导 — 参数分片、混合精度、CPU 卸载、FSDP2。 | `mlops/training/pytorch-fsdp` |
| `pytorch-lightning` | 带有 Trainer 类、自动分布式训练（DDP/FSDP/DeepSpeed）、回调系统和最少样板代码的高级 PyTorch 框架。 | `mlops/training/pytorch-lightning` |
| `simpo-training` | 用于 LLM 对齐的简单偏好优化。DPO 的无参考替代方案，性能更好（AlpacaEval 2.0 上 +6.4 分）。 | `mlops/training/simpo` |
| `slime-rl-training` | 使用 slime（Megatron+SGLang 框架）进行 LLM 后训练 RL 的指导。 | `mlops/training/slime` |
| `unsloth` | 使用 Unsloth 进行快速微调的专家指导 — 2-5 倍更快的训练、50-80% 更少的内存。 | `mlops/training/unsloth` |

## mlops/vector-databases

用于 RAG、语义搜索和 AI 应用后端的向量相似性搜索和嵌入数据库。

| 技能 | 描述 | 路径 |
|------|------|------|
| `chroma` | 开源嵌入数据库。存储嵌入和元数据，执行向量和全文搜索。简单的 4 函数 API。 | `mlops/vector-databases/chroma` |
| `faiss` | Facebook 的高效相似性搜索和密集向量聚类库。支持数十亿向量、GPU 加速和多种索引类型。 | `mlops/vector-databases/faiss` |
| `pinecone` | 生产级 AI 的托管向量数据库。全托管、自动扩展、混合搜索、元数据过滤。低延迟（p95 低于 100ms）。 | `mlops/vector-databases/pinecone` |
| `qdrant-vector-search` | 高性能向量相似性搜索引擎，用于 RAG 和语义搜索。Rust 驱动的快速近邻搜索。 | `mlops/vector-databases/qdrant` |

## note-taking

笔记技能，用于保存信息、协助研究以及在多会话规划和信息共享中协作。

| 技能 | 描述 | 路径 |
|------|------|------|
| `obsidian` | 在 Obsidian 库中阅读、搜索和创建笔记。 | `note-taking/obsidian` |

## productivity

文档创建、演示文稿、电子表格和其他生产力工作流的技能。

| 技能 | 描述 | 路径 |
|------|------|------|
| `google-workspace` | 通过 Python 集成 Gmail、Calendar、Drive、Contacts、Sheets 和 Docs。使用 OAuth2 自动令牌刷新。 | `productivity/google-workspace` |
| `linear` | 通过 GraphQL API 管理 Linear Issue、项目和团队。创建、更新、搜索和组织 Issue。 | `productivity/linear` |
| `nano-pdf` | 使用 nano-pdf CLI 通过自然语言指令编辑 PDF。修改文本、修正错别字、更新标题等。 | `productivity/nano-pdf` |
| `notion` | Notion API，用于通过 curl 创建和管理页面、数据库和块。 | `productivity/notion` |
| `ocr-and-documents` | 从 PDF 和扫描文档中提取文本。远程 URL 使用 web_extract，本地文本 PDF 使用 pymupdf，OCR/扫描文档使用 marker-pdf。 | `productivity/ocr-and-documents` |
| `powerpoint` | 当涉及 .pptx 文件时使用此技能 — 包括创建幻灯片、演示文稿以及读取、解析或提取 .pptx 文件中的文本。 | `productivity/powerpoint` |

## research

学术研究、论文发现、文献综述、域名侦察、市场数据、内容监控和科学知识检索的技能。

| 技能 | 描述 | 路径 |
|------|------|------|
| `arxiv` | 使用 arXiv 免费 REST API 搜索和检索学术论文。无需 API 密钥。按关键词、作者、类别或 ID 搜索。 | `research/arxiv` |
| `blogwatcher` | 使用 blogwatcher CLI 监控博客和 RSS/Atom 订阅源的更新。 | `research/blogwatcher` |
| `llm-wiki` | Karpathy 的 LLM Wiki — 构建和维护持久的、互相链接的 markdown 知识库。Wiki 路径由 `WIKI_PATH` 环境变量控制（默认为 `~/wiki`）。 | `research/llm-wiki` |
| `domain-intel` | 使用 Python 标准库的被动域名侦察。子域发现、SSL 证书检查、WHOIS 查询、DNS 记录和批量分析。无需 API 密钥。 | `research/domain-intel` |
| `duckduckgo-search` | 通过 DuckDuckGo 的免费网络搜索 — 文本、新闻、图片、视频。无需 API 密钥。 | `research/duckduckgo-search` |
| `ml-paper-writing` | 为 NeurIPS、ICML、ICLR、ACL、AAAI、COLM 撰写出版级 ML/AI 论文。包括 LaTeX 模板、审稿人指南和引文验证。 | `research/ml-paper-writing` |
| `polymarket` | 查询 Polymarket 预测市场数据 — 搜索市场、获取价格、订单簿和价格历史。只读，通过公共 REST API，无需 API 密钥。 | `research/polymarket` |

## red-teaming

用于 LLM 红队测试、越狱和安全过滤器绕过研究的技能。

| 技能 | 描述 | 路径 |
|------|------|------|
| `godmode` | 使用 G0DM0D3 技术越狱 API 服务的 LLM — Parseltongue 输入混淆（33 种技术）、GODMODE CLASSIC 系统提示模板、ULTRAPLINIAN 多模型竞赛等。 | `red-teaming/godmode` |

## smart-home

控制智能家居设备的技能 — 灯光、开关、传感器和家庭自动化系统。

| 技能 | 描述 | 路径 |
|------|------|------|
| `openhue` | 通过 OpenHue CLI 控制 Philips Hue 灯光、房间和场景。开关灯、调节亮度、颜色、色温和激活场景。 | `smart-home/openhue` |

## social-media

与社交平台交互的技能 — 发布、阅读、监控和账号操作。

| 技能 | 描述 | 路径 |
|------|------|------|
| `xitter` | 通过 x-cli 终端客户端使用官方 X API 凭证与 X/Twitter 交互。 | `social-media/xitter` |

## software-development

| 技能 | 描述 | 路径 |
|------|------|------|
| `code-review` | 以安全和质量为重点执行彻底代码审查的指南。 | `software-development/code-review` |
| `plan` | Hermes 的规划模式 — 检查上下文，在活跃工作区/后端工作目录的 `.hermes/plans/` 中编写 markdown 计划，不执行实际工作。 | `software-development/plan` |
| `requesting-code-review` | 在完成任务、实现主要功能或合并前使用。通过系统化审查过程验证工作是否满足需求。 | `software-development/requesting-code-review` |
| `subagent-driven-development` | 在执行具有独立任务的实施计划时使用。为每个任务分派新的 delegate_task，进行两阶段审查（规范合规性然后代码质量）。 | `software-development/subagent-driven-development` |
| `systematic-debugging` | 遇到任何 bug、测试失败或意外行为时使用。4 阶段根因调查 — 在理解问题之前不进行修复。 | `software-development/systematic-debugging` |
| `test-driven-development` | 在实现任何功能或修复 bug 时使用，在编写实现代码之前。强制执行红-绿-重构循环。 | `software-development/test-driven-development` |
| `writing-plans` | 当你有多步骤任务的规范或需求时使用。创建包含细粒度任务、精确文件路径和完整代码示例的全面实施计划。 | `software-development/writing-plans` |

---

# 可选技能

可选技能位于仓库的 `optional-skills/` 目录下，但**默认不激活**。它们涵盖更重或小众的用例。使用以下命令安装：

```bash
hermes skills install official/<category>/<skill>
```

## autonomous-ai-agents

| 技能 | 描述 | 路径 |
|------|------|------|
| `blackbox` | 将编码任务委派给 Blackbox AI CLI 代理。内置评判器的多模型代理，通过多个 LLM 运行任务并选择最佳结果。 | `autonomous-ai-agents/blackbox` |

## blockchain

| 技能 | 描述 | 路径 |
|------|------|------|
| `base` | 查询 Base（以太坊 L2）区块链数据并显示美元价格 — 钱包余额、代币信息、交易详情、Gas 分析、合约检查、鲸鱼检测和实时网络统计。无需 API 密钥。 | `blockchain/base` |
| `solana` | 查询 Solana 区块链数据并显示美元价格 — 钱包余额、代币投资组合、交易详情、NFT、鲸鱼检测和实时网络统计。无需 API 密钥。 | `blockchain/solana` |

## creative

| 技能 | 描述 | 路径 |
|------|------|------|
| `blender-mcp` | 通过 blender-mcp 插件的套接字连接直接从 Hermes 控制 Blender。创建 3D 对象、材质、动画和运行任意 Blender Python（bpy）代码。 | `creative/blender-mcp` |
| `meme-generation` | 通过选择模板和用 Pillow 叠加文字生成真实的表情包图片。生成实际的 `.png` 表情包文件。 | `creative/meme-generation` |

## devops

| 技能 | 描述 | 路径 |
|------|------|------|
| `docker-management` | 管理 Docker 容器、镜像、卷、网络和 Compose 栈 — 生命周期操作、调试、清理和 Dockerfile 优化。 | `devops/docker-management` |

## email

| 技能 | 描述 | 路径 |
|------|------|------|
| `agentmail` | 通过 AgentMail 为代理提供专属邮箱。使用代理拥有的邮箱地址自主发送、接收和管理邮件。 | `email/agentmail` |

## health

| 技能 | 描述 | 路径 |
|------|------|------|
| `neuroskill-bci` | 连接运行中的 NeuroSkill 实例，将用户的实时认知和情绪状态融入响应。需要 BCI 穿戴设备（Muse 2/S 或 OpenBCI）和 NeuroSkill 桌面应用。 | `health/neuroskill-bci` |

## mcp

| 技能 | 描述 | 路径 |
|------|------|------|
| `fastmcp` | 使用 Python 中的 FastMCP 构建、测试、检查、安装和部署 MCP 服务器。 | `mcp/fastmcp` |

## migration

| 技能 | 描述 | 路径 |
|------|------|------|
| `openclaw-migration` | 将用户的 OpenClaw 定制迁移到 Hermes Agent。导入记忆、SOUL.md、命令白名单、用户技能和选定的工作区资源。 | `migration/openclaw-migration` |

## productivity

| 技能 | 描述 | 路径 |
|------|------|------|
| `telephony` | 为 Hermes 提供电话功能 — 配置 Twilio 号码、收发 SMS/MMS、拨打电话，以及通过 Bland.ai 或 Vapi 发起 AI 驱动的外呼。 | `productivity/telephony` |

## research

| 技能 | 描述 | 路径 |
|------|------|------|
| `bioinformatics` | 来自 bioSkills 和 ClawBio 的 400+ 生物信息学技能入口。涵盖基因组学、转录组学、单细胞、变异检测、药物基因组学、宏基因组学和结构生物学。 | `research/bioinformatics` |
| `qmd` | 使用 qmd 在本地搜索个人知识库、笔记、文档和会议记录 — 具有 BM25、向量搜索和 LLM 重排序的混合检索引擎。 | `research/qmd` |

## security

| 技能 | 描述 | 路径 |
|------|------|------|
| `1password` | 设置和使用 1Password CLI（op）。安装 CLI、启用桌面应用集成、登录以及为命令读取/注入密钥。 | `security/1password` |
| `oss-forensics` | 供应链调查、证据恢复和 GitHub 仓库的取证分析。 | `security/oss-forensics` |
| `sherlock` | 跨 400+ 社交网络的 OSINT 用户名搜索。通过用户名追踪社交媒体账户。 | `security/sherlock` |
