// sql.js detects the runtime before using these Node-only modules.
config.resolve = config.resolve || {};
config.resolve.fallback = { ...config.resolve.fallback, fs: false, path: false, crypto: false };
