#!/usr/bin/env python3
"""
获取 YouTube 视频字幕并以结构化 JSON 格式输出。

用法:
    python fetch_transcript.py <url_or_video_id> [--language en,tr] [--timestamps]

输出 (JSON):
    {
        "video_id": "...",
        "language": "en",
        "segments": [{"text": "...", "start": 0.0, "duration": 2.5}, ...],
        "full_text": "完整字幕的纯文本",
        "timestamped_text": "00:00 第一行\n00:05 第二行\n..."
    }

安装依赖:  pip install youtube-transcript-api
"""

import argparse
import json
import re
import sys


def extract_video_id(url_or_id: str) -> str:
    """从各种 YouTube URL 格式中提取 11 个字符的视频 ID。"""
    url_or_id = url_or_id.strip()
    patterns = [
        r'(?:v=|youtu\.be/|shorts/|embed/|live/)([a-zA-Z0-9_-]{11})',
        r'^([a-zA-Z0-9_-]{11})$',
    ]
    for pattern in patterns:
        match = re.search(pattern, url_or_id)
        if match:
            return match.group(1)
    return url_or_id


def format_timestamp(seconds: float) -> str:
    """将秒数转换为 HH:MM:SS 或 MM:SS 格式。"""
    total = int(seconds)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def fetch_transcript(video_id: str, languages: list = None):
    """从 YouTube 获取字幕片段。

    返回包含 'text'、'start' 和 'duration' 键的字典列表。
    兼容 youtube-transcript-api v1.x。
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        print("Error: youtube-transcript-api not installed. Run: pip install youtube-transcript-api",
              file=sys.stderr)
        sys.exit(1)

    api = YouTubeTranscriptApi()
    if languages:
        result = api.fetch(video_id, languages=languages)
    else:
        result = api.fetch(video_id)

    # v1.x 返回 FetchedTranscriptSnippet 对象；标准化为字典
    return [
        {"text": seg.text, "start": seg.start, "duration": seg.duration}
        for seg in result
    ]


def main():
    parser = argparse.ArgumentParser(description="Fetch YouTube transcript as JSON")
    parser.add_argument("url", help="YouTube URL or video ID")
    parser.add_argument("--language", "-l", default=None,
                        help="Comma-separated language codes (e.g. en,tr). Default: auto")
    parser.add_argument("--timestamps", "-t", action="store_true",
                        help="Include timestamped text in output")
    parser.add_argument("--text-only", action="store_true",
                        help="Output plain text instead of JSON")
    args = parser.parse_args()

    video_id = extract_video_id(args.url)
    languages = [l.strip() for l in args.language.split(",")] if args.language else None

    try:
        segments = fetch_transcript(video_id, languages)
    except Exception as e:
        error_msg = str(e)
        if "disabled" in error_msg.lower():
            print(json.dumps({"error": "Transcripts are disabled for this video."}))
        elif "no transcript" in error_msg.lower():
            print(json.dumps({"error": f"No transcript found. Try specifying a language with --language."}))
        else:
            print(json.dumps({"error": error_msg}))
        sys.exit(1)

    full_text = " ".join(seg["text"] for seg in segments)
    timestamped = "\n".join(
        f"{format_timestamp(seg['start'])} {seg['text']}" for seg in segments
    )

    if args.text_only:
        print(timestamped if args.timestamps else full_text)
        return

    result = {
        "video_id": video_id,
        "segment_count": len(segments),
        "duration": format_timestamp(segments[-1]["start"] + segments[-1]["duration"]) if segments else "0:00",
        "full_text": full_text,
    }
    if args.timestamps:
        result["timestamped_text"] = timestamped

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
