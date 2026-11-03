// The page's only network code: one event stream and two JSON requests.

export function openStream(store) {
  let source = null;
  let delay = 1000;
  let leaving = false;
  window.addEventListener("pagehide", () => {
    leaving = true;
    source.close();
  });
  // A page restored from the back/forward cache needs its stream again.
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) {
      leaving = false;
      start();
    }
  });
  store.on("gap", () => {
    source.close();
    start();
  });

  const read = (handler) => (event) => {
    let payload = null;
    try {
      payload = JSON.parse(event.data);
    } catch (error) {
      return;
    }
    if (payload && typeof payload === "object") handler(payload);
  };

  function start() {
    source = new EventSource("/api/events");
    source.addEventListener("snapshot", read((payload) => {
      delay = 1000;
      store.applySnapshot(payload);
      store.setStream("open");
    }));
    source.addEventListener("sessions", read(store.applySessions));
    source.addEventListener("patch", read(store.applyPatch));
    source.addEventListener("error", () => {
      if (leaving) return;
      store.setStream("lost");
      // EventSource retries a dropped stream itself, but not a refused one.
      if (source.readyState === EventSource.CLOSED) refused();
    });
  }

  async function refused() {
    try {
      const response = await fetch("/api/ping", { cache: "no-store" });
      if (response.status === 401) {
        store.setStream("signed_out");
        return;
      }
    } catch (error) {
      // The web process is gone; keep trying.
    }
    setTimeout(start, delay);
    delay = Math.min(delay * 2, 30000);
  }

  start();
}

export function sessionFields(session) {
  return { agent_id: session.agent_id, session_id: session.session_id, project_id: session.project_id };
}

export async function control(body) {
  try {
    const response = await fetch("/api/control", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await response.json().catch(() => ({}));
    return { ok: response.ok, payload };
  } catch (error) {
    // The server may have acted before the response was lost.
    return { ok: false, payload: { error: "request_lost" } };
  }
}

export async function catalog(session) {
  const query = new URLSearchParams(sessionFields(session));
  try {
    const response = await fetch(`/api/catalog?${query}`, { cache: "no-store" });
    const payload = await response.json().catch(() => ({}));
    return { ok: response.ok, payload };
  } catch (error) {
    return { ok: false, payload: { error: "request_lost" } };
  }
}
