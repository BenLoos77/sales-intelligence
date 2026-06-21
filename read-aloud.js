/* Sales Intelligence — Vorlesen.
   Bevorzugt eine vorgefertigte MP3 (hochwertige neuronale Stimme, Element
   #ra-audio). Ist keine vorhanden, fällt es auf die Browser-Sprachausgabe
   (Web Speech API) zurück. Eine Datei: Style + Bedienleiste + Logik. */
(function () {
  "use strict";

  function init() {
    var body = document.querySelector(".article-body");
    if (!body) return;
    // Direkt unter die Schlagzeile — dort wird der Button am ehesten gefunden.
    var anchor = document.querySelector(".article-title") ||
                 document.querySelector(".article-standfirst") ||
                 document.querySelector(".article-byline");
    if (!anchor) return;

    var audioEl = document.getElementById("ra-audio");
    var hasSpeech = "speechSynthesis" in window;
    if (!audioEl && !hasSpeech) return;

    injectStyle();
    var bar = buildBar(anchor);

    if (audioEl) audioEngine(bar, audioEl);
    else speechEngine(bar, body);
  }

  // ---- UI ---------------------------------------------------------------
  var ICON_PLAY = "M8 5v14l11-7z";
  var ICON_PAUSE = "M6 5h4v14H6zM14 5h4v14h-4z";

  function injectStyle() {
    if (document.getElementById("ra-style")) return;
    var css = document.createElement("style");
    css.id = "ra-style";
    css.textContent =
      ".ra-bar{display:flex;align-items:center;gap:14px;margin:20px 0 26px;flex-wrap:wrap}" +
      ".ra-btn{display:inline-flex;align-items:center;gap:10px;cursor:pointer;" +
      "font-family:'Space Mono', monospace;font-size:12px;letter-spacing:.12em;" +
      "text-transform:uppercase;font-weight:600;color:var(--ink,#0a0a0a);background:transparent;" +
      "border:1.5px solid var(--ink,#0a0a0a);border-radius:999px;padding:11px 20px;transition:all .25s}" +
      ".ra-btn:hover{background:var(--accent,#00B4E6);border-color:var(--accent,#00B4E6);color:#050608}" +
      ".ra-btn svg{width:16px;height:16px;flex:none}" +
      ".ra-btn[hidden]{display:none}" +
      ".ra-stop{border-color:rgba(255,255,255,.25);color:var(--muted,#8e98a0);padding:11px 14px}" +
      ".ra-stop:hover{background:transparent;color:var(--ink,#0a0a0a);border-color:var(--ink,#0a0a0a)}" +
      ".ra-progress{font-family:'Space Mono', monospace;font-size:11px;" +
      "letter-spacing:.08em;color:var(--muted,#888);font-variant-numeric:tabular-nums}" +
      ".ra-reading{background:rgba(0,180,230,.16);box-shadow:-10px 0 0 rgba(0,180,230,.16),10px 0 0 rgba(0,180,230,.16);" +
      "border-radius:2px;transition:background .3s}";
    document.head.appendChild(css);
  }

  function buildBar(anchor) {
    var bar = document.createElement("div");
    bar.className = "ra-bar";
    bar.setAttribute("role", "group");
    bar.setAttribute("aria-label", "Artikel vorlesen");
    bar.innerHTML =
      '<button type="button" class="ra-btn ra-toggle" aria-label="Vorlesen starten">' +
        '<svg class="ra-ic" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="' + ICON_PLAY + '"/></svg>' +
        '<span class="ra-label">Vorlesen</span>' +
      "</button>" +
      '<button type="button" class="ra-btn ra-stop" aria-label="Vorlesen stoppen" hidden>Stopp</button>' +
      '<span class="ra-progress" aria-live="polite"></span>';
    anchor.parentNode.insertBefore(bar, anchor.nextSibling);
    var b = {
      bar: bar,
      toggle: bar.querySelector(".ra-toggle"),
      stop: bar.querySelector(".ra-stop"),
      icon: bar.querySelector(".ra-ic path"),
      label: bar.querySelector(".ra-label"),
      progress: bar.querySelector(".ra-progress"),
    };
    b.idle = function () {
      b.icon.setAttribute("d", ICON_PLAY); b.label.textContent = "Vorlesen";
      b.toggle.setAttribute("aria-label", "Vorlesen starten");
      b.stop.hidden = true; b.progress.textContent = "";
    };
    b.playing = function () {
      b.icon.setAttribute("d", ICON_PAUSE); b.label.textContent = "Pause";
      b.toggle.setAttribute("aria-label", "Vorlesen pausieren"); b.stop.hidden = false;
    };
    b.paused = function () {
      b.icon.setAttribute("d", ICON_PLAY); b.label.textContent = "Weiter";
      b.toggle.setAttribute("aria-label", "Vorlesen fortsetzen");
    };
    return b;
  }

  // ---- Engine A: vorgefertigte MP3 -------------------------------------
  function audioEngine(b, audio) {
    var state = "idle"; // idle | playing | paused
    function fmt(s) {
      if (!isFinite(s)) return "0:00";
      var m = Math.floor(s / 60), x = Math.floor(s % 60);
      return m + ":" + (x < 10 ? "0" : "") + x;
    }
    audio.addEventListener("timeupdate", function () {
      if (state !== "idle") b.progress.textContent = fmt(audio.currentTime) + " / " + fmt(audio.duration);
    });
    audio.addEventListener("ended", function () { state = "idle"; b.idle(); });
    b.toggle.addEventListener("click", function () {
      if (state === "idle" || state === "paused") {
        audio.play().then(function () { state = "playing"; b.playing(); })
                    .catch(function () { /* Autoplay-Block o.ä. */ });
      } else { audio.pause(); state = "paused"; b.paused(); }
    });
    b.stop.addEventListener("click", function () {
      audio.pause(); audio.currentTime = 0; state = "idle"; b.idle();
    });
    window.addEventListener("beforeunload", function () { audio.pause(); });
  }

  // ---- Engine B: Browser-Sprachausgabe (Fallback) ----------------------
  function speechEngine(b, body) {
    function blockText(el) {
      if (el.classList && el.classList.contains("pullquote")) {
        var c = el.cloneNode(true); var s = c.querySelector(".source"); if (s) s.remove();
        return { text: c.textContent.replace(/\s+/g, " ").trim(), el: el };
      }
      return { text: el.textContent.replace(/\s+/g, " ").trim(), el: el };
    }
    var chunks = [];
    var title = document.querySelector(".article-title");
    var standfirst = document.querySelector(".article-standfirst");
    if (title) chunks.push(blockText(title));
    if (standfirst) chunks.push(blockText(standfirst));
    Array.prototype.forEach.call(body.children, function (el) {
      if (el.classList && el.classList.contains("byline-end")) return;
      var t = el.tagName;
      var ok = t === "P" || t === "H2" || t === "H3" || t === "H4" ||
               (el.classList && el.classList.contains("pullquote"));
      if (!ok) return;
      var bk = blockText(el); if (bk.text) chunks.push(bk);
    });
    if (!chunks.length) { b.bar.remove(); return; }

    var voice = null;
    function pickVoice() {
      var vs = window.speechSynthesis.getVoices() || [];
      voice = vs.filter(function (v) { return /^de(-|_|$)/i.test(v.lang); })
                .sort(function (a, c) { return (c.localService ? 1 : 0) - (a.localService ? 1 : 0); })[0] || null;
    }
    pickVoice(); window.speechSynthesis.onvoiceschanged = pickVoice;

    var state = "idle", stopped = false;
    function hl(el, on) { if (el && el.classList) el.classList.toggle("ra-reading", !!on); }
    function speakFrom(i) {
      if (stopped) return;
      if (i >= chunks.length) { finish(); return; }
      var u = new SpeechSynthesisUtterance(chunks[i].text);
      u.lang = "de-DE"; u.rate = 1.0; if (voice) u.voice = voice;
      b.progress.textContent = "Absatz " + (i + 1) + " / " + chunks.length;
      u.onstart = function () { hl(chunks[i].el, true); };
      u.onend = function () { hl(chunks[i].el, false); if (!stopped) speakFrom(i + 1); };
      u.onerror = function () { hl(chunks[i].el, false); };
      window.speechSynthesis.speak(u);
    }
    function finish() { stopped = true; window.speechSynthesis.cancel(); state = "idle"; b.idle(); }
    b.toggle.addEventListener("click", function () {
      if (state === "idle") { stopped = false; window.speechSynthesis.cancel(); state = "playing"; b.playing(); speakFrom(0); }
      else if (state === "playing") { window.speechSynthesis.pause(); state = "paused"; b.paused(); }
      else if (state === "paused") { window.speechSynthesis.resume(); state = "playing"; b.playing(); }
    });
    b.stop.addEventListener("click", finish);
    setInterval(function () {
      if (state === "playing" && window.speechSynthesis.speaking && !window.speechSynthesis.paused) {
        window.speechSynthesis.resume();
      }
    }, 10000);
    window.addEventListener("beforeunload", function () { window.speechSynthesis.cancel(); });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
