// Sentences in the guide use `inline code` and nothing else.
function wikiText(node, text) {
  String(text || '').split('`').forEach((part, index) => {
    if (part) node.append(index % 2 ? el('code', '', part) : part);
  });
  return node;
}

function wikiPlace(text) {
  const line = el('p', 'wiki-place');
  line.append(el('span', '', '位置'));
  return wikiText(line, text);
}

const WIKI_LEVELS = {'核心': 'core', '重要': 'major', '辅助': 'minor'};

function wikiFeature(feature) {
  const item = el('div', 'wiki-feature'), head = el('div', 'wiki-feature-head');
  head.append(el('strong', '', feature.name));
  if (feature.level) head.append(el('span', 'wiki-level ' + WIKI_LEVELS[feature.level], feature.level));
  if (feature.needs) head.append(el('span', 'wiki-needs', '权限：' + feature.needs));
  item.append(head, wikiText(el('p'), feature.detail));
  return item;
}

function renderWiki(guide) {
  const available = !!(guide && guide.available);
  $('wikiBody').hidden = !available;
  $('wikiStatus').hidden = available;
  if (!available) {
    $('wikiStatus').textContent = '这次部署没有附带功能说明文档（description/description.md）。';
    return;
  }
  $('wikiPartial').hidden = !guide.partial;
  $('wikiOverviewBox').hidden = !guide.overview.length;
  $('wikiOverviewHint').textContent = `最核心的 ${guide.overview.length} 项功能`;
  $('wikiOverview').replaceChildren(...guide.overview.map(feature => {
    const tile = el('article', 'wiki-tile');
    tile.append(el('h4', '', feature.name), wikiPlace(feature.place), wikiText(el('p', 'wiki-detail'), feature.detail));
    return tile;
  }));
  $('wikiAreas').replaceChildren(...guide.areas.map(area => {
    const section = el('section', 'wiki-section wiki-area');
    const total = area.pages.reduce((sum, page) => sum + page.features.length, 0);
    section.append(el('h3', '', area.title),
      el('p', 'wiki-count', area.pages.length > 1 ? `${area.pages.length} 个页面 · ${total} 项功能` : `${total} 项功能`));
    const pages = el('div', 'wiki-page-list');
    for (const page of area.pages) {
      const box = el('details', 'wiki-page'), summary = el('summary');
      summary.append(el('strong', '', page.title === area.title ? '功能列表' : page.title),
        el('span', '', `${page.features.length} 项功能`));
      box.append(summary);
      if (page.location) box.append(wikiPlace(page.location));
      const features = el('div', 'wiki-feature-list');
      features.append(...page.features.map(wikiFeature), ...page.notes.map(note => wikiText(el('p', 'wiki-note'), note)));
      box.append(features);
      pages.append(box);
    }
    section.append(pages);
    return section;
  }));
}

$('aboutTab').onclick = async () => {
  showPage('about');
  try {
    const data = await api('/api/admin/about');
    $('aboutVersion').textContent = data.version;
    renderWiki(data.guide);
  } catch (_) {
    $('aboutVersion').textContent = '暂时无法读取版本，请稍后重新打开本页。';
    $('wikiStatus').hidden = false;
    $('wikiStatus').textContent = '暂时无法读取功能说明，请稍后重新打开本页。';
    $('wikiBody').hidden = true;
  }
};
