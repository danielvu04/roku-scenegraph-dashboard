/* roku-couch phone remote. Plain JS, no build step, no dependencies. */
(function () {
  "use strict";

  var REPEAT_DELAY_MS = 400;
  var REPEAT_INTERVAL_MS = 150;
  var STATUS_POLL_MS = 10000;
  var TOAST_MS = 6000;

  var $ = function (id) { return document.getElementById(id); };

  // ---------------------------------------------------------------- helpers

  function buzz() {
    if (navigator.vibrate) {
      try { navigator.vibrate(10); } catch (e) { /* ignore */ }
    }
  }

  function ApiError(message, hint, status) {
    this.message = message;
    this.hint = hint || "";
    this.status = status || 0;
  }

  function api(method, path, body, timeoutMs) {
    var ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
    var timer = ctrl ? setTimeout(function () { ctrl.abort(); }, timeoutMs || 8000) : null;
    var opts = { method: method, headers: {}, cache: "no-store" };
    if (ctrl) opts.signal = ctrl.signal;
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    return fetch(path, opts).then(function (resp) {
      return resp.text().then(function (text) {
        var data = null;
        try { data = text ? JSON.parse(text) : {}; } catch (e) { data = null; }
        if (!resp.ok) {
          throw new ApiError(
            (data && data.error) || ("Request failed (HTTP " + resp.status + ")"),
            data && data.hint,
            resp.status
          );
        }
        return data || {};
      });
    }).catch(function (err) {
      // Covers both the fetch itself and the body download, so an abort
      // during either one gets the friendly message.
      if (err instanceof ApiError) throw err;
      if (err && err.name === "AbortError") {
        throw new ApiError("The bridge took too long to answer.", "Is the bridge computer awake?");
      }
      throw new ApiError("Can't reach the bridge.",
        "Check that couch_bridge.py is running and your phone is on the same Wi-Fi.");
    }).finally(function () {
      if (timer) clearTimeout(timer);
    });
  }

  // ---------------------------------------------------------------- toast

  var toastTimer = null;
  var lastToast = { text: "", at: 0 };

  function toast(title, hint, kind) {
    var key = title + "|" + (hint || "");
    var now = Date.now();
    if (key === lastToast.text && now - lastToast.at < 1500) return; // de-dupe bursts
    lastToast = { text: key, at: now };
    var el = $("toast");
    $("toast-title").textContent = title;
    $("toast-hint").textContent = hint || "";
    el.className = "toast" + (kind === "info" ? " toast--info" : "");
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(hideToast, kind === "info" ? 2000 : TOAST_MS);
  }

  function hideToast() {
    $("toast").hidden = true;
  }

  function showError(err) {
    if (err instanceof ApiError) {
      toast(err.message, err.hint);
    } else {
      toast("Something went wrong.", String(err));
    }
  }

  $("toast").addEventListener("click", hideToast);

  // ---------------------------------------------------------------- keys

  var inflight = {};

  function sendKey(key, isRepeat) {
    inflight[key] = inflight[key] || 0;
    if (isRepeat && inflight[key] > 2) return Promise.resolve(); // don't pile up
    inflight[key] += 1;
    return api("POST", "/api/key/" + encodeURIComponent(key))
      .catch(function (err) {
        stopRepeat();
        showError(err);
        refreshStatus();
      })
      .finally(function () { inflight[key] -= 1; });
  }

  var repeat = { delay: null, interval: null, el: null };

  function stopRepeat() {
    clearTimeout(repeat.delay);
    clearInterval(repeat.interval);
    if (repeat.el) repeat.el.classList.remove("is-pressed");
    repeat = { delay: null, interval: null, el: null };
  }

  function bindRepeatButton(btn) {
    var key = btn.getAttribute("data-key");
    var pointerUsed = false;
    btn.style.touchAction = "none"; // no scroll/zoom gestures from the d-pad

    btn.addEventListener("pointerdown", function (ev) {
      if (ev.button !== undefined && ev.button !== 0) return;
      ev.preventDefault();
      pointerUsed = true;
      stopRepeat();
      repeat.el = btn;
      btn.classList.add("is-pressed");
      buzz();
      sendKey(key, false);
      repeat.delay = setTimeout(function () {
        repeat.interval = setInterval(function () {
          sendKey(key, true);
        }, REPEAT_INTERVAL_MS);
      }, REPEAT_DELAY_MS);
    });
    ["pointerup", "pointercancel", "pointerleave", "lostpointercapture"].forEach(function (type) {
      btn.addEventListener(type, function () {
        if (repeat.el === btn) stopRepeat();
      });
    });
    // A keyboard press always starts with keydown, so clearing the flag there
    // stops a cancelled touch from swallowing the next keyboard activation.
    btn.addEventListener("keydown", function () { pointerUsed = false; });
    // Keyboard / assistive activation still works (no pointer involved).
    btn.addEventListener("click", function () {
      if (pointerUsed) { pointerUsed = false; return; }
      buzz();
      sendKey(key, false);
    });
    btn.addEventListener("contextmenu", function (ev) { ev.preventDefault(); });
  }

  function bindTapButton(btn) {
    var key = btn.getAttribute("data-key");
    btn.addEventListener("click", function () {
      buzz();
      sendKey(key, false);
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll("[data-key]"), function (btn) {
    if (btn.hasAttribute("data-repeat")) bindRepeatButton(btn);
    else bindTapButton(btn);
  });

  window.addEventListener("blur", stopRepeat);
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) stopRepeat();
    else refreshStatus();
  });

  // ---------------------------------------------------------------- text + search

  $("text-form").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var input = $("text-input");
    var text = input.value;
    if (!text) return;
    buzz();
    input.value = "";
    // Roku types ~1 char per request; allow plenty of time for long strings.
    api("POST", "/api/text", { text: text }, 8000 + text.length * 400)
      .catch(function (err) {
        input.value = text;
        showError(err);
      });
  });

  var searchType = "";
  Array.prototype.forEach.call(document.querySelectorAll(".type-chips .chip"), function (chip) {
    chip.addEventListener("click", function () {
      searchType = chip.getAttribute("data-type") || "";
      Array.prototype.forEach.call(document.querySelectorAll(".type-chips .chip"), function (c) {
        c.classList.toggle("is-on", c === chip);
      });
      buzz();
    });
  });

  $("search-form").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var input = $("search-input");
    var keyword = input.value.trim();
    if (!keyword) return;
    buzz();
    var body = { keyword: keyword, launch: true };
    if (searchType) body.type = searchType;
    api("POST", "/api/search", body)
      .then(function () {
        toast("Searching for “" + keyword + "”", "", "info");
        input.blur();
      })
      .catch(showError);
  });

  $("dashboard").addEventListener("click", function () {
    buzz();
    api("POST", "/api/dashboard")
      .then(function () { toast("Opening dashboard", "", "info"); })
      .catch(showError);
  });

  // ---------------------------------------------------------------- status

  var statusInfo = null;

  function setStatus(cls, text, title) {
    var pill = $("status");
    pill.className = "pill " + cls;
    $("status-text").textContent = text;
    pill.title = title || "Tap to refresh";
  }

  function refreshNowPlaying() {
    api("GET", "/api/active-app").then(function (data) {
      var el = $("now");
      var name = data && data.app && data.app.name;
      el.textContent = name ? "Now playing: " + name : "On the Roku home screen";
      el.hidden = false;
    }).catch(function () { $("now").hidden = true; });
  }

  function refreshStatus() {
    refreshNowPlaying();
    return api("GET", "/api/status").then(function (data) {
      var volumeRow = document.getElementById("volume-row");
      if (volumeRow) volumeRow.hidden = data.show_volume_buttons === false;
      statusInfo = data;
      if (data.reachable && data.device) {
        var dev = data.device;
        var standby = dev.power_mode && dev.power_mode !== "PowerOn";
        setStatus(standby ? "pill--standby" : "pill--ok",
          dev.name + (standby ? " · standby" : ""),
          [dev.model, data.roku_ip].filter(Boolean).join(" · "));
      } else {
        setStatus("pill--bad", "Roku unreachable", data.hint || data.error);
      }
    }, function (err) {
      statusInfo = { reachable: false, error: err.message, hint: err.hint };
      setStatus("pill--bad", "Bridge offline", err.hint);
    });
  }

  $("status").addEventListener("click", function () {
    if (statusInfo && !statusInfo.reachable) {
      toast(statusInfo.error || "Roku unreachable", statusInfo.hint);
    }
    refreshStatus();
  });

  // ---------------------------------------------------------------- apps

  // Pinned apps first, then popular ones; everything else alphabetically.
  var PINNED = ["netflix", "youtube", "cartoon network"];
  var POPULAR = ["prime video", "disney+", "hulu", "hbo max", "max", "peacock",
                 "paramount+", "tubi", "pluto tv", "plex", "apple tv", "spotify"];

  function appRank(name) {
    var n = (name || "").toLowerCase();
    var i, j;
    for (i = 0; i < PINNED.length; i++) if (n.indexOf(PINNED[i]) !== -1) return i;
    for (j = 0; j < POPULAR.length; j++) if (n.indexOf(POPULAR[j]) !== -1) return PINNED.length + j;
    return PINNED.length + POPULAR.length;
  }

  function sortApps(apps) {
    return apps.slice().sort(function (a, b) {
      var d = appRank(a.name) - appRank(b.name);
      return d !== 0 ? d : (a.name || "").localeCompare(b.name || "");
    });
  }

  function renderApps(apps) {
    var grid = $("apps");
    grid.textContent = "";
    if (!apps.length) {
      var empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = "No apps reported by the Roku.";
      grid.appendChild(empty);
      return;
    }
    sortApps(apps).forEach(function (app) {
      var tile = document.createElement("button");
      tile.type = "button";
      tile.className = "app-tile" + (app.favorite ? " is-fav" : "");
      tile.setAttribute("role", "listitem");
      tile.setAttribute("aria-label", "Open " + app.name);
      tile.title = app.name;

      var img = document.createElement("img");
      img.loading = "lazy";
      img.decoding = "async";
      img.alt = "";
      img.src = "/api/apps/" + encodeURIComponent(app.id) + "/icon";
      img.addEventListener("error", function () {
        var fb = document.createElement("div");
        fb.className = "app-fallback";
        fb.textContent = (app.name || "?").charAt(0).toUpperCase();
        if (img.parentNode) img.parentNode.replaceChild(fb, img);
      });

      var name = document.createElement("span");
      name.className = "name";
      name.textContent = app.name;

      tile.appendChild(img);
      tile.appendChild(name);
      tile.addEventListener("click", function () {
        buzz();
        api("POST", "/api/launch/" + encodeURIComponent(app.id))
          .then(function () { toast("Opening " + app.name, "", "info"); })
          .catch(showError);
      });
      grid.appendChild(tile);
    });
  }

  function loadApps() {
    var grid = $("apps");
    return api("GET", "/api/apps").then(function (data) {
      renderApps(data.apps || []);
    }, function (err) {
      grid.textContent = "";
      var p = document.createElement("p");
      p.className = "muted";
      p.textContent = "Couldn't load apps: " + err.message + (err.hint ? " " + err.hint : "");
      grid.appendChild(p);
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (tab) {
    tab.addEventListener("click", function () {
      var name = tab.getAttribute("data-tab");
      Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (t) {
        var on = t === tab;
        t.classList.toggle("is-active", on);
        t.setAttribute("aria-selected", on ? "true" : "false");
      });
      Array.prototype.forEach.call(document.querySelectorAll(".pane"), function (p) {
        p.classList.toggle("is-active", p.getAttribute("data-pane") === name);
      });
      buzz();
    });
  });

  $("apps-refresh").addEventListener("click", function () {
    buzz();
    loadApps();
  });

  // ---------------------------------------------------------------- boot

  refreshStatus().then(function () {
    loadApps();
  });
  setInterval(function () {
    if (!document.hidden) refreshStatus();
  }, STATUS_POLL_MS);
})();
