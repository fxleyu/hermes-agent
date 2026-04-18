#!/usr/bin/env python3
"""
RL 训练 CLI 运行器

专用于 RL 训练工作流的 CLI 运行器，具有以下特性：
- 为长时间运行的训练提供扩展超时
- 以 RL 为重点的系统提示词
- 包含 RL 训练工具在内的完整工具集
- 特别处理 30 分钟检查间隔

用法:
    python rl_cli.py "Train a model on GSM8k for math reasoning"
    python rl_cli.py --interactive
    python rl_cli.py --list-environments

环境变量:
    TINKER_API_KEY: Tinker 服务的 API 密钥（必需）
    WANDB_API_KEY: WandB 指标的 API 密钥（必需）
    OPENROUTER_API_KEY: OpenRouter 的 API 密钥（Agent 必需）
"""

import asyncio
import os
import sys
from pathlib import Path

import fire
import yaml

# 首先从 ~/.hermes/.env 加载 .env，然后从项目根目录作为开发回退。
# 用户管理的 env 文件应在重启时覆盖过时的 shell 导出。
_hermes_home = get_hermes_home()
_project_env = Path(__file__).parent / '.env'

from hermes_cli.env_loader import load_hermes_dotenv

_loaded_env_paths = load_hermes_dotenv(hermes_home=_hermes_home, project_env=_project_env)
for _env_path in _loaded_env_paths:
    print(f"✅ Loaded environment variables from {_env_path}")

# 将终端工作目录设置为 tinker-atropos 子模块
# 这确保终端命令在 RL 工作的正确上下文中运行
tinker_atropos_dir = Path(__file__).parent / 'tinker-atropos'
if tinker_atropos_dir.exists():
    os.environ['TERMINAL_CWD'] = str(tinker_atropos_dir)
    os.environ['HERMES_QUIET'] = '1'  # 禁用临时子目录创建
    print(f"📂 Terminal working directory: {tinker_atropos_dir}")
else:
    # 如果子模块未找到，回退到 hermes-agent 目录
    os.environ['TERMINAL_CWD'] = str(Path(__file__).parent)
    os.environ['HERMES_QUIET'] = '1'
    print(f"⚠️  tinker-atropos submodule not found, using: {Path(__file__).parent}")

# 导入 Agent 和工具
from run_agent import AIAgent
from tools.rl_training_tool import get_missing_keys


# ============================================================================
# 配置加载
# ============================================================================

from hermes_constants import get_hermes_home, OPENROUTER_BASE_URL

DEFAULT_MODEL = "anthropic/claude-opus-4.5"
DEFAULT_BASE_URL = OPENROUTER_BASE_URL


def load_hermes_config() -> dict:
    """
    从 ~/.hermes/config.yaml 加载配置。

    返回:
        dict: 包含 model、base_url 等的配置
    """
    config_path = _hermes_home / 'config.yaml'
    
    config = {
        "model": DEFAULT_MODEL,
        "base_url": DEFAULT_BASE_URL,
    }
    
    if config_path.exists():
        try:
            with open(config_path, "r") as f:
                file_config = yaml.safe_load(f) or {}
            
            # 从配置中获取模型
            if "model" in file_config:
                if isinstance(file_config["model"], str):
                    config["model"] = file_config["model"]
                elif isinstance(file_config["model"], dict):
                    config["model"] = file_config["model"].get("default", DEFAULT_MODEL)
            
            # 如果指定了 base_url 则获取
            if "base_url" in file_config:
                config["base_url"] = file_config["base_url"]
                
        except Exception as e:
            print(f"⚠️  Warning: Failed to load config.yaml: {e}")
    
    return config


# ============================================================================
# RL 专用配置
# ============================================================================

# 为长时间运行的 RL 操作扩展超时
RL_MAX_ITERATIONS = 200  # 允许更多迭代以支持长工作流

# 以 RL 为重点的系统提示词
RL_SYSTEM_PROMPT = """You are an automated post-training engineer specializing in reinforcement learning for language models.

## Your Capabilities

You have access to RL training tools for running reinforcement learning on models through Tinker-Atropos:

1. **DISCOVER**: Use `rl_list_environments` to see available RL environments
2. **INSPECT**: Read environment files to understand how they work (verifiers, data loading, rewards)
3. **INSPECT DATA**: Use terminal to explore HuggingFace datasets and understand their format
4. **CREATE**: Copy existing environments as templates, modify for your needs
5. **CONFIGURE**: Use `rl_select_environment` and `rl_edit_config` to set up training
6. **TEST**: Always use `rl_test_inference` before full training to validate your setup
7. **TRAIN**: Use `rl_start_training` to begin, `rl_check_status` to monitor
8. **EVALUATE**: Use `rl_get_results` and analyze WandB metrics to assess performance

## Environment Files

Environment files are located in: `tinker-atropos/tinker_atropos/environments/`

Study existing environments to learn patterns. Look for:
- `load_dataset()` calls - how data is loaded
- `score_answer()` / `score()` - verification logic
- `get_next_item()` - prompt formatting
- `system_prompt` - instruction format
- `config_init()` - default configuration

## Creating New Environments

To create a new environment:
1. Read an existing environment file (e.g., gsm8k_tinker.py)
2. Use terminal to explore the target dataset format
3. Copy the environment file as a template
4. Modify the dataset loading, prompt formatting, and verifier logic
5. Test with `rl_test_inference` before training

## Important Guidelines

- **Always test before training**: Training runs take hours - verify everything works first
- **Monitor metrics**: Check WandB for reward/mean and percent_correct
- **Status check intervals**: Wait at least 30 minutes between status checks
- **Early stopping**: Stop training early if metrics look bad or stagnant
- **Iterate quickly**: Start with small total_steps to validate, then scale up

## Available Toolsets

You have access to:
- **RL tools**: Environment discovery, config management, training, testing
- **Terminal**: Run commands, inspect files, explore datasets
- **Web**: Search for information, documentation, papers
- **File tools**: Read and modify code files

When asked to train a model, follow this workflow:
1. List available environments
2. Select and configure the appropriate environment
3. Test with sample prompts
4. Start training with conservative settings
5. Monitor progress and adjust as needed
"""

# 为 RL 工作流启用的工具集
RL_TOOLSETS = ["terminal", "web", "rl"]


# ============================================================================
# 辅助函数
# ============================================================================

def check_requirements():
    """检查所有必需的环境变量和服务是否可用。"""
    errors = []
    
    # 检查 API 密钥
    if not os.getenv("OPENROUTER_API_KEY"):
        errors.append("OPENROUTER_API_KEY not set - required for agent")
    
    missing_rl_keys = get_missing_keys()
    if missing_rl_keys:
        errors.append(f"Missing RL API keys: {', '.join(missing_rl_keys)}")
    
    if errors:
        print("❌ Missing requirements:")
        for error in errors:
            print(f"   - {error}")
        print("\nPlease set these environment variables in your .env file or shell.")
        return False
    
    return True


def check_tinker_atropos():
    """检查 tinker-atropos 子模块是否正确设置。"""
    tinker_path = Path(__file__).parent / "tinker-atropos"
    
    if not tinker_path.exists():
        return False, "tinker-atropos submodule not found. Run: git submodule update --init"
    
    envs_path = tinker_path / "tinker_atropos" / "environments"
    if not envs_path.exists():
        return False, f"environments directory not found at {envs_path}"
    
    env_files = list(envs_path.glob("*.py"))
    env_files = [f for f in env_files if not f.name.startswith("_")]
    
    return True, {"path": str(tinker_path), "environments_count": len(env_files)}


def list_environments_sync():
    """列出可用环境（同步包装器）。"""
    from tools.rl_training_tool import rl_list_environments
    import json
    
    async def _list():
        result = await rl_list_environments()
        return json.loads(result)
    
    return asyncio.run(_list())


# ============================================================================
# 主 CLI
# ============================================================================

def main(
    task: str = None,
    model: str = None,
    api_key: str = None,
    base_url: str = None,
    max_iterations: int = RL_MAX_ITERATIONS,
    interactive: bool = False,
    list_environments: bool = False,
    check_server: bool = False,
    verbose: bool = False,
    save_trajectories: bool = True,
):
    """
    RL 训练 CLI - 专用于 RL 训练工作流的运行器。

    参数:
        task: 训练任务/目标（例如 "Train a model on GSM8k for math"）
        model: Agent 使用的模型（未提供时从 ~/.hermes/config.yaml 读取）
        api_key: OpenRouter API 密钥（未提供时使用 OPENROUTER_API_KEY 环境变量）
        base_url: API 基础 URL（从配置读取或默认使用 OpenRouter）
        max_iterations: 最大 Agent 迭代次数（默认: 200，适用于长工作流）
        interactive: 以交互模式运行（多轮对话）
        list_environments: 仅列出可用的 RL 环境并退出
        check_server: 检查 RL API 服务器是否运行并退出
        verbose: 启用详细日志
        save_trajectories: 保存对话轨迹（默认: True，用于 RL）

    示例:
        # 在特定环境上训练
        python rl_cli.py "Train a model on GSM8k math problems"

        # 交互模式
        python rl_cli.py --interactive

        # 列出可用环境
        python rl_cli.py --list-environments

        # 检查服务器状态
        python rl_cli.py --check-server
    """
    # 从 ~/.hermes/config.yaml 加载配置
    config = load_hermes_config()
    
    # 如果未显式提供，则使用配置值
    if model is None:
        model = config["model"]
    if base_url is None:
        base_url = config["base_url"]
    
    print("🎯 RL Training Agent")
    print("=" * 60)
    
    # 处理设置检查
    if check_server:
        print("\n🔍 Checking tinker-atropos setup...")
        ok, result = check_tinker_atropos()
        if ok:
            print("✅ tinker-atropos submodule found")
            print(f"   Path: {result.get('path')}")
            print(f"   Environments found: {result.get('environments_count', 0)}")
            
            # 同时检查 API 密钥
            missing = get_missing_keys()
            if missing:
                print(f"\n⚠️  Missing API keys: {', '.join(missing)}")
                print("   Add them to ~/.hermes/.env")
            else:
                print("✅ API keys configured")
        else:
            print(f"❌ tinker-atropos not set up: {result}")
            print("\nTo set up:")
            print("  git submodule update --init")
            print("  pip install -e ./tinker-atropos")
        return
    
    # 处理环境列表
    if list_environments:
        print("\n📋 Available RL Environments:")
        print("-" * 40)
        try:
            data = list_environments_sync()
            if "error" in data:
                print(f"❌ Error: {data['error']}")
                return
            
            envs = data.get("environments", [])
            if not envs:
                print("No environments found.")
                print("\nMake sure tinker-atropos is set up:")
                print("  git submodule update --init")
                return
            
            for env in envs:
                print(f"\n  📦 {env['name']}")
                print(f"     Class: {env['class_name']}")
                print(f"     Path: {env['file_path']}")
                if env.get('description'):
                    desc = env['description'][:100] + "..." if len(env.get('description', '')) > 100 else env.get('description', '')
                    print(f"     Description: {desc}")
            
            print(f"\n📊 Total: {len(envs)} environments")
            print("\nUse `rl_select_environment(name)` to select an environment for training.")
        except Exception as e:
            print(f"❌ Error listing environments: {e}")
            print("\nMake sure tinker-atropos is set up:")
            print("  git submodule update --init")
            print("  pip install -e ./tinker-atropos")
        return
    
    # 检查必要条件
    if not check_requirements():
        sys.exit(1)
    
    # 如果没有提供任务且非交互模式，设置默认任务
    if not task and not interactive:
        print("\n⚠️  No task provided. Use --interactive for interactive mode or provide a task.")
        print("\nExamples:")
        print('  python rl_cli.py "Train a model on GSM8k math problems"')
        print('  python rl_cli.py "Create an RL environment for code generation"')
        print('  python rl_cli.py --interactive')
        return
    
    # 获取 API 密钥
    api_key = api_key or os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("❌ No API key provided. Set OPENROUTER_API_KEY or pass --api-key")
        sys.exit(1)
    
    print(f"\n🤖 Model: {model}")
    print(f"🔧 Max iterations: {max_iterations}")
    print(f"📁 Toolsets: {', '.join(RL_TOOLSETS)}")
    print("=" * 60)
    
    # 使用 RL 配置创建 Agent
    agent = AIAgent(
        base_url=base_url,
        api_key=api_key,
        model=model,
        max_iterations=max_iterations,
        enabled_toolsets=RL_TOOLSETS,
        save_trajectories=save_trajectories,
        verbose_logging=verbose,
        quiet_mode=False,
        ephemeral_system_prompt=RL_SYSTEM_PROMPT,
    )
    
    if interactive:
        # 交互模式 - 多轮对话
        print("\n🔄 Interactive RL Training Mode")
        print("Type 'quit' or 'exit' to end the session.")
        print("Type 'status' to check active training runs.")
        print("-" * 40)
        
        while True:
            try:
                user_input = input("\n🎯 RL Task> ").strip()
                
                if not user_input:
                    continue
                
                if user_input.lower() in ('quit', 'exit', 'q'):
                    print("\n👋 Goodbye!")
                    break
                
                if user_input.lower() == 'status':
                    # 快速状态检查
                    from tools.rl_training_tool import rl_list_runs
                    import json
                    result = asyncio.run(rl_list_runs())
                    runs = json.loads(result)
                    if isinstance(runs, list) and runs:
                        print("\n📊 Active Runs:")
                        for run in runs:
                            print(f"  - {run['run_id']}: {run['environment']} ({run['status']})")
                    else:
                        print("\nNo active runs.")
                    continue
                
                # 运行 Agent
                print("\n" + "=" * 60)
                response = agent.run_conversation(user_input)
                print("\n" + "=" * 60)
                
            except KeyboardInterrupt:
                print("\n\n👋 Interrupted. Goodbye!")
                break
            except Exception as e:
                print(f"\n❌ Error: {e}")
                if verbose:
                    import traceback
                    traceback.print_exc()
    else:
        # 单任务模式
        print(f"\n📝 Task: {task}")
        print("-" * 40)
        
        try:
            response = agent.run_conversation(task)
            print("\n" + "=" * 60)
            print("✅ Task completed")
        except KeyboardInterrupt:
            print("\n\n⚠️ Interrupted by user")
        except Exception as e:
            print(f"\n❌ Error: {e}")
            if verbose:
                import traceback
                traceback.print_exc()
            sys.exit(1)


if __name__ == "__main__":
    fire.Fire(main)
