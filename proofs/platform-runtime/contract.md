# Platform runtime acceptance

Use synthetic data only. Each Android, Windows and browser adapter must execute the same durable transaction: initial stock 10, sell 2 at 100 with cost 60, stock 8, revenue 200, cost 120. Verify balanced journal, duplicate command replay, changed payload rejection, injected failure rollback, restart persistence, and company isolation. A browser must additionally prove persistent storage after reload and exact integer arithmetic. Build success is not runtime success. Record platform, storage adapter, version, actual execution logs and failures independently. No production approval or technology selection follows from a partial result.

Both candidate implementations assert the stock result, exact cash and cost amounts, journal balance, outbox count, changed-command rejection and rollback after an injected fault. Both exercise SQLite arithmetic above 2^53 and transfer the exact result as decimal text. The test fixture uses small business amounts and a separate precision assertion.

| Target | Kotlin | Flutter | Persistence boundary |
| --- | --- | --- | --- |
| Android | AndroidX BundledSQLiteDriver and instrumentation | sqflite and a launched Flutter APK | Force stop and separate process launch on an API 35 emulator |
| Windows | AndroidX BundledSQLiteDriver on the JVM | sqflite FFI and a launched Windows executable | Separate processes using the same database |
| Web | Kotlin/JS and sql.js 1.13.0 | sqflite WASM worker and a built Flutter web application | Chromium page reload within the same storage origin |

All six cells executed successfully in [GitHub Actions run 45819426997](https://github.com/bnmhywb32-spec/BN-MAHYOUB-V3/actions/runs/45819426997), checked out at `175172b5180e3470fd021e26d026ec785db71958`. The Kotlin browser runner compiles the same contract as Android and Windows. Its localStorage snapshot is bounded to this experiment. Both browser launchers use Web Locks to serialize the fixture across tabs on one origin. The contention harness holds the lock until two tabs queue, then checks that exactly one seeds the sale and the other sees persisted state. Eviction, offline asset delivery, larger databases and other browsers still require evaluation. The Kotlin Windows runner establishes the storage contract on the target OS. The Compose shell is covered separately by the build smoke. The platform result does not measure V2 task speed, prove hardware printing or choose a production storage adapter.
