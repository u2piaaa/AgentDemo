/** Discard completions from a stream whose workspace has been replaced. */
export class StreamScope {
  private controller = new AbortController();
  private version = 0;
  begin() {
    this.invalidate();
    return { version: this.version, signal: this.controller.signal };
  }
  invalidate() {
    this.controller.abort();
    this.controller = new AbortController();
    this.version += 1;
  }
  isCurrent(version: number) { return version === this.version; }
  stop() { this.controller.abort(); }
}
