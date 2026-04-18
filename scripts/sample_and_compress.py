#!/usr/bin/env python3
"""
采样与压缩 HuggingFace 数据集

从多个 HuggingFace 数据集下载轨迹数据，随机采样，
并运行轨迹压缩以适应目标 token 预算。

用法:
    python scripts/sample_and_compress.py

    # 自定义采样数量
    python scripts/sample_and_compress.py --total_samples=5000

    # 自定义输出名称
    python scripts/sample_and_compress.py --output_name=compressed_16k
"""

import json
import random
from pathlib import Path
from typing import List, Dict, Any, Tuple
import fire

# 加载环境变量
from dotenv import load_dotenv
load_dotenv()


# 默认的采样数据集列表
DEFAULT_DATASETS = [
    "NousResearch/swe-terminus-agent-glm-kimi-minimax",
    "NousResearch/hermes-agent-megascience-sft1",
    "NousResearch/Hermes-Agent-Thinking-GLM-4.7-SFT2",
    "NousResearch/Hermes-Agent-Thinking-GLM-4.7-SFT1",
    "NousResearch/terminal-tasks-glm-hermes-agent"
]


def load_dataset_from_hf(dataset_name: str) -> List[Dict[str, Any]]:
    """
    从 HuggingFace 加载数据集。

    参数:
        dataset_name: HuggingFace 数据集名称（例如 "NousResearch/dataset-name"）

    返回:
        轨迹条目列表
    """
    from datasets import load_dataset
    
    print(f"   Loading {dataset_name}...")
    
    try:
        # 尝试使用默认配置加载
        ds = load_dataset(dataset_name, split="train")
    except Exception as e:
        print(f"   ⚠️  Error loading {dataset_name}: {e}")
        return []
    
    # 转换为字典列表
    entries = []
    for item in ds:
        # 处理不同的数据格式
        if "conversations" in item:
            entries.append({"conversations": item["conversations"]})
        elif "messages" in item:
            # 如果需要，将 messages 格式转换为 conversations 格式
            entries.append({"conversations": item["messages"]})
        else:
            # 假设整个条目就是数据项
            entries.append(dict(item))
    
    print(f"   ✅ Loaded {len(entries):,} entries from {dataset_name}")
    return entries


# 用于多进程的全局分词器（在 worker 初始化时设置）
_TOKENIZER = None


def _init_tokenizer_worker(tokenizer_name: str):
    """在 worker 进程中初始化分词器。"""
    global _TOKENIZER
    from transformers import AutoTokenizer
    _TOKENIZER = AutoTokenizer.from_pretrained(tokenizer_name, trust_remote_code=True)


def _count_tokens_for_entry(entry: Dict) -> Tuple[Dict, int]:
    """
    统计单个条目的 token 数（用于并行处理）。

    参数:
        entry: 包含 'conversations' 字段的轨迹条目

    返回:
        (条目, token数) 的元组
    """
    global _TOKENIZER
    
    conversations = entry.get("conversations", [])
    if not conversations:
        return entry, 0
    
    total = 0
    for turn in conversations:
        value = turn.get("value", "")
        if value:
            try:
                total += len(_TOKENIZER.encode(value))
            except Exception:
                # 回退到字符数估算
                total += len(value) // 4
    
    return entry, total


def sample_from_datasets(
    datasets: List[str],
    total_samples: int,
    min_tokens: int = 16000,
    tokenizer_name: str = "moonshotai/Kimi-K2-Thinking",
    seed: int = 42,
    num_proc: int = 8
) -> List[Dict[str, Any]]:
    """
    加载所有数据集，按 token 数过滤，然后从合并池中随机采样。

    参数:
        datasets: HuggingFace 数据集名称列表
        total_samples: 要收集的总样本数
        min_tokens: 最小 token 数阈值（仅采样 >= 此值的轨迹）
        tokenizer_name: 用于计数 token 的 HuggingFace 分词器
        seed: 用于可复现性的随机种子
        num_proc: 分词并行进程数

    返回:
        采样后的轨迹条目列表
    """
    from multiprocessing import Pool
    
    random.seed(seed)
    
    print(f"\n📥 Loading {len(datasets)} datasets...")
    print(f"   Minimum tokens: {min_tokens:,} (filtering smaller trajectories)")
    print(f"   Parallel workers: {num_proc}")
    print()
    
    # 将所有数据集的条目加载到一个池中
    all_entries = []
    
    for dataset_name in datasets:
        entries = load_dataset_from_hf(dataset_name)
        
        if not entries:
            print(f"   ⚠️  Skipping {dataset_name} (no entries loaded)")
            continue
        
        # 为每个条目添加来源元数据
        for entry in entries:
            entry["_source_dataset"] = dataset_name
        
        all_entries.extend(entries)
    
    print(f"\n📊 Total entries loaded: {len(all_entries):,}")
    
    # 使用并行处理按 token 数过滤
    print(f"\n🔍 Filtering trajectories with >= {min_tokens:,} tokens (using {num_proc} workers)...")
    
    filtered_entries = []
    token_counts = []
    
    # 使用多进程进行 token 计数
    with Pool(
        processes=num_proc,
        initializer=_init_tokenizer_worker,
        initargs=(tokenizer_name,)
    ) as pool:
        # 分块处理并显示进度
        chunk_size = 1000
        processed = 0
        
        for result in pool.imap_unordered(_count_tokens_for_entry, all_entries, chunksize=100):
            entry, token_count = result
            processed += 1
            
            if processed % chunk_size == 0:
                print(f"   Processed {processed:,}/{len(all_entries):,}...", end="\r")
            
            if token_count >= min_tokens:
                entry["_original_tokens"] = token_count
                filtered_entries.append(entry)
                token_counts.append(token_count)
    
    print(f"\n   ✅ Found {len(filtered_entries):,} trajectories >= {min_tokens:,} tokens")
    
    if token_counts:
        avg_tokens = sum(token_counts) / len(token_counts)
        print(f"   📈 Token stats: min={min(token_counts):,}, max={max(token_counts):,}, avg={avg_tokens:,.0f}")
    
    # 从过滤后的池中随机采样
    if len(filtered_entries) <= total_samples:
        print(f"\n⚠️  Only {len(filtered_entries):,} trajectories available, using all of them")
        sampled = filtered_entries
    else:
        sampled = random.sample(filtered_entries, total_samples)
        print(f"\n✅ Randomly sampled {len(sampled):,} trajectories from pool of {len(filtered_entries):,}")
    
    # 显示来源分布
    source_counts = {}
    for entry in sampled:
        source = entry.get("_source_dataset", "unknown").split("/")[-1]
        source_counts[source] = source_counts.get(source, 0) + 1
    
    print(f"\n📌 Sample distribution by source:")
    for source, count in sorted(source_counts.items()):
        print(f"      {source}: {count:,}")
    
    # 打乱顺序
    random.shuffle(sampled)
    
    return sampled


def save_samples_for_compression(
    samples: List[Dict[str, Any]],
    output_dir: Path,
    batch_size: int = 100
):
    """
    将样本保存为 JSONL 文件以供轨迹压缩使用。

    参数:
        samples: 轨迹条目列表
        output_dir: 保存 JSONL 文件的目录
        batch_size: 每个文件包含的条目数
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 分批保存
    num_batches = (len(samples) + batch_size - 1) // batch_size
    
    print(f"\n💾 Saving {len(samples)} samples to {output_dir}")
    print(f"   Batch size: {batch_size}, Total batches: {num_batches}")
    
    for i in range(num_batches):
        start_idx = i * batch_size
        end_idx = min((i + 1) * batch_size, len(samples))
        batch = samples[start_idx:end_idx]
        
        output_file = output_dir / f"batch_{i}.jsonl"
        with open(output_file, 'w', encoding='utf-8') as f:
            for entry in batch:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    
    print(f"   ✅ Saved {num_batches} batch files")


def run_compression(input_dir: Path, output_dir: Path, config_path: str):
    """
    对采样数据运行轨迹压缩。

    参数:
        input_dir: 包含待压缩 JSONL 文件的目录
        output_dir: 压缩输出目录
        config_path: 压缩配置 YAML 文件路径
    """
    # 导入压缩器
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from trajectory_compressor import TrajectoryCompressor, CompressionConfig
    
    print(f"\n🗜️  Running trajectory compression...")
    print(f"   Input: {input_dir}")
    print(f"   Output: {output_dir}")
    print(f"   Config: {config_path}")
    
    # 加载配置
    config = CompressionConfig.from_yaml(config_path)
    
    # 初始化压缩器
    compressor = TrajectoryCompressor(config)
    
    # 运行压缩
    compressor.process_directory(input_dir, output_dir)


def merge_output_to_single_jsonl(input_dir: Path, output_file: Path):
    """
    将目录中的所有 JSONL 文件合并为单个 JSONL 文件。

    参数:
        input_dir: 包含 JSONL 文件的目录
        output_file: 输出的 JSONL 文件路径
    """
    print(f"\n📦 Merging output files into {output_file.name}...")
    
    all_entries = []
    for jsonl_file in sorted(input_dir.glob("*.jsonl")):
        if jsonl_file.name == output_file.name:
            continue
        with open(jsonl_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    all_entries.append(json.loads(line))
    
    # 写入合并后的文件
    with open(output_file, 'w', encoding='utf-8') as f:
        for entry in all_entries:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    
    print(f"   ✅ Merged {len(all_entries):,} entries into {output_file.name}")
    return output_file


def main(
    total_samples: int = 2500,
    output_name: str = "compressed_agentic",
    datasets: str = None,
    config: str = "configs/trajectory_compression.yaml",
    seed: int = 42,
    batch_size: int = 100,
    min_tokens: int = 16000,
    num_proc: int = 8,
    skip_download: bool = False,
):
    """
    从 HuggingFace 数据集采样轨迹并运行压缩。

    参数:
        total_samples: 要收集的总样本数（默认: 2500）
        output_name: 输出目录/文件的名称（默认: "compressed_agentic"）
        datasets: 逗号分隔的数据集名称列表（未提供时使用默认列表）
        config: 压缩配置 YAML 文件路径
        seed: 用于可复现性的随机种子
        batch_size: 处理期间每个 JSONL 文件的条目数
        min_tokens: 过滤轨迹的最小 token 数（默认: 16000）
        num_proc: 分词并行 worker 数（默认: 8）
        skip_download: 跳过下载，使用已有的采样数据
    """
    print("=" * 70)
    print("📊 TRAJECTORY SAMPLING AND COMPRESSION")
    print("=" * 70)
    
    # 解析数据集列表
    if datasets:
        dataset_list = [d.strip() for d in datasets.split(",")]
    else:
        dataset_list = DEFAULT_DATASETS
    
    print(f"\n📋 Configuration:")
    print(f"   Total samples: {total_samples:,}")
    print(f"   Min tokens filter: {min_tokens:,}")
    print(f"   Parallel workers: {num_proc}")
    print(f"   Datasets: {len(dataset_list)}")
    for ds in dataset_list:
        print(f"      - {ds}")
    print(f"   Output name: {output_name}")
    print(f"   Config: {config}")
    print(f"   Seed: {seed}")
    
    # 设置路径
    base_dir = Path(__file__).parent.parent
    sampled_dir = base_dir / "data" / f"{output_name}_raw"
    compressed_dir = base_dir / "data" / f"{output_name}_batches"
    final_output = base_dir / "data" / f"{output_name}.jsonl"
    
    if not skip_download:
        # 步骤 1: 下载，按 token 数过滤，并从合并池中采样
        samples = sample_from_datasets(
            dataset_list, 
            total_samples, 
            min_tokens=min_tokens,
            seed=seed,
            num_proc=num_proc
        )
        
        if not samples:
            print("❌ No samples collected. Exiting.")
            return
        
        # 步骤 2: 保存为 JSONL 文件
        save_samples_for_compression(samples, sampled_dir, batch_size)
    else:
        print(f"\n⏭️  Skipping download, using existing data in {sampled_dir}")
    
    # 步骤 3: 运行压缩
    config_path = base_dir / config
    if not config_path.exists():
        print(f"❌ Config not found: {config_path}")
        return
    
    run_compression(sampled_dir, compressed_dir, str(config_path))
    
    # 步骤 4: 合并为单个 JSONL 文件
    merge_output_to_single_jsonl(compressed_dir, final_output)
    
    print("\n" + "=" * 70)
    print("✅ COMPLETE!")
    print("=" * 70)
    print(f"\n📁 Raw samples:        {sampled_dir}")
    print(f"📁 Compressed batches: {compressed_dir}")
    print(f"📁 Final output:       {final_output}")
    print(f"\nTo upload to HuggingFace:")
    print(f"   huggingface-cli upload NousResearch/{output_name} {final_output}")


if __name__ == "__main__":
    fire.Fire(main)
