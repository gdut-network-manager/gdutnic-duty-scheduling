$('aboutTab').onclick = async () => {
  showPage('about');
  try {
    const data = await api('/api/admin/about');
    $('aboutVersion').textContent = data.version;
  } catch (_) {
    $('aboutVersion').textContent = '暂时无法读取版本，请稍后重新打开本页。';
  }
};
