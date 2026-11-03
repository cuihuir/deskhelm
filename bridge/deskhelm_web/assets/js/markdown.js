// A small Markdown renderer for agent replies. It builds DOM nodes and sets
// text through textContent only, so nothing in a reply becomes markup. It
// covers what agents write: paragraphs, headings, fenced code, lists, quotes,
// tables, rules, and inline code, emphasis, and links. Single newlines stay
// line breaks, as agents mean them.

import { copyText, h, icon } from "./dom.js";

const FENCE = /^ {0,3}(`{3,}|~{3,})\s*([^\s`]*)/;
const HEADING = /^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const RULE = /^ {0,3}([-*_])(?:\s*\1){2,}\s*$/;
const QUOTE = /^ {0,3}>\s?(.*)$/;
const ITEM = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const TABLE_DIVIDER = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;

export function renderMarkdown(text) {
  const fragment = document.createDocumentFragment();
  blocks(String(text).replace(/\r\n?/g, "\n").split("\n"), fragment);
  return fragment;
}

function blocks(lines, parent) {
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    let match;
    if (!line.trim()) {
      index += 1;
    } else if ((match = FENCE.exec(line))) {
      index = fence(lines, index, match, parent);
    } else if ((match = HEADING.exec(line))) {
      parent.append(inline(h(`h${match[1].length}`), match[2]));
      index += 1;
    } else if (RULE.test(line)) {
      parent.append(h("hr"));
      index += 1;
    } else if (QUOTE.test(line)) {
      const inner = [];
      while (index < lines.length && (match = QUOTE.exec(lines[index]))) {
        inner.push(match[1]);
        index += 1;
      }
      const quote = h("blockquote");
      blocks(inner, quote);
      parent.append(quote);
    } else if (ITEM.test(line)) {
      index = list(lines, index, parent);
    } else if (isTable(lines, index)) {
      index = table(lines, index, parent);
    } else {
      index = paragraph(lines, index, parent);
    }
  }
}

function startsBlock(line) {
  return FENCE.test(line) || HEADING.test(line) || RULE.test(line) || QUOTE.test(line) || ITEM.test(line);
}

function leading(line) {
  return line.length - line.trimStart().length;
}

function paragraph(lines, index, parent) {
  const node = h("p");
  let first = true;
  while (index < lines.length && lines[index].trim() && (first || (!startsBlock(lines[index]) && !isTable(lines, index)))) {
    if (!first) node.append(h("br"));
    inline(node, lines[index].trim());
    first = false;
    index += 1;
  }
  parent.append(node);
  return index;
}

function fence(lines, index, match, parent) {
  const marker = match[1];
  const body = [];
  index += 1;
  while (index < lines.length) {
    const close = lines[index].trim();
    if (close[0] === marker[0] && close.length >= marker.length && /^(`+|~+)$/.test(close)) {
      index += 1;
      break;
    }
    body.push(lines[index]);
    index += 1;
  }
  parent.append(codeBlock(body.join("\n"), match[2]));
  return index;
}

export function codeBlock(code, lang) {
  const copy = h("button", { class: "code-copy", type: "button", title: "复制", "aria-label": "复制代码" }, icon("copy"));
  copy.hidden = !navigator.clipboard;
  copy.addEventListener("click", async () => {
    if (await copyText(code)) {
      copy.classList.add("is-done");
      setTimeout(() => copy.classList.remove("is-done"), 1200);
    }
  });
  const body = /^(diff|patch)$/i.test(lang || "") ? diffBlock(code) : h("pre", null, h("code", { text: code }));
  return h("div", { class: "code" }, h("div", { class: "code-head" }, h("span", { text: lang || "" }), copy), body);
}

export function looksLikeDiff(text) {
  return /^@@ .* @@/m.test(text) || (/^\+(?!\+\+)/m.test(text) && /^-(?!--)/m.test(text));
}

export function diffBlock(text) {
  const pre = h("pre", { class: "diff" });
  for (const line of text.split("\n")) {
    let kind = "";
    if (line.startsWith("@@")) kind = "hunk";
    else if (line.startsWith("+") && !line.startsWith("+++")) kind = "add";
    else if (line.startsWith("-") && !line.startsWith("---")) kind = "del";
    pre.append(h("span", { class: kind ? `line ${kind}` : "line", text: `${line}\n` }));
  }
  return pre;
}

function list(lines, index, parent) {
  const first = ITEM.exec(lines[index]);
  const indent = first[1].length;
  const ordered = /\d/.test(first[2]);
  const node = h(ordered ? "ol" : "ul");
  const start = parseInt(first[2], 10);
  if (ordered && start !== 1) node.start = start;
  let inner = null;
  const flush = () => {
    if (inner) node.append(listItem(inner));
  };
  while (index < lines.length) {
    const line = lines[index];
    const match = ITEM.exec(line);
    if (match && match[1].length === indent && /\d/.test(match[2]) === ordered) {
      flush();
      inner = [match[3]];
      index += 1;
    } else if (!line.trim()) {
      const next = lines[index + 1];
      const nextItem = next === undefined ? null : ITEM.exec(next);
      if (next !== undefined && next.trim() && (leading(next) > indent || (nextItem && nextItem[1].length === indent))) {
        if (leading(next) > indent) inner.push("");
        index += 1;
      } else {
        break;
      }
    } else if (leading(line) > indent) {
      inner.push(line.slice(Math.min(leading(line), indent + 2)));
      index += 1;
    } else if (!match && !startsBlock(line)) {
      inner.push(line.trim());
      index += 1;
    } else {
      break;
    }
  }
  flush();
  parent.append(node);
  return index;
}

function listItem(inner) {
  const item = h("li");
  const task = /^\[([ xX])\]\s+(.*)$/.exec(inner[0]);
  if (task) {
    item.classList.add("task");
    item.append(h("span", { class: task[1] === " " ? "box" : "box is-checked", "aria-hidden": "true" }));
    inner[0] = task[2];
  }
  if (inner.length === 1) return inline(item, inner[0]);
  const content = document.createDocumentFragment();
  blocks(inner, content);
  item.append(content);
  return item;
}

function isTable(lines, index) {
  return index + 1 < lines.length && lines[index].includes("|") && lines[index + 1].includes("-")
    && TABLE_DIVIDER.test(lines[index + 1]);
}

function cells(line) {
  let row = line.trim();
  if (row.startsWith("|")) row = row.slice(1);
  if (row.endsWith("|") && !row.endsWith("\\|")) row = row.slice(0, -1);
  return row.split(/(?<!\\)\|/).map((cell) => cell.trim().replace(/\\\|/g, "|"));
}

function table(lines, index, parent) {
  const head = cells(lines[index]);
  const aligns = cells(lines[index + 1]).map((cell) => {
    if (cell.startsWith(":") && cell.endsWith(":")) return "center";
    return cell.endsWith(":") ? "right" : "";
  });
  const cell = (tag, column, text) => inline(h(tag, aligns[column] ? { class: `align-${aligns[column]}` } : null), text);
  const body = h("tbody");
  index += 2;
  while (index < lines.length && lines[index].trim() && lines[index].includes("|")) {
    const row = cells(lines[index]);
    body.append(h("tr", null, head.map((_, column) => cell("td", column, row[column] || ""))));
    index += 1;
  }
  const headRow = h("tr", null, head.map((text, column) => cell("th", column, text)));
  parent.append(h("div", { class: "table" }, h("table", null, h("thead", null, headRow), body)));
  return index;
}

// Inline ----------------------------------------------------------------

const INLINE = [
  ["escape", /\\([\\`*_{}[\]()#+\-.!|~>])/],
  ["code", /(`+)([\s\S]*?[^`])\1(?!`)/],
  ["image", /!\[([^\]]*)\]\(\s*([^)\s]+)(?:\s+"[^"]*")?\s*\)/],
  ["link", /\[([^\]]+)\]\(\s*([^)\s]+)(?:\s+"[^"]*")?\s*\)/],
  ["url", /\bhttps?:\/\/[^\s<>]*[^\s<>.,;:!?'")\]]/],
  ["strong", /\*\*(?=\S)([\s\S]*?\S)\*\*|__(?=\S)([\s\S]*?\S)__(?![A-Za-z0-9])/],
  ["strike", /~~(?=\S)([\s\S]*?\S)~~/],
  ["em", /\*(?=[^\s*])([^*]*?[^\s*])\*|(?<![A-Za-z0-9_])_(?=[^\s_])([^_]*?[^\s_])_(?![A-Za-z0-9_])/],
];

function inline(node, text) {
  let rest = text;
  while (rest) {
    let best = null;
    for (const [kind, pattern] of INLINE) {
      const match = pattern.exec(rest);
      if (match && (best === null || match.index < best.match.index)) best = { kind, match };
    }
    if (best === null) {
      node.append(rest);
      break;
    }
    const { kind, match } = best;
    if (match.index > 0) node.append(rest.slice(0, match.index));
    node.append(token(kind, match));
    rest = rest.slice(match.index + match[0].length);
  }
  return node;
}

function token(kind, match) {
  switch (kind) {
    case "escape":
      return match[1];
    case "code":
      return h("code", { text: match[2].replace(/^ (.*) $/, "$1") });
    case "image":
      return link(`图片：${match[1] || match[2]}`, match[2], false);
    case "link":
      return link(match[1], match[2], true);
    case "url":
      return link(match[0], match[0], false);
    case "strong":
      return inline(h("strong"), match[1] || match[2]);
    case "strike":
      return inline(h("del"), match[1]);
    default:
      return inline(h("em"), match[1] || match[2]);
  }
}

function link(label, url, parse) {
  if (!/^(https?:|mailto:)/i.test(url)) {
    const plain = parse ? inline(h("span"), label) : h("span", { text: label });
    plain.title = url;
    return plain;
  }
  const anchor = h("a", { href: url, target: "_blank", rel: "noopener noreferrer" });
  return parse ? inline(anchor, label) : append(anchor, label);
}

function append(node, text) {
  node.append(text);
  return node;
}
