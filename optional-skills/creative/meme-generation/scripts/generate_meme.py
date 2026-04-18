#!/usr/bin/env python3
"""通过在模板图片上叠加文字来生成表情包。

用法:
    python generate_meme.py <模板ID或名称> <输出路径> <文字1> [文字2] [文字3] [文字4]

示例:
    python generate_meme.py drake /tmp/meme.png "Writing tests" "Shipping to prod and hoping"
    python generate_meme.py "Disaster Girl" /tmp/meme.png "Top text" "Bottom text"
    python generate_meme.py --list                    # 显示精选模板
    python generate_meme.py --search "distracted"     # 搜索所有 imgflip 模板

带有自定义文字位置的模板保存在 templates.json 中（10 个精选模板）。
也可以通过名称或 ID 使用约 100 个热门 imgflip 模板 —
未知模板会根据其 box_count 自动生成智能默认文字位置。
"""

import json
import os
import sys
import textwrap
from io import BytesIO
from pathlib import Path

try:
    import requests as _requests
except ImportError:
    _requests = None

from PIL import Image, ImageDraw, ImageFont

SCRIPT_DIR = Path(__file__).parent
TEMPLATES_FILE = SCRIPT_DIR / "templates.json"
CACHE_DIR = SCRIPT_DIR / ".cache"
IMGFLIP_API = "https://api.imgflip.com/get_memes"
IMGFLIP_CACHE_FILE = CACHE_DIR / "imgflip_memes.json"
IMGFLIP_CACHE_MAX_AGE = 86400  # 24 小时


def _fetch_url(url: str, timeout: int = 15) -> bytes:
    """获取 URL 内容，优先使用 requests 库，否则使用 urllib。"""
    if _requests is not None:
        resp = _requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.content
    import urllib.request
    return urllib.request.urlopen(url, timeout=timeout).read()


def load_curated_templates() -> dict:
    """加载带有手动调优文字区域位置的精选模板。"""
    with open(TEMPLATES_FILE) as f:
        return json.load(f)


def _default_fields(box_count: int) -> list:
    """为未知模板生成合理的默认文字区域位置。"""
    if box_count <= 0:
        box_count = 2
    if box_count == 1:
        return [{"name": "text", "x_pct": 0.5, "y_pct": 0.5, "w_pct": 0.90, "align": "center"}]
    if box_count == 2:
        return [
            {"name": "top", "x_pct": 0.5, "y_pct": 0.08, "w_pct": 0.95, "align": "center"},
            {"name": "bottom", "x_pct": 0.5, "y_pct": 0.92, "w_pct": 0.95, "align": "center"},
        ]
    # 3 个以上: 纵向均匀分布
    fields = []
    for i in range(box_count):
        y = 0.08 + (0.84 * i / (box_count - 1)) if box_count > 1 else 0.5
        fields.append({
            "name": f"text{i+1}",
            "x_pct": 0.5,
            "y_pct": round(y, 2),
            "w_pct": 0.90,
            "align": "center",
        })
    return fields


def fetch_imgflip_templates() -> list:
    """从 imgflip API 获取热门表情包模板，缓存 24 小时。"""
    import time

    CACHE_DIR.mkdir(exist_ok=True)
    # 检查缓存
    if IMGFLIP_CACHE_FILE.exists():
        age = time.time() - IMGFLIP_CACHE_FILE.stat().st_mtime
        if age < IMGFLIP_CACHE_MAX_AGE:
            with open(IMGFLIP_CACHE_FILE) as f:
                return json.load(f)

    try:
        data = json.loads(_fetch_url(IMGFLIP_API))
        memes = data.get("data", {}).get("memes", [])
        with open(IMGFLIP_CACHE_FILE, "w") as f:
            json.dump(memes, f)
        return memes
    except Exception as e:
        # 获取失败时，如有过期缓存则使用过期缓存
        if IMGFLIP_CACHE_FILE.exists():
            with open(IMGFLIP_CACHE_FILE) as f:
                return json.load(f)
        print(f"Warning: could not fetch imgflip templates: {e}", file=sys.stderr)
        return []


def _slugify(name: str) -> str:
    """将模板名称转为 slug 格式以便匹配。"""
    return name.lower().replace(" ", "-").replace("'", "").replace("\"", "")


def resolve_template(identifier: str) -> dict:
    """通过精选 ID、imgflip 名称或 imgflip ID 解析模板。

    返回包含 name, url, fields, source 的字典。
    """
    curated = load_curated_templates()

    # 1. 精确匹配精选 ID
    if identifier in curated:
        tmpl = curated[identifier]
        return {**tmpl, "source": "curated"}

    # 2. Slug 化精选匹配
    slug = _slugify(identifier)
    for tid, tmpl in curated.items():
        if _slugify(tmpl["name"]) == slug or tid == slug:
            return {**tmpl, "source": "curated"}

    # 3. 搜索 imgflip 模板
    imgflip_memes = fetch_imgflip_templates()
    slug_lower = slug.lower()
    id_lower = identifier.strip()

    for meme in imgflip_memes:
        meme_slug = _slugify(meme["name"])
        # 优先检查该 imgflip 模板是否有精选版本（自定义位置）
        for tid, ctmpl in curated.items():
            if _slugify(ctmpl["name"]) == meme_slug:
                if meme_slug == slug_lower or meme["id"] == id_lower:
                    return {**ctmpl, "source": "curated"}

        if meme_slug == slug_lower or meme["id"] == id_lower or slug_lower in meme_slug:
            return {
                "name": meme["name"],
                "url": meme["url"],
                "fields": _default_fields(meme.get("box_count", 2)),
                "source": "imgflip",
            }

    return None


def get_template_image(url: str) -> Image.Image:
    """下载模板图片，并缓存到本地。"""
    CACHE_DIR.mkdir(exist_ok=True)
    # 使用 URL 哈希作为缓存键
    cache_name = url.split("/")[-1]
    cache_path = CACHE_DIR / cache_name

    # 始终缓存为 PNG 以避免 JPEG/RGBA 冲突
    cache_path = cache_path.with_suffix(".png")

    if cache_path.exists():
        return Image.open(cache_path).convert("RGBA")

    data = _fetch_url(url)
    img = Image.open(BytesIO(data)).convert("RGBA")
    img.save(cache_path, "PNG")
    return img


def find_font(size: int) -> ImageFont.FreeTypeFont:
    """查找适合表情包的粗体字体。优先使用 Impact，否则回退到其他字体。"""
    candidates = [
        "/usr/share/fonts/truetype/msttcorefonts/Impact.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/liberation-sans/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu-sans/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFCompact.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except (OSError, IOError):
                continue
    # 最后手段: Pillow 默认字体
    try:
        return ImageFont.truetype("DejaVuSans-Bold", size)
    except (OSError, IOError):
        return ImageFont.load_default()


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
    """自动换行文字以适应最大像素宽度。不会在单词中间断开。"""
    words = text.split()
    if not words:
        return text
    lines = []
    current_line = words[0]
    for word in words[1:]:
        test_line = current_line + " " + word
        if font.getlength(test_line) <= max_width:
            current_line = test_line
        else:
            lines.append(current_line)
            current_line = word
    lines.append(current_line)
    return "\n".join(lines)


def draw_outlined_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: int,
    y: int,
    font_size: int,
    max_width: int,
    align: str = "center",
):
    """绘制带黑色描边的白色文字，自动缩放以适应最大宽度。"""
    # 自动缩放: 逐步减小字号直到文字合理适配
    size = font_size
    while size > 12:
        font = find_font(size)
        wrapped = _wrap_text(text, font, max_width)
        bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, align=align)
        text_w = bbox[2] - bbox[0]
        line_count = wrapped.count("\n") + 1
        # 宽度合适且行数不超过 4 行时接受
        if text_w <= max_width * 1.05 and line_count <= 4:
            break
        size -= 2
    else:
        font = find_font(size)
        wrapped = _wrap_text(text, font, max_width)

    # 测量文字块整体尺寸
    bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, align=align)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    # 以 (x, y) 为中心水平和垂直居中
    tx = x - text_w // 2
    ty = y - text_h // 2

    # 绘制描边（黑色边框）
    outline_range = max(2, font.size // 18)
    for dx in range(-outline_range, outline_range + 1):
        for dy in range(-outline_range, outline_range + 1):
            if dx == 0 and dy == 0:
                continue
            draw.multiline_text(
                (tx + dx, ty + dy), wrapped, font=font, fill="black", align=align
            )
    # 绘制主体文字（白色）
    draw.multiline_text((tx, ty), wrapped, font=font, fill="white", align=align)


def _overlay_on_image(img: Image.Image, texts: list, fields: list) -> Image.Image:
    """根据字段位置在图片上叠加表情包文字。"""
    draw = ImageDraw.Draw(img)
    w, h = img.size
    base_font_size = max(16, min(w, h) // 12)

    for i, field in enumerate(fields):
        if i >= len(texts):
            break
        text = texts[i].strip()
        if not text:
            continue
        fx = int(field["x_pct"] * w)
        fy = int(field["y_pct"] * h)
        fw = int(field["w_pct"] * w)
        draw_outlined_text(draw, text, fx, fy, base_font_size, fw, field.get("align", "center"))
    return img


def _add_bars(img: Image.Image, texts: list) -> Image.Image:
    """在图片上下添加黑色条带并写入白色文字。

    文字分配方式: 第一段文字放顶部条带，最后一段放底部条带，
    中间的文字叠加在图片中央。
    """
    w, h = img.size
    bar_font_size = max(20, w // 16)
    font = find_font(bar_font_size)
    padding = bar_font_size // 2

    top_text = texts[0].strip() if texts else ""
    bottom_text = texts[-1].strip() if len(texts) > 1 else ""
    middle_texts = [t.strip() for t in texts[1:-1]] if len(texts) > 2 else []

    def _measure_bar(text: str) -> int:
        if not text:
            return 0
        wrapped = _wrap_text(text, font, int(w * 0.92))
        bbox = ImageDraw.Draw(Image.new("RGB", (1, 1))).multiline_textbbox(
            (0, 0), wrapped, font=font, align="center"
        )
        return (bbox[3] - bbox[1]) + padding * 2

    top_h = _measure_bar(top_text)
    bottom_h = _measure_bar(bottom_text)
    new_h = h + top_h + bottom_h

    canvas = Image.new("RGB", (w, new_h), (0, 0, 0))
    canvas.paste(img.convert("RGB"), (0, top_h))
    draw = ImageDraw.Draw(canvas)

    if top_text:
        wrapped = _wrap_text(top_text, font, int(w * 0.92))
        bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, align="center")
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        tx = (w - tw) // 2
        ty = (top_h - th) // 2
        draw.multiline_text((tx, ty), wrapped, font=font, fill="white", align="center")

    if bottom_text:
        wrapped = _wrap_text(bottom_text, font, int(w * 0.92))
        bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, align="center")
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        tx = (w - tw) // 2
        ty = top_h + h + (bottom_h - th) // 2
        draw.multiline_text((tx, ty), wrapped, font=font, fill="white", align="center")

    # 将中间文字叠加在图片中央
    if middle_texts:
        mid_fields = _default_fields(len(middle_texts))
        # 调整 y 坐标以补偿顶部条带偏移
        for field in mid_fields:
            field["y_pct"] = (top_h + field["y_pct"] * h) / new_h
            field["w_pct"] = 0.90
        _overlay_on_image(canvas, middle_texts, mid_fields)

    return canvas


def generate_meme(template_id: str, texts: list[str], output_path: str) -> str:
    """根据模板生成表情包并保存。返回文件路径。"""
    tmpl = resolve_template(template_id)

    if tmpl is None:
        print(f"Unknown template: {template_id}", file=sys.stderr)
        print("Use --list to see curated templates or --search to find imgflip templates.", file=sys.stderr)
        sys.exit(1)

    fields = tmpl["fields"]
    print(f"Using template: {tmpl['name']} ({tmpl['source']}, {len(fields)} fields)", file=sys.stderr)

    img = get_template_image(tmpl["url"])
    img = _overlay_on_image(img, texts, fields)

    output = Path(output_path)
    if output.suffix.lower() in (".jpg", ".jpeg"):
        img = img.convert("RGB")
    img.save(str(output), quality=95)
    return str(output)


def generate_from_image(
    image_path: str, texts: list[str], output_path: str, use_bars: bool = False
) -> str:
    """从自定义图片（如 AI 生成的图片）生成表情包。返回文件路径。"""
    img = Image.open(image_path).convert("RGBA")
    print(f"Custom image: {img.size[0]}x{img.size[1]}, {len(texts)} text(s), mode={'bars' if use_bars else 'overlay'}", file=sys.stderr)

    if use_bars:
        result = _add_bars(img, texts)
    else:
        fields = _default_fields(len(texts))
        result = _overlay_on_image(img, texts, fields)

    output = Path(output_path)
    if output.suffix.lower() in (".jpg", ".jpeg"):
        result = result.convert("RGB")
    result.save(str(output), quality=95)
    return str(output)


def list_templates():
    """打印带有自定义位置的精选模板列表。"""
    templates = load_curated_templates()
    print(f"{'ID':<25} {'Name':<30} {'Fields':<8} Best for")
    print("-" * 90)
    for tid, tmpl in sorted(templates.items()):
        fields = len(tmpl["fields"])
        print(f"{tid:<25} {tmpl['name']:<30} {fields:<8} {tmpl['best_for']}")
    print(f"\n{len(templates)} curated templates with custom text positioning.")
    print("Use --search to find any of the ~100 popular imgflip templates.")


def search_templates(query: str):
    """按名称搜索 imgflip 模板。"""
    imgflip_memes = fetch_imgflip_templates()
    curated = load_curated_templates()
    curated_slugs = {_slugify(t["name"]) for t in curated.values()}
    query_lower = query.lower()

    matches = []
    for meme in imgflip_memes:
        if query_lower in meme["name"].lower():
            slug = _slugify(meme["name"])
            has_custom = "curated" if slug in curated_slugs else "default"
            matches.append((meme["name"], meme["id"], meme.get("box_count", 2), has_custom))

    if not matches:
        print(f"No templates found matching '{query}'")
        return

    print(f"{'Name':<40} {'ID':<12} {'Fields':<8} Positioning")
    print("-" * 75)
    for name, mid, boxes, positioning in matches:
        print(f"{name:<40} {mid:<12} {boxes:<8} {positioning}")
    print(f"\n{len(matches)} template(s) found. Use the name or ID as the first argument.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: generate_meme.py <template_id_or_name> <output_path> <text1> [text2] ...")
        print("       generate_meme.py --image <path> [--bars] <output_path> <text1> [text2] ...")
        print("       generate_meme.py --list              # curated templates")
        print("       generate_meme.py --search <query>    # search all imgflip templates")
        sys.exit(1)

    if sys.argv[1] == "--list":
        list_templates()
        sys.exit(0)

    if sys.argv[1] == "--search":
        if len(sys.argv) < 3:
            print("Usage: generate_meme.py --search <query>")
            sys.exit(1)
        search_templates(sys.argv[2])
        sys.exit(0)

    if sys.argv[1] == "--image":
        # 自定义图片模式: --image <路径> [--bars] <输出> <文字1> ...
        args = sys.argv[2:]
        if len(args) < 3:
            print("Usage: generate_meme.py --image <image_path> [--bars] <output_path> <text1> ...")
            sys.exit(1)
        image_path = args.pop(0)
        use_bars = False
        if args and args[0] == "--bars":
            use_bars = True
            args.pop(0)
        if len(args) < 2:
            print("Need at least: output_path and one text argument")
            sys.exit(1)
        output_path = args.pop(0)
        result = generate_from_image(image_path, args, output_path, use_bars=use_bars)
        print(f"Meme saved to: {result}")
        sys.exit(0)

    if len(sys.argv) < 4:
        print("Need at least: template_id_or_name, output_path, and one text argument")
        sys.exit(1)

    template_id = sys.argv[1]
    output_path = sys.argv[2]
    texts = sys.argv[3:]

    result = generate_meme(template_id, texts, output_path)
    print(f"Meme saved to: {result}")
