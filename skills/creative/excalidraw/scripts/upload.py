#!/usr/bin/env python3
"""
将 .excalidraw 文件上传到 excalidraw.com 并打印可分享的 URL。

无需账号。图表在上传前会在客户端进行 AES-GCM 加密——加密密钥嵌入在 URL 片段中，
因此服务器永远不会看到明文内容。

依赖:
    pip install cryptography

用法:
    python upload.py <path-to-file.excalidraw>

示例:
    python upload.py ~/diagrams/architecture.excalidraw
    # 输出: https://excalidraw.com/#json=abc123,encryptionKeyHere
"""

import json
import os
import struct
import sys
import zlib
import base64
import urllib.request

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    print("Error: 'cryptography' package is required for upload.")
    print("Install it with: pip install cryptography")
    sys.exit(1)

# Excalidraw 公开上传端点（无需认证）
UPLOAD_URL = "https://json.excalidraw.com/api/v2/post/"


def concat_buffers(*buffers: bytes) -> bytes:
    """
    构建 Excalidraw v2 concat-buffers 二进制格式。

    布局: [版本号=1 (4字节大端序)] 然后对每个缓冲区:
           [长度 (4字节大端序)] [数据字节]
    """
    parts = [struct.pack(">I", 1)]  # 版本号 = 1
    for buf in buffers:
        parts.append(struct.pack(">I", len(buf)))
        parts.append(buf)
    return b"".join(parts)


def upload(excalidraw_json: str) -> str:
    """
    加密并上传 Excalidraw JSON 到 excalidraw.com。

    参数:
        excalidraw_json: 完整的 .excalidraw 文件内容字符串。

    返回:
        可分享的 URL 字符串。
    """
    # 1. 内部载荷: concat_buffers(文件元数据, 数据)
    file_metadata = json.dumps({}).encode("utf-8")
    data_bytes = excalidraw_json.encode("utf-8")
    inner_payload = concat_buffers(file_metadata, data_bytes)

    # 2. 使用 zlib 压缩
    compressed = zlib.compress(inner_payload)

    # 3. AES-GCM 128位加密
    raw_key = os.urandom(16)   # 128位密钥
    iv = os.urandom(12)        # 12字节随机数（nonce）
    aesgcm = AESGCM(raw_key)
    encrypted = aesgcm.encrypt(iv, compressed, None)

    # 4. 编码元数据
    encoding_meta = json.dumps({
        "version": 2,
        "compression": "pako@1",
        "encryption": "AES-GCM",
    }).encode("utf-8")

    # 5. 外部载荷: concat_buffers(编码元数据, iv, 密文)
    payload = concat_buffers(encoding_meta, iv, encrypted)

    # 6. 上传到服务器
    req = urllib.request.Request(UPLOAD_URL, data=payload, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Upload failed with HTTP {resp.status}")
        result = json.loads(resp.read().decode("utf-8"))

    file_id = result.get("id")
    if not file_id:
        raise RuntimeError(f"Upload returned no file ID. Response: {result}")

    # 7. 将密钥转换为 base64url 格式（JWK 'k' 格式，无填充）
    key_b64 = base64.urlsafe_b64encode(raw_key).rstrip(b"=").decode("ascii")

    return f"https://excalidraw.com/#json={file_id},{key_b64}"


def main():
    if len(sys.argv) < 2:
        print("Usage: python upload.py <path-to-file.excalidraw>")
        sys.exit(1)

    file_path = sys.argv[1]

    if not os.path.isfile(file_path):
        print(f"Error: File not found: {file_path}")
        sys.exit(1)

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 基本验证: 应为包含 "elements" 键的有效 JSON
    try:
        doc = json.loads(content)
    except json.JSONDecodeError as e:
        print(f"Error: File is not valid JSON: {e}")
        sys.exit(1)

    if "elements" not in doc:
        print("Warning: File does not contain an 'elements' key. Uploading anyway.")

    url = upload(content)
    print(url)


if __name__ == "__main__":
    main()
