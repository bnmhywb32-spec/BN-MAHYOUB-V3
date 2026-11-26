function openProofStore() {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open("bn-v3-proof-snapshots", 1);
        request.onupgradeneeded = () => request.result.createObjectStore("snapshots");
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
    });
}

function readProofSnapshot(db) {
    return new Promise((resolve, reject) => {
        const transaction = db.transaction("snapshots");
        const request = transaction.objectStore("snapshots").get("catalog");
        transaction.oncomplete = () => resolve(request.result);
        transaction.onabort = () => reject(transaction.error);
    });
}

function publishProofSnapshot(db, bytes, abort) {
    return new Promise((resolve, reject) => {
        const transaction = db.transaction("snapshots", "readwrite", { durability: "strict" });
        const request = transaction.objectStore("snapshots").put(bytes, "catalog");
        if (abort) request.onsuccess = () => transaction.abort();
        transaction.oncomplete = () => resolve();
        transaction.onabort = () => reject(
            transaction.error ?? new Error("Injected snapshot abort")
        );
    });
}

if (navigator.serviceWorker) {
    navigator.serviceWorker.register("./proof-worker.js").catch(error => {
        console.error("BN_V3_OFFLINE_SETUP: " + error.message);
    });
}
