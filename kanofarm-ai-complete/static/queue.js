/* Offline record queue (plain script: loads in the browser AND in Node for tests; no framework, no build step).
 *
 * Why it is safe to resend:  every record gets a random client_id BEFORE the first attempt. The server stores
 * (farm_id, client_id) as unique, so if a record reached the server but the reply was lost, sending it again changes nothing.
 *
 * What may be queued: ONLY new observations and finance entries for one farm. Nothing else (no deletes, no sign-in, no market
 * posts with photos), so a queued item can never do more than add one record the farmer typed.
 *
 * Failure handling:
 *   - no network / timeout ........ keep the item, try again later
 *   - 401 (signed out/expired) ..... keep the item, stop; it is sent after the farmer signs in again
 *   - 429 / 5xx ................... keep it, but after 5 server-side failures give up (move to "failed") so it cannot loop forever
 *   - any other 4xx ............... the server refused it (e.g. bad data): move to "failed" with the reason so the farmer can see it
 * Items remember which account created them and are sent only while that same account is signed in. */
(function (root) {
  "use strict";
  const ALLOWED = /^\/api\/farms\/[0-9a-fA-F-]{36}\/(observations|finance)$/;
  const MAX_QUEUE = 200, MAX_TRIES = 5, MAX_BODY_CHARS = 5000;

  function makeUuid(randomUUID, rnd) {
    if (randomUUID) return randomUUID();
    const r = rnd || Math.random;                      // fallback for old browsers
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, c => {
      const n = Math.floor(r() * 16); return (c === "x" ? n : (n & 3) | 8).toString(16);
    });
  }

  function create(deps) {
    const { store, send, owner = () => null, online = () => true, now = Date.now, randomUUID } = deps;
    const QK = "kf_queue", FK = "kf_queue_failed";
    let flushing = false;
    const read = k => { const v = store.get(k, []); return Array.isArray(v) ? v : []; };
    const write = (k, v) => store.set(k, v);
    const isNetwork = e => !e || e.status === undefined || e.status === null || e.network === true;

    function enqueue(path, body, label) {
      if (!ALLOWED.test(path)) throw new Error("This kind of record cannot be saved offline.");
      if (JSON.stringify(body || {}).length > MAX_BODY_CHARS) throw new Error("This record is too large to save offline.");
      const q = read(QK);
      if (q.length >= MAX_QUEUE) throw new Error("Too many records are waiting to upload. Connect to the internet to send them first.");
      const id = body.client_id || makeUuid(randomUUID);
      const item = { id, path, body: { ...body, client_id: id }, label: label || "Record", owner: owner(), created: now(), tries: 0 };
      q.push(item); write(QK, q);
      return item;
    }

    /* Try to send now; keep it on this phone if the network is the problem. Server refusals are rethrown so the form can show them. */
    async function submit(path, body, label) {
      const item = { ...body, client_id: body.client_id || makeUuid(randomUUID) };
      if (!online()) return { queued: true, item: enqueue(path, item, label) };
      try {
        const result = await send(path, item);
        return { queued: false, result };
      } catch (e) {
        if (isNetwork(e)) return { queued: true, item: enqueue(path, item, label) };
        throw e;
      }
    }

    function fail(item, reason) {
      const f = read(FK); f.push({ ...item, reason: String(reason || "The server refused this record.").slice(0, 200), failed_at: now() });
      write(FK, f.slice(-50));
    }

    async function flush() {
      if (flushing) return { sent: 0, failed: 0, remaining: read(QK).length, stopped: "busy" };
      flushing = true;
      const out = { sent: 0, failed: 0, remaining: 0, stopped: null };
      try {
        const me = owner();
        if (!me) { out.remaining = read(QK).length; out.stopped = read(QK).length ? "signed_out" : null; return out; }
        for (const item of read(QK)) {
          if (item.owner && item.owner !== me) continue;             // belongs to another account on this phone
          let drop = false;
          try {
            await send(item.path, item.body);
            out.sent++; drop = true;
          } catch (e) {
            if (isNetwork(e)) { out.stopped = "network"; break; }
            if (e.status === 401) { out.stopped = "auth"; break; }
            if (e.status === 429 || e.status >= 500) {
              item.tries = (item.tries || 0) + 1;
              if (item.tries >= MAX_TRIES) { fail(item, e.message); out.failed++; drop = true; }
              else { write(QK, read(QK).map(x => x.id === item.id ? { ...x, tries: item.tries } : x)); out.stopped = e.status === 429 ? "busy" : "server"; break; }
            } else { fail(item, e.message); out.failed++; drop = true; }
          }
          if (drop) write(QK, read(QK).filter(x => x.id !== item.id));
        }
      } finally {
        out.remaining = read(QK).length; flushing = false;
      }
      return out;
    }

    const visible = () => { const me = owner(); return read(QK).filter(x => !x.owner || x.owner === me); };
    return {
      enqueue, submit, flush,
      pending: farmId => visible().filter(x => !farmId || x.path.includes("/" + farmId + "/")),
      failed: () => read(FK).filter(x => !x.owner || x.owner === owner()),
      count: () => visible().length,
      cancel: id => { write(QK, read(QK).filter(x => x.id !== id)); },
      dismissFailed: id => { write(FK, read(FK).filter(x => x.id !== id)); },
      _isBusy: () => flushing,
    };
  }

  const api = { create, ALLOWED, MAX_QUEUE, MAX_TRIES, makeUuid };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.KFQ = api;
})(typeof window !== "undefined" ? window : globalThis);
