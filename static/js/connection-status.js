(() => {
  let status = "unknown";
  let timer = null;

  function update(next) {
    status = next;

    document.querySelectorAll("[data-gotya-connection-banner]").forEach(el => {
      el.dataset.status = next;
      el.hidden = next === "online";
      el.textContent = next === "connecting"
        ? "● Connecting..."
        : next === "offline" ? "● No Internet Connection" : "";
    });

    window.dispatchEvent(new CustomEvent("gotya:connectionchange", {
      detail: { status }
    }));
  }

  window.GotyaConnection = {
    getStatus: () => status,
    onChange: callback => window.addEventListener(
      "gotya:connectionchange",
      event => callback(event.detail.status)
    )
  };

  async function check() {
    if (!navigator.onLine) {
      update("offline");
      return;
    }

    const controller = new AbortController();
    timer = setTimeout(() => update("connecting"), 1200);
    const timeout = setTimeout(() => controller.abort(), 7000);
    const started = performance.now();

    try {
      const response = await fetch("/sw.js?connection_check=" + Date.now(), {
        cache: "no-store",
        credentials: "same-origin",
        signal: controller.signal
      });
      update(response.ok && performance.now() - started <= 1200
        ? "online" : response.ok ? "connecting" : "offline");
    } catch (error) {
      update(error.name === "AbortError" && navigator.onLine
        ? "connecting" : "offline");
    } finally {
      clearTimeout(timer);
      clearTimeout(timeout);
    }
  }

  window.addEventListener("online", check);
  window.addEventListener("offline", () => update("offline"));

  check();
  setInterval(check, 15000);
})();
