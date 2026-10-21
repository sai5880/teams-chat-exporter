"""Compile an export folder into a self-contained Teams-style HTML reader.

Usage:
    python viewer.py <export folder> [--embed] [--open]

The generated `chat.html` works offline, makes no network requests, and keeps
media paths relative to the export folder. Use `--embed` to inline images into
a single portable file.
"""

import argparse
import base64
import json
import mimetypes
import sys
import webbrowser
from pathlib import Path

TEMPLATE = r"""<!doctype html>
<html lang="en" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>__TITLE__</title>
<style>
:root{
  --bg:#f3f3f8; --pane:#ffffff; --bubble:#ffffff; --text:#242424; --muted:#616161;
  --line:#e0e0e6; --brand:#6264a7; --brand-soft:#e8e8f4; --hover:#f0f0f5;
  --shadow:0 1px 2px rgba(0,0,0,.08),0 2px 8px rgba(0,0,0,.06);
  --radius:14px;
}
html[data-theme="dark"]{
  --bg:#1b1b1f; --pane:#252529; --bubble:#2e2e33; --text:#ececf1; --muted:#a0a0aa;
  --line:#3a3a41; --brand:#8b8de0; --brand-soft:#33334a; --hover:#2b2b31;
  --shadow:0 1px 2px rgba(0,0,0,.5),0 2px 10px rgba(0,0,0,.35);
}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--text);
  font-family:"Segoe UI",-apple-system,BlinkMacSystemFont,Roboto,Helvetica,Arial,sans-serif;
  font-size:14px;line-height:1.45;padding-top:61px;}
.appbar{position:fixed;top:0;left:0;right:0;z-index:20;display:flex;align-items:center;gap:12px;
  padding:10px 20px;background:var(--pane);border-bottom:1px solid var(--line);}
.appbar .avatar.lg{width:40px;height:40px;font-size:15px;flex:0 0 auto}
.titles{min-width:0;flex:1}
h1{margin:0;font-size:16px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.meta{font-size:12px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tools{display:flex;align-items:center;gap:8px}
.search{display:flex;align-items:center;gap:6px;background:var(--bg);border:1px solid var(--line);
  border-radius:8px;padding:5px 10px;min-width:210px}
.search svg{opacity:.6;flex:0 0 auto}
.search input{border:0;background:transparent;color:var(--text);font:inherit;outline:none;width:100%}
.iconbtn{display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;
  border:1px solid var(--line);border-radius:8px;background:var(--pane);color:var(--text);
  cursor:pointer;padding:0}
.iconbtn:hover{background:var(--hover)}
main{max-width:920px;margin:0 auto;padding:20px 16px 72px}
.hint{display:flex;align-items:center;justify-content:center;gap:10px;padding:10px 0 18px;color:var(--muted);font-size:12px}
.chip{border:1px solid var(--line);background:var(--pane);color:var(--muted);border-radius:999px;
  padding:4px 10px;font-size:12px;cursor:pointer}
.chip:hover{background:var(--hover)}
.day{display:flex;justify-content:center;margin:22px 0 14px}
.day span{background:var(--brand-soft);color:var(--brand);border-radius:999px;padding:3px 12px;
  font-size:12px;font-weight:600}
.msg{display:flex;gap:10px;margin:0 0 14px;align-items:flex-start}
.msg.grouped{margin-top:-8px}
.avatar{width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;
  color:#fff;font-size:12px;font-weight:600;flex:0 0 auto;background:hsl(var(--h,220) 45% 48%);
  user-select:none}
.avatar.ghost{visibility:hidden}
.body{min-width:0;flex:1;max-width:760px}
.head{display:flex;align-items:baseline;gap:8px;margin:0 0 3px}
.name{font-weight:600;font-size:13px}
.time{font-size:11px;color:var(--muted);opacity:0;transition:opacity .12s}
.msg:hover .time{opacity:1}
.bubble{background:var(--bubble);border:1px solid var(--line);border-radius:var(--radius);
  border-top-left-radius:4px;padding:9px 13px;box-shadow:var(--shadow);display:inline-block;
  max-width:100%;min-width:64px}
.grouped .bubble{border-top-left-radius:var(--radius)}
.text{white-space:pre-wrap;overflow-wrap:anywhere}
.text a{color:var(--brand);text-decoration:none}
.text a:hover{text-decoration:underline}
.images{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
.images img{max-width:100%;max-height:420px;width:auto;height:auto;border-radius:10px;cursor:zoom-in;
  background:var(--hover);display:block}
.images .broken{display:none}
.files{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
.file{display:flex;align-items:center;gap:9px;border:1px solid var(--line);background:var(--pane);
  border-radius:10px;padding:7px 10px;text-decoration:none;color:inherit;max-width:100%}
.file:hover{background:var(--hover)}
.file .ext{width:30px;height:30px;border-radius:7px;background:var(--brand-soft);color:var(--brand);
  display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:700;text-transform:uppercase;flex:0 0 auto}
.file .fname{max-width:230px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px}
.file .fsize{font-size:11px;color:var(--muted)}
.file.missing{opacity:.6;cursor:default}
.reactions{display:flex;flex-wrap:wrap;gap:5px;margin-top:7px}
.reaction{background:var(--brand-soft);color:var(--brand);border-radius:999px;padding:1px 9px;font-size:12px}
.empty{padding:40px;text-align:center;color:var(--muted)}
#jump{position:fixed;right:22px;bottom:22px;width:42px;height:42px;border-radius:50%;border:1px solid var(--line);
  background:var(--pane);color:var(--text);box-shadow:var(--shadow);cursor:pointer;display:none;z-index:15}
#jump.show{display:flex;align-items:center;justify-content:center}
.results{position:fixed;top:61px;left:0;bottom:0;width:340px;max-width:92vw;z-index:19;
  background:var(--pane);border-right:1px solid var(--line);box-shadow:6px 0 18px rgba(0,0,0,.08);
  display:none;flex-direction:column}
.results.open{display:flex}
.results header{display:flex;align-items:center;gap:10px;padding:11px 12px;border-bottom:1px solid var(--line)}
.results h2{margin:0;font-size:13px;font-weight:600;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rlist{overflow-y:auto;flex:1;padding:6px}
.rnote{padding:10px 12px;color:var(--muted);font-size:11px;border-top:1px solid var(--line)}
.result{display:block;width:100%;text-align:left;border:0;background:transparent;color:inherit;
  padding:8px 10px;border-radius:8px;cursor:pointer;font:inherit}
.result:hover,.result:focus-visible{background:var(--hover);outline:none}
.result .rtop{display:flex;gap:8px;align-items:baseline;font-size:11px;color:var(--muted)}
.result .rname{font-weight:600;color:var(--text);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:160px}
.result .rtime{margin-left:auto;white-space:nowrap}
.result .rtext{font-size:12px;color:var(--text);margin-top:2px;display:-webkit-box;-webkit-line-clamp:2;
  -webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}
.result mark{background:#ffe58f;color:#000;border-radius:2px;padding:0 1px}
html[data-theme="dark"] .result mark{background:#7a6a00;color:#fff}
.msg.flash .bubble{outline:2px solid var(--brand);outline-offset:2px}
.msg.flash .bubble{animation:flash 2.4s ease-out}
@keyframes flash{0%{background:var(--brand-soft)}100%{background:var(--bubble)}}
@media (min-width:761px){body.results-open main{margin-left:356px;margin-right:16px}}
@media (max-width:760px){.results{width:100%;top:0;z-index:40}}
main{transition:margin-left .15s ease}
.lightbox{position:fixed;inset:0;z-index:50;background:rgba(0,0,0,.88);display:none;
  align-items:center;justify-content:center;flex-direction:column;gap:12px;padding:24px}
.lightbox.open{display:flex}
.lightbox img{max-width:92vw;max-height:78vh;border-radius:8px;box-shadow:0 10px 40px rgba(0,0,0,.6)}
.lb-caption{color:#e8e8e8;font-size:13px;text-align:center;max-width:80vw}
.lb-caption a{color:#b9bbff}
.lb-nav{display:flex;gap:10px}
.lb-nav button{background:rgba(255,255,255,.12);border:0;color:#fff;border-radius:8px;padding:7px 14px;cursor:pointer;font:inherit}
.lb-nav button:disabled{opacity:.35;cursor:default}
@media (max-width:700px){
  .appbar{padding:8px 12px;flex-wrap:wrap}
  .search{min-width:0;flex:1}
  .meta{white-space:normal}
  main{padding:14px 10px 72px}
  .body{max-width:none}
}
@media print{
  body{background:#fff;color:#000}
  body{padding-top:0}
  .appbar{position:static;box-shadow:none}
  .tools,#jump,.hint,.lightbox{display:none!important}
  main{max-width:none;padding:0}
  .msg{break-inside:avoid}
  .bubble{box-shadow:none}
  .images img{max-height:240px}
}
</style>
</head>
<body>
<header class="appbar">
  <div class="avatar lg" id="chatAvatar"></div>
  <div class="titles">
    <h1 id="chatTitle"></h1>
    <div class="meta" id="chatMeta"></div>
  </div>
  <div class="tools">
    <label class="search" title="Search messages, senders and files">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>
      <input id="search" type="search" placeholder="Search" autocomplete="off" aria-label="Search messages">
    </label>
    <button class="iconbtn" id="themeBtn" title="Toggle dark mode" aria-label="Toggle dark mode"></button>
  </div>
</header>
<main>
  <div class="hint" id="hint"><span class="chip" id="countChip"></span></div>
  <div id="list"></div>
  <div class="empty" id="empty" hidden>No messages match your search.</div>
</main>
<aside class="results" id="results" aria-label="Search results">
  <header>
    <h2 id="resultsTitle">Results</h2>
    <button class="iconbtn" id="resultsClose" title="Close results" aria-label="Close results">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg>
    </button>
  </header>
  <div class="rlist" id="resultList"></div>
</aside>
<button id="jump" title="Back to newest" aria-label="Back to newest">
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 19V5"/><path d="M5 12l7-7 7 7"/></svg>
</button>
<div class="lightbox" id="lightbox" role="dialog" aria-modal="true" aria-label="Image preview">
  <img id="lbImage" alt="">
  <div class="lb-caption" id="lbCaption"></div>
  <div class="lb-nav">
    <button id="lbPrev">Previous</button>
    <button id="lbOpen">Open original</button>
    <button id="lbNext">Next</button>
    <button id="lbClose">Close</button>
  </div>
</div>
<script id="chat-data" type="application/json">__DATA__</script>
<script>
(function(){
  "use strict";
  var DATA = JSON.parse(document.getElementById("chat-data").textContent);
  var messages = DATA.messages || [];
  var list = document.getElementById("list");
  var empty = document.getElementById("empty");
  var hint = document.getElementById("hint");
  var countChip = document.getElementById("countChip");
  var scroller = window;
  var PAGE = 200;
  var state = {limit: PAGE, query: "", shown: 0, results: []};

  function initials(name){
    var parts = (name || "?").trim().split(/\s+/).filter(Boolean);
    if(!parts.length) return "?";
    if(parts.length === 1) return parts[0].slice(0,2).toUpperCase();
    return (parts[0][0] + parts[parts.length-1][0]).toUpperCase();
  }
  function hue(name){
    var h = 0, s = name || "";
    for(var i=0;i<s.length;i++){ h = (h*31 + s.charCodeAt(i)) % 360; }
    return h;
  }
  function avatarEl(name){
    var el = document.createElement("div");
    el.className = "avatar";
    el.style.setProperty("--h", hue(name));
    el.textContent = initials(name);
    el.title = name || "";
    return el;
  }
  function fmtSize(bytes){
    if(!bytes) return "";
    var units = ["B","KB","MB","GB"], i = 0, n = bytes;
    while(n >= 1024 && i < units.length-1){ n /= 1024; i++; }
    return (n >= 10 || i === 0 ? Math.round(n) : n.toFixed(1)) + " " + units[i];
  }
  function fmtDate(iso){
    var d = new Date((iso || "").slice(0,10) + "T00:00:00");
    if(isNaN(d)) return iso || "";
    return d.toLocaleDateString(undefined, {weekday:"long", year:"numeric", month:"long", day:"numeric"});
  }
  function clock(iso){
    return (iso || "").slice(11,16);
  }
  function linkify(container, text){
    var re = /(https?:\/\/[^\s<>"]+)/g, last = 0, m;
    while((m = re.exec(text))){
      if(m.index > last) container.appendChild(document.createTextNode(text.slice(last, m.index)));
      var a = document.createElement("a");
      a.href = m[0]; a.target = "_blank"; a.rel = "noopener noreferrer";
      a.textContent = m[0];
      container.appendChild(a);
      last = m.index + m[0].length;
    }
    if(last < text.length) container.appendChild(document.createTextNode(text.slice(last)));
  }
  var gallery = [];
  function openLightbox(index){
    var item = gallery[index];
    if(!item) return;
    document.getElementById("lbImage").src = item.src;
    document.getElementById("lbCaption").textContent =
      (item.sender ? item.sender + " · " : "") + (item.date || "") + (item.name ? " · " + item.name : "");
    var box = document.getElementById("lightbox");
    box.classList.add("open");
    box.dataset.index = index;
    document.getElementById("lbPrev").disabled = index === 0;
    document.getElementById("lbNext").disabled = index === gallery.length - 1;
  }
  function closeLightbox(){
    document.getElementById("lightbox").classList.remove("open");
    document.getElementById("lbImage").src = "";
  }
  function matches(msg, q){
    if(!q) return true;
    if((msg.s || "").toLowerCase().indexOf(q) !== -1) return true;
    if((msg.x || "").toLowerCase().indexOf(q) !== -1) return true;
    if((msg.a || "").toLowerCase().indexOf(q) !== -1) return true;
    if((msg.m || []).some(function(item){ return (item.n || "").toLowerCase().indexOf(q) !== -1; })) return true;
    return false;
  }
  function searchableText(msg){
    var parts = [msg.x || "", msg.a || ""];
    (msg.m || []).forEach(function(item){ if(item.n) parts.push(item.n); });
    return parts.join("\n");
  }
  function shortDate(iso){
    var d = new Date((iso || "").slice(0,10) + "T00:00:00");
    return isNaN(d) ? (iso || "").slice(0,10) : d.toLocaleDateString(undefined, {year:"numeric", month:"short", day:"numeric"});
  }
  function addHighlight(el, text, q){
    var lower = text.toLowerCase(), pos = 0, idx;
    while((idx = lower.indexOf(q, pos)) !== -1){
      if(idx > pos) el.appendChild(document.createTextNode(text.slice(pos, idx)));
      var mark = document.createElement("mark");
      mark.textContent = text.slice(idx, idx + q.length);
      el.appendChild(mark);
      pos = idx + q.length;
    }
    if(pos < text.length) el.appendChild(document.createTextNode(text.slice(pos)));
  }
  function buildMessage(msg, previous){
    var time = msg.t || msg.u || "";
    var day = time.slice(0,10);
    var prevDay = previous ? ((previous.t || previous.u || "").slice(0,10)) : null;
    var frag = document.createDocumentFragment();
    if(day && day !== prevDay){
      var sep = document.createElement("div");
      sep.className = "day";
      var pill = document.createElement("span");
      pill.textContent = fmtDate(time);
      sep.appendChild(pill);
      frag.appendChild(sep);
    }
    var sameSender = previous && previous.s === msg.s && prevDay === day;
    var row = document.createElement("div");
    row.className = "msg" + (sameSender ? " grouped" : "");
    row.dataset.idx = String(msg.i);
    if(sameSender){
      var ghost = document.createElement("div");
      ghost.className = "avatar ghost";
      row.appendChild(ghost);
    } else {
      row.appendChild(avatarEl(msg.s));
    }
    var body = document.createElement("div");
    body.className = "body";
    if(!sameSender){
      var head = document.createElement("div");
      head.className = "head";
      var name = document.createElement("span");
      name.className = "name";
      name.textContent = msg.s || "(unknown sender)";
      var t = document.createElement("span");
      t.className = "time";
      t.textContent = clock(time);
      head.appendChild(name); head.appendChild(t);
      body.appendChild(head);
    }
    var bubble = document.createElement("div");
    bubble.className = "bubble";
    if(msg.x){
      var text = document.createElement("div");
      text.className = "text";
      linkify(text, msg.x);
      bubble.appendChild(text);
    }
    var media = (msg.m || []).filter(function(item){ return item.st === "downloaded" || item.st === "reused"; });
    var images = media.filter(function(item){ return item.k === "image"; });
    var files = media.filter(function(item){ return item.k !== "image"; });
    if(images.length){
      var grid = document.createElement("div");
      grid.className = "images";
      images.forEach(function(item){
        var img = document.createElement("img");
        img.loading = "lazy";
        img.decoding = "async";
        img.src = item.p;
        img.alt = item.n || "image";
        img.dataset.name = item.n || "";
        img.dataset.sender = msg.s || "";
        img.dataset.date = time;
        img.addEventListener("click", function(){
          gallery = Array.prototype.slice.call(document.querySelectorAll(".images img"))
            .filter(function(node){ return node.src; })
            .map(function(node){ return {src: node.getAttribute("src"), name: node.dataset.name, sender: node.dataset.sender, date: node.dataset.date}; });
          var idx = gallery.findIndex(function(g){ return g.src === img.getAttribute("src"); });
          openLightbox(idx < 0 ? 0 : idx);
        });
        img.addEventListener("error", function(){ img.classList.add("broken"); });
        grid.appendChild(img);
      });
      bubble.appendChild(grid);
    }
    var allFiles = files.slice();
    var attachmentNames = (msg.a || "").split(";").map(function(s){ return s.trim(); }).filter(Boolean);
    attachmentNames.forEach(function(name){
      var has = files.some(function(item){ return item.n === name; });
      if(!has) allFiles.push({n:name, st:"missing"});
    });
    if(allFiles.length){
      var wrap = document.createElement("div");
      wrap.className = "files";
      allFiles.forEach(function(item){
        var ok = item.st === "downloaded" || item.st === "reused";
        var el = document.createElement(ok ? "a" : "div");
        el.className = "file" + (ok ? "" : " missing");
        if(ok){ el.href = item.p; el.download = item.n || ""; }
        else { el.title = item.error || "File was not downloaded"; }
        var ext = document.createElement("span");
        ext.className = "ext";
        var nm = item.n || "file";
        ext.textContent = (nm.indexOf(".") > -1 ? nm.split(".").pop() : "file").slice(0,4);
        var label = document.createElement("span");
        label.style.minWidth = "0";
        var fname = document.createElement("div");
        fname.className = "fname"; fname.textContent = nm;
        var fsize = document.createElement("div");
        fsize.className = "fsize"; fsize.textContent = ok ? fmtSize(item.b) : "not downloaded";
        label.appendChild(fname); label.appendChild(fsize);
        el.appendChild(ext); el.appendChild(label);
        wrap.appendChild(el);
      });
      bubble.appendChild(wrap);
    }
    if(msg.r){
      var reactions = document.createElement("div");
      reactions.className = "reactions";
      msg.r.split(";").map(function(s){ return s.trim(); }).filter(Boolean).forEach(function(value){
        var chip = document.createElement("span");
        chip.className = "reaction";
        chip.textContent = value;
        reactions.appendChild(chip);
      });
      bubble.appendChild(reactions);
    }
    body.appendChild(bubble);
    row.appendChild(body);
    frag.appendChild(row);
    return frag;
  }
  function render(keepScroll){
    var slice = messages.slice(Math.max(0, messages.length - state.limit));
    var prevHeight = document.documentElement.scrollHeight;
    var prevTop = window.scrollY;
    list.textContent = "";
    gallery = [];
    var frag = document.createDocumentFragment();
    var previous = null;
    slice.forEach(function(msg){ frag.appendChild(buildMessage(msg, previous)); previous = msg; });
    list.appendChild(frag);
    state.shown = slice.length;
    empty.hidden = slice.length > 0;
    countChip.textContent = state.shown >= messages.length
      ? "All " + messages.length.toLocaleString() + " messages"
      : "Showing " + state.shown.toLocaleString() + " of " + messages.length.toLocaleString() + " messages";
    hint.style.display = "flex";
    var oldEarlier = document.getElementById("loadEarlier");
    if(oldEarlier) oldEarlier.remove();
    var oldAll = document.getElementById("loadAll");
    if(oldAll) oldAll.remove();
    if(state.shown < messages.length){
      var earlier = document.createElement("button");
      earlier.id = "loadEarlier";
      earlier.className = "chip";
      earlier.textContent = "Load earlier messages";
      earlier.addEventListener("click", function(){
        state.limit += PAGE;
        render(true);
      });
      hint.appendChild(earlier);
      var all = document.createElement("button");
      all.id = "loadAll";
      all.className = "chip";
      all.textContent = "Load all " + messages.length.toLocaleString() + " messages";
      all.addEventListener("click", function(){
        all.textContent = "Loading?";
        setTimeout(function(){ state.limit = messages.length; render(true); }, 30);
      });
      hint.appendChild(all);
    }
    if(keepScroll){
      var delta = document.documentElement.scrollHeight - prevHeight;
      window.scrollTo(0, prevTop + delta);
    }
  }
  function closeResults(){ document.getElementById("results").classList.remove("open"); document.body.classList.remove("results-open"); }
  function renderResults(){
    var resultsPanel = document.getElementById("results");
    var resultList = document.getElementById("resultList");
    var title = document.getElementById("resultsTitle");
    resultList.textContent = "";
    title.textContent = state.results.length.toLocaleString() +
      (state.results.length === 1 ? " result for " : " results for ") + "\u201c" + search.value.trim() + "\u201d";
    var cap = Math.min(state.results.length, 500);
    var frag = document.createDocumentFragment();
    for(var i = 0; i < cap; i++){
      var msg = state.results[i];
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "result";
      btn.dataset.idx = String(msg.i);
      var top = document.createElement("div");
      top.className = "rtop";
      var name = document.createElement("span");
      name.className = "rname";
      name.textContent = msg.s || "(unknown)";
      var time = document.createElement("span");
      time.className = "rtime";
      time.textContent = shortDate(msg.t) + " " + clock(msg.t);
      top.appendChild(name); top.appendChild(time);
      var body = document.createElement("div");
      body.className = "rtext";
      var text = searchableText(msg).replace(/\s+/g, " ").trim();
      var q = state.query;
      var at = text.toLowerCase().indexOf(q);
      var snippet = text;
      if(at > 90){
        var start = at - 70;
        snippet = "?" + text.slice(start, start + 190) + (start + 190 < text.length ? "?" : "");
      } else if(text.length > 190){
        snippet = text.slice(0, 190) + "?";
      }
      if(!snippet) snippet = "(media only)";
      addHighlight(body, snippet, q);
      btn.appendChild(top); btn.appendChild(body);
      frag.appendChild(btn);
    }
    resultList.appendChild(frag);
    if(state.results.length > cap){
      var note = document.createElement("div");
      note.className = "rnote";
      note.textContent = "Showing first " + cap + " of " + state.results.length.toLocaleString() + " results. Refine your search to narrow it down.";
      resultList.appendChild(note);
    }
    resultsPanel.classList.add("open");
    document.body.classList.add("results-open");
  }
  function runSearch(){
    var q = search.value.trim().toLowerCase();
    state.query = q;
    if(!q){ closeResults(); return; }
    state.results = messages.filter(function(msg){ return matches(msg, q); });
    renderResults();
  }
  function jumpTo(idx){
    var el = list.querySelector('[data-idx="' + idx + '"]');
    if(!el){
      state.limit = Math.min(messages.length, messages.length - idx + 40);
      render(false);
      el = list.querySelector('[data-idx="' + idx + '"]');
    }
    if(!el) return;
    el.scrollIntoView({block: "center", behavior: "smooth"});
    el.classList.add("flash");
    setTimeout(function(){ el.classList.remove("flash"); }, 2600);
  }
  document.getElementById("chatTitle").textContent = DATA.title || "Teams chat";
  var stats = [];
  stats.push((DATA.participants || []).length + " participants");
  stats.push(messages.length.toLocaleString() + " messages");
  if(DATA.mediaCount) stats.push(DATA.mediaCount.toLocaleString() + " media");
  if(DATA.range) stats.push(DATA.range);
  document.getElementById("chatMeta").textContent = stats.join(" · ");
  var chatAvatar = document.getElementById("chatAvatar");
  chatAvatar.textContent = initials(DATA.title);
  chatAvatar.style.setProperty("--h", hue(DATA.title));

  var themeBtn = document.getElementById("themeBtn");
  var sun = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
  var moon = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';
  function applyTheme(theme){
    document.documentElement.dataset.theme = theme;
    themeBtn.innerHTML = theme === "dark" ? sun : moon;
    try { localStorage.setItem("teamsExportTheme", theme); } catch(e) {}
  }
  var savedTheme = "light";
  try { savedTheme = localStorage.getItem("teamsExportTheme") || "light"; } catch(e) {}
  applyTheme(savedTheme);
  themeBtn.addEventListener("click", function(){
    applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  });

  var search = document.getElementById("search");
  var timer = null;
  search.addEventListener("input", function(){
    clearTimeout(timer);
    timer = setTimeout(runSearch, 160);
  });
  document.getElementById("resultsClose").addEventListener("click", closeResults);
  document.getElementById("resultList").addEventListener("click", function(event){
    var button = event.target.closest(".result");
    if(button) jumpTo(Number(button.dataset.idx));
  });
  document.addEventListener("keydown", function(event){
    if(event.key === "/" && document.activeElement !== search){ event.preventDefault(); search.focus(); }
    if(event.key === "Escape"){
      closeLightbox();
      if(document.getElementById("results").classList.contains("open")){ closeResults(); }
      if(document.activeElement === search){ search.value = ""; state.query = ""; }
    }
  });

  document.getElementById("jump").addEventListener("click", function(){
    window.scrollTo({top: document.documentElement.scrollHeight, behavior: "smooth"});
  });
  window.addEventListener("scroll", function(){
    var jump = document.getElementById("jump");
    var far = window.scrollY + window.innerHeight < document.documentElement.scrollHeight - 900;
    jump.classList.toggle("show", far);
  });

  document.getElementById("lbClose").addEventListener("click", closeLightbox);
  document.getElementById("lbPrev").addEventListener("click", function(){
    openLightbox(Number(document.getElementById("lightbox").dataset.index) - 1);
  });
  document.getElementById("lbNext").addEventListener("click", function(){
    openLightbox(Number(document.getElementById("lightbox").dataset.index) + 1);
  });
  document.getElementById("lbOpen").addEventListener("click", function(){
    var idx = Number(document.getElementById("lightbox").dataset.index);
    if(gallery[idx]) window.open(gallery[idx].src, "_blank", "noopener");
  });
  document.getElementById("lightbox").addEventListener("click", function(event){
    if(event.target === this) closeLightbox();
  });

  window.onbeforeprint = function(){
    if(state.limit < messages.length){
      state.limit = messages.length;
      render(false);
    }
  };
  render(false);
})();
</script>
</body>
</html>
"""


def load_export(folder):
    folder = Path(folder)
    messages_path = folder / "messages.json"
    if not messages_path.exists():
        raise FileNotFoundError(f"messages.json not found in {folder}")
    payload = json.loads(messages_path.read_text(encoding="utf-8"))
    manifest_path = folder / "manifest.json"
    manifest = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return payload, manifest


def compact(payload):
    media_count = 0
    messages = []
    senders = []
    for index, message in enumerate(payload.get("messages", [])):
        media = []
        for item in message.get("media") or []:
            if item.get("status") not in ("downloaded", "reused"):
                continue
            media.append({
                "n": item.get("name") or "",
                "p": item.get("path") or "",
                "k": item.get("kind") or "file",
                "b": item.get("bytes") or 0,
                "st": item.get("status") or "",
            })
        media_count += len(media)
        sender = message.get("sender") or ""
        if sender and sender not in senders:
            senders.append(sender)
        messages.append({
            "i": index,
            "t": message.get("timestampLocal") or message.get("timestampUtc") or "",
            "u": message.get("timestampUtc") or "",
            "s": sender,
            "x": message.get("text") or "",
            "a": message.get("attachments") or "",
            "r": message.get("reactions") or "",
            "m": media,
        })
    dates = [m["t"][:10] for m in messages if m.get("t")]
    date_range = None
    if dates:
        first, last = min(dates), max(dates)
        if first == last:
            date_range = first
        else:
            date_range = f"{first} to {last}"
    return {
        "title": payload.get("chatTitle") or "Teams chat",
        "participants": senders,
        "mediaCount": media_count,
        "range": date_range,
        "messages": messages,
        "exportedAt": payload.get("exportedAt"),
    }


def embed_media(folder, data):
    folder = Path(folder)
    for message in data["messages"]:
        for item in message["m"]:
            path = folder / item["p"]
            if not path.is_file():
                continue
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            item["p"] = f"data:{mime};base64,{encoded}"
    return data


def build_viewer(folder, embed=False, open_after=False):
    folder = Path(folder)
    payload, _manifest = load_export(folder)
    data = compact(payload)
    if embed:
        data = embed_media(folder, data)
    serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    serialized = serialized.replace("</", "<\\/")
    title = (data.get("title") or "Teams chat").replace("<", "&lt;").replace(">", "&gt;")
    html = TEMPLATE.replace("__TITLE__", title).replace("__DATA__", serialized)
    target = folder / "chat.html"
    target.write_text(html, encoding="utf-8")
    if open_after:
        webbrowser.open(target.resolve().as_uri())
    return target


def main():
    parser = argparse.ArgumentParser(description="Build a Teams-style HTML reader for an export folder")
    parser.add_argument("folder", help="export folder containing messages.json")
    parser.add_argument("--embed", action="store_true", help="inline media into a single portable HTML file")
    parser.add_argument("--open", action="store_true", help="open the result in the default browser")
    args = parser.parse_args()
    target = build_viewer(args.folder, args.embed, args.open)
    size = target.stat().st_size / 1024
    print(f"Built {target} ({size:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

