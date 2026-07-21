import '@testing-library/jest-dom/vitest';

// Node's global fetch/Request (undici) reject jsdom's AbortSignal instances with
// "Expected signal to be an instance of AbortSignal". React-router's data router passes one when
// it builds a navigation Request, which throws under jsdom. In the browser every primitive comes
// from the same realm, so this only bites in tests — strip the signal at construction here (no
// navigation needs to be aborted in a unit test).
const OriginalRequest = globalThis.Request;
class SignalTolerantRequest extends OriginalRequest {
  constructor(input: RequestInfo | URL, init?: RequestInit) {
    if (init && 'signal' in init) {
      const rest = { ...init };
      delete rest.signal;
      super(input, rest);
    } else {
      super(input, init);
    }
  }
}
globalThis.Request = SignalTolerantRequest as unknown as typeof Request;

// jsdom implements no layout engine, so the browser APIs React Flow measures with are missing
// entirely. Without these the topology graph throws on mount instead of rendering its nodes.
if (!('ResizeObserver' in globalThis)) {
  globalThis.ResizeObserver = class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  } as unknown as typeof ResizeObserver;
}
if (!('DOMMatrixReadOnly' in globalThis)) {
  globalThis.DOMMatrixReadOnly = class {
    m22 = 1;
  } as unknown as typeof DOMMatrixReadOnly;
}
