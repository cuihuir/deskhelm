// A small Markdown renderer for agent replies. It builds DOM nodes and sets
// text through textContent only, so nothing in a reply becomes markup. It
// covers what agents write: paragraphs, headings, fenced code, lists, quotes,
// tables, rules, and inline code, emphasis, and links. Single newlines stay
// line breaks, as agents mean them. Nesting is bounded and every pattern runs
// in linear time on a line, so no reply can stall the page.

import { copyText, h, icon } from "./dom.js";

const FENCE = /^ {0,3}(`{3,}|~{3,})(.*)$/;
const HEADING = /^ {0,3}(#{1,6})(?:[ \t]+(.*))?$/;
const RULE = /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/;
const QUOTE = /^ {0,3}>[ \t]?(.*)$/;
const ITEM = /^([ \t]*)([-*+]|\d{1,9}[.)])[ \t]+(.*)$/;
const TABLE_DIVIDER = /^[ \t]*\|?[ \t]*:?-+:?[ \t]*(\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$/;
// Deeper lists, quotes, or emphasis than anyone writes stay plain text.
const MAX_DEPTH = 8;

export function renderMarkdown(text) {
  const fragment = document.createDocumentFragment();
  blocks(String(text).replace(/\r\n?/g, "\n").split("\n"), fragment, 0);
  return fragment;
}

function blocks(lines, parent, depth) {
  if (depth > MAX_DEPTH) {
    parent.append(h("p", { class: "plain", text: lines.join("\n") }));
    return;
  }
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    let match;
    let open;
    if (!line.trim()) {
      index += 1;
    } else if ((open = fenceOpen(line))) {
      index = fence(lines, index, open, parent);
    } else if ((match = HEADING.exec(line))) {
      parent.append(inline(h(`h${match[1].length}`), headingText(match[2] || ""), 0));
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
      blocks(inner, quote, depth + 1);
      parent.append(quote);
    } else if (ITEM.test(line)) {
      index = list(lines, index, parent, depth);
    } else if (isTable(lines, index)) {
      index = table(lines, index, parent);
    } else {
      index = paragraph(lines, index, parent);
    }
  }
}

// A backtick fence's info string may not hold a backtick, so a line such as
// "```js x()```" is inline code, not the start of a block.
function fenceOpen(line) {
  const match = FENCE.exec(line);
  if (!match || (match[1][0] === "`" && match[2].includes("`"))) return null;
  return { marker: match[1], lang: match[2].trim().split(/[ \t]/)[0] };
}

// A closing run of # counts only after a space, so "Using C#" keeps its #.
function headingText(text) {
  const trimmed = text.trim();
  const closing = /(^|[ \t])#+$/.exec(trimmed);
  return closing ? trimmed.slice(0, closing.index).trim() : trimmed;
}

function startsBlock(line) {
  return fenceOpen(line) !== null || HEADING.test(line) || RULE.test(line) || QUOTE.test(line) || ITEM.test(line);
}

function leading(line) {
  return line.length - line.trimStart().length;
}

function paragraph(lines, index, parent) {
  const node = h("p");
  let first = true;
  while (index < lines.length && lines[index].trim() && (first || (!startsBlock(lines[index]) && !isTable(lines, index)))) {
    if (!first) node.append(h("br"));
    inline(node, lines[index].trim(), 0);
    first = false;
    index += 1;
  }
  parent.append(node);
  return index;
}

function fence(lines, index, open, parent) {
  const body = [];
  index += 1;
  while (index < lines.length) {
    const close = lines[index].trim();
    if (close[0] === open.marker[0] && close.length >= open.marker.length && /^(`+|~+)$/.test(close)) {
      index += 1;
      break;
    }
    body.push(lines[index]);
    index += 1;
  }
  parent.append(codeBlock(body.join("\n"), open.lang));
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

function list(lines, index, parent, depth) {
  const first = ITEM.exec(lines[index]);
  const indent = first[1].length;
  const ordered = /\d/.test(first[2]);
  const node = h(ordered ? "ol" : "ul");
  const start = parseInt(first[2], 10);
  if (ordered && start !== 1) node.start = start;
  let inner = null;
  const flush = () => {
    if (inner) node.append(listItem(inner, depth));
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

function listItem(inner, depth) {
  const item = h("li");
  const task = /^\[([ xX])\][ \t]+(.*)$/.exec(inner[0]);
  if (task) {
    item.classList.add("task");
    item.append(h("span", { class: task[1] === " " ? "box" : "box is-checked", "aria-hidden": "true" }));
    inner[0] = task[2];
  }
  if (inner.length === 1) return inline(item, inner[0], 0);
  const content = document.createDocumentFragment();
  blocks(inner, content, depth + 1);
  item.append(content);
  return item;
}

function isTable(lines, index) {
  return index + 1 < lines.length && lines[index].includes("|") && lines[index + 1].includes("-")
    && TABLE_DIVIDER.test(lines[index + 1]);
}

// Split a table row on the pipes that are not escaped as "\|".
function cells(line) {
  let row = line.trim();
  if (row.startsWith("|")) row = row.slice(1);
  if (row.endsWith("|") && !row.endsWith("\\|")) row = row.slice(0, -1);
  const found = [];
  let cell = "";
  for (let index = 0; index < row.length; index += 1) {
    if (row[index] === "\\" && row[index + 1] === "|") {
      cell += "|";
      index += 1;
    } else if (row[index] === "|") {
      found.push(cell.trim());
      cell = "";
    } else {
      cell += row[index];
    }
  }
  found.push(cell.trim());
  return found;
}

function table(lines, index, parent) {
  const head = cells(lines[index]);
  const aligns = cells(lines[index + 1]).map((cell) => {
    if (cell.startsWith(":") && cell.endsWith(":")) return "center";
    return cell.endsWith(":") ? "right" : "";
  });
  const cell = (tag, column, text) => inline(h(tag, aligns[column] ? { class: `align-${aligns[column]}` } : null), text, 0);
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

// Earlier patterns win at the same position. Each pattern is searched with
// lastIndex set just before exec, so nested calls cannot disturb it.
const INLINE = [
  ["escape", /\\([\\`*_{}[\]()#+\-.!|~>])/g],
  ["code", /(`+)([\s\S]*?[^`])\1(?!`)/g],
  ["image", /!\[([^\]]*)\]\(\s*([^)\s]+)(?:\s+"[^"]*")?\s*\)/g],
  ["link", /\[([^\]]+)\]\(\s*([^)\s]+)(?:\s+"[^"]*")?\s*\)/g],
  ["url", /\bhttps?:\/\/[^\s<>]*[^\s<>.,;:!?'")\]]/g],
  ["strong", /\*\*(?=\S)([\s\S]*?\S)\*\*|__(?=\S)([\s\S]*?\S)__(?![A-Za-z0-9])/g],
  ["strike", /~~(?=\S)([\s\S]*?\S)~~/g],
  ["em", /\*(?=[^\s*])([^*]*?[^\s*])\*/g],
];

// Each pattern's next match is kept until the text before it is consumed,
// so a long line is scanned once per pattern instead of once per token.
function inline(node, text, depth) {
  if (depth > MAX_DEPTH) {
    node.append(text);
    return node;
  }
  const next = new Array(INLINE.length);
  let position = 0;
  while (position < text.length) {
    let best = -1;
    for (let index = 0; index < INLINE.length; index += 1) {
      const cached = next[index];
      if (cached === undefined || (cached !== null && cached.index < position)) {
        const pattern = INLINE[index][1];
        pattern.lastIndex = position;
        next[index] = pattern.exec(text);
      }
      const match = next[index];
      if (match && (best < 0 || match.index < next[best].index)) best = index;
    }
    if (best < 0) {
      node.append(text.slice(position));
      break;
    }
    const match = next[best];
    if (match.index > position) node.append(text.slice(position, match.index));
    node.append(token(INLINE[best][0], match, depth));
    position = match.index + match[0].length;
  }
  return node;
}

function token(kind, match, depth) {
  switch (kind) {
    case "escape":
      return match[1];
    case "code":
      return h("code", { text: match[2].replace(/^ (.*) $/, "$1") });
    case "image":
      return link(`图片：${match[1] || match[2]}`, match[2], false, depth);
    case "link":
      return link(match[1], match[2], true, depth);
    case "url":
      return link(match[0], match[0], false, depth);
    case "strong":
      return inline(h("strong"), match[1] || match[2], depth + 1);
    case "strike":
      return inline(h("del"), match[1], depth + 1);
    default:
      return inline(h("em"), match[1], depth + 1);
  }
}

function link(label, url, parse, depth) {
  if (!/^(https?:|mailto:)/i.test(url)) {
    const plain = parse ? inline(h("span"), label, depth + 1) : h("span", { text: label });
    plain.title = url;
    return plain;
  }
  const anchor = h("a", { href: url, target: "_blank", rel: "noopener noreferrer" });
  if (parse) return inline(anchor, label, depth + 1);
  anchor.append(label);
  return anchor;
}
