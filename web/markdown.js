function renderMarkdown(src) {
  const text = (src || "").replace(/\r\n/g, "\n");
  const fences = [];
  const withFences = text.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    const i = fences.length;
    fences.push(
      `<pre><code class="lang-${mdEscape(lang)}">${mdEscape(code.replace(/\n$/, ""))}</code></pre>`
    );
    return `\n%%FENCE${i}%%\n`;
  });
  const lines = withFences.split("\n");
  const out = [];
  let listType = null;

  const closeList = () => {
    if (listType) {
      out.push(listType === "ol" ? "</ol>" : "</ul>");
      listType = null;
    }
  };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const fence = line.match(/^%%FENCE(\d+)%%$/);
    if (fence) {
      closeList();
      out.push(fences[Number(fence[1])]);
      continue;
    }
    if (mdIsTableRow(line) && i + 1 < lines.length && mdIsTableSep(lines[i + 1])) {
      closeList();
      const rows = [];
      while (i < lines.length && (mdIsTableRow(lines[i]) || mdIsTableSep(lines[i]))) {
        if (!mdIsTableSep(lines[i])) rows.push(mdSplitCells(lines[i]));
        i += 1;
      }
      i -= 1;
      out.push(mdRenderTable(rows));
      continue;
    }
    if (/^---+$/.test(line.trim())) {
      closeList();
      out.push("<hr>");
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      closeList();
      const level = heading[1].length;
      out.push(`<h${level}>${mdInline(heading[2])}</h${level}>`);
      continue;
    }
    const quote = line.match(/^>\s?(.*)$/);
    if (quote) {
      closeList();
      out.push(`<blockquote>${mdInline(quote[1])}</blockquote>`);
      continue;
    }
    const ol = line.match(/^\d+\.\s+(.+)$/);
    if (ol) {
      if (listType !== "ol") {
        closeList();
        listType = "ol";
        out.push("<ol>");
      }
      out.push(`<li>${mdInline(ol[1])}</li>`);
      continue;
    }
    const ul = line.match(/^[-*]\s+(.+)$/);
    if (ul) {
      if (listType !== "ul") {
        closeList();
        listType = "ul";
        out.push("<ul>");
      }
      out.push(`<li>${mdInline(ul[1])}</li>`);
      continue;
    }
    closeList();
    if (!line.trim()) continue;
    out.push(`<p>${mdInline(line)}</p>`);
  }
  closeList();
  return out.join("") || "<p></p>";
}

function mdEscape(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function mdInline(text) {
  let html = mdEscape(text);
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/__([^_]+)__/g, "<strong>$1</strong>");
  html = html.replace(/(^|[^*])\*([^*]+)\*(?!\*)/g, "$1<em>$2</em>");
  html = html.replace(/(^|[^_])_([^_]+)_(?!_)/g, "$1<em>$2</em>");
  html = html.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  html = html.replace(/https:\/\/[^\s<]+/g, (url, offset, str) => {
    const before = str.slice(Math.max(0, offset - 6), offset);
    if (before.endsWith("href=") || before.endsWith('="') || before.endsWith("='")) {
      return url;
    }
    const cleaned = url.replace(/[),.;:，。；：]+$/, "");
    const trail = url.slice(cleaned.length);
    return `<a href="${cleaned}" target="_blank" rel="noreferrer">${cleaned}</a>${trail}`;
  });
  return html;
}

function mdIsTableRow(line) {
  return /^\s*\|.*\|\s*$/.test(line);
}

function mdIsTableSep(line) {
  return /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/.test(line);
}

function mdSplitCells(line) {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|\s*$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function mdRenderTable(rows) {
  const body = rows.filter((row) => !mdIsTableSep(row.join("|")));
  if (!body.length) return "";
  const head = body[0];
  const rest = body.slice(1);
  const thead = `<thead><tr>${head.map((c) => `<th>${mdInline(c)}</th>`).join("")}</tr></thead>`;
  const tbody = `<tbody>${rest
    .map((row) => `<tr>${row.map((c) => `<td>${mdInline(c)}</td>`).join("")}</tr>`)
    .join("")}</tbody>`;
  return `<div class="table-wrap"><table>${thead}${tbody}</table></div>`;
}
