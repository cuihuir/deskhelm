// Small DOM helpers. Text always goes through textContent; nothing here
// parses HTML, so content from agents cannot become markup.

const SVG = "http://www.w3.org/2000/svg";

export function h(tag, props, ...children) {
  const node = document.createElement(tag);
  if (props) {
    for (const [name, value] of Object.entries(props)) {
      if (value === null || value === undefined || value === false) continue;
      if (name === "class") node.className = value;
      else if (name === "text") node.textContent = value;
      else if (name === "on") for (const [event, handler] of Object.entries(value)) node.addEventListener(event, handler);
      else if (name === "dataset") Object.assign(node.dataset, value);
      else if (name in node && typeof value !== "string") node[name] = value;
      else node.setAttribute(name, value === true ? "" : String(value));
    }
  }
  append(node, children);
  return node;
}

export function append(node, children) {
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

// 24x24 stroke icons drawn for this page.
const ICONS = {
  menu: ["M4 7h16", "M4 12h16", "M4 17h16"],
  close: ["M6 6l12 12", "M18 6 6 18"],
  search: ["M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13Z", "m15.5 15.5 4.5 4.5"],
  send: ["M12 19V5", "m5.5 11.5 6.5-6.5 6.5 6.5"],
  stop: ["M7 7h10v10H7z"],
  mic: ["M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Z", "M6 11a6 6 0 0 0 12 0", "M12 17v3"],
  info: ["M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z", "M12 11v5", "M12 8h.01"],
  terminal: ["M4 5h16v14H4z", "m7.5 9.5 3 2.5-3 2.5", "M12.5 15h4"],
  file: ["M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z", "M14 3v5h5", "m9.5 15.5 5-5"],
  globe: ["M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z", "M3 12h18", "M12 3c2.5 2.6 3.5 5.6 3.5 9s-1 6.4-3.5 9c-2.5-2.6-3.5-5.6-3.5-9s1-6.4 3.5-9Z"],
  plug: ["M9 3v4", "M15 3v4", "M6.5 7h11v3a5.5 5.5 0 0 1-11 0Z", "M12 15.5V21"],
  tool: ["M14.5 6.5a4 4 0 0 0-5.3 5.3L4 17l3 3 5.2-5.2a4 4 0 0 0 5.3-5.3l-2.6 2.6-2.4-.6-.6-2.4Z"],
  check: ["m5 12.5 4.5 4.5L19 7.5"],
  cross: ["M7 7l10 10", "M17 7 7 17"],
  alert: ["M12 4 2.8 19.5h18.4Z", "M12 10v4.5", "M12 17h.01"],
  chevron: ["m9 6 6 6-6 6"],
  copy: ["M9 9h10v10H9z", "M5 15V5h10"],
  sun: ["M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z", "M12 2.5v2", "M12 19.5v2", "M2.5 12h2", "M19.5 12h2", "m5.3 5.3 1.4 1.4", "m17.3 17.3 1.4 1.4", "m5.3 18.7 1.4-1.4", "m17.3 6.7 1.4-1.4"],
  moon: ["M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5Z"],
  refresh: ["M20 11a8 8 0 0 0-14.3-4.6L4 8", "M4 4v4h4", "M4 13a8 8 0 0 0 14.3 4.6L20 16", "M20 20v-4h-4"],
  question: ["M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z", "M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6v.6", "M12 17h.01"],
  down: ["M12 5v14", "m5.5 12.5 6.5 6.5 6.5-6.5"],
  sparkle: ["M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8Z", "M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8Z"],
};

export function icon(name, className) {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("class", className ? `icon ${className}` : "icon");
  for (const d of ICONS[name] || ICONS.tool) {
    const path = document.createElementNS(SVG, "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
}

export function nearBottom(scroller, slack = 120) {
  return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < slack;
}

export function newId() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function copyText(text) {
  if (!navigator.clipboard) return false;
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (error) {
    return false;
  }
}
