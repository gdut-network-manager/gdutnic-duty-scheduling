"""Grant every permission to an existing account (console recovery helper).

    .venv/bin/python -m server.grant_admin AndyZhang123
"""
import argparse
import sys

from server import member_store, permissions


def grant(username, grant=permissions.ADMIN_DEFAULTS):
    name = username.strip()
    if not name:
        raise ValueError('请填写账号名')
    existing = {row['username'] for row in member_store.list_accounts()}
    if name not in existing:
        raise LookupError(f'账号不存在：{name}（可先运行 python -m server.provision_members 补齐名单账号）')
    perms = permissions.set_permissions(name, grant)
    return {'username': name, 'permissions': sorted(perms)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('username')
    parser.add_argument('--permissions', default=','.join(permissions.ADMIN_DEFAULTS),
                        help='英文逗号分隔的权限 id，默认全部')
    args = parser.parse_args(argv)
    try:
        result = grant(args.username, args.permissions.split(','))
    except (ValueError, LookupError) as exc:
        print(f'失败：{exc}', file=sys.stderr)
        return 1
    print(f"已授予 {result['username']}：{', '.join(result['permissions'])}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
