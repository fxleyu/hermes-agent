"""
基础 GRPO 训练模板
=============================

一个最小化的、可用于生产的 GRPO 训练模板，基于 TRL 框架。
通过修改以下内容来适配你的具体任务:
1. 数据集加载 (get_dataset 函数)
2. 奖励函数 (reward_*_func)
3. 系统提示词 (SYSTEM_PROMPT)
4. 超参数 (GRPOConfig)
"""

import torch
import re
from datasets import load_dataset
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import LoraConfig
from trl import GRPOTrainer, GRPOConfig

# ==================== 配置 ====================

MODEL_NAME = "Qwen/Qwen2.5-1.5B-Instruct"
OUTPUT_DIR = "outputs/grpo-model"
MAX_PROMPT_LENGTH = 256
MAX_COMPLETION_LENGTH = 512

SYSTEM_PROMPT = """
Respond in the following format:
<reasoning>
[Your step-by-step thinking]
</reasoning>
<answer>
[Final answer]
</answer>
"""

# ==================== 数据集 ====================

def get_dataset(split="train"):
    """
    加载并准备数据集。

    返回: 包含以下列的 Dataset:
    - 'prompt': List[Dict] 包含 role/content
    - 'answer': str (标准答案，可选)
    """
    # 示例: GSM8K 数学数据集
    data = load_dataset('openai/gsm8k', 'main')[split]

    def process_example(x):
        # 提取标准答案
        answer = x['answer'].split('####')[1].strip() if '####' in x['answer'] else None

        return {
            'prompt': [
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': x['question']}
            ],
            'answer': answer
        }

    return data.map(process_example)

# ==================== 辅助函数 ====================

def extract_xml_tag(text: str, tag: str) -> str:
    """提取 XML 标签之间的内容。"""
    pattern = f'<{tag}>(.*?)</{tag}>'
    match = re.search(pattern, text, re.DOTALL)
    return match.group(1).strip() if match else ""

def extract_answer(text: str) -> str:
    """从结构化输出中提取最终答案。"""
    return extract_xml_tag(text, 'answer')

# ==================== 奖励函数 ====================

def correctness_reward_func(prompts, completions, answer, **kwargs):
    """
    奖励正确答案。
    权重: 2.0（最高优先级）
    """
    responses = [comp[0]['content'] for comp in completions]
    extracted = [extract_answer(r) for r in responses]
    return [2.0 if ans == gt else 0.0 for ans, gt in zip(extracted, answer)]

def format_reward_func(completions, **kwargs):
    """
    奖励正确的 XML 格式。
    权重: 0.5
    """
    pattern = r'<reasoning>.*?</reasoning>\s*<answer>.*?</answer>'
    responses = [comp[0]['content'] for comp in completions]
    return [0.5 if re.search(pattern, r, re.DOTALL) else 0.0 for r in responses]

def incremental_format_reward_func(completions, **kwargs):
    """
    渐进式奖励部分格式合规。
    权重: 最高 0.5
    """
    responses = [comp[0]['content'] for comp in completions]
    rewards = []

    for r in responses:
        score = 0.0
        if '<reasoning>' in r:
            score += 0.125
        if '</reasoning>' in r:
            score += 0.125
        if '<answer>' in r:
            score += 0.125
        if '</answer>' in r:
            score += 0.125

        # 惩罚结束标签后的额外内容
        if '</answer>' in r:
            extra = r.split('</answer>')[-1].strip()
            score -= len(extra) * 0.001

        rewards.append(score)

    return rewards

# ==================== 模型设置 ====================

def setup_model_and_tokenizer():
    """加载模型和分词器并进行优化配置。"""
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_NAME,
        torch_dtype=torch.bfloat16,
        attn_implementation="flash_attention_2",
        device_map="auto"
    )

    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    tokenizer.pad_token = tokenizer.eos_token

    return model, tokenizer

def get_peft_config():
    """用于参数高效训练的 LoRA 配置。"""
    return LoraConfig(
        r=16,
        lora_alpha=32,
        target_modules=[
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj"
        ],
        task_type="CAUSAL_LM",
        lora_dropout=0.05,
    )

# ==================== 训练 ====================

def main():
    """主训练函数。"""

    # 加载数据
    print("Loading dataset...")
    dataset = get_dataset()
    print(f"Dataset size: {len(dataset)}")

    # 设置模型
    print("Loading model...")
    model, tokenizer = setup_model_and_tokenizer()

    # 训练配置
    training_args = GRPOConfig(
        output_dir=OUTPUT_DIR,
        run_name="grpo-training",

        # 学习率
        learning_rate=5e-6,
        adam_beta1=0.9,
        adam_beta2=0.99,
        weight_decay=0.1,
        warmup_ratio=0.1,
        lr_scheduler_type='cosine',

        # 批次设置
        per_device_train_batch_size=1,
        gradient_accumulation_steps=4,

        # GRPO 特有参数
        num_generations=8,
        max_prompt_length=MAX_PROMPT_LENGTH,
        max_completion_length=MAX_COMPLETION_LENGTH,

        # 训练时长
        num_train_epochs=1,

        # 优化设置
        bf16=True,
        optim="adamw_8bit",
        max_grad_norm=0.1,

        # 日志记录
        logging_steps=1,
        save_steps=100,
        report_to="wandb",  # 改为 "none" 以禁用日志记录
    )

    # 初始化训练器
    trainer = GRPOTrainer(
        model=model,
        processing_class=tokenizer,
        reward_funcs=[
            incremental_format_reward_func,
            format_reward_func,
            correctness_reward_func,
        ],
        args=training_args,
        train_dataset=dataset,
        peft_config=get_peft_config(),
    )

    # 开始训练
    print("Starting training...")
    trainer.train()

    # 保存最终模型
    print(f"Saving model to {OUTPUT_DIR}/final")
    trainer.save_model(f"{OUTPUT_DIR}/final")

    print("Training complete!")

if __name__ == "__main__":
    main()
