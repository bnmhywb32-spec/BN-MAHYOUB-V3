// Isolated proof: atomically publish only changed 64 KiB chunks.
const PROOF_CHUNK_SIZE = 65536;

function openProofStore() {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open("bn-v3-proof-snapshots", 2);
        request.onupgradeneeded = () => {
            if (!request.result.objectStoreNames.contains("snapshots"))
                request.result.createObjectStore("snapshots");
            request.result.createObjectStore("chunks");
        };
        request.onsuccess = () => {
            request.result.onversionchange = () => request.result.close();
            resolve(request.result);
        };
        request.onerror = () => reject(request.error);
        request.onblocked = () => reject(new Error("Snapshot upgrade blocked"));
    });
}

function readProofSnapshot(db) {
    return new Promise((resolve, reject) => {
        const tx = db.transaction(["snapshots", "chunks"]);
        const snapshots = tx.objectStore("snapshots");
        const chunks = tx.objectStore("chunks");
        let bytes;
        const meta = snapshots.get("chunk-length");
        meta.onsuccess = () => {
            const length = meta.result;
            if (length === undefined) {
                const legacy = snapshots.get("catalog");
                legacy.onsuccess = () => { bytes = legacy.result; };
                return;
            }
            if (!Number.isSafeInteger(length) || length <= 0) {
                tx.abort();
                return;
            }
            bytes = new Uint8Array(length);
            for (let offset = 0; offset < length; offset += PROOF_CHUNK_SIZE) {
                const request = chunks.get(offset / PROOF_CHUNK_SIZE);
                request.onsuccess = () => {
                    const part = request.result;
                    if (!(part instanceof Uint8Array) ||
                        part.length !== Math.min(PROOF_CHUNK_SIZE, length - offset)) {
                        tx.abort();
                        return;
                    }
                    bytes.set(part, offset);
                };
            }
        };
        tx.oncomplete = () => resolve(bytes);
        tx.onabort = () => reject(tx.error ?? new Error("Invalid snapshot chunks"));
    });
}

function publishProofSnapshot(db, bytes, abort) {
    return new Promise((resolve, reject) => {
        const tx = db.transaction(["snapshots", "chunks"], "readwrite", { durability: "strict" });
        const snapshots = tx.objectStore("snapshots");
        const chunks = tx.objectStore("chunks");
        let written = 0;
        const old = snapshots.get("chunk-length");
        old.onsuccess = () => {
            const previousLength = old.result ?? 0;
            if (!Number.isSafeInteger(previousLength) || previousLength < 0 ||
                !(bytes instanceof Uint8Array) || bytes.length === 0) {
                tx.abort();
                return;
            }
            for (let offset = 0; offset < bytes.length; offset += PROOF_CHUNK_SIZE) {
                const key = offset / PROOF_CHUNK_SIZE;
                const part = bytes.subarray(offset, offset + PROOF_CHUNK_SIZE);
                const request = chunks.get(key);
                request.onsuccess = () => {
                    const prior = request.result;
                    if (!(prior instanceof Uint8Array) || prior.length !== part.length ||
                        !part.every((value, i) => value === prior[i])) {
                        chunks.put(part, key);
                        written += part.length;
                    }
                };
            }
            for (let key = Math.ceil(bytes.length / PROOF_CHUNK_SIZE);
                key < Math.ceil(previousLength / PROOF_CHUNK_SIZE); key++) chunks.delete(key);
            snapshots.put(bytes.length, "chunk-length");
            snapshots.delete("catalog");
            // Queue after all reads; their callbacks enqueue changed writes before this abort.
            if (abort) {
                const barrier = snapshots.get("chunk-length");
                barrier.onsuccess = () => {
                    const final = snapshots.get("chunk-length");
                    final.onsuccess = () => tx.abort();
                };
            }
        };
        tx.oncomplete = () => {
            console.log("BN_V3_CHUNK_BYTES: " + written + "/" + bytes.length);
            resolve();
        };
        tx.onabort = () => reject(tx.error ?? new Error(abort ? "Injected snapshot abort" : "Snapshot publication aborted"));
    });
}

if (navigator.serviceWorker) {
    navigator.serviceWorker.register("./proof-worker.js").catch(error => {
        console.error("BN_V3_OFFLINE_SETUP: " + error.message);
    });
}
