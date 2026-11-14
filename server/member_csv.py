"""Two-column student ID CSV exchange and guarded conflict replacement."""
import csv
import io

from server import db, member_store, root_admin

MAX_BYTES = 2 * 1024 * 1024


def export_csv():
    out = io.StringIO(newline='')
    writer = csv.writer(out)
    writer.writerow(['用户名', '编号'])
    for account in member_store.list_accounts():
        writer.writerow([account['username'], account['student_id']])
    return ('\ufeff' + out.getvalue()).encode('utf-8')


def parse_csv(content):
    if len(content) > MAX_BYTES:
        raise ValueError('CSV 文件不能超过 2 MB')
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        try:
            text = content.decode('gb18030')
        except UnicodeDecodeError as exc:
            raise ValueError('请使用 UTF-8 或 GB18030 编码的 CSV 文件') from exc
    try:
        rows = [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(text, newline=''), strict=True)
                if any(cell.strip() for cell in row)]
    except csv.Error as exc:
        raise ValueError('CSV 格式不正确') from exc
    if rows and rows[0] in (['用户名', '编号'], ['用户名', '学号']):
        rows.pop(0)
    if not rows:
        raise ValueError('CSV 没有用户数据')
    if len(rows) > 10000 or any(len(row) != 2 for row in rows):
        raise ValueError('CSV 每行应为两列：用户名、编号，最多 10000 行')
    return rows


def apply_rows(rows, may_manage, *, replace=False):
    """Serialize writes and re-check the expected old ID before replacing conflicts."""
    updated, skipped, unmatched = set(), 0, 0
    conflicts = {}
    with db.write() as conn:
        for item in rows:
            name, number = item['username'].strip(), item['student_id'].strip()
            if not number:
                skipped += 1
                continue
            account = conn.execute('SELECT student_id FROM member_accounts WHERE username=?', (name,)).fetchone()
            if not account or root_admin.is_root(name):
                unmatched += 1
                continue
            old = account['student_id']
            if old == number:
                skipped += 1
                continue
            reason = ''
            if not may_manage(name):
                reason = '没有修改该账号的权限'
            elif replace and old != item['original_id']:
                reason = '原有编号已变化，请核对后再次替代'
            elif old and not replace:
                reason = '已有编号与导入编号不一致'
            if not reason:
                try:
                    member_store._set_student_id(conn, name, number)
                    updated.add(name)
                    continue
                except (ValueError, LookupError) as exc:
                    reason = str(exc)
            conflicts[name] = {'username': name, 'original_id': old, 'student_id': number, 'reason': reason}
    return {'updated': len(updated), 'skipped': skipped, 'unmatched': unmatched,
            'conflicts': list(conflicts.values())}
