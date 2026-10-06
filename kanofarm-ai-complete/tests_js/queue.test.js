// Run with:  node --test tests_js/queue.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const { create, MAX_QUEUE, MAX_TRIES } = require("../static/queue.js");

const FARM = "22222222-2222-2222-2222-222222222222";
const OBS = `/api/farms/${FARM}/observations`, FIN = `/api/farms/${FARM}/finance`;
const mem = () => { const d = {}; return { get: (k, def) => (k in d ? JSON.parse(d[k]) : def), set: (k, v) => { d[k] = JSON.stringify(v); }, raw: d }; };
const netErr = () => Object.assign(new TypeError("Failed to fetch"));
const httpErr = (status, message = "nope") => Object.assign(new Error(message), { status });
let n = 0;
const make = (over = {}) => {
  const store = mem(), sent = [];
  const q = create({ store, owner: () => "u1", online: () => true, randomUUID: () => `00000000-0000-4000-8000-${String(++n).padStart(12, "0")}`,
                     send: async (p, b) => { sent.push({ p, b }); return { ok: true }; }, ...over });
  return { q, store, sent };
};

test("only new observations and finance entries can be queued", () => {
  const { q } = make();
  for (const bad of ["/api/farms/x/observations", `/api/farms/${FARM}`, `/api/farms/${FARM}/crops`, "/api/market", "/api/profile", `/api/farms/${FARM}/finance/abc`]) {
    assert.throws(() => q.enqueue(bad, { a: 1 }), /cannot be saved offline/, bad);
  }
  assert.equal(q.enqueue(OBS, { kind: "note" }).path, OBS);
});

test("every queued record gets a client_id and remembers its owner", () => {
  const { q } = make();
  const item = q.enqueue(FIN, { kind: "income" }, "Sale");
  assert.match(item.body.client_id, /^[0-9a-f-]{36}$/); assert.equal(item.owner, "u1"); assert.equal(item.label, "Sale");
});

test("online and working: sent at once, nothing queued, client_id included", async () => {
  const { q, sent } = make();
  const r = await q.submit(OBS, { kind: "note" });
  assert.equal(r.queued, false); assert.equal(q.count(), 0); assert.ok(sent[0].b.client_id);
});

test("offline: queued without trying the network", async () => {
  const { q, sent } = make({ online: () => false });
  const r = await q.submit(OBS, { kind: "note" });
  assert.equal(r.queued, true); assert.equal(sent.length, 0); assert.equal(q.count(), 1);
});

test("network failure: queued with the SAME client_id that was attempted", async () => {
  let seen;
  const { q } = make({ send: async (p, b) => { seen = b.client_id; throw netErr(); } });
  const r = await q.submit(OBS, { kind: "note" });
  assert.equal(r.queued, true); assert.equal(q.pending()[0].body.client_id, seen);
});

test("server refusal is shown to the farmer, not queued", async () => {
  const { q } = make({ send: async () => { throw httpErr(422, "amount must be more than zero"); } });
  await assert.rejects(q.submit(FIN, { kind: "income" }), /amount must be more than zero/);
  assert.equal(q.count(), 0);
});

test("flush sends in order and empties the queue", async () => {
  const { q, sent } = make({ online: () => false });
  await q.submit(OBS, { text: "first" }); await q.submit(FIN, { text: "second" });
  const r = await q.flush();
  assert.deepEqual([r.sent, r.remaining, r.stopped], [2, 0, null]);
  assert.deepEqual(sent.map(s => s.b.text), ["first", "second"]);
});

test("flush stops at the first network failure and keeps the rest", async () => {
  let calls = 0;
  const { q } = make({ online: () => false, send: async () => { if (++calls === 2) throw netErr(); return {}; } });
  for (const t of ["a", "b", "c"]) await q.submit(OBS, { text: t });
  const r = await q.flush();
  assert.deepEqual([r.sent, r.remaining, r.stopped], [1, 2, "network"]);
});

test("401 keeps everything and stops (sent after the farmer signs in again)", async () => {
  const { q } = make({ online: () => false, send: async () => { throw httpErr(401); } });
  await q.submit(OBS, { text: "a" });
  const r = await q.flush();
  assert.deepEqual([r.sent, r.failed, r.remaining, r.stopped], [0, 0, 1, "auth"]);
});

test("a record the server refuses moves to 'failed' with its reason; the next record is still sent", async () => {
  const { q } = make({ online: () => false, send: async (p, b) => { if (b.text === "bad") throw httpErr(422, "kind is not valid"); return {}; } });
  await q.submit(OBS, { text: "bad" }); await q.submit(OBS, { text: "good" });
  const r = await q.flush();
  assert.deepEqual([r.sent, r.failed, r.remaining], [1, 1, 0]);
  assert.equal(q.failed()[0].reason, "kind is not valid");
  q.dismissFailed(q.failed()[0].id); assert.equal(q.failed().length, 0);
});

test("repeated server errors give up after MAX_TRIES instead of looping forever", async () => {
  const { q } = make({ online: () => false, send: async () => { throw httpErr(502); } });
  await q.submit(OBS, { text: "a" });
  for (let i = 1; i < MAX_TRIES; i++) { const r = await q.flush(); assert.equal(r.stopped, "server"); assert.equal(q.count(), 1); }
  const last = await q.flush();
  assert.deepEqual([last.failed, last.remaining], [1, 0]); assert.equal(q.failed().length, 1);
});

test("429 keeps the record and asks to try later", async () => {
  const { q } = make({ online: () => false, send: async () => { throw httpErr(429); } });
  await q.submit(OBS, { text: "a" });
  assert.equal((await q.flush()).stopped, "busy"); assert.equal(q.count(), 1);
});

test("another account's records are never sent, and are hidden", async () => {
  let me = "u1"; const { q, sent } = make({ online: () => false, owner: () => me });
  await q.submit(OBS, { text: "mine" });
  me = "u2"; await q.submit(OBS, { text: "theirs" });
  assert.equal(q.count(), 1);
  const r = await q.flush();
  assert.equal(r.sent, 1); assert.equal(sent[0].b.text, "theirs");
  me = "u1"; assert.equal(q.count(), 1); assert.equal(q.pending()[0].body.text, "mine");
});

test("signed out: nothing is sent and nothing is lost", async () => {
  let me = "u1"; const { q, sent } = make({ online: () => false, owner: () => me });
  await q.submit(OBS, { text: "a" }); me = null;
  const r = await q.flush();
  assert.equal(sent.length, 0); assert.equal(r.stopped, "signed_out"); me = "u1"; assert.equal(q.count(), 1);
});

test("lost reply: the server saved it but the phone never heard back; resending creates NO duplicate", async () => {
  const db = new Map(); let dropReply = true;
  const server = async (p, b) => {
    if (!db.has(b.client_id)) db.set(b.client_id, b);               // the server's unique (farm_id, client_id)
    if (dropReply) { dropReply = false; throw netErr(); }
    return { ok: true };
  };
  const { q } = make({ send: server });
  const r = await q.submit(FIN, { amount_ngn: 5000 });
  assert.equal(r.queued, true); assert.equal(db.size, 1);
  await q.flush();
  assert.equal(db.size, 1); assert.equal(q.count(), 0);
});

test("the queue has a size limit", () => {
  const { q } = make();
  for (let i = 0; i < MAX_QUEUE; i++) q.enqueue(OBS, { text: "x" });
  assert.throws(() => q.enqueue(OBS, { text: "x" }), /Too many records/);
});

test("oversized records are refused", () => {
  const { q } = make();
  assert.throws(() => q.enqueue(OBS, { text: "x".repeat(6000) }), /too large/);
});

test("pending can be filtered by farm and cancelled", async () => {
  const other = "33333333-3333-3333-3333-333333333333";
  const { q } = make({ online: () => false });
  await q.submit(OBS, { text: "a" }); await q.submit(`/api/farms/${other}/finance`, { text: "b" });
  assert.equal(q.pending(FARM).length, 1); assert.equal(q.pending(other).length, 1);
  q.cancel(q.pending(FARM)[0].id); assert.equal(q.pending(FARM).length, 0); assert.equal(q.count(), 1);
});

test("two flushes at once do not double-send", async () => {
  let release, calls = 0; const gate = new Promise(r => { release = r; });
  const { q } = make({ online: () => false, send: async () => { calls++; await gate; return {}; } });
  await q.submit(OBS, { text: "a" });
  const first = q.flush(), second = await q.flush();
  assert.equal(second.stopped, "busy"); release(); await first; assert.equal(calls, 1);
});
