// The page's state and the only code that changes it. Views subscribe to
// topics and read state; they never change it directly.
//
// The server sends one snapshot, then ordered "sessions" and "patch" events,
// each with a revision. An event at or below the snapshot's revision is
// already in the snapshot and is dropped (ADR 0044).

const SELECTION_KEY = "deskhelm-web-selection";

export function createStore() {
  const listeners = new Map();
  const state = {
    stream: "connecting", // connecting, open, lost, signed_out
    connected: false, // the web process's Bridge connection
    epoch: "",
    rev: 0,
    notice: "",
    focus: null,
    sessions: new Map(),
    transcripts: new Map(),
    selected: null,
    // The session the user last chose; it is shown again whenever it is present.
    preferred: storageGet(SELECTION_KEY),
    search: "",
    pending: new Map(), // session key -> prompts sent from this page
    deciding: new Set(), // approval ids in flight from this page
    catalogs: new Map(), // session key -> {status: "loading" | "ready" | "failed", data}
    seen: new Map(), // session key -> the updated_at the user last saw
    ui: { details: false, drawer: false, menu: false },
  };

  function on(topic, listener) {
    if (!listeners.has(topic)) listeners.set(topic, new Set());
    listeners.get(topic).add(listener);
  }

  function emit(topic, detail) {
    for (const listener of listeners.get(topic) || []) {
      try {
        listener(detail);
      } catch (error) {
        // One view that fails must not stop the others or the stream.
        console.error(error);
      }
    }
  }

  // Server ------------------------------------------------------------

  function applySnapshot(payload) {
    // A snapshot queued before the one this stream began with is older.
    if (payload.epoch === state.epoch && typeof payload.rev === "number" && payload.rev <= state.rev) return;
    if (payload.epoch !== state.epoch) {
      state.pending.clear();
      state.deciding.clear();
      state.catalogs.clear();
      // Another Bridge or web process: what is listed now counts as seen.
      state.seen.clear();
    }
    state.epoch = payload.epoch;
    state.rev = payload.rev;
    state.transcripts = new Map();
    for (const [key, items] of Object.entries(payload.transcripts || {})) {
      state.transcripts.set(key, new Map(items.map((item) => [item.id, item])));
    }
    readSessions(payload);
    settlePending();
    ensureSelection();
    emit("snapshot");
  }

  function applySessions(payload) {
    if (!fresh(payload)) return;
    readSessions(payload);
    ensureSelection();
    emit("sessions");
  }

  function applyPatch(payload) {
    if (!fresh(payload)) return;
    let items = state.transcripts.get(payload.key);
    if (!items) {
      items = new Map();
      state.transcripts.set(payload.key, items);
    }
    let id = payload.id;
    if (payload.op === "upsert") {
      id = payload.item.id;
      items.set(id, payload.item);
    } else if (payload.op === "append") {
      const item = items.get(id);
      if (item && typeof item[payload.field] === "string") {
        const kept = payload.trim ? item[payload.field].slice(payload.trim) : item[payload.field];
        item[payload.field] = kept + payload.text;
      }
    } else if (payload.op === "remove") {
      items.delete(id);
    }
    const session = state.sessions.get(payload.key);
    if (session) {
      if (typeof payload.preview === "string") session.preview = payload.preview;
      if (payload.updated_at) session.updated_at = payload.updated_at;
      if (payload.key === state.selected) state.seen.set(payload.key, session.updated_at);
    }
    if (payload.op === "upsert" && id.startsWith("m:")) settlePending(payload.key, payload.item);
    emit("patch", { key: payload.key, op: payload.op, id });
    if (session && (typeof payload.preview === "string" || payload.updated_at)) emit("preview", payload.key);
  }

  function fresh(payload) {
    if (typeof payload.rev !== "number" || payload.rev <= state.rev) return false;
    if (payload.rev !== state.rev + 1) {
      // Every event reaches every page in order, so a skipped revision means
      // something was lost; the stream starts again from a snapshot.
      emit("gap");
      return false;
    }
    state.rev = payload.rev;
    return true;
  }

  function readSessions(payload) {
    state.connected = Boolean(payload.connected);
    state.notice = payload.notice || "";
    state.focus = payload.focus || null;
    const next = new Map();
    for (const session of payload.sessions || []) next.set(session.key, session);
    for (const key of state.transcripts.keys()) if (!next.has(key)) state.transcripts.delete(key);
    state.sessions = next;
    // Activity from before the page opened, or in the open session, is seen.
    for (const [key, session] of next) {
      if (!state.seen.has(key) || key === state.selected) state.seen.set(key, session.updated_at);
    }
  }

  function unseen(session) {
    return session.key !== state.selected && session.updated_at > (state.seen.get(session.key) || 0);
  }

  function setStream(value) {
    if (state.stream === value) return;
    state.stream = value;
    emit("connection");
  }

  // Selection ---------------------------------------------------------

  // A session that leaves for a moment, as during a Bridge restart, comes
  // back into view when it returns; the fallback is never stored.
  function ensureSelection() {
    let next = state.selected;
    if (state.preferred && state.sessions.has(state.preferred)) {
      next = state.preferred;
    } else if (!next || !state.sessions.has(next)) {
      const sessions = [...state.sessions.values()];
      const focused = state.focus && state.sessions.has(state.focus) ? state.focus : null;
      const waiting = sessions.find((session) => session.approvals.length);
      const latest = sessions.sort((a, b) => b.updated_at - a.updated_at)[0];
      next = focused || (waiting || latest || {}).key || null;
    }
    if (next !== state.selected) {
      if (next) state.seen.set(next, state.sessions.get(next).updated_at);
      state.selected = next;
      emit("selection");
    }
  }

  function select(key) {
    if (!state.sessions.has(key)) return;
    state.preferred = key;
    storageSet(SELECTION_KEY, key);
    if (key === state.selected) return;
    state.seen.set(key, state.sessions.get(key).updated_at);
    state.selected = key;
    emit("selection");
  }

  function selected() {
    return state.selected ? state.sessions.get(state.selected) || null : null;
  }

  function items(key) {
    return state.transcripts.get(key) || new Map();
  }

  // Page actions ------------------------------------------------------

  function addPending(key, prompt) {
    // A new prompt replaces the notes of earlier ones that never arrived.
    const kept = (state.pending.get(key) || []).filter((entry) => entry.status === "sending" || entry.status === "sent");
    state.pending.set(key, [...kept, prompt]);
    emit("pending", key);
  }

  function removePending(key, commandId) {
    const prompts = state.pending.get(key) || [];
    state.pending.set(key, prompts.filter((entry) => entry.commandId !== commandId));
    emit("pending", key);
  }

  function updatePending(key, commandId, change) {
    const prompt = (state.pending.get(key) || []).find((entry) => entry.commandId === commandId);
    if (!prompt) return;
    Object.assign(prompt, change);
    settlePending(key);
    emit("pending", key);
  }

  function delivered(transcript, prompt) {
    return [`m:${prompt.commandId}`, `m:user:${prompt.commandId}`]
      .some((id) => (transcript.get(id) || {}).role === "user");
  }

  // A prompt settles when its user message arrives under its command id, or,
  // if a vendor sends the message under its own id, under the same text.
  function settlePending(only, arrived) {
    for (const [key, prompts] of state.pending) {
      if (only && key !== only) continue;
      const transcript = items(key);
      let left = prompts.filter((prompt) => !delivered(transcript, prompt));
      if (arrived && arrived.role === "user" && left.length === prompts.length) {
        const index = left.findIndex((prompt) => prompt.status !== "failed" && prompt.text === arrived.text);
        if (index >= 0) left = left.filter((_, position) => position !== index);
      }
      if (left.length !== prompts.length) {
        state.pending.set(key, left);
        emit("pending", key);
      }
    }
  }

  function setDeciding(approvalId, on) {
    if (on) state.deciding.add(approvalId);
    else state.deciding.delete(approvalId);
    emit("approvals");
  }

  function setCatalog(key, value) {
    state.catalogs.set(key, value);
    emit("catalog", key);
  }

  function setSearch(value) {
    state.search = value;
    emit("search");
  }

  // Without a value, flip the flag.
  function setUi(name, value) {
    const next = value === undefined ? !state.ui[name] : Boolean(value);
    if (state.ui[name] === next) return;
    state.ui[name] = next;
    emit("ui");
  }

  return {
    state, on, emit, applySnapshot, applySessions, applyPatch, setStream, select, selected, items, unseen,
    addPending, removePending, updatePending, setDeciding, setCatalog, setSearch, setUi,
  };
}

function storageGet(key) {
  try {
    return window.localStorage.getItem(key);
  } catch (error) {
    return null;
  }
}

function storageSet(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch (error) {
    // Private windows may refuse storage; the selection then lasts one page.
  }
}

export { storageGet, storageSet };
