"""Feature guide for the About page, parsed from description/description.md.

The document stays the single source: editing it changes the page after a reload of the
file (its modification time is checked on every request), with no second copy to keep
in step. Only the structure the page needs is read: areas (``##``), pages (``###``),
each page's 位置 line, its first table of features, and the notes around it.
"""
from __future__ import annotations

from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / 'description' / 'description.md'
# Headers that name the feature column of a table worth showing.
FEATURE_HEADERS = ('功能', '行为')
LEVELS = ('核心', '重要', '辅助')
# The area whose pages are sidebar entries gated by a permission of the same name.
ADMIN_AREA = '管理端'
_cache: tuple[float, dict] | None = None


def _cells(line):
    return [cell.strip() for cell in line.strip().strip('|').split('|')]


def _is_rule(line):
    return bool(line) and set(line.replace('|', '').strip()) <= set('-: ')


def _title(text):
    """`4.3 概览` → `概览`."""
    head, _, rest = text.partition(' ')
    return rest.strip() if rest and head.replace('.', '').isdigit() else text.strip()


def parse(text):
    areas, area, page, in_code, table = [], None, None, False, None

    def open_page(title):
        nonlocal page
        page = {'title': title, 'location': '', 'features': [], 'notes': []}
        area['pages'].append(page)

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith('```'):
            in_code = not in_code
            continue
        if in_code:
            continue
        if not line.startswith('|'):
            table = None
        if line.startswith('## '):
            area = {'title': _title(line[3:]), 'pages': []}
            areas.append(area)
            page = None
        elif line.startswith('### ') and area is not None:
            open_page(_title(line[4:]))
        elif area is None or not line:
            continue
        elif line.startswith('|'):
            cells = _cells(line)
            if table is None:
                # A header row: remember which columns matter, or skip the whole table.
                name = next((i for i, cell in enumerate(cells) if cell in FEATURE_HEADERS), None)
                table = False if name is None or '说明' not in cells else {
                    'name': name, 'detail': cells.index('说明'),
                    'level': cells.index('重要程度') if '重要程度' in cells else None,
                    'needs': cells.index('所需权限') if '所需权限' in cells else None}
                continue
            if table is False or _is_rule(line) or len(cells) <= table['detail']:
                continue
            if page is None:
                open_page(area['title'])
            level = cells[table['level']] if table['level'] is not None else ''
            page['features'].append({
                'name': cells[table['name']], 'detail': cells[table['detail']],
                'level': level if level in LEVELS else '',
                'needs': cells[table['needs']] if table['needs'] is not None else ''})
        else:
            if page is None:
                open_page(area['title'])
            if line.startswith('位置：') and not page['location']:
                page['location'] = line[3:].strip()
            else:
                page['notes'].append(line)
    for item in areas:
        # A paragraph that only introduces a table we do not show says nothing on its own.
        for entry in item['pages']:
            entry['notes'] = [note for note in entry['notes'] if not note.endswith('：')]
        item['pages'] = [entry for entry in item['pages'] if entry['features']]
    return [item for item in areas if item['pages']]


def load():
    """Parsed guide, or an empty one when the document is not shipped with this copy."""
    global _cache
    try:
        stamp = SOURCE.stat().st_mtime
    except OSError:
        return {'available': False, 'areas': [], 'overview': []}
    if _cache is None or _cache[0] != stamp:
        areas = parse(SOURCE.read_text(encoding='utf-8'))
        _cache = (stamp, {'available': True, 'areas': areas, 'overview': _overview(areas)})
    return _cache[1]


def _overview(areas):
    return [{'area': area['title'], 'page': page['title'],
             'place': page['location'].split('。')[0] or f"{area['title']} → {page['title']}", **feature}
            for area in areas for page in area['pages'] for feature in page['features']
            if feature['level'] == LEVELS[0]]


def for_account(held, root=False):
    """The guide as one administrator may see it.

    A 管理端 page named after a permission (概览, 每周排班, …) is shown only to accounts holding
    it, exactly like its sidebar entry. A feature with a 所需权限 cell needs that permission
    too; a label that matches no permission (仅原始管理员) is for the root account alone.
    Everything else (成员端, login pages, automatic behaviour) describes the system in
    general and stays visible. `partial` tells the page that something was left out.
    """
    guide = load()
    if root or not guide['available']:
        return {**guide, 'partial': False}
    from server import permissions
    labels = {permissions.PERMISSIONS[key][0]: key for key in permissions.ADMIN_PERMISSIONS}
    may = lambda label: labels.get(label) in held
    areas, partial = [], False
    for area in guide['areas']:
        pages = []
        for page in area['pages']:
            if area['title'] == ADMIN_AREA and page['title'] in labels and not may(page['title']):
                partial = True
                continue
            features = [feature for feature in page['features'] if not feature['needs'] or may(feature['needs'])]
            partial = partial or len(features) != len(page['features'])
            if features:
                pages.append({**page, 'features': features})
        if pages:
            areas.append({**area, 'pages': pages})
    return {'available': True, 'areas': areas, 'overview': _overview(areas), 'partial': partial}
