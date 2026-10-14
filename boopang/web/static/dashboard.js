(() => {
  const $ = (id) => document.getElementById(id);
  const STATE_LABEL = {idle: "대기", thinking: "구상 중", coding: "코딩 중", reviewing: "검수 중", done: "완료", skipped: "생략", error: "오류"};
  const MAX_LOOPS = 3, MAX_LINES = 1500;
  const cards = {};           // key -> {root, badge, task}
  const agentInfo = {};       // key -> {emoji, name}
  const finished = new Set();
  let file = null, source = null, jobId = null, stick = true, total = 12;

  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };
  const won = (s) => Number(s).toLocaleString("ko-KR") + "원";
  const say = (t, err) => { $("form-msg").textContent = t; $("form-msg").className = err ? "error" : ""; };

  function setState(key, state, task) {
    const c = cards[key]; if (!c) return;
    c.root.className = "agent " + state;
    c.badge.textContent = STATE_LABEL[state] || state;
    if (task !== undefined) c.task.textContent = task;
    if (["done", "skipped", "error"].includes(state)) finished.add(key); else finished.delete(key);
    const n = finished.size;
    $("bar").style.width = Math.round(n / total * 100) + "%";
    $("progress-text").textContent = `${n} / ${total} 에이전트 완료`;
  }

  function buildCards(agents) {
    total = agents.length;
    const root = $("agents"); root.replaceChildren();
    agents.forEach((a) => {
      agentInfo[a.key] = a;
      const head = el("div", "head");
      head.append(el("span", "emoji", a.emoji), el("span", "name", a.name));
      const badge = el("span", "badge", STATE_LABEL.idle); head.append(badge);
      const task = el("div", "task", "");
      const card = el("div", "agent idle");
      card.append(head, el("div", "role", a.role), task, el("div", "fx"));
      root.append(card);
      cards[a.key] = {root: card, badge, task};
    });
  }

  function resetCards() {
    finished.clear();
    Object.keys(cards).forEach((k) => setState(k, "idle", ""));
    $("bar").style.width = "0"; $("progress-text").textContent = `0 / ${total} 에이전트 완료`;
    $("mon-agent").textContent = "—"; $("mon-task").textContent = "대기 중"; $("mon-loop").textContent = `0 / ${MAX_LOOPS}`;
    $("result").hidden = true;
    $("term").replaceChildren();
  }

  function logLine(ev) {
    const term = $("term");
    const a = agentInfo[ev.agent] || {emoji: "", name: ev.agent || ""};
    const line = el("div", "lv-" + (ev.level || "info"));
    const d = new Date(ev.ts * 1000), p2 = (n) => String(n).padStart(2, "0");
    const t = `${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}`;
    line.append(el("span", "ts", `[${t}] `), el("span", "who", `${a.emoji} ${a.name} ▸ `), el("span", "msg", ev.message));
    term.append(line);
    while (term.childElementCount > MAX_LINES) term.firstElementChild.remove();
    if (stick) term.scrollTop = term.scrollHeight;
  }

  function showResult(summary, downloads) {
    const wrap = $("cards"); wrap.replaceChildren();
    const stat = (label, value, cls) => { const d = el("div", "stat " + (cls || "")); d.append(el("b", "", value), el("span", "", label)); return d; };
    wrap.append(stat("라인", summary.line_count), stat("주문", summary.order_count),
      stat("오류(HOLD)", summary.issues.ERROR, summary.issues.ERROR ? "bad" : ""), stat("경고", summary.issues.WARN, summary.issues.WARN ? "warn" : ""),
      stat("공급가", won(summary.supply_amt)), stat("부가세", won(summary.vat_amt)),
      stat("배송비", won(summary.shipping_fee)), stat("총 합계", won(summary.total_amt)));
    ["csv", "json", "xlsx"].forEach((f) => {
      const a = $("dl-" + f);
      if (downloads.includes(f)) { a.href = `/api/jobs/${jobId}/download/${f}`; a.hidden = false; } else a.hidden = true;
    });
    $("result").hidden = false;
    $("result").scrollIntoView({behavior: "smooth", block: "nearest"});
  }

  function setConn(kind, text) { const c = $("conn"); c.className = "conn " + kind; c.textContent = text; }

  function connect() {
    source = new EventSource(`/api/jobs/${jobId}/events`);
    source.onopen = () => setConn("on", "실시간 연결됨");
    source.onmessage = (m) => {
      const ev = JSON.parse(m.data);
      if (ev.type === "agent") {
        setState(ev.agent, ev.state, ev.task);
        if (!["done", "skipped"].includes(ev.state) || ev.agent === "ceo") {
          const a = agentInfo[ev.agent];
          $("mon-agent").textContent = `${a.emoji} ${a.name} (상태: ${STATE_LABEL[ev.state]})`;
          $("mon-task").textContent = ev.task || "—";
        }
        $("mon-loop").textContent = `${ev.loop || 0} / ${MAX_LOOPS}`;
      } else if (ev.type === "log") {
        logLine(ev);
      } else if (ev.type === "done") {
        showResult(ev.summary, ev.downloads); end("완료");
      } else if (ev.type === "error") {
        say(ev.message, true); end("오류로 종료", true);
      }
    };
    source.onerror = () => { if (source && source.readyState === EventSource.CLOSED) setConn("err", "연결 끊김"); else setConn("off", "재연결 중…"); };
  }

  function end(text, bad) { if (source) { source.close(); source = null; } setConn(bad ? "err" : "off", text); $("start").disabled = !file; }

  async function submit(e) {
    e.preventDefault();
    if (!file) return;
    if (source) { source.close(); source = null; }
    resetCards(); say("업로드 중…"); $("start").disabled = true;
    const fd = new FormData(); fd.append("file", file); fd.append("instruction", $("instruction").value);
    try {
      const res = await fetch("/api/jobs", {method: "POST", body: fd});
      if (!res.ok) { let d = "작업 시작 실패"; try { d = (await res.json()).detail || d; } catch (_) {} throw new Error(d); }
      jobId = (await res.json()).job_id; say("에이전트 팀 가동 중…"); connect();
    } catch (err) { say(err.message, true); $("start").disabled = false; }
  }

  function pick(f) {
    if (!f) return;
    file = f; $("drop").classList.add("has"); $("drop-label").textContent = `📄 ${f.name} (${(f.size / 1024).toFixed(1)} KB)`;
    $("start").disabled = false; say("");
  }

  const drop = $("drop"), input = $("file");
  drop.addEventListener("click", () => input.click());
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
  input.addEventListener("change", () => pick(input.files[0]));
  ["dragover", "dragenter"].forEach((n) => drop.addEventListener(n, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((n) => drop.addEventListener(n, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => pick(e.dataTransfer.files[0]));
  $("job-form").addEventListener("submit", submit);
  $("term").addEventListener("scroll", () => { const t = $("term"); stick = t.scrollTop + t.clientHeight >= t.scrollHeight - 24; });

  fetch("/api/agents").then((r) => r.json()).then((a) => { buildCards(a); $("term").append(el("div", "empty", "파일을 올리고 '에이전트 팀 가동'을 누르면 로그가 여기에 실시간으로 표시됩니다.")); })
    .catch(() => say("에이전트 목록을 불러오지 못했습니다.", true));
})();
