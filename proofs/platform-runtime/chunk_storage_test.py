"""Exercise chunked IndexedDB persistence and migration in an isolated browser origin."""

import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from playwright.sync_api import sync_playwright


def main():
    resource_dir = Path(__file__).parent / "web" / "src" / "main" / "resources"
    handler = partial(SimpleHTTPRequestHandler, directory=resource_dir)
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            with sync_playwright() as playwright:
                executable = os.getenv("BN_PROOF_CHROMIUM")
                browser = playwright.chromium.launch(
                    **({"executable_path": executable} if executable else {"channel": "chrome"})
                )
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{server.server_port}/proof-store.js")
                page.evaluate("""() => new Promise((resolve, reject) => {
                    const request = indexedDB.open("bn-v3-proof-snapshots", 1);
                    request.onupgradeneeded = () => request.result.createObjectStore("snapshots");
                    request.onsuccess = () => {
                        const db = request.result;
                        const tx = db.transaction("snapshots", "readwrite");
                        const bytes = new Uint8Array(33_087_488);
                        for (let index = 0; index < bytes.length; index++) bytes[index] = index % 251;
                        tx.objectStore("snapshots").put(bytes, "catalog");
                        tx.oncomplete = () => { db.close(); resolve(); };
                        tx.onabort = () => reject(tx.error);
                    };
                    request.onerror = () => reject(request.error);
                })""")
                page.add_script_tag(path=str(resource_dir / "proof-store.js"))
                result = page.evaluate("""async () => {
                    const db = await openProofStore();
                    try {
                        let written = 0, largestBuffer = 0;
                        const original = IDBObjectStore.prototype.put;
                        IDBObjectStore.prototype.put = function(value, key) {
                            if (this.name === "chunks") {
                                written += value.byteLength;
                                largestBuffer = Math.max(largestBuffer, value.buffer.byteLength);
                            }
                            return original.call(this, value, key);
                        };
                        try {
                            const legacy = await readProofSnapshot(db);
                            if (legacy.length !== 33_087_488 || legacy[1000] !== 247)
                                throw new Error("Legacy snapshot read failed");
                            if (written !== legacy.length || largestBuffer > 65536)
                                throw new Error("Migration write size mismatch");
                            let restored = await readProofSnapshot(db);
                            if (restored.length !== legacy.length || restored[1000] !== legacy[1000])
                                throw new Error("Migrated snapshot mismatch");
                            written = 0;
                            await publishProofSnapshot(db, restored, false);
                            if (written !== 0) throw new Error("Unchanged chunks were written");
                            const changed = restored.slice();
                            changed[1000] ^= 255;
                            let aborted = false;
                            try { await publishProofSnapshot(db, changed, true); }
                            catch (error) { aborted = error.message === "Injected snapshot abort"; }
                            if (!aborted) throw new Error("Abort was not observed");
                            restored = await readProofSnapshot(db);
                            if (restored[1000] !== legacy[1000]) throw new Error("Aborted write persisted");
                            written = 0;
                            await publishProofSnapshot(db, changed, false);
                            if (written !== 65536) throw new Error("Changed-byte count mismatch");
                            const changedBytes = written;
                            restored = await readProofSnapshot(db);
                            if (restored[1000] !== changed[1000]) throw new Error("Updated chunk lost");
                            await new Promise((resolve, reject) => {
                                const tx = db.transaction("chunks", "readwrite");
                                const request = tx.objectStore("chunks").get(0);
                                request.onsuccess = () => {
                                    request.result[1] ^= 1;
                                    tx.objectStore("chunks").put(request.result, 0);
                                };
                                tx.oncomplete = resolve;
                                tx.onabort = () => reject(tx.error);
                            });
                            let rejected = false;
                            try { await readProofSnapshot(db); }
                            catch (error) { rejected = error.message === "Snapshot integrity mismatch"; }
                            if (!rejected) throw new Error("Same-length corruption accepted");
                            return {legacyBytes: legacy.length, largestBuffer, changedBytes};
                        } finally { IDBObjectStore.prototype.put = original; }
                    } finally { db.close(); }
                }""")
                print(f"PASS: legacy migration, abort, changed write and corruption checks {result}")
                browser.close()
        finally:
            server.shutdown()
            thread.join()


if __name__ == "__main__":
    main()
