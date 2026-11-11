"""Shipped icons and their references must work without a separate source library."""
import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPONENTS = ROOT / 'icon' / 'components'
CSS = ROOT / 'web' / 'component-icons.css'


def web_sources():
    return [path for path in (ROOT / 'web').iterdir() if path.suffix in {'.html', '.css', '.js'}]


class TestComponentIcons(unittest.TestCase):
    def test_shipped_svg_files_are_valid(self):
        icons = list((ROOT / 'icon').rglob('*.svg'))
        self.assertTrue(icons, '没有找到 SVG 图标')
        for path in icons:
            with self.subTest(icon=path.name):
                svg = ET.parse(path).getroot()
                self.assertEqual(svg.tag, '{http://www.w3.org/2000/svg}svg')
                self.assertTrue(svg.get('viewBox'), '图标缺少 viewBox')

    def test_css_only_references_shipped_icons(self):
        declared = re.findall(r'\[data-icon="([^"]+)"\]\s*\{[^}]*url\("/icon/components/([^"]+)"\)',
                              CSS.read_text(encoding='utf-8'))
        self.assertTrue(declared, '没有找到图标样式映射')
        for name, filename in declared:
            with self.subTest(icon=name):
                self.assertEqual(filename, name + '.svg')
                self.assertTrue((COMPONENTS / filename).is_file(), f'缺少图标：{filename}')

    def test_every_used_component_icon_has_a_css_mapping(self):
        declared = set(re.findall(r'\[data-icon="([^"]+)"\]', CSS.read_text(encoding='utf-8')))
        used = set()
        for path in web_sources():
            if path.suffix == '.css':
                continue
            source = path.read_text(encoding='utf-8')
            used.update(re.findall(r'data-icon="([\w-]+)"', source))
            used.update(re.findall(r"\.dataset\.icon\s*=\s*['\"]([\w-]+)['\"]", source))
            for action_map in re.findall(r'const ACTION_ICONS\s*=\s*\{([^}]+)\}', source):
                used.update(re.findall(r":\s*['\"]([\w-]+)['\"]", action_map))
        self.assertTrue(used)
        self.assertFalse(used - declared, f'使用了没有样式映射的图标：{used - declared}')

    def test_external_icon_files_are_under_icon_and_exist(self):
        count = 0
        for path in web_sources():
            source = path.read_text(encoding='utf-8')
            for url in re.findall(r'''["'(](/[^\s"'<>)]*\.(?:svg|png|ico))''', source):
                with self.subTest(source=path.name, url=url):
                    self.assertTrue(url.startswith('/icon/'), f'图标没有放在 icon 下：{url}')
                    self.assertTrue((ROOT / url.lstrip('/')).is_file(), f'图标文件不存在：{url}')
                    count += 1
        self.assertGreater(count, 0)

    def test_runtime_has_no_source_library_dependency(self):
        removed_directory = 'par' + 'ment'
        sources = web_sources() + list((ROOT / 'server').glob('*.py'))
        for path in sources:
            self.assertNotIn(removed_directory, path.read_text(encoding='utf-8'), str(path))


if __name__ == '__main__':
    unittest.main()
