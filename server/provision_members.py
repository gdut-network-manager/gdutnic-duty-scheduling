"""Create missing roster accounts. Administrators issue passwords in the web console."""
from server import member_store

if __name__ == '__main__':
    result = member_store.provision()
    print(f"普通用户账号：新增 {result['created']} 个，已存在 {result['existing']} 个（保留原密码）")
    if result['created']:
        print("如需使用密码登录，请通过用户管理重置新账号并向用户发放临时密码。")
