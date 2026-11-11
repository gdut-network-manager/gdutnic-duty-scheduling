"""Create missing ordinary roster accounts using the requested initial password."""
from server import member_store

if __name__ == '__main__':
    result = member_store.provision()
    print(f"普通用户账号：新增 {result['created']} 个，已存在 {result['existing']} 个（保留原密码）")
