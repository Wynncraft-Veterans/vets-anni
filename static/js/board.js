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
  var backoff = 1000;          // reconnect backoff, capped below
  var refreshTimer = null;
  var pingTimer = null;

  function setState(text, cls) {
    var el = document.getElementById("ws-state");
    if (!el) return;
    el.textContent = text;
    el.className = "pill " + (cls || "pill-muted");
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
      backoff = 1000;
      setState("live", "pill-ok");
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
      if (
        msg.type === "WELCOME" ||
        msg.type === "PATCH" ||
        msg.type === "BOARD_WIPE"
      ) {
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
      setState("reconnecting… (forms still work)", "pill-muted");
      setTimeout(connect, backoff);
      backoff = Math.min(backoff * 2, 15000);
    };
    ws.onerror = function () {
      try { ws.close(); } catch (e) {}
    };
  }

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
          onEnd: onDrop,
        })
      );
    });
  }

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

  document.addEventListener("DOMContentLoaded", function () {
    initBoard();
    connect();
  });
})();
