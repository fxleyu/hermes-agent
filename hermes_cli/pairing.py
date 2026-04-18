"""
DM 配对系统的 CLI 命令。

用法:
    hermes pairing list              # 显示所有待审核和已批准的用户
    hermes pairing approve <platform> <code>  # 批准一个配对码
    hermes pairing revoke <platform> <user_id> # 撤销用户访问权限
    hermes pairing clear-pending     # 清除所有过期/待处理的配对码
"""

def pairing_command(args):
    """处理 hermes pairing 子命令。"""
    from gateway.pairing import PairingStore

    store = PairingStore()
    action = getattr(args, "pairing_action", None)

    # 根据子命令分发到对应的处理函数
    if action == "list":
        _cmd_list(store)
    elif action == "approve":
        _cmd_approve(store, args.platform, args.code)
    elif action == "revoke":
        _cmd_revoke(store, args.platform, args.user_id)
    elif action == "clear-pending":
        _cmd_clear_pending(store)
    else:
        print("Usage: hermes pairing {list|approve|revoke|clear-pending}")
        print("Run 'hermes pairing --help' for details.")


def _cmd_list(store):
    """列出所有待审核和已批准的用户。"""
    pending = store.list_pending()
    approved = store.list_approved()

    if not pending and not approved:
        print("No pairing data found. No one has tried to pair yet~")
        return

    # 显示待审核的配对请求
    if pending:
        print(f"\n  Pending Pairing Requests ({len(pending)}):")
        print(f"  {'Platform':<12} {'Code':<10} {'User ID':<20} {'Name':<20} {'Age'}")
        print(f"  {'--------':<12} {'----':<10} {'-------':<20} {'----':<20} {'---'}")
        for p in pending:
            print(
                f"  {p['platform']:<12} {p['code']:<10} {p['user_id']:<20} "
                f"{p.get('user_name', ''):<20} {p['age_minutes']}m ago"
            )
    else:
        print("\n  No pending pairing requests.")

    # 显示已批准的用户
    if approved:
        print(f"\n  Approved Users ({len(approved)}):")
        print(f"  {'Platform':<12} {'User ID':<20} {'Name':<20}")
        print(f"  {'--------':<12} {'-------':<20} {'----':<20}")
        for a in approved:
            print(f"  {a['platform']:<12} {a['user_id']:<20} {a.get('user_name', ''):<20}")
    else:
        print("\n  No approved users.")

    print()


def _cmd_approve(store, platform: str, code: str):
    """批准一个配对码。"""
    # 统一转换为小写和大写以进行匹配
    platform = platform.lower().strip()
    code = code.upper().strip()

    result = store.approve_code(platform, code)
    if result:
        uid = result["user_id"]
        name = result.get("user_name", "")
        display = f"{name} ({uid})" if name else uid
        print(f"\n  Approved! User {display} on {platform} can now use the bot~")
        print("  They'll be recognized automatically on their next message.\n")
    else:
        print(f"\n  Code '{code}' not found or expired for platform '{platform}'.")
        print("  Run 'hermes pairing list' to see pending codes.\n")


def _cmd_revoke(store, platform: str, user_id: str):
    """撤销用户的访问权限。"""
    platform = platform.lower().strip()

    if store.revoke(platform, user_id):
        print(f"\n  Revoked access for user {user_id} on {platform}.\n")
    else:
        print(f"\n  User {user_id} not found in approved list for {platform}.\n")


def _cmd_clear_pending(store):
    """清除所有待处理的配对码。"""
    count = store.clear_pending()
    if count:
        print(f"\n  Cleared {count} pending pairing request(s).\n")
    else:
        print("\n  No pending requests to clear.\n")
