// Clean City offline queue. A report taken without internet is kept on the phone (IndexedDB)
// and sent automatically when the connection comes back, even if the app is closed (Background Sync).
// Works in the page and inside the service worker.
(function (scope) {
  const DB_NAME = "clean-city";
  const CLAIM_MS = 2 * 60 * 1000;
  let dbPromise = null;

  function openDb() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains("queue")) db.createObjectStore("queue", { keyPath: "id", autoIncrement: true });
        if (!db.objectStoreNames.contains("results")) db.createObjectStore("results", { keyPath: "id", autoIncrement: true });
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => { dbPromise = null; reject(req.error); };
    });
    return dbPromise;
  }

  function run(storeName, mode, work) {
    return openDb().then((db) => new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, mode);
      const store = tx.objectStore(storeName);
      let result;
      Promise.resolve(work(store, (v) => { result = v; })).catch(reject);
      tx.oncomplete = () => resolve(result);
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error);
    }));
  }

  function getAll(storeName) {
    return run(storeName, "readonly", (store, done) => {
      const req = store.getAll();
      req.onsuccess = () => done(req.result || []);
    });
  }

  const Q = {};

  Q.add = (item) => run("queue", "readwrite", (store, done) => {
    const req = store.add(Object.assign({ saved_at: new Date().toISOString(), sendingAt: 0 }, item));
    req.onsuccess = () => done(req.result);
  });

  Q.list = () => getAll("queue");

  Q.remove = (id) => run("queue", "readwrite", (store) => { store.delete(id); });

  // Mark an item as being sent, so the page and the service worker never send it twice.
  function claim(id) {
    return run("queue", "readwrite", (store, done) => {
      const req = store.get(id);
      req.onsuccess = () => {
        const item = req.result;
        if (!item || (item.sendingAt && Date.now() - item.sendingAt < CLAIM_MS)) return done(null);
        item.sendingAt = Date.now();
        store.put(item);
        done(item);
      };
    });
  }

  function release(id) {
    return run("queue", "readwrite", (store) => {
      const req = store.get(id);
      req.onsuccess = () => {
        if (req.result) { req.result.sendingAt = 0; store.put(req.result); }
      };
    });
  }

  function addResult(result) {
    return run("results", "readwrite", (store) => { store.add(result); });
  }

  // Results of reports sent in the background, shown to the person the next time the app is open.
  Q.takeResults = () => run("results", "readwrite", (store, done) => {
    const req = store.getAll();
    req.onsuccess = () => { done(req.result || []); store.clear(); };
  });

  async function sendOne(item) {
    const form = new FormData();
    form.append("photo", item.photo, "photo.jpg");
    form.append("latitude", item.latitude);
    form.append("longitude", item.longitude);
    if (item.gps_latitude != null) {
      form.append("gps_latitude", item.gps_latitude);
      form.append("gps_longitude", item.gps_longitude);
    }
    form.append("description", item.description || "");
    form.append("taken_at", item.taken_at || "");
    const res = await fetch("/api/complaints", {
      method: "POST",
      body: form,
      headers: { Authorization: "Bearer " + item.token },
    });
    const data = await res.json().catch(() => ({}));
    return { status: res.status, data: data };
  }

  // Send everything in the queue. Returns the results; network errors leave items queued.
  Q.flush = async () => {
    const items = await Q.list();
    const results = [];
    for (const queued of items) {
      const item = await claim(queued.id);
      if (!item) continue;
      let outcome;
      try {
        outcome = await sendOne(item);
      } catch (err) {
        await release(item.id);
        break; // still offline
      }
      if (outcome.status >= 200 && outcome.status < 300) {
        await Q.remove(item.id);
        const r = { ok: true, report_id: outcome.data.id, saved_at: item.saved_at };
        results.push(r);
        await addResult(r);
      } else if (outcome.status === 401 || outcome.status === 429 || outcome.status >= 500) {
        await release(item.id); // try again later (after login, or when the server is back)
      } else {
        await Q.remove(item.id);
        const message = typeof outcome.data.detail === "string" ? outcome.data.detail : "The report was not accepted.";
        const r = { ok: false, message: message, saved_at: item.saved_at };
        results.push(r);
        await addResult(r);
      }
    }
    return results;
  };

  scope.CCOffline = Q;
})(typeof self !== "undefined" ? self : window);
