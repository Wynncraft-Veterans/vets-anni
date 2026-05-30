/* Organizer board client.
 *
 * Deliberately thin: the server renders the board (one board_view.snapshot),
 * so this never templates HTML. It does two things:
 *   1. holds a WebSocket to board_hub and, on ANY authoritative signal
 *      (WELCOME / PATCH / BOARD_WIPE), re-fetches the #board fragment via
 *      HTMX — so every tab converges on the server's truth;
 *   2. turns drag-drop into a MOVE intent (WS if connected, else the REST
 *      twin) — the single-instance UPSERT + ordering all live server-side.
 *
 * Everything still works with the socket down: the in-board forms post to the
 * /staff/board/* REST twins (graceful degradation, per .claude/ws_protocol.md).
 */
(function () {
  "use strict";

  var WS_URL =
    (location.protocol === "https:" ? "wss" : "ws") +
    "://" + location.host + "/staff/board/ws";
  var FRAGMENT_URL = "/staff/board/fragment";

  var ws = null;
  /* Initial reconnect backoff. Kept short so a transient blip (or a tab
     waking from background-throttled WebSocket suspension) reconnects
     within a single user-visible frame; doubles up to a 15s cap below. */
  var INITIAL_BACKOFF = 250;
  var backoff = INITIAL_BACKOFF;
  var refreshTimer = null;
  var pingTimer = null;
  var safetyTimer = null;

  /* One pill — #ws-state — carries both the WS connection state and the
   * auto-promoter monitoring phase. Connection problems always win
   * (reconnecting / offline override the monitoring label) because if we
   * can't trust the socket, the monitoring info is stale anyway. When
   * connected, the pill renders the server's snapshot.event.monitoring_label
   * with a colour matching the phase (idle = muted, early/late = ok). */
  var lastMonitoring = { label: null, state: null };
  var wsOpen = false;

  function setState(text, cls) {
    var el = document.getElementById("ws-state");
    if (!el) return;
    el.textContent = text;
    el.className = "pill " + (cls || "pill-muted");
  }

  function classForMonitoring(state) {
    /* idle = not yet in the hot window; muted (informational, no signal
     * for staff). early / late = actively monitoring; ok-green so the pill
     * matches the legacy "live" colour the screen used to show. */
    return state === "idle" ? "pill-muted" : "pill-ok";
  }

  function renderConnectedPill() {
    /* Only call this when ws is OPEN. Renders the last-known monitoring
     * state; falls back to a generic "live" when no snapshot has arrived
     * yet. */
    if (!wsOpen) return;
    if (lastMonitoring.label) {
      setState(lastMonitoring.label, classForMonitoring(lastMonitoring.state));
    } else {
      setState("live", "pill-ok");
    }
  }

  function applyMonitoring(snapshot) {
    /* Snapshot frames update the cached monitoring state. If the socket is
     * still open, re-render the pill; if not, the next ws.onopen will
     * pick up the cached label. */
    if (!snapshot || !snapshot.event) return;
    if (snapshot.event.monitoring_label) {
      lastMonitoring.label = snapshot.event.monitoring_label;
    }
    if (snapshot.event.monitoring) {
      lastMonitoring.state = snapshot.event.monitoring;
    }
    renderConnectedPill();
  }

  /* Coalesce bursts of signals (a multi-op change, or our own POST echoing
   * back as a broadcast) into one fragment fetch. */
  function scheduleRefresh() {
    if (refreshTimer) return;
    refreshTimer = setTimeout(function () {
      refreshTimer = null;
      if (window.htmx) {
        htmx.ajax("GET", FRAGMENT_URL, {
          target: "#board",
          swap: "outerHTML",
        });
      }
    }, 150);
  }

  function send(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(obj));
      return true;
    }
    return false;
  }

  function connect() {
    try {
      ws = new WebSocket(WS_URL);
    } catch (e) {
      setState("offline (forms still work)", "pill-danger");
      return;
    }
    ws.onopen = function () {
      backoff = INITIAL_BACKOFF;
      wsOpen = true;
      renderConnectedPill();  // shows monitoring label, or "live" if no snap yet
      send({ v: 1, type: "HELLO" });
      clearInterval(pingTimer);
      pingTimer = setInterval(function () {
        send({ v: 1, type: "PING" });
      }, 25000);
    };
    ws.onmessage = function (ev) {
      var msg;
      try {
        msg = JSON.parse(ev.data);
      } catch (e) {
        return;
      }
      // WELCOME / PATCH (snapshot or presence) / BOARD_WIPE all just mean
      // "the server state changed" — re-render from the server.
      if (msg.type === "WELCOME") {
        applyMonitoring(msg.snapshot);
        scheduleRefresh();
      } else if (msg.type === "PATCH") {
        // The post-mutation reconcile carries a full snapshot under
        // ops[0].snapshot; presence-only PATCH frames don't, and that's fine
        // (monitoring stays as last seen, the pill never wipes).
        if (msg.ops && msg.ops.length) {
          for (var i = 0; i < msg.ops.length; i++) {
            if (msg.ops[i] && msg.ops[i].op === "snapshot") {
              applyMonitoring(msg.ops[i].snapshot);
              break;
            }
          }
        }
        scheduleRefresh();
      } else if (msg.type === "BOARD_WIPE") {
        scheduleRefresh();
      } else if (msg.type === "REJECTED") {
        // The optimistic DOM move is undone by the reconciling snapshot the
        // server also broadcast; surface the friendly reason meanwhile.
        setState("rejected: " + (msg.reason || "see board"), "pill-danger");
        scheduleRefresh();
      }
    };
    ws.onclose = function () {
      clearInterval(pingTimer);
      wsOpen = false;
      setState("reconnecting… (forms still work)", "pill-muted");
      setTimeout(connect, backoff);
      backoff = Math.min(backoff * 2, 15000);
    };
    ws.onerror = function () {
      try { ws.close(); } catch (e) {}
    };
  }

  /* ---- dropdown-move (opt-in alt to drag-drop) -------------------------- */
  /* Wired from inline onchange on .person-move <select>. Value format:
     "party:<id>"  or  "bucket:<name>:<is_late01>"  — parse and POST through
     the same /staff/board/move REST twin the drag-drop fallback uses, so it
     funnels through board_hub (single-instance UPSERT + WS broadcast). */
  window.__moveViaSelect = function (sel) {
    var val = sel.value;
    if (!val) return;
    var uuid = sel.getAttribute("data-uuid");
    if (!uuid) return;
    var values = { player_uuid: uuid, sort_index: 0 };
    if (val.indexOf("party:") === 0) {
      values.party_id = val.slice(6);
    } else if (val.indexOf("bucket:") === 0) {
      var parts = val.slice(7).split(":");
      values.bucket = parts[0];
      values.is_late = parts[1] === "1" ? "true" : "false";
    } else {
      return;
    }
    if (window.htmx) {
      htmx.ajax("POST", "/staff/board/move", {
        target: "#board", swap: "outerHTML", values: values,
      });
    }
  };

  /* ---- drag-drop -> MOVE ------------------------------------------------ */
  function targetOf(zone) {
    if (zone.dataset.partyId) {
      return { party_id: zone.dataset.partyId };
    }
    return {
      bucket: zone.dataset.bucket,
      is_late: zone.dataset.late === "1",
    };
  }

  function onDrop(evt) {
    var item = evt.item;
    var zone = evt.to;
    var uuid = item.getAttribute("data-uuid");
    if (!uuid || !zone) return;
    // sort_index = position among real person cards in the destination.
    var index = 0;
    var kids = zone.querySelectorAll(".person");
    for (var i = 0; i < kids.length; i++) {
      if (kids[i] === item) { index = i; break; }
    }
    var target = targetOf(zone);
    target.sort_index = index;

    if (!send({ v: 1, type: "MOVE", op_id: "m" + Date.now(),
                player_uuid: uuid, target: target })) {
      // Socket down: same mutation via the REST twin (keeps single-instance
      // because it funnels through the same board_hub.handle path).
      if (window.htmx) {
        htmx.ajax("POST", "/staff/board/move", {
          target: "#board",
          swap: "outerHTML",
          values: {
            player_uuid: uuid,
            party_id: target.party_id || "",
            bucket: target.bucket || "",
            is_late: target.is_late ? "true" : "false",
            sort_index: index,
          },
        });
      }
    }
  }

  var sortables = [];
  function initBoard() {
    sortables.forEach(function (s) { try { s.destroy(); } catch (e) {} });
    sortables = [];
    var board = document.getElementById("board");
    if (!board || board.dataset.frozen === "1" || !window.Sortable) return;
    document.querySelectorAll(".dropzone").forEach(function (zone) {
      sortables.push(
        new Sortable(zone, {
          group: "board",
          animation: 120,
          draggable: ".person",
          ghostClass: "person-ghost",
          /* Sortable picks up mouse-downs on any descendant of `.person` and
             treats them as the start of a drag; this filter excludes the
             interactive controls (capability dots + their popovers + the role
             <select>) so a click on a dot is a real click, not a drag-start
             that swallows the event. `preventOnFilter:false` lets the native
             click still fire. */
          filter: ".cap-dot, .cap-dot-wrap, .cap-popover, .person-role, .person-move",
          preventOnFilter: false,
          /* Auto-scroll the document while dragging so a card can be moved
             from a party at the top to one off the bottom of the viewport.
             The board is a single tall scroll layout (no internal scroll
             regions), so bubbleScroll lets sortable walk up to the document
             scroller. */
          scroll: true,
          scrollSensitivity: 80,
          scrollSpeed: 20,
          bubbleScroll: true,
          onEnd: onDrop,
        })
      );
    });
  }

  /* ---- capability-dot click mode ---------------------------------------- */
  /* In click mode (body.dotmode-click), clicking a dot toggles its sibling
     popover's `.open` class; an outside click dismisses any open popover.
     Hover mode is pure CSS — this handler is a no-op there. The handler is
     bound once on document.body so it survives the WS-driven #board
     refreshes (which destroy and recreate every dot). */
  function closeAllPopovers() {
    document.querySelectorAll(".cap-popover.open").forEach(function (el) {
      el.classList.remove("open");
    });
  }

  document.addEventListener("click", function (ev) {
    if (!document.body.classList.contains("dotmode-click")) return;
    var dot = ev.target.closest && ev.target.closest(".cap-dot");
    if (dot) {
      ev.preventDefault();
      var wrap = dot.parentElement;
      var pop = wrap && wrap.querySelector(".cap-popover");
      if (!pop) return;
      var wasOpen = pop.classList.contains("open");
      closeAllPopovers();
      if (!wasOpen) pop.classList.add("open");
      return;
    }
    /* Click landed somewhere that's neither a dot nor the popover itself —
       treat it as "dismiss" so popovers don't linger after the user has
       moved on (the natural escape for a click-to-open UI). */
    if (!(ev.target.closest && ev.target.closest(".cap-popover"))) {
      closeAllPopovers();
    }
  });
  /* Escape always closes (a11y); harmless in hover mode. */
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") closeAllPopovers();
  });

  // Re-init after every #board swap (HTMX replaces the node, so the old
  // Sortable instances are dead) and dismiss the add-player popup once its
  // submit has swapped the board back in (same pattern as the dashboard
  // modals — close on the read fragment landing).
  document.body.addEventListener("htmx:afterSwap", function (e) {
    if (e.detail && e.detail.target && e.detail.target.id === "board") {
      initBoard();
      var m = document.getElementById("board-modal-mount");
      if (m) m.innerHTML = "";
    }
  });

  /* ---- staleness defenses ----------------------------------------------- */
  /* Two backstops against the well-known background-tab WebSocket failure
     mode (browsers throttle/suspend hidden tabs after a while, so a
     mutation broadcast from another staff can be missed entirely):
       1. visibilitychange: when a tab becomes visible again, force a
          fresh fragment fetch immediately and — if the socket dropped
          while we were hidden — reset the backoff and reconnect now.
       2. safety-net poll: every 30s while the tab is visible, re-fetch
          the fragment as a backstop. Coalesces with scheduleRefresh's
          150ms debounce so a live socket pays no extra cost.

     Note: the server-side audit (.claude/ws_protocol.md) confirms every
     mutation funnels through board_hub.handle (or maybe_broadcast_for),
     so these are *purely* about closing the visibility/reconnect window
     — they're not papering over silent server-side writes. */
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState !== "visible") return;
    if (!wsOpen) {
      backoff = INITIAL_BACKOFF;
      try { if (ws) ws.close(); } catch (e) {}
      connect();
    } else {
      scheduleRefresh();
    }
  });

  document.addEventListener("DOMContentLoaded", function () {
    initBoard();
    connect();
    if (safetyTimer) clearInterval(safetyTimer);
    safetyTimer = setInterval(function () {
      if (document.visibilityState === "visible") scheduleRefresh();
    }, 30000);
  });
})();
