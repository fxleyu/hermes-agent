"""Hermes CLI 的共享 curses 界面组件。

被 `hermes tools` 和 `hermes skills` 用于交互式复选列表。
提供带键盘导航的 curses 多选界面，以及用于不支持 curses
的终端的基于文本的编号回退方案。
"""
import sys
from typing import Callable, List, Optional, Set

from hermes_cli.colors import Colors, color


def flush_stdin() -> None:
    """清空 stdin 输入缓冲区中的杂散字节。

    必须在 ``curses.wrapper()``（或任何终端模式库如
    simple_term_menu）返回后、下一次 ``input()`` /
    ``getpass.getpass()`` 调用之前调用。``curses.endwin()``
    恢复了终端，但不会排空操作系统输入缓冲区 —— 遗留的
    转义序列字节（来自方向键、终端模式切换响应或快速按键）
    仍然被缓冲，并会被下一次 ``input()`` 调用静默消费，
    导致用户数据损坏（例如向 .env 文件写入 ``^[^[``）。

    在非 TTY stdin（管道、重定向）或 Windows 上，此操作为空操作。
    """
    try:
        if not sys.stdin.isatty():
            return
        import termios
        termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except Exception:
        pass


def curses_checklist(
    title: str,
    items: List[str],
    selected: Set[int],
    *,
    cancel_returns: Set[int] | None = None,
    status_fn: Optional[Callable[[Set[int]], str]] = None,
) -> Set[int]:
    """curses 多选复选列表。返回已选索引的集合。

    参数:
        title: 显示在复选列表上方的标题行。
        items: 每行的显示标签。
        selected: 初始选中的索引（预选项）。
        cancel_returns: 按 ESC/q 时返回的值。默认为原始 *selected*。
        status_fn: 可选回调 ``f(chosen_indices) -> str``，其返回值
            渲染在终端底行。用于实时聚合信息（例如估计的 token 数量）。
    """
    if cancel_returns is None:
        cancel_returns = set(selected)

    # 安全措施：当 stdin 不是终端时（例如子进程管道），
    # curses 和 input() 都会挂起或空转。立即返回默认值。
    if not sys.stdin.isatty():
        return cancel_returns

    try:
        import curses
        chosen = set(selected)
        result_holder: list = [None]

        def _draw(stdscr):
            curses.curs_set(0)
            if curses.has_colors():
                curses.start_color()
                curses.use_default_colors()
                curses.init_pair(1, curses.COLOR_GREEN, -1)
                curses.init_pair(2, curses.COLOR_YELLOW, -1)
                curses.init_pair(3, 8, -1)  # dim gray
            cursor = 0
            scroll_offset = 0

            while True:
                stdscr.clear()
                max_y, max_x = stdscr.getmaxyx()

                # 当提供 status_fn 时，为状态栏保留底行
                footer_rows = 1 if status_fn else 0

                # 标题
                try:
                    hattr = curses.A_BOLD
                    if curses.has_colors():
                        hattr |= curses.color_pair(2)
                    stdscr.addnstr(0, 0, title, max_x - 1, hattr)
                    stdscr.addnstr(
                        1, 0,
                        "  ↑↓ navigate  SPACE toggle  ENTER confirm  ESC cancel",
                        max_x - 1, curses.A_DIM,
                    )
                except curses.error:
                    pass

                # 可滚动的项目列表
                visible_rows = max_y - 3 - footer_rows
                if cursor < scroll_offset:
                    scroll_offset = cursor
                elif cursor >= scroll_offset + visible_rows:
                    scroll_offset = cursor - visible_rows + 1

                for draw_i, i in enumerate(
                    range(scroll_offset, min(len(items), scroll_offset + visible_rows))
                ):
                    y = draw_i + 3
                    if y >= max_y - 1 - footer_rows:
                        break
                    check = "✓" if i in chosen else " "
                    arrow = "→" if i == cursor else " "
                    line = f" {arrow} [{check}] {items[i]}"
                    attr = curses.A_NORMAL
                    if i == cursor:
                        attr = curses.A_BOLD
                        if curses.has_colors():
                            attr |= curses.color_pair(1)
                    try:
                        stdscr.addnstr(y, 0, line, max_x - 1, attr)
                    except curses.error:
                        pass

                # 状态栏（底行，右对齐）
                if status_fn:
                    try:
                        status_text = status_fn(chosen)
                        if status_text:
                            # 右对齐到底行
                            sx = max(0, max_x - len(status_text) - 1)
                            sattr = curses.A_DIM
                            if curses.has_colors():
                                sattr |= curses.color_pair(3)
                            stdscr.addnstr(max_y - 1, sx, status_text, max_x - sx - 1, sattr)
                    except curses.error:
                        pass

                stdscr.refresh()
                key = stdscr.getch()

                if key in (curses.KEY_UP, ord("k")):
                    cursor = (cursor - 1) % len(items)
                elif key in (curses.KEY_DOWN, ord("j")):
                    cursor = (cursor + 1) % len(items)
                elif key == ord(" "):
                    chosen.symmetric_difference_update({cursor})
                elif key in (curses.KEY_ENTER, 10, 13):
                    result_holder[0] = set(chosen)
                    return
                elif key in (27, ord("q")):
                    result_holder[0] = cancel_returns
                    return

        curses.wrapper(_draw)
        flush_stdin()
        return result_holder[0] if result_holder[0] is not None else cancel_returns

    except Exception:
        return _numbered_fallback(title, items, selected, cancel_returns, status_fn)


def curses_radiolist(
    title: str,
    items: List[str],
    selected: int = 0,
    *,
    cancel_returns: int | None = None,
    description: str | None = None,
) -> int:
    """curses 单选列表。返回选中的索引。

    参数:
        title: 显示在列表上方的标题行。
        items: 每行的显示标签。
        selected: 初始选中的索引（预选项）。
        cancel_returns: 按 ESC/q 时返回的值。默认为原始 *selected*。
        description: 在标题和项目列表之间显示的可选多行文本。
            用于在 curses 屏幕清除后仍需保留的上下文信息。
    """
    if cancel_returns is None:
        cancel_returns = selected

    if not sys.stdin.isatty():
        return cancel_returns

    desc_lines: list[str] = []
    if description:
        desc_lines = description.splitlines()

    try:
        import curses
        result_holder: list = [None]

        def _draw(stdscr):
            curses.curs_set(0)
            if curses.has_colors():
                curses.start_color()
                curses.use_default_colors()
                curses.init_pair(1, curses.COLOR_GREEN, -1)
                curses.init_pair(2, curses.COLOR_YELLOW, -1)
            cursor = selected
            scroll_offset = 0

            while True:
                stdscr.clear()
                max_y, max_x = stdscr.getmaxyx()

                row = 0

                # 标题
                try:
                    hattr = curses.A_BOLD
                    if curses.has_colors():
                        hattr |= curses.color_pair(2)
                    stdscr.addnstr(row, 0, title, max_x - 1, hattr)
                    row += 1

                    # 描述文本行
                    for dline in desc_lines:
                        if row >= max_y - 1:
                            break
                        stdscr.addnstr(row, 0, dline, max_x - 1, curses.A_NORMAL)
                        row += 1

                    stdscr.addnstr(
                        row, 0,
                        "  \u2191\u2193 navigate  ENTER/SPACE select  ESC cancel",
                        max_x - 1, curses.A_DIM,
                    )
                    row += 1
                except curses.error:
                    pass

                # 可滚动的项目列表
                items_start = row + 1
                visible_rows = max_y - items_start - 1
                if cursor < scroll_offset:
                    scroll_offset = cursor
                elif cursor >= scroll_offset + visible_rows:
                    scroll_offset = cursor - visible_rows + 1

                for draw_i, i in enumerate(
                    range(scroll_offset, min(len(items), scroll_offset + visible_rows))
                ):
                    y = draw_i + items_start
                    if y >= max_y - 1:
                        break
                    radio = "\u25cf" if i == selected else "\u25cb"
                    arrow = "\u2192" if i == cursor else " "
                    line = f" {arrow} ({radio}) {items[i]}"
                    attr = curses.A_NORMAL
                    if i == cursor:
                        attr = curses.A_BOLD
                        if curses.has_colors():
                            attr |= curses.color_pair(1)
                    try:
                        stdscr.addnstr(y, 0, line, max_x - 1, attr)
                    except curses.error:
                        pass

                stdscr.refresh()
                key = stdscr.getch()

                if key in (curses.KEY_UP, ord("k")):
                    cursor = (cursor - 1) % len(items)
                elif key in (curses.KEY_DOWN, ord("j")):
                    cursor = (cursor + 1) % len(items)
                elif key in (ord(" "), curses.KEY_ENTER, 10, 13):
                    result_holder[0] = cursor
                    return
                elif key in (27, ord("q")):
                    result_holder[0] = cancel_returns
                    return

        curses.wrapper(_draw)
        flush_stdin()
        return result_holder[0] if result_holder[0] is not None else cancel_returns

    except Exception:
        return _radio_numbered_fallback(title, items, selected, cancel_returns)


def _radio_numbered_fallback(
    title: str,
    items: List[str],
    selected: int,
    cancel_returns: int,
) -> int:
    """基于文本编号的单选回退方案。"""
    print(color(f"\n  {title}", Colors.YELLOW))
    print(color("  Select by number, Enter to confirm.\n", Colors.DIM))

    for i, label in enumerate(items):
        marker = color("(\u25cf)", Colors.GREEN) if i == selected else "(\u25cb)"
        print(f"  {marker} {i + 1:>2}. {label}")
    print()
    try:
        val = input(color(f"  Choice [default {selected + 1}]: ", Colors.DIM)).strip()
        if not val:
            return selected
        idx = int(val) - 1
        if 0 <= idx < len(items):
            return idx
        return selected
    except (ValueError, KeyboardInterrupt, EOFError):
        return cancel_returns


def curses_single_select(
    title: str,
    items: List[str],
    default_index: int = 0,
    *,
    cancel_label: str = "Cancel",
) -> int | None:
    """curses 单选菜单。返回选中的索引，取消时返回 None。

    可在 prompt_toolkit 内部使用，因为 curses.wrapper() 能安全
    恢复终端，而 simple_term_menu 会与 /dev/tty 冲突。
    """
    if not sys.stdin.isatty():
        return None

    try:
        import curses
        result_holder: list = [None]

        all_items = list(items) + [cancel_label]
        cancel_idx = len(items)

        def _draw(stdscr):
            curses.curs_set(0)
            if curses.has_colors():
                curses.start_color()
                curses.use_default_colors()
                curses.init_pair(1, curses.COLOR_GREEN, -1)
                curses.init_pair(2, curses.COLOR_YELLOW, -1)
            cursor = min(default_index, len(all_items) - 1)
            scroll_offset = 0

            while True:
                stdscr.clear()
                max_y, max_x = stdscr.getmaxyx()

                try:
                    hattr = curses.A_BOLD
                    if curses.has_colors():
                        hattr |= curses.color_pair(2)
                    stdscr.addnstr(0, 0, title, max_x - 1, hattr)
                    stdscr.addnstr(
                        1, 0,
                        "  ↑↓ navigate  ENTER confirm  ESC/q cancel",
                        max_x - 1, curses.A_DIM,
                    )
                except curses.error:
                    pass

                visible_rows = max_y - 3
                if cursor < scroll_offset:
                    scroll_offset = cursor
                elif cursor >= scroll_offset + visible_rows:
                    scroll_offset = cursor - visible_rows + 1

                for draw_i, i in enumerate(
                    range(scroll_offset, min(len(all_items), scroll_offset + visible_rows))
                ):
                    y = draw_i + 3
                    if y >= max_y - 1:
                        break
                    arrow = "→" if i == cursor else " "
                    line = f" {arrow} {all_items[i]}"
                    attr = curses.A_NORMAL
                    if i == cursor:
                        attr = curses.A_BOLD
                        if curses.has_colors():
                            attr |= curses.color_pair(1)
                    try:
                        stdscr.addnstr(y, 0, line, max_x - 1, attr)
                    except curses.error:
                        pass

                stdscr.refresh()
                key = stdscr.getch()

                if key in (curses.KEY_UP, ord("k")):
                    cursor = (cursor - 1) % len(all_items)
                elif key in (curses.KEY_DOWN, ord("j")):
                    cursor = (cursor + 1) % len(all_items)
                elif key in (curses.KEY_ENTER, 10, 13):
                    result_holder[0] = cursor
                    return
                elif key in (27, ord("q")):
                    result_holder[0] = None
                    return

        curses.wrapper(_draw)
        flush_stdin()
        if result_holder[0] is not None and result_holder[0] >= cancel_idx:
            return None
        return result_holder[0]

    except Exception:
        all_items = list(items) + [cancel_label]
        cancel_idx = len(items)
        return _numbered_single_fallback(title, all_items, cancel_idx)


def _numbered_single_fallback(
    title: str,
    items: List[str],
    cancel_idx: int,
) -> int | None:
    """基于文本编号的单选回退方案。"""
    print(f"\n  {title}\n")
    for i, label in enumerate(items, 1):
        print(f"  {i}. {label}")
    print()
    try:
        val = input(f"  Choice [1-{len(items)}]: ").strip()
        if not val:
            return None
        idx = int(val) - 1
        if 0 <= idx < len(items) and idx < cancel_idx:
            return idx
        if idx == cancel_idx:
            return None
    except (ValueError, KeyboardInterrupt, EOFError):
        pass
    return None


def _numbered_fallback(
    title: str,
    items: List[str],
    selected: Set[int],
    cancel_returns: Set[int],
    status_fn: Optional[Callable[[Set[int]], str]] = None,
) -> Set[int]:
    """用于不支持 curses 的终端的基于文本切换的回退方案。"""
    chosen = set(selected)
    print(color(f"\n  {title}", Colors.YELLOW))
    print(color("  Toggle by number, Enter to confirm.\n", Colors.DIM))

    while True:
        for i, label in enumerate(items):
            marker = color("[✓]", Colors.GREEN) if i in chosen else "[ ]"
            print(f"  {marker} {i + 1:>2}. {label}")
        if status_fn:
            status_text = status_fn(chosen)
            if status_text:
                print(color(f"\n  {status_text}", Colors.DIM))
        print()
        try:
            val = input(color("  Toggle # (or Enter to confirm): ", Colors.DIM)).strip()
            if not val:
                break
            idx = int(val) - 1
            if 0 <= idx < len(items):
                chosen.symmetric_difference_update({idx})
        except (ValueError, KeyboardInterrupt, EOFError):
            return cancel_returns
        print()

    return chosen
