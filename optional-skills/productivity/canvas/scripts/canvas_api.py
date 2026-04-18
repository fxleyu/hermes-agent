#!/usr/bin/env python3
"""Canvas LMS API CLI（Hermes Agent 专用）。

围绕 Canvas REST API 的轻量 CLI 封装。
使用环境变量中的个人访问令牌进行认证。

用法:
  python canvas_api.py list_courses [--per-page N] [--enrollment-state STATE]
  python canvas_api.py list_assignments COURSE_ID [--per-page N] [--order-by FIELD]
"""

import argparse
import json
import os
import sys

import requests

CANVAS_API_TOKEN = os.environ.get("CANVAS_API_TOKEN", "")
CANVAS_BASE_URL = os.environ.get("CANVAS_BASE_URL", "").rstrip("/")


def _check_config():
    """验证必需的环境变量已设置。"""
    missing = []
    if not CANVAS_API_TOKEN:
        missing.append("CANVAS_API_TOKEN")
    if not CANVAS_BASE_URL:
        missing.append("CANVAS_BASE_URL")
    if missing:
        print(
            f"Missing required environment variables: {', '.join(missing)}\n"
            "Set them in ~/.hermes/.env or export them in your shell.\n"
            "See the canvas skill SKILL.md for setup instructions.",
            file=sys.stderr,
        )
        sys.exit(1)


def _headers():
    return {"Authorization": f"Bearer {CANVAS_API_TOKEN}"}


def _paginated_get(url, params=None, max_items=200):
    """获取所有分页数据（最多 max_items 条），跟踪 Canvas Link 头部。"""
    results = []
    while url and len(results) < max_items:
        resp = requests.get(url, headers=_headers(), params=params, timeout=30)
        resp.raise_for_status()
        results.extend(resp.json())
        params = None  # 后续页面的参数已包含在 Link URL 中
        url = None
        link = resp.headers.get("Link", "")
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split(";")[0].strip().strip("<>")
    return results[:max_items]


# =========================================================================
# 命令
# =========================================================================


def list_courses(args):
    """列出已注册的课程。"""
    _check_config()
    url = f"{CANVAS_BASE_URL}/api/v1/courses"
    params = {"per_page": args.per_page}
    if args.enrollment_state:
        params["enrollment_state"] = args.enrollment_state
    try:
        courses = _paginated_get(url, params)
    except requests.HTTPError as e:
        print(f"API error: {e.response.status_code} {e.response.text}", file=sys.stderr)
        sys.exit(1)
    output = [
        {
            "id": c["id"],
            "name": c.get("name", ""),
            "course_code": c.get("course_code", ""),
            "enrollment_term_id": c.get("enrollment_term_id"),
            "start_at": c.get("start_at"),
            "end_at": c.get("end_at"),
            "workflow_state": c.get("workflow_state", ""),
        }
        for c in courses
    ]
    print(json.dumps(output, indent=2))


def list_assignments(args):
    """列出课程的作业。"""
    _check_config()
    url = f"{CANVAS_BASE_URL}/api/v1/courses/{args.course_id}/assignments"
    params = {"per_page": args.per_page}
    if args.order_by:
        params["order_by"] = args.order_by
    try:
        assignments = _paginated_get(url, params)
    except requests.HTTPError as e:
        print(f"API error: {e.response.status_code} {e.response.text}", file=sys.stderr)
        sys.exit(1)
    output = [
        {
            "id": a["id"],
            "name": a.get("name", ""),
            "description": (a.get("description") or "")[:500],
            "due_at": a.get("due_at"),
            "points_possible": a.get("points_possible"),
            "submission_types": a.get("submission_types", []),
            "html_url": a.get("html_url", ""),
            "course_id": a.get("course_id"),
        }
        for a in assignments
    ]
    print(json.dumps(output, indent=2))


# =========================================================================
# 命令行解析器
# =========================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Canvas LMS API CLI for Hermes Agent"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- list_courses ---
    p = sub.add_parser("list_courses", help="List enrolled courses")
    p.add_argument("--per-page", type=int, default=50, help="Results per page (default 50)")
    p.add_argument(
        "--enrollment-state",
        default="",
        help="Filter by enrollment state (active, invited_or_pending, completed)",
    )
    p.set_defaults(func=list_courses)

    # --- list_assignments ---
    p = sub.add_parser("list_assignments", help="List assignments for a course")
    p.add_argument("course_id", help="Canvas course ID")
    p.add_argument("--per-page", type=int, default=50, help="Results per page (default 50)")
    p.add_argument(
        "--order-by",
        default="",
        help="Order by field (due_at, name, position)",
    )
    p.set_defaults(func=list_assignments)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
