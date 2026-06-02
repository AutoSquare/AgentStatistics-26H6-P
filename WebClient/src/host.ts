import type { HostMessage } from "./types";

export function postToHost(message: HostMessage) {
  window.chrome?.webview?.postMessage(message);
}

export function onHostMessage(listener: (message: HostMessage) => void) {
  window.chrome?.webview?.addEventListener("message", (event) => {
    if (event.data && typeof event.data === "object") {
      listener(event.data as HostMessage);
    }
  });
}
