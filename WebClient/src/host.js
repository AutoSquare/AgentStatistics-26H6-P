export function postToHost(message) {
    window.chrome?.webview?.postMessage(message);
}
export function onHostMessage(listener) {
    window.chrome?.webview?.addEventListener("message", (event) => {
        if (event.data && typeof event.data === "object") {
            listener(event.data);
        }
    });
}
//# sourceMappingURL=host.js.map