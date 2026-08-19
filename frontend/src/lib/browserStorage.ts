export function readBrowserStorage(key: string): string {
  try {
    return globalThis.localStorage?.getItem(key) ?? "";
  } catch {
    return "";
  }
}

export function writeBrowserStorage(key: string, value: string): void {
  try {
    globalThis.localStorage?.setItem(key, value);
  } catch {
    // In-memory token state still supports restricted or disabled storage environments.
  }
}

export function removeBrowserStorage(key: string): void {
  try {
    globalThis.localStorage?.removeItem(key);
  } catch {
    // Clearing the in-memory token remains sufficient for the current session.
  }
}
