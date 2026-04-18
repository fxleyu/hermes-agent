#!/usr/bin/env python3
"""
工具集分布模块 (Toolset Distributions Module)

本模块定义了用于数据生成运行的工具集分布。
每个分布指定了在批处理过程中应使用哪些工具集，
以及每个工具集在任意给定提示下被选中的概率。

分布是一个将工具集名称映射到其选择概率(%)的字典。
概率之和应为 100，但如果不是，系统会自动归一化。

使用方法:
    from toolset_distributions import get_distribution, list_distributions

    # 获取特定分布
    dist = get_distribution("image_gen")

    # 列出所有可用分布
    all_dists = list_distributions()
"""

from typing import Dict, List, Optional
import random
from toolsets import validate_toolset


# 分布定义
# 每个键是分布名称，值是 {工具集名称: 概率百分比} 的字典
DISTRIBUTIONS = {
    # 默认: 所有工具 100% 可用
    "default": {
        "description": "All available tools, all the time",
        "toolsets": {
            "web": 100,
            "vision": 100,
            "image_gen": 100,
            "terminal": 100,
            "file": 100,
            "moa": 100,
            "browser": 100
        }
    },

    # 侧重图像生成的分布
    "image_gen": {
        "description": "Heavy focus on image generation with vision and web support",
        "toolsets": {
            "image_gen": 90,  # 90% 概率包含图像生成工具
            "vision": 90,      # 90% 概率包含视觉工具
            "web": 55,         # 55% 概率包含网络工具
            "terminal": 45,
            "moa": 10          # 10% 概率包含推理工具
        }
    },

    # 侧重研究的分布
    "research": {
        "description": "Web research with vision analysis and reasoning",
        "toolsets": {
            "web": 90,       # 90% 概率包含网络工具
            "browser": 70,   # 70% 概率包含浏览器工具用于深度研究
            "vision": 50,    # 50% 概率包含视觉工具
            "moa": 40,       # 40% 概率包含推理工具
            "terminal": 10   # 10% 概率包含终端工具
        }
    },

    # 侧重科学问题求解的分布
    "science": {
        "description": "Scientific research with web, terminal, file, and browser capabilities",
        "toolsets": {
            "web": 94,       # 94% 概率包含网络工具
            "terminal": 94,  # 94% 概率包含终端工具
            "file": 94,      # 94% 概率包含文件工具
            "vision": 65,    # 65% 概率包含视觉工具
            "browser": 50,   # 50% 概率包含浏览器用于访问论文/数据库
            "image_gen": 15, # 15% 概率包含图像生成工具
            "moa": 10        # 10% 概率包含推理工具
        }
    },

    # 侧重开发的分布
    "development": {
        "description": "Terminal, file tools, and reasoning with occasional web lookup",
        "toolsets": {
            "terminal": 80,  # 80% 概率包含终端工具
            "file": 80,      # 80% 概率包含文件工具（读、写、补丁、搜索）
            "moa": 60,       # 60% 概率包含推理工具
            "web": 30,       # 30% 概率包含网络工具
            "vision": 10     # 10% 概率包含视觉工具
        }
    },

    # 安全模式（无终端）
    "safe": {
        "description": "All tools except terminal for safety",
        "toolsets": {
            "web": 80,
            "browser": 70,   # 浏览器是安全的（不访问本地文件系统）
            "vision": 60,
            "image_gen": 60,
            "moa": 50
        }
    },

    # 均衡分布
    "balanced": {
        "description": "Equal probability of all toolsets",
        "toolsets": {
            "web": 50,
            "vision": 50,
            "image_gen": 50,
            "terminal": 50,
            "file": 50,
            "moa": 50,
            "browser": 50
        }
    },

    # 最小化（仅网络）
    "minimal": {
        "description": "Only web tools for basic research",
        "toolsets": {
            "web": 100
        }
    },

    # 仅终端
    "terminal_only": {
        "description": "Terminal and file tools for code execution tasks",
        "toolsets": {
            "terminal": 100,
            "file": 100
        }
    },

    # 终端 + 网络（常用于需要查阅文档的编码任务）
    "terminal_web": {
        "description": "Terminal and file tools with web search for documentation lookup",
        "toolsets": {
            "terminal": 100,
            "file": 100,
            "web": 100
        }
    },

    # 创意型（视觉 + 图像生成）
    "creative": {
        "description": "Image generation and vision analysis focus",
        "toolsets": {
            "image_gen": 90,
            "vision": 90,
            "web": 30
        }
    },

    # 侧重推理
    "reasoning": {
        "description": "Heavy mixture of agents usage with minimal other tools",
        "toolsets": {
            "moa": 90,
            "web": 30,
            "terminal": 20
        }
    },

    # 基于浏览器的网页交互
    "browser_use": {
        "description": "Full browser-based web interaction with search, vision, and page control",
        "toolsets": {
            "browser": 100,  # 所有浏览器工具始终可用
            "web": 80,       # 网络搜索用于查找 URL 和快速查询
            "vision": 70     # 视觉分析用于页面上发现的图片
        }
    },

    # 仅浏览器（无其他工具）
    "browser_only": {
        "description": "Only browser automation tools for pure web interaction tasks",
        "toolsets": {
            "browser": 100
        }
    },

    # 侧重浏览器任务的分布（用于 browser-use-tasks.jsonl）
    "browser_tasks": {
        "description": "Browser-focused distribution (browser toolset includes web_search for finding URLs since Google blocks direct browser searches)",
        "toolsets": {
            "browser": 97,   # 97% - 浏览器工具（包含 web_search）几乎始终可用
            "vision": 12,    # 12% - 偶尔使用视觉分析
            "terminal": 15   # 15% - 偶尔使用终端进行本地操作
        }
    },

    # 侧重终端任务的分布（用于 nous-terminal-tasks.jsonl）
    "terminal_tasks": {
        "description": "Terminal-focused distribution with high terminal/file availability, occasional other tools",
        "toolsets": {
            "terminal": 97,   # 97% - 终端几乎始终可用
            "file": 97,       # 97% - 文件工具几乎始终可用
            "web": 97,        # 97% - 网络搜索/抓取用于查阅文档
            "browser": 75,    # 75% - 偶尔使用浏览器进行网页交互
            "vision": 50,     # 50% - 偶尔使用视觉分析
            "image_gen": 10   # 10% - 极少使用图像生成
        }
    },

    # 混合浏览器+终端任务分布（用于 mixed-browser-terminal-tasks.jsonl）
    "mixed_tasks": {
        "description": "Mixed distribution with high browser, terminal, and file availability for complex tasks",
        "toolsets": {
            "browser": 92,    # 92% - 浏览器工具高可用
            "terminal": 92,   # 92% - 终端高可用
            "file": 92,       # 92% - 文件工具高可用
            "web": 35,        # 35% - 网络搜索/抓取较常见
            "vision": 15,     # 15% - 偶尔使用视觉分析
            "image_gen": 15   # 15% - 偶尔使用图像生成
        }
    }
}


def get_distribution(name: str) -> Optional[Dict[str, any]]:
    """
    根据名称获取工具集分布。

    参数:
        name (str): 分布名称

    返回:
        Dict: 包含 description 和 toolsets 的分布定义
        None: 如果未找到该分布
    """
    return DISTRIBUTIONS.get(name)


def list_distributions() -> Dict[str, Dict]:
    """
    列出所有可用的分布。

    返回:
        Dict: 所有分布定义
    """
    return DISTRIBUTIONS.copy()


def sample_toolsets_from_distribution(distribution_name: str) -> List[str]:
    """
    根据分布的概率进行工具集采样。

    分布中的每个工具集有一定百分比的概率被包含。
    这允许多个工具集同时处于激活状态。

    参数:
        distribution_name (str): 要采样的分布名称

    返回:
        List[str]: 采样得到的工具集名称列表

    异常:
        ValueError: 如果分布名称未找到
    """
    dist = get_distribution(distribution_name)
    if not dist:
        raise ValueError(f"Unknown distribution: {distribution_name}")

    # 对每个工具集独立地根据其概率进行采样
    selected_toolsets = []

    for toolset_name, probability in dist["toolsets"].items():
        # 验证工具集是否存在
        if not validate_toolset(toolset_name):
            print(f"⚠️  Warning: Toolset '{toolset_name}' in distribution '{distribution_name}' is not valid")
            continue

        # 掷骰子 —— 如果随机值小于概率，则包含此工具集
        if random.random() * 100 < probability:
            selected_toolsets.append(toolset_name)

    # 如果没有工具集被选中（在低概率时可能发生），
    # 确保至少选中一个工具集：选择概率最高的那个
    if not selected_toolsets and dist["toolsets"]:
        # 找到概率最高的工具集
        highest_prob_toolset = max(dist["toolsets"].items(), key=lambda x: x[1])[0]
        if validate_toolset(highest_prob_toolset):
            selected_toolsets.append(highest_prob_toolset)

    return selected_toolsets


def validate_distribution(distribution_name: str) -> bool:
    """
    检查分布名称是否有效。

    参数:
        distribution_name (str): 要验证的分布名称

    返回:
        bool: 有效返回 True，否则返回 False
    """
    return distribution_name in DISTRIBUTIONS


def print_distribution_info(distribution_name: str) -> None:
    """
    打印分布的详细信息。

    参数:
        distribution_name (str): 分布名称
    """
    dist = get_distribution(distribution_name)
    if not dist:
        print(f"❌ Unknown distribution: {distribution_name}")
        return

    print(f"\n📊 Distribution: {distribution_name}")
    print(f"   Description: {dist['description']}")
    print("   Toolsets:")
    for toolset, prob in sorted(dist["toolsets"].items(), key=lambda x: x[1], reverse=True):
        print(f"     • {toolset:15} : {prob:3}% chance")


if __name__ == "__main__":
    """
    分布系统的演示和测试
    """
    print("📊 Toolset Distributions Demo")
    print("=" * 60)

    # 列出所有分布
    print("\n📋 Available Distributions:")
    print("-" * 40)
    for name, dist in list_distributions().items():
        print(f"\n  {name}:")
        print(f"    {dist['description']}")
        toolset_list = ", ".join([f"{ts}({p}%)" for ts, p in dist["toolsets"].items()])
        print(f"    Toolsets: {toolset_list}")

    # 演示采样
    print("\n\n🎲 Sampling Examples:")
    print("-" * 40)

    test_distributions = ["image_gen", "research", "balanced", "default"]

    for dist_name in test_distributions:
        print(f"\n{dist_name}:")
        # 采样 5 次以展示变化性
        samples = []
        for _ in range(5):
            sampled = sample_toolsets_from_distribution(dist_name)
            samples.append(sorted(sampled))

        print(f"  Sample 1: {samples[0]}")
        print(f"  Sample 2: {samples[1]}")
        print(f"  Sample 3: {samples[2]}")
        print(f"  Sample 4: {samples[3]}")
        print(f"  Sample 5: {samples[4]}")

    # 展示详细信息
    print("\n\n📊 Detailed Distribution Info:")
    print("-" * 40)
    print_distribution_info("image_gen")
    print_distribution_info("research")
