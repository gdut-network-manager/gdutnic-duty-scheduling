"""Source-network restrictions for member attendance and field registration."""
from ipaddress import ip_address, ip_network
from server import db

KEY = 'checkin_access'
KINDS = {'checkin': '打卡', 'field': '外勤'}


def normalize(body):
    if not isinstance(body, dict):
        raise ValueError('请填写打卡管理设置')
    result = {}
    for kind, label in KINDS.items():
        rule = body.get(kind)
        if not isinstance(rule, dict) or rule.get('mode') not in ('allow', 'exclude'):
            raise ValueError(f'{label}请选择允许或排除')
        entries = rule.get('entries', '')
        if not isinstance(entries, str) or len(entries) > 20000:
            raise ValueError(f'{label}地址列表格式错误或过长')
        lines = []
        for number, line in enumerate(entries.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                value = 'localhost' if line.lower() == 'localhost' else str(ip_network(line, strict=False))
            except ValueError as exc:
                raise ValueError(f'{label}第 {number} 行不是有效的 IP 或网段：{line}') from exc
            if value not in lines:
                lines.append(value)
        result[kind] = {'mode': rule['mode'], 'entries': '\n'.join(lines)}
    return result


def load():
    with db.read() as conn:
        saved = db.get_setting(conn, KEY)
    if saved is None:
        return {kind: {'mode': 'exclude', 'entries': ''} for kind in KINDS}
    try:
        return normalize(saved)
    except (ValueError, TypeError):
        # A hand-edited or half-written row must not take the member pages down: every
        # check-in and field call reads this setting. Fail open to "no restriction" and
        # let the administrator re-save the card to repair the stored value.
        return {kind: {'mode': 'exclude', 'entries': ''} for kind in KINDS}


def save(body):
    value = normalize(body)
    with db.write() as conn:
        db.set_setting(conn, KEY, value)
    return value


def allowed(rule, host):
    if not rule['entries']:
        return rule['mode'] == 'exclude'
    try:
        address = ip_address(host)
        if getattr(address, 'ipv4_mapped', None):
            address = address.ipv4_mapped
    except ValueError:
        # No resolvable peer means we cannot verify the network, even in exclusion mode.
        return False
    matched = False
    for line in rule['entries'].splitlines():
        if line == 'localhost':
            matched = address.is_loopback
        else:
            network = ip_network(line, strict=False)
            matched = address.version == network.version and address in network
        if matched:
            break
    return matched if rule['mode'] == 'allow' else not matched


def status(host):
    rules = load()
    return {'ip': host, **{kind: allowed(rule, host) for kind, rule in rules.items()}}
