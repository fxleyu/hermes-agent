#!/usr/bin/env python3
"""
Base 区块链 CLI 工具（Hermes Agent 专用）
------------------------------------------
通过 Base（以太坊 L2）JSON-RPC API 和 CoinGecko 查询丰富的链上数据。
仅使用 Python 标准库，无需安装额外依赖包。

用法:
  python3 base_client.py stats
  python3 base_client.py wallet   <address> [--limit N] [--all] [--no-prices]
  python3 base_client.py tx       <hash>
  python3 base_client.py token    <contract_address>
  python3 base_client.py gas
  python3 base_client.py contract <address>
  python3 base_client.py whales   [--min-eth N]
  python3 base_client.py price    <contract_address_or_symbol>

环境变量:
  BASE_RPC_URL  覆盖默认的 RPC 端点（默认值: https://mainnet.base.org）
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Tuple

RPC_URL = os.environ.get(
    "BASE_RPC_URL",
    "https://mainnet.base.org",
)

WEI_PER_ETH = 10**18
GWEI = 10**9

# ERC-20 函数选择器（keccak256 哈希的前 4 字节）
SEL_BALANCE_OF   = "70a08231"
SEL_NAME         = "06fdde03"
SEL_SYMBOL       = "95d89b41"
SEL_DECIMALS     = "313ce567"
SEL_TOTAL_SUPPLY = "18160ddd"

# ERC-165 supportsInterface(bytes4) 选择器
SEL_SUPPORTS_INTERFACE = "01ffc9a7"

# 用于 ERC-165 检测的接口 ID
IFACE_ERC721  = "80ac58cd"
IFACE_ERC1155 = "d9b67a26"

# Transfer(address,address,uint256) 事件主题
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# 已知的 Base 代币 — 将小写地址映射到 (符号, 名称, 精度)。
KNOWN_TOKENS: Dict[str, Tuple[str, str, int]] = {
    "0x4200000000000000000000000000000000000006": ("WETH",   "Wrapped Ether",               18),
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": ("USDC",   "USD Coin",                     6),
    "0x2ae3f1ec7f1f5012cfeab0185bfc7aa3cf0dec22": ("cbETH",  "Coinbase Wrapped Staked ETH", 18),
    "0x940181a94a35a4569e4529a3cdfb74e38fd98631": ("AERO",   "Aerodrome Finance",           18),
    "0x4ed4e862860bed51a9570b96d89af5e1b0efefed": ("DEGEN",  "Degen",                       18),
    "0xac1bd2486aaf3b5c0fc3fd868558b082a531b2b4": ("TOSHI",  "Toshi",                       18),
    "0x532f27101965dd16442e59d40670faf5ebb142e4": ("BRETT",  "Brett",                       18),
    "0xa88594d404727625a9437c3f886c7643872296ae": ("WELL",   "Moonwell",                    18),
    "0xc1cba3fcea344f92d9239c08c0568f6f2f0ee452": ("wstETH", "Wrapped Lido Staked ETH",     18),
    "0xb6fe221fe9eef5aba221c348ba20a1bf5e73624c": ("rETH",   "Rocket Pool ETH",             18),
    "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf": ("cbBTC",  "Coinbase Wrapped BTC",         8),
}

# 反向查找: 符号 -> 合约地址（用于 `price` 命令）。
_SYMBOL_TO_ADDRESS = {v[0].upper(): k for k, v in KNOWN_TOKENS.items()}
_SYMBOL_TO_ADDRESS["ETH"] = "ETH"


# ---------------------------------------------------------------------------
# HTTP / RPC 辅助函数
# ---------------------------------------------------------------------------

def _http_get_json(url: str, timeout: int = 10, retries: int = 2) -> Any:
    """通过 GET 请求获取 JSON 数据，遇到 429 限流时自动重试。返回解析后的 JSON 或 None。"""
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url, headers={"Accept": "application/json", "User-Agent": "HermesAgent/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < retries:
                time.sleep(2.0 * (attempt + 1))
                continue
            return None
        except Exception:
            return None
    return None


def _rpc_call(method: str, params: list = None, retries: int = 2) -> Any:
    """发送 JSON-RPC 请求，遇到 429 限流时自动重试。"""
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "method": method, "params": params or [],
    }).encode()

    _headers = {"Content-Type": "application/json", "User-Agent": "HermesAgent/1.0"}

    for attempt in range(retries + 1):
        req = urllib.request.Request(
            RPC_URL, data=payload, headers=_headers, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = json.load(resp)
            if "error" in body:
                err = body["error"]
                if isinstance(err, dict) and err.get("code") == 429:
                    if attempt < retries:
                        time.sleep(1.5 * (attempt + 1))
                        continue
                sys.exit(f"RPC error: {err}")
            return body.get("result")
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            sys.exit(f"RPC HTTP error: {exc}")
        except urllib.error.URLError as exc:
            sys.exit(f"RPC connection error: {exc}")
    return None


# 保持向后兼容的别名。
rpc = _rpc_call


_BATCH_LIMIT = 10  # Base 公共 RPC 限制每批最多 10 个调用


def _rpc_batch_chunk(items: list) -> list:
    """发送单个批量 JSON-RPC 请求（最多 _BATCH_LIMIT 个）。"""
    payload = json.dumps(items).encode()
    _headers = {"Content-Type": "application/json", "User-Agent": "HermesAgent/1.0"}

    for attempt in range(3):
        req = urllib.request.Request(
            RPC_URL, data=payload, headers=_headers, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.load(resp)
            # 如果 RPC 返回错误字典而非列表，视为失败
            if isinstance(data, dict) and "error" in data:
                sys.exit(f"RPC batch error: {data['error']}")
            return data if isinstance(data, list) else []
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            sys.exit(f"RPC batch HTTP error: {exc}")
        except urllib.error.URLError as exc:
            sys.exit(f"RPC batch error: {exc}")
    return []


def rpc_batch(calls: list) -> list:
    """发送批量 JSON-RPC 请求，自动分块以遵守限流。"""
    items = [
        {"jsonrpc": "2.0", "id": i, "method": c["method"], "params": c.get("params", [])}
        for i, c in enumerate(calls)
    ]

    if len(items) <= _BATCH_LIMIT:
        return _rpc_batch_chunk(items)

    # 按 _BATCH_LIMIT 大小分块发送
    all_results = []
    for start in range(0, len(items), _BATCH_LIMIT):
        chunk = items[start:start + _BATCH_LIMIT]
        all_results.extend(_rpc_batch_chunk(chunk))
    return all_results


def wei_to_eth(wei: int) -> float:
    return wei / WEI_PER_ETH


def wei_to_gwei(wei: int) -> float:
    return wei / GWEI


def hex_to_int(hex_str: Optional[str]) -> int:
    """将十六进制字符串（0x...）转换为整数。None 或空值返回 0。"""
    if not hex_str or hex_str == "0x":
        return 0
    return int(hex_str, 16)


def print_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2))


def _short_addr(addr: str) -> str:
    """缩写地址用于显示: 前 6 位 + 后 4 位。"""
    if len(addr) <= 14:
        return addr
    return f"{addr[:6]}...{addr[-4:]}"


# ---------------------------------------------------------------------------
# ABI 编解码辅助函数
# ---------------------------------------------------------------------------

def _encode_address(addr: str) -> str:
    """将地址 ABI 编码为 32 字节的十六进制字符串（无 0x 前缀）。"""
    clean = addr.lower().replace("0x", "")
    return clean.zfill(64)


def _decode_uint(hex_data: Optional[str]) -> int:
    """解码十六进制编码的 uint256 返回值。"""
    if not hex_data or hex_data == "0x":
        return 0
    return int(hex_data.replace("0x", ""), 16)


def _decode_string(hex_data: Optional[str]) -> str:
    """解码 ABI 编码的字符串返回值。"""
    if not hex_data or hex_data == "0x" or len(hex_data) < 130:
        return ""
    data = hex_data[2:] if hex_data.startswith("0x") else hex_data
    try:
        length = int(data[64:128], 16)
        if length == 0 or length > 256:
            return ""
        str_hex = data[128:128 + length * 2]
        return bytes.fromhex(str_hex).decode("utf-8").strip("\x00")
    except (ValueError, UnicodeDecodeError):
        return ""


def _eth_call(to: str, selector: str, args: str = "", block: str = "latest") -> Optional[str]:
    """使用函数选择器执行 eth_call。回退/出错时返回 None。"""
    data = "0x" + selector + args
    try:
        payload = json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "method": "eth_call", "params": [{"to": to, "data": data}, block],
        }).encode()
        req = urllib.request.Request(
            RPC_URL, data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "HermesAgent/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.load(resp)
        if "error" in body:
            return None
        return body.get("result")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 价格与代币名称辅助函数（CoinGecko — 免费，无需 API 密钥）
# ---------------------------------------------------------------------------

def fetch_prices(addresses: List[str], max_lookups: int = 20) -> Dict[str, float]:
    """通过 CoinGecko 获取 Base 代币地址的 USD 价格（逐个请求）。

    CoinGecko 免费版不支持批量查询 Base 代币价格，
    因此逐个发起请求 — 限制为 *max_lookups* 次以遵守
    速率限制。返回 {小写地址: USD 价格}。
    """
    prices: Dict[str, float] = {}
    for i, addr in enumerate(addresses[:max_lookups]):
        url = (
            f"https://api.coingecko.com/api/v3/simple/token_price/base"
            f"?contract_addresses={addr}&vs_currencies=usd"
        )
        data = _http_get_json(url, timeout=10)
        if data and isinstance(data, dict):
            for key, info in data.items():
                if isinstance(info, dict) and "usd" in info:
                    prices[addr.lower()] = info["usd"]
                    break
        # 在请求之间暂停以遵守 CoinGecko 免费版速率限制
        if i < len(addresses[:max_lookups]) - 1:
            time.sleep(1.0)
    return prices


def fetch_eth_price() -> Optional[float]:
    """通过 CoinGecko 获取当前 ETH 的 USD 价格。"""
    data = _http_get_json(
        "https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd"
    )
    if data and "ethereum" in data:
        return data["ethereum"].get("usd")
    return None


def resolve_token_name(addr: str) -> Optional[Dict[str, str]]:
    """查找代币名称和符号。优先检查已知代币，然后查询链上数据。

    返回 {"name": ..., "symbol": ...} 或 None。
    """
    addr_lower = addr.lower()
    if addr_lower in KNOWN_TOKENS:
        sym, name, _ = KNOWN_TOKENS[addr_lower]
        return {"symbol": sym, "name": name}
    # 尝试从合约读取 name() 和 symbol()
    name_hex = _eth_call(addr, SEL_NAME)
    symbol_hex = _eth_call(addr, SEL_SYMBOL)
    name = _decode_string(name_hex) if name_hex else ""
    symbol = _decode_string(symbol_hex) if symbol_hex else ""
    if symbol:
        return {"symbol": symbol.upper(), "name": name}
    return None


def _token_label(addr: str) -> str:
    """返回人类可读的标签: 如果是已知代币则返回符号，否则返回缩写地址。"""
    addr_lower = addr.lower()
    if addr_lower in KNOWN_TOKENS:
        return KNOWN_TOKENS[addr_lower][0]
    return _short_addr(addr)


# ---------------------------------------------------------------------------
# 1. 网络状态
# ---------------------------------------------------------------------------

def cmd_stats(_args):
    """Base 网络健康状况: 区块号、Gas、链 ID、ETH 价格。"""
    results = rpc_batch([
        {"method": "eth_blockNumber"},
        {"method": "eth_gasPrice"},
        {"method": "eth_chainId"},
        {"method": "eth_getBlockByNumber", "params": ["latest", False]},
    ])

    by_id = {r["id"]: r.get("result") for r in results}

    block_num = hex_to_int(by_id.get(0))
    gas_price = hex_to_int(by_id.get(1))
    chain_id  = hex_to_int(by_id.get(2))
    block     = by_id.get(3) or {}

    base_fee  = hex_to_int(block.get("baseFeePerGas")) if block.get("baseFeePerGas") else None
    timestamp = hex_to_int(block.get("timestamp")) if block.get("timestamp") else None
    gas_used  = hex_to_int(block.get("gasUsed")) if block.get("gasUsed") else None
    gas_limit = hex_to_int(block.get("gasLimit")) if block.get("gasLimit") else None
    tx_count  = len(block.get("transactions", []))

    eth_price = fetch_eth_price()

    out = {
        "chain":            "Base" if chain_id == 8453 else f"Chain {chain_id}",
        "chain_id":         chain_id,
        "latest_block":     block_num,
        "gas_price_gwei":   round(wei_to_gwei(gas_price), 4),
    }
    if base_fee is not None:
        out["base_fee_gwei"] = round(wei_to_gwei(base_fee), 4)
    if timestamp:
        out["block_timestamp"] = timestamp
    if gas_used is not None and gas_limit:
        out["block_gas_used"]         = gas_used
        out["block_gas_limit"]        = gas_limit
        out["block_utilization_pct"]  = round(gas_used / gas_limit * 100, 2)
    out["block_tx_count"] = tx_count
    if eth_price is not None:
        out["eth_price_usd"] = eth_price
    print_json(out)


# ---------------------------------------------------------------------------
# 2. 钱包信息（ETH + ERC-20 余额及价格）
# ---------------------------------------------------------------------------

def cmd_wallet(args):
    """ETH 余额 + ERC-20 代币持仓及 USD 估值。"""
    address  = args.address.lower()
    show_all = getattr(args, "all", False)
    limit    = getattr(args, "limit", 20) or 20
    skip_prices = getattr(args, "no_prices", False)

    # 批量请求: ETH 余额 + 所有已知代币的 balanceOf
    calls = [{"method": "eth_getBalance", "params": [address, "latest"]}]
    token_addrs = list(KNOWN_TOKENS.keys())
    for token_addr in token_addrs:
        calls.append({
            "method": "eth_call",
            "params": [
                {"to": token_addr, "data": "0x" + SEL_BALANCE_OF + _encode_address(address)},
                "latest",
            ],
        })

    results = rpc_batch(calls)
    by_id = {r["id"]: r.get("result") for r in results}

    eth_balance = wei_to_eth(hex_to_int(by_id.get(0)))

    # 解析代币余额
    tokens = []
    for i, token_addr in enumerate(token_addrs):
        raw = hex_to_int(by_id.get(i + 1))
        if raw == 0:
            continue
        sym, name, decimals = KNOWN_TOKENS[token_addr]
        amount = raw / (10 ** decimals)
        tokens.append({
            "address":  token_addr,
            "symbol":   sym,
            "name":     name,
            "amount":   amount,
            "decimals": decimals,
        })

    # 获取价格
    eth_price = None
    prices: Dict[str, float] = {}
    if not skip_prices:
        eth_price = fetch_eth_price()
        if tokens:
            mints_to_price = [t["address"] for t in tokens]
            prices = fetch_prices(mints_to_price, max_lookups=20)

    # 附加 USD 估值，过滤小额代币（灰尘），排序
    enriched = []
    dust_count = 0
    dust_value = 0.0
    for t in tokens:
        usd_price = prices.get(t["address"])
        usd_value = round(usd_price * t["amount"], 2) if usd_price else None

        if not show_all and usd_value is not None and usd_value < 0.01:
            dust_count += 1
            dust_value += usd_value
            continue

        entry = {"token": t["symbol"], "address": t["address"], "amount": t["amount"]}
        if usd_price is not None:
            entry["price_usd"] = usd_price
            entry["value_usd"] = usd_value
        enriched.append(entry)

    # 排序: 有已知 USD 价值的优先（从高到低），然后是未知的
    enriched.sort(
        key=lambda x: (x.get("value_usd") is not None, x.get("value_usd") or 0),
        reverse=True,
    )

    # 除非 --all，否则应用数量限制
    total_tokens = len(enriched)
    if not show_all and len(enriched) > limit:
        enriched = enriched[:limit]
    hidden_tokens = total_tokens - len(enriched)

    # 计算投资组合总价值
    total_usd = sum(t.get("value_usd", 0) for t in enriched)
    eth_value_usd = round(eth_price * eth_balance, 2) if eth_price else None
    if eth_value_usd:
        total_usd += eth_value_usd
    total_usd += dust_value

    output = {
        "address":     args.address,
        "eth_balance": round(eth_balance, 18),
    }
    if eth_price:
        output["eth_price_usd"] = eth_price
        output["eth_value_usd"] = eth_value_usd
    output["tokens_shown"] = len(enriched)
    if hidden_tokens > 0:
        output["tokens_hidden"] = hidden_tokens
    output["erc20_tokens"] = enriched
    if dust_count > 0:
        output["dust_filtered"] = {"count": dust_count, "total_value_usd": round(dust_value, 4)}
    if total_usd > 0:
        output["portfolio_total_usd"] = round(total_usd, 2)
    if hidden_tokens > 0 and not show_all:
        output["warning"] = (
            "portfolio_total_usd may be partial because hidden tokens are not "
            "included when --limit is applied."
        )
    output["note"] = f"Checked {len(KNOWN_TOKENS)} known Base tokens. Unknown ERC-20s not shown."

    print_json(output)


# ---------------------------------------------------------------------------
# 3. 交易详情
# ---------------------------------------------------------------------------

def cmd_tx(args):
    """通过哈希查询完整的交易详情。"""
    tx_hash = args.hash

    results = rpc_batch([
        {"method": "eth_getTransactionByHash", "params": [tx_hash]},
        {"method": "eth_getTransactionReceipt", "params": [tx_hash]},
    ])

    by_id = {r["id"]: r.get("result") for r in results}
    tx      = by_id.get(0)
    receipt = by_id.get(1)

    if tx is None:
        sys.exit("Transaction not found.")

    value_wei = hex_to_int(tx.get("value"))
    tx_gas_price = hex_to_int(tx.get("gasPrice"))
    gas_used = hex_to_int(receipt.get("gasUsed")) if receipt else None
    effective_gas_price = (
        hex_to_int(receipt.get("effectiveGasPrice")) if receipt and receipt.get("effectiveGasPrice")
        else tx_gas_price
    )
    l2_fee_wei = effective_gas_price * gas_used if gas_used is not None else None
    l1_fee_wei = hex_to_int(receipt.get("l1Fee")) if receipt and receipt.get("l1Fee") else 0
    fee_wei = (l2_fee_wei + l1_fee_wei) if l2_fee_wei is not None else None

    eth_price = fetch_eth_price()

    out = {
        "hash":           tx_hash,
        "block":          hex_to_int(tx.get("blockNumber")),
        "from":           tx.get("from"),
        "to":             tx.get("to"),
        "value_ETH":      round(wei_to_eth(value_wei), 18) if value_wei else 0,
        "gas_price_gwei": round(wei_to_gwei(effective_gas_price), 4),
    }
    if gas_used is not None:
        out["gas_used"] = gas_used
    if l2_fee_wei is not None:
        out["l2_fee_ETH"] = round(wei_to_eth(l2_fee_wei), 12)
    if l1_fee_wei:
        out["l1_fee_ETH"] = round(wei_to_eth(l1_fee_wei), 12)
    if fee_wei is not None:
        out["fee_ETH"] = round(wei_to_eth(fee_wei), 12)
    if receipt:
        out["status"] = "success" if receipt.get("status") == "0x1" else "failed"
        out["contract_created"] = receipt.get("contractAddress")
        out["log_count"] = len(receipt.get("logs", []))

    # 从日志中解码 ERC-20 转账记录
    transfers = []
    if receipt:
        for log in receipt.get("logs", []):
            topics = log.get("topics", [])
            if len(topics) >= 3 and topics[0] == TRANSFER_TOPIC:
                from_addr = "0x" + topics[1][-40:]
                to_addr   = "0x" + topics[2][-40:]
                token_contract = log.get("address", "")
                label = _token_label(token_contract)

                entry = {
                    "token":    label,
                    "contract": token_contract,
                    "from":     from_addr,
                    "to":       to_addr,
                }
                # ERC-20: 3 个主题, 金额在 data 中
                if len(topics) == 3:
                    amount_hex = log.get("data", "0x")
                    if amount_hex and amount_hex != "0x":
                        raw_amount = hex_to_int(amount_hex)
                        addr_lower = token_contract.lower()
                        if addr_lower in KNOWN_TOKENS:
                            decimals = KNOWN_TOKENS[addr_lower][2]
                            entry["amount"] = raw_amount / (10 ** decimals)
                        else:
                            entry["raw_amount"] = raw_amount
                # ERC-721: 4 个主题, tokenId 在 topics[3] 中
                elif len(topics) == 4:
                    entry["token_id"] = hex_to_int(topics[3])
                    entry["type"] = "ERC-721"

                transfers.append(entry)

    if transfers:
        out["token_transfers"] = transfers

    if eth_price is not None:
        if value_wei:
            out["value_USD"] = round(wei_to_eth(value_wei) * eth_price, 2)
        if l2_fee_wei is not None:
            out["l2_fee_USD"] = round(wei_to_eth(l2_fee_wei) * eth_price, 4)
        if l1_fee_wei:
            out["l1_fee_USD"] = round(wei_to_eth(l1_fee_wei) * eth_price, 4)
        if fee_wei is not None:
            out["fee_USD"] = round(wei_to_eth(fee_wei) * eth_price, 4)

    print_json(out)


# ---------------------------------------------------------------------------
# 4. 代币信息
# ---------------------------------------------------------------------------

def cmd_token(args):
    """ERC-20 代币元数据、供应量、价格、市值。"""
    addr = args.address.lower()

    # 批量请求: name, symbol, decimals, totalSupply, 合约代码检查
    calls = [
        {"method": "eth_call", "params": [{"to": addr, "data": "0x" + SEL_NAME}, "latest"]},
        {"method": "eth_call", "params": [{"to": addr, "data": "0x" + SEL_SYMBOL}, "latest"]},
        {"method": "eth_call", "params": [{"to": addr, "data": "0x" + SEL_DECIMALS}, "latest"]},
        {"method": "eth_call", "params": [{"to": addr, "data": "0x" + SEL_TOTAL_SUPPLY}, "latest"]},
        {"method": "eth_getCode", "params": [addr, "latest"]},
    ]
    results = rpc_batch(calls)
    by_id = {r["id"]: r.get("result") for r in results}

    code = by_id.get(4)
    if not code or code == "0x":
        sys.exit("Address is not a contract.")

    name     = _decode_string(by_id.get(0))
    symbol   = _decode_string(by_id.get(1))
    decimals_raw = by_id.get(2)
    decimals = _decode_uint(decimals_raw)
    total_supply_raw = _decode_uint(by_id.get(3))

    # 如果链上读取失败，回退到已知代币
    if not symbol and addr in KNOWN_TOKENS:
        symbol   = KNOWN_TOKENS[addr][0]
        name     = KNOWN_TOKENS[addr][1]
        decimals = KNOWN_TOKENS[addr][2]

    is_known_token = addr in KNOWN_TOKENS
    is_erc20 = bool((symbol or is_known_token) and decimals_raw and decimals_raw != "0x")
    if not is_erc20:
        sys.exit("Contract does not appear to be an ERC-20 token.")

    total_supply = total_supply_raw / (10 ** decimals) if decimals else total_supply_raw

    # Fetch price
    price_data = fetch_prices([addr])

    out = {"address": args.address}
    if name:
        out["name"] = name
    if symbol:
        out["symbol"] = symbol
    out["decimals"]    = decimals
    out["total_supply"] = round(total_supply, min(decimals, 6))
    out["code_size_bytes"] = (len(code) - 2) // 2
    if addr in price_data:
        out["price_usd"]      = price_data[addr]
        out["market_cap_usd"] = round(price_data[addr] * total_supply, 0)

    print_json(out)


# ---------------------------------------------------------------------------
# 5. Gas 分析（Base 特有: L2 执行费用 + L1 数据费用）
# ---------------------------------------------------------------------------

def cmd_gas(_args):
    """详细的 Gas 分析，包含 L1 数据费用上下文和成本估算。"""
    latest_hex = _rpc_call("eth_blockNumber")
    latest = hex_to_int(latest_hex)

    # 获取最近 10 个区块用于趋势分析 + 当前 Gas 价格
    block_calls = []
    for i in range(10):
        block_calls.append({
            "method": "eth_getBlockByNumber",
            "params": [hex(latest - i), False],
        })
    block_calls.append({"method": "eth_gasPrice"})

    results = rpc_batch(block_calls)
    by_id = {r["id"]: r.get("result") for r in results}

    current_gas_price = hex_to_int(by_id.get(10))

    base_fees = []
    gas_utilizations = []
    tx_counts = []
    latest_block_info = None

    for i in range(10):
        b = by_id.get(i)
        if not b:
            continue
        bf  = hex_to_int(b.get("baseFeePerGas", "0x0"))
        gu  = hex_to_int(b.get("gasUsed", "0x0"))
        gl  = hex_to_int(b.get("gasLimit", "0x0"))
        txc = len(b.get("transactions", []))
        base_fees.append(bf)
        if gl > 0:
            gas_utilizations.append(gu / gl * 100)
        tx_counts.append(txc)

        if i == 0:
            latest_block_info = {
                "block":            hex_to_int(b.get("number")),
                "base_fee_gwei":    round(wei_to_gwei(bf), 6),
                "gas_used":         gu,
                "gas_limit":        gl,
                "utilization_pct":  round(gu / gl * 100, 2) if gl > 0 else 0,
                "tx_count":         txc,
            }

    avg_base_fee    = sum(base_fees) / len(base_fees) if base_fees else 0
    avg_utilization = sum(gas_utilizations) / len(gas_utilizations) if gas_utilizations else 0
    avg_tx_count    = sum(tx_counts) / len(tx_counts) if tx_counts else 0

    # 估算常见操作的费用
    eth_price = fetch_eth_price()

    simple_transfer_gas = 21_000
    erc20_transfer_gas  = 65_000
    swap_gas            = 200_000

    def _estimate_cost(gas: int) -> Dict[str, Any]:
        cost_wei = gas * current_gas_price
        cost_eth = wei_to_eth(cost_wei)
        entry: Dict[str, Any] = {"gas_units": gas, "cost_ETH": round(cost_eth, 10)}
        if eth_price:
            entry["cost_USD"] = round(cost_eth * eth_price, 6)
        return entry

    out: Dict[str, Any] = {
        "current_gas_price_gwei": round(wei_to_gwei(current_gas_price), 6),
        "latest_block":           latest_block_info,
        "trend_10_blocks": {
            "avg_base_fee_gwei":    round(wei_to_gwei(avg_base_fee), 6),
            "avg_utilization_pct":  round(avg_utilization, 2),
            "avg_tx_count":         round(avg_tx_count, 1),
            "min_base_fee_gwei":    round(wei_to_gwei(min(base_fees)), 6) if base_fees else None,
            "max_base_fee_gwei":    round(wei_to_gwei(max(base_fees)), 6) if base_fees else None,
        },
        "cost_estimates": {
            "eth_transfer":   _estimate_cost(simple_transfer_gas),
            "erc20_transfer": _estimate_cost(erc20_transfer_gas),
            "swap":           _estimate_cost(swap_gas),
        },
        "note": "Base is an L2. Total tx cost = L2 execution fee + L1 data posting fee. "
                "L1 data fee depends on calldata size and L1 gas prices (not shown here). "
                "Actual costs may be slightly higher than estimates.",
    }
    if eth_price:
        out["eth_price_usd"] = eth_price
    print_json(out)


# ---------------------------------------------------------------------------
# 6. 合约检查
# ---------------------------------------------------------------------------

def cmd_contract(args):
    """检查地址: EOA vs 合约、ERC 类型检测、代理合约解析。"""
    addr = args.address.lower()

    # 批量请求: getCode, getBalance, name, symbol, decimals, totalSupply, ERC-721, ERC-1155
    calls = [
        {"method": "eth_getCode",    "params": [addr, "latest"]},
        {"method": "eth_getBalance", "params": [addr, "latest"]},
        {"method": "eth_call", "params": [{"to": addr, "data": "0x" + SEL_NAME}, "latest"]},
        {"method": "eth_call", "params": [{"to": addr, "data": "0x" + SEL_SYMBOL}, "latest"]},
        {"method": "eth_call", "params": [{"to": addr, "data": "0x" + SEL_DECIMALS}, "latest"]},
        {"method": "eth_call", "params": [{"to": addr, "data": "0x" + SEL_TOTAL_SUPPLY}, "latest"]},
        {"method": "eth_call", "params": [
            {"to": addr, "data": "0x" + SEL_SUPPORTS_INTERFACE + IFACE_ERC721.zfill(64)},
            "latest",
        ]},
        {"method": "eth_call", "params": [
            {"to": addr, "data": "0x" + SEL_SUPPORTS_INTERFACE + IFACE_ERC1155.zfill(64)},
            "latest",
        ]},
    ]
    results = rpc_batch(calls)

    # 优雅地处理每项错误
    by_id: Dict[int, Any] = {}
    for r in results:
        if "error" not in r:
            by_id[r["id"]] = r.get("result")
        else:
            by_id[r["id"]] = None

    code        = by_id.get(0, "0x")
    eth_balance = hex_to_int(by_id.get(1))

    if not code or code == "0x":
        out = {
            "address":     args.address,
            "is_contract": False,
            "eth_balance": round(wei_to_eth(eth_balance), 18),
            "note":        "This is an externally owned account (EOA), not a contract.",
        }
        print_json(out)
        return

    code_size = (len(code) - 2) // 2

    # 检查 ERC-20
    name         = _decode_string(by_id.get(2))
    symbol       = _decode_string(by_id.get(3))
    decimals_raw = by_id.get(4)
    supply_raw   = by_id.get(5)
    is_erc20     = bool(symbol and decimals_raw and decimals_raw != "0x")

    # 通过 ERC-165 检查 ERC-721 / ERC-1155
    erc721_result  = by_id.get(6)
    erc1155_result = by_id.get(7)
    is_erc721  = erc721_result is not None and _decode_uint(erc721_result) == 1
    is_erc1155 = erc1155_result is not None and _decode_uint(erc1155_result) == 1

    # 检测代理模式（EIP-1967 实现槽）
    impl_slot = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
    impl_result = _rpc_call("eth_getStorageAt", [addr, impl_slot, "latest"])
    is_proxy = False
    impl_address = None
    if impl_result and impl_result != "0x" + "0" * 64:
        impl_address = "0x" + impl_result[-40:]
        if impl_address != "0x" + "0" * 40:
            is_proxy = True

    out: Dict[str, Any] = {
        "address":        args.address,
        "is_contract":    True,
        "code_size_bytes": code_size,
        "eth_balance":    round(wei_to_eth(eth_balance), 18),
    }

    interfaces = []
    if is_erc20:
        interfaces.append("ERC-20")
    if is_erc721:
        interfaces.append("ERC-721")
    if is_erc1155:
        interfaces.append("ERC-1155")
    if interfaces:
        out["detected_interfaces"] = interfaces

    if is_erc20:
        decimals = _decode_uint(decimals_raw)
        supply   = _decode_uint(supply_raw)
        out["erc20"] = {
            "name":         name,
            "symbol":       symbol,
            "decimals":     decimals,
            "total_supply": supply / (10 ** decimals) if decimals else supply,
        }

    if is_proxy:
        out["proxy"] = {
            "is_proxy":       True,
            "implementation": impl_address,
            "standard":       "EIP-1967",
        }

    # 检查已知代币
    if addr in KNOWN_TOKENS:
        sym, tname, _ = KNOWN_TOKENS[addr]
        out["known_token"] = {"symbol": sym, "name": tname}

    print_json(out)


# ---------------------------------------------------------------------------
# 7. 巨鲸检测
# ---------------------------------------------------------------------------

def cmd_whales(args):
    """扫描最新区块中的大额 ETH 转账并显示 USD 价值。"""
    min_wei = int(args.min_eth * WEI_PER_ETH)

    block = rpc("eth_getBlockByNumber", ["latest", True])
    if block is None:
        sys.exit("Could not retrieve latest block.")

    eth_price = fetch_eth_price()

    whales = []
    for tx in (block.get("transactions") or []):
        value = hex_to_int(tx.get("value"))
        if value >= min_wei:
            entry: Dict[str, Any] = {
                "hash": tx.get("hash"),
                "from": tx.get("from"),
                "to":   tx.get("to"),
                "value_ETH": round(wei_to_eth(value), 6),
            }
            if eth_price:
                entry["value_USD"] = round(wei_to_eth(value) * eth_price, 2)
            whales.append(entry)

    # 按金额降序排序
    whales.sort(key=lambda x: x["value_ETH"], reverse=True)

    out: Dict[str, Any] = {
        "block":              hex_to_int(block.get("number")),
        "block_time":         hex_to_int(block.get("timestamp")),
        "min_threshold_ETH":  args.min_eth,
        "large_transfers":    whales,
        "note":               "Scans latest block only — point-in-time snapshot.",
    }
    if eth_price:
        out["eth_price_usd"] = eth_price
    print_json(out)


# ---------------------------------------------------------------------------
# 8. 价格查询
# ---------------------------------------------------------------------------

def cmd_price(args):
    """通过合约地址或已知符号快速查询代币价格。"""
    query = args.token

    # 检查是否是已知符号
    addr = _SYMBOL_TO_ADDRESS.get(query.upper(), query).lower()

    # 特殊情况: ETH 本身
    if addr == "eth":
        eth_price = fetch_eth_price()
        out: Dict[str, Any] = {"query": query, "token": "ETH", "name": "Ethereum"}
        if eth_price:
            out["price_usd"] = eth_price
        else:
            out["price_usd"] = None
            out["note"] = "Price not available."
        print_json(out)
        return

    # 解析代币名称
    token_meta = resolve_token_name(addr)

    # Fetch price
    prices = fetch_prices([addr])

    out = {"query": query, "address": addr}
    if token_meta:
        out["name"]   = token_meta["name"]
        out["symbol"] = token_meta["symbol"]
    if addr in prices:
        out["price_usd"] = prices[addr]
    else:
        out["price_usd"] = None
        out["note"] = "Price not available — token may not be listed on CoinGecko."
    print_json(out)


# ---------------------------------------------------------------------------
# 命令行界面
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="base_client.py",
        description="Base blockchain query tool for Hermes Agent",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("stats", help="Network stats: block, gas, chain ID, ETH price")

    p_wallet = sub.add_parser("wallet", help="ETH balance + ERC-20 tokens with USD values")
    p_wallet.add_argument("address")
    p_wallet.add_argument("--limit", type=int, default=20,
                          help="Max tokens to display (default: 20)")
    p_wallet.add_argument("--all", action="store_true",
                          help="Show all tokens (no limit, no dust filter)")
    p_wallet.add_argument("--no-prices", action="store_true",
                          help="Skip price lookups (faster, RPC-only)")

    p_tx = sub.add_parser("tx", help="Transaction details by hash")
    p_tx.add_argument("hash")

    p_token = sub.add_parser("token", help="ERC-20 token metadata, price, and market cap")
    p_token.add_argument("address")

    sub.add_parser("gas", help="Gas analysis with cost estimates and L1 data fee context")

    p_contract = sub.add_parser("contract", help="Contract inspection: type detection, proxy check")
    p_contract.add_argument("address")

    p_whales = sub.add_parser("whales", help="Large ETH transfers in the latest block")
    p_whales.add_argument("--min-eth", type=float, default=1.0,
                          help="Minimum ETH transfer size (default: 1.0)")

    p_price = sub.add_parser("price", help="Quick price lookup by address or symbol")
    p_price.add_argument("token", help="Contract address or known symbol (ETH, USDC, AERO, ...)")

    args = parser.parse_args()

    dispatch = {
        "stats":    cmd_stats,
        "wallet":   cmd_wallet,
        "tx":       cmd_tx,
        "token":    cmd_token,
        "gas":      cmd_gas,
        "contract": cmd_contract,
        "whales":   cmd_whales,
        "price":    cmd_price,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
