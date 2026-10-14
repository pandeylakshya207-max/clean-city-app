// Clean City shared helpers: icons, API calls, small UI pieces.
(function () {
  const ICONS = {
    home: '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V20h14V9.5"/><path d="M10 20v-5h4v5"/>',
    camera: '<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
    list: '<path d="M8 6h12M8 12h12M8 18h12"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/>',
    map: '<path d="M9 4 3 6.5v13.5L9 17.5l6 2.5 6-2.5V4l-6 2.5z"/><path d="M9 4v13.5M15 6.5V20"/>',
    logout: '<path d="M15 4h4v16h-4"/><path d="M10 8l-4 4 4 4"/><path d="M6 12h10"/>',
    arrow: '<path d="M5 12h14"/><path d="m13 6 6 6-6 6"/>',
    back: '<path d="M19 12H5"/><path d="m11 6-6 6 6 6"/>',
    close: '<path d="M6 6l12 12M18 6 6 18"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    pin: '<path d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>',
    mail: '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="m4 7 8 6 8-6"/>',
    refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 5v6h-6"/>',
    upload: '<path d="M12 16V4"/><path d="m7 9 5-5 5 5"/><path d="M4 16v4h16v-4"/>',
    flame: '<path d="M12 21c4 0 7-2.8 7-6.8 0-3.6-2.6-5.6-3.7-8.7-.4 2-1.5 3.3-2.8 3.9C12.7 6.6 11 4.6 9 3c.3 3-3 5.5-3 10.2C6 18.2 8.5 21 12 21z"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    alert: '<path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17v.5"/>',
    team: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c1-3.6 3.5-5.5 6.5-5.5s5.5 1.9 6.5 5.5"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 14.6c1.8.8 3 2.6 3.5 5.4"/>',
    trash: '<path d="M4 7h16"/><path d="M9 7V4h6v3"/><path d="M6 7l1 13h10l1-13"/>',
    spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M5.6 18.4l2.8-2.8M15.6 8.4l2.8-2.8"/>',
  };
  const CC = {};
  CC.icon = (name, size) => {
    const s = size || 24;
    return '<svg viewBox="0 0 24 24" width="' + s + '" height="' + s + '" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (ICONS[name] || "") + "</svg>";
  };
  CC.mark = () => '<svg class="mark" viewBox="0 0 36 36" aria-hidden="true"><rect width="36" height="36" rx="11" fill="#17211b"/><path d="M9 15V12a3 3 0 0 1 3-3h3M21 9h3a3 3 0 0 1 3 3v3M27 21v3a3 3 0 0 1-3 3h-3M15 27h-3a3 3 0 0 1-3-3v-3" stroke="#c8f169" stroke-width="2.6" fill="none" stroke-linecap="round"/><path d="M14 16.5h8l-.8 6.5h-6.4z" fill="#c8f169"/><path d="M13.2 15h9.6M16.5 13.6h3" stroke="#c8f169" stroke-width="1.6" stroke-linecap="round"/></svg>';
  CC.$ = (id) => document.getElementById(id);
  CC.el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null && text !== "") node.textContent = text;
    return node;
  };
  CC.token = () => { try { return localStorage.getItem("cc_token"); } catch (e) { return null; } };
  CC.logout = () => {
    try { localStorage.removeItem("cc_token"); } catch (e) {}
    window.location.href = "/";
  };
  CC.api = async (path, options) => {
    const opts = options || {};
    opts.headers = Object.assign({}, opts.headers, { Authorization: "Bearer " + CC.token() });
    const res = await fetch(path, opts);
    if (res.status === 401) {
      CC.logout();
      throw new Error("Please log in again.");
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const error = new Error(typeof data.detail === "string" ? data.detail : "Something went wrong. Please try again.");
      error.status = res.status;
      throw error;
    }
    return data;
  };
  CC.formatDate = (iso) => new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
  CC.shortDate = (iso) => new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
  CC.plural = (n, one, many) => n + " " + (n === 1 ? one : many);
  CC.greeting = () => {
    const h = new Date().getHours();
    return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
  };
  CC.initial = (contact) => {
    const c = (contact || "?").replace(/^\+91/, "");
    return c.charAt(0).toUpperCase();
  };
  CC.toast = (text, isError) => {
    const old = document.querySelector(".toast");
    if (old) old.remove();
    const t = CC.el("div", "toast" + (isError ? " err" : ""));
    t.setAttribute("role", "status");
    const tick = CC.el("span", "tick");
    tick.innerHTML = CC.icon(isError ? "alert" : "check");
    t.appendChild(tick);
    t.appendChild(CC.el("span", "", text));
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 4200);
  };
  CC.STATUS = { pending: "Received", assigned: "Team assigned", cleaned: "Cleaned", rejected: "Rejected" };
  CC.badge = (status) => CC.el("span", "badge " + status, CC.STATUS[status] || status);
  CC.tracker = (status) => {
    const box = CC.el("div", "track");
    const order = ["pending", "assigned", "cleaned"];
    const reached = status === "cleaned" ? 2 : status === "assigned" ? 1 : 0;
    order.forEach((s, i) => {
      if (i > 0) box.appendChild(CC.el("span", "bar" + (i <= reached ? " done" : "")));
      const step = CC.el("span", "step" + (i <= reached ? " done" : ""));
      const dot = CC.el("span", "dot");
      if (i <= reached) dot.innerHTML = CC.icon("check");
      step.appendChild(dot);
      step.appendChild(document.createTextNode(CC.STATUS[s]));
      box.appendChild(step);
    });
    return box;
  };
  CC.fillIcons = (root) => {
    (root || document).querySelectorAll("[data-icon]").forEach((n) => {
      n.insertAdjacentHTML("afterbegin", CC.icon(n.getAttribute("data-icon")));
      n.removeAttribute("data-icon");
    });
    (root || document).querySelectorAll("[data-mark]").forEach((n) => { n.innerHTML = CC.mark(); });
  };
  window.CC = CC;
})();
