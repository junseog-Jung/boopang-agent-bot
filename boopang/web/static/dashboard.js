(() => {
  const $ = (id) => document.getElementById(id);
  const SVGNS = "http://www.w3.org/2000/svg";
  const STATE_LABEL = {idle: "대기", thinking: "구상 중", coding: "코딩 중", reviewing: "검수 중", done: "완료", skipped: "생략", error: "오류"};
  const WORKING = new Set(["thinking", "coding", "reviewing"]);
  const ALL_STATES = Object.keys(STATE_LABEL);
  const MAX_LOOPS = 3, MAX_LINES = 1500;
  const REDUCED = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const EXAMPLES = [
    "재고를 입출고로 관리하고 부족 알림을 보여주는 웹앱 (FastAPI + SQLite)",
    "마크다운 메모를 저장하고 검색하는 간단한 메모 서비스 REST API",
    "CSV 파일을 올리면 통계를 계산해 보여주는 파이썬 CLI 도구",
  ];

  const cards = {}, agentInfo = {}, states = {};
  const finished = new Set();
  let mode = "logistics", file = null, source = null, jobId = null, stick = true, total = 12;
  let lastTouched = null, factory = {mode: "disabled", hint: ""}, running = false;

  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };
  const won = (s) => Number(s).toLocaleString("ko-KR") + "원";
  const say = (t, err) => { $("form-msg").textContent = t; $("form-msg").className = err ? "error" : ""; };
  const svg = (tag, attrs) => { const e = document.createElementNS(SVGNS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; };

  /* ---------------------------------------------------------------- 카드 */
  function buildCards(agents) {
    total = agents.length;
    const root = $("agents"); root.replaceChildren();
    Object.keys(cards).forEach((k) => delete cards[k]);
    agents.forEach((a, i) => {
      agentInfo[a.key] = a; states[a.key] = "idle";
      const card = el("div", "agent idle"); card.dataset.key = a.key;
      const sceneWrap = el("div", "scene-wrap"); sceneWrap.innerHTML = window.Avatars.render(a.key);  // 정적 SVG (서버/사용자 데이터 없음)
      const sv = sceneWrap.querySelector("svg"); if (sv) sv.setAttribute("aria-label", `${a.name} 담당 직원`);
      const info = el("div", "info"), head = el("div", "ihead");
      const badge = el("span", "badge", STATE_LABEL.idle);
      head.append(el("span", "name", `${a.emoji} ${a.name}`), badge);
      const task = el("div", "task", "");
      info.append(head, el("div", "role", a.role), task);
      const bubble = el("div", "say");
      card.append(el("span", "no", String(i + 1)), bubble, sceneWrap, info);
      root.append(card);
      cards[a.key] = {root: card, badge, task, bubble, timer: null};
    });
    finished.clear();
    updateProgress();
  }

  function updateProgress() {
    const n = finished.size;
    $("bar").style.width = Math.round(n / total * 100) + "%";
    $("progress-text").textContent = `${n} / ${total} 에이전트 완료`;
  }

  function setState(key, state, task) {
    const c = cards[key]; if (!c) return;
    ALL_STATES.forEach((s) => c.root.classList.remove(s));
    c.root.classList.add(state);
    c.root.classList.toggle("working", WORKING.has(state));
    c.badge.textContent = STATE_LABEL[state] || state;
    if (task !== undefined) c.task.textContent = task;
    if (["done", "skipped", "error"].includes(state)) finished.add(key); else finished.delete(key);
    updateProgress();
  }

  function resetCards() {
    Object.keys(cards).forEach((k) => { states[k] = "idle"; setState(k, "idle", ""); cards[k].bubble.classList.remove("on"); });
    lastTouched = null;
    clearFlow();
    $("mon-agent").textContent = "—"; $("mon-task").textContent = "대기 중"; $("mon-loop").textContent = `0 / ${MAX_LOOPS}`;
    $("result").hidden = true;
    $("term").replaceChildren();
  }

  function speak(key, text) {
    const c = cards[key]; if (!c) return;
    c.bubble.textContent = text.length > 70 ? text.slice(0, 69) + "…" : text;
    c.bubble.classList.add("on");
    clearTimeout(c.timer);
    c.timer = setTimeout(() => c.bubble.classList.remove("on"), 3400);
  }

  /* ---------------------------------------------------------------- 핸드오프 애니메이션 */
  const flow = () => $("flow");
  function clearFlow() { Array.from(flow().children).forEach((n) => { if (n.tagName.toLowerCase() !== "defs") n.remove(); }); }

  function center(card, yRatio) {
    const o = $("office").getBoundingClientRect(), r = card.getBoundingClientRect();
    return {x: r.left - o.left + r.width / 2, y: r.top - o.top + r.height * yRatio};
  }
  const bez = (p0, c, p1, t) => ({
    x: (1 - t) * (1 - t) * p0.x + 2 * (1 - t) * t * c.x + t * t * p1.x,
    y: (1 - t) * (1 - t) * p0.y + 2 * (1 - t) * t * c.y + t * t * p1.y,
  });

  function packetIcon() {
    const g = svg("g", {});
    g.append(svg("circle", {r: 15, fill: "#a78bfa", "fill-opacity": ".28"}));
    g.append(svg("path", {d: "M-7 -9H3L8 -4V9H-7Z", fill: "#fff", stroke: "#6366f1", "stroke-width": "1.4", "stroke-linejoin": "round"}));
    g.append(svg("path", {d: "M3 -9V-4H8", fill: "#e0e7ff", stroke: "#6366f1", "stroke-width": "1.2"}));
    g.append(svg("path", {d: "M-4 0H5M-4 3.5H5M-4 7H1", stroke: "#818cf8", "stroke-width": "1.2", "stroke-linecap": "round"}));
    return g;
  }

  function handoff(fromKey, toKey) {
    const from = cards[fromKey], to = cards[toKey];
    if (!from || !to) return;
    from.root.classList.add("sending");
    setTimeout(() => from.root.classList.remove("sending"), 650);
    const arrive = () => { to.root.classList.add("received"); setTimeout(() => to.root.classList.remove("received"), 900); };
    if (REDUCED || !flow().animate) { arrive(); return; }

    const a = center(from.root, 0.38), b = center(to.root, 0.38);
    const dx = b.x - a.x, dy = b.y - a.y, dist = Math.hypot(dx, dy) || 1;
    const bend = Math.min(90, dist * 0.28);
    const c = {x: (a.x + b.x) / 2 - (dy / dist) * bend, y: (a.y + b.y) / 2 + (dx / dist) * bend - 18};
    const layer = flow(), made = [];

    // 빛줄기
    const gid = "g" + Math.random().toString(36).slice(2, 8);
    const grad = svg("linearGradient", {id: gid, gradientUnits: "userSpaceOnUse", x1: a.x, y1: a.y, x2: b.x, y2: b.y});
    grad.append(svg("stop", {offset: "0", "stop-color": "#38bdf8", "stop-opacity": "0"}), svg("stop", {offset: ".55", "stop-color": "#a78bfa"}), svg("stop", {offset: "1", "stop-color": "#34d399"}));
    layer.querySelector("defs").append(grad); made.push(grad);
    const beam = svg("path", {d: `M${a.x} ${a.y} Q${c.x} ${c.y} ${b.x} ${b.y}`, fill: "none", stroke: `url(#${gid})`, "stroke-width": "3.5", "stroke-linecap": "round", pathLength: "100", "stroke-dasharray": "100", "stroke-dashoffset": "100"});
    layer.append(beam); made.push(beam);
    beam.animate([{strokeDashoffset: 100, opacity: 1}, {strokeDashoffset: 0, opacity: 1, offset: .55}, {strokeDashoffset: 0, opacity: 0}], {duration: 1500, easing: "ease-in-out", fill: "forwards"});

    // 입자 꼬리 + 문서 패킷
    const N = 18, path = [];
    for (let i = 0; i <= N; i++) path.push(bez(a, c, b, i / N));
    const frames = (scale) => path.map((p, i) => ({transform: `translate(${p.x}px, ${p.y}px) scale(${scale})`, offset: i / N}));
    for (let k = 1; k <= 7; k++) {
      const dot = svg("circle", {r: 4.2 - k * 0.4, fill: k % 2 ? "#a78bfa" : "#38bdf8", "fill-opacity": String(0.75 - k * 0.08)});
      layer.append(dot); made.push(dot);
      dot.animate(frames(1), {duration: 950, delay: k * 55, easing: "ease-in-out", fill: "both"});
    }
    const pkt = packetIcon(); layer.append(pkt); made.push(pkt);
    const anim = pkt.animate(frames(1), {duration: 950, easing: "ease-in-out", fill: "forwards"});
    anim.onfinish = () => {
      arrive();
      for (let i = 0; i < 9; i++) {                       // 도착 지점 입자 폭발
        const ang = (i / 9) * Math.PI * 2, spark = svg("circle", {r: 2.6, fill: i % 2 ? "#34d399" : "#fbbf24", cx: b.x, cy: b.y});
        layer.append(spark); made.push(spark);
        spark.animate([{transform: "translate(0,0)", opacity: 1}, {transform: `translate(${Math.cos(ang) * 26}px, ${Math.sin(ang) * 26}px)`, opacity: 0}], {duration: 650, easing: "ease-out", fill: "forwards"});
      }
      pkt.animate([{opacity: 1}, {opacity: 0}], {duration: 300, fill: "forwards"});
    };
    setTimeout(() => made.forEach((n) => n.remove()), 2200);
  }

  /* ---------------------------------------------------------------- 터미널 */
  function logLine(ev) {
    const term = $("term");
    const a = agentInfo[ev.agent] || {emoji: "", name: ev.agent || ""};
    const line = el("div", "hot lv-" + (ev.level || "info"));
    const d = new Date(ev.ts * 1000), p2 = (n) => String(n).padStart(2, "0");
    line.append(el("span", "ts", `[${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}] `), el("span", "who", `${a.emoji} ${a.name} ▸ `), el("span", "msg", ev.message));
    term.append(line);
    while (term.childElementCount > MAX_LINES) term.firstElementChild.remove();
    if (stick) term.scrollTop = term.scrollHeight;
    speak(ev.agent, ev.message);
  }

  function setConn(kind, text) { const c = $("conn"); c.className = "conn " + kind; c.textContent = text; }

  /* ---------------------------------------------------------------- 결과: 물류 */
  const stat = (label, value, cls) => { const d = el("div", "stat " + (cls || "")); d.append(el("b", "", String(value)), el("span", "", label)); return d; };

  function showLogistics(summary, downloads) {
    $("result-title").textContent = "변환 결과";
    $("res-software").hidden = true; $("res-logistics").hidden = false;
    const wrap = $("cards"); wrap.replaceChildren(
      stat("라인", summary.line_count), stat("주문", summary.order_count),
      stat("오류(HOLD)", summary.issues.ERROR, summary.issues.ERROR ? "bad" : ""), stat("경고", summary.issues.WARN, summary.issues.WARN ? "warn" : ""),
      stat("공급가", won(summary.supply_amt)), stat("부가세", won(summary.vat_amt)), stat("배송비", won(summary.shipping_fee)), stat("총 합계", won(summary.total_amt)));
    ["csv", "json", "xlsx"].forEach((f) => {
      const a = $("dl-" + f);
      if (downloads.includes(f)) { a.href = `/api/jobs/${jobId}/download/${f}`; a.hidden = false; } else a.hidden = true;
    });
    $("result").hidden = false;
    $("result").scrollIntoView({behavior: "smooth", block: "nearest"});
  }

  /* ---------------------------------------------------------------- 결과: 소프트웨어 */
  const list = (items) => { const ul = el("ul"); items.forEach((t) => ul.append(el("li", "", t))); return ul; };
  const h3 = (t) => el("h3", "", t);

  function table(headers, rows) {
    const t = el("table", "kv"), tr = el("tr");
    headers.forEach((h) => tr.append(el("th", "", h)));
    t.append(tr);
    rows.forEach((r) => { const row = el("tr"); r.forEach((c) => row.append(el("td", "", c))); t.append(row); });
    return t;
  }

  function showSoftware(sw) {
    $("result-title").textContent = `생성 결과 — ${sw.title}`;
    $("res-logistics").hidden = true; $("res-software").hidden = false;
    $("dl-zip").href = `/api/jobs/${jobId}/download/zip`;

    const sum = $("sw-summary"); sum.replaceChildren();
    if (sw.demo) sum.append(el("div", "banner", "⚠ 데모 모드 결과입니다. 요청 내용과 무관한 고정 샘플('할 일 관리')이며 실제 범용 생성이 아닙니다."));
    if (sw.escalated) sum.append(el("div", "banner err", `자가 치유 ${sw.loops}회 후에도 해결되지 않은 오류가 있습니다. 사람의 검토가 필요합니다.`));
    const cardsBox = el("div", "cards");
    cardsBox.append(stat("파일", sw.files.length), stat("자가 치유 루프", `${sw.loops}/${MAX_LOOPS}`),
      stat("ERROR", sw.findings.ERROR, sw.findings.ERROR ? "bad" : ""), stat("WARN", sw.findings.WARN, sw.findings.WARN ? "warn" : ""),
      stat("LLM 호출", `${sw.llm_calls} (${sw.provider || "-"})`), stat("토큰 (입력/출력)", `${sw.input_tokens.toLocaleString()} / ${sw.output_tokens.toLocaleString()}`),
      stat("MCP 도구 호출", sw.mcp && sw.mcp.enabled ? sw.mcp.calls : "미사용"),
      stat("테스트 실행", sw.exec && sw.exec.ran ? (sw.exec.passed ? "통과" : "실패") : "안 함", sw.exec && sw.exec.ran && !sw.exec.passed ? "bad" : ""));
    sum.append(cardsBox, el("p", "", sw.summary));
    sum.append(h3("남은 점검 항목"));
    sum.append(sw.remaining.length ? list(sw.remaining.map((f) => `[${f.severity}] ${f.path}:${f.line} ${f.code} — ${f.message}`)) : el("p", "muted", "없음"));

    const spec = $("sw-spec"); spec.replaceChildren();
    const prd = sw.prd, arch = sw.architecture;
    spec.append(h3("PRD"), el("p", "", prd.summary));
    if (prd.goals.length) spec.append(h3("목표"), list(prd.goals));
    spec.append(h3("기능"), table(["우선순위", "기능", "설명"], prd.features.map((f) => [f.priority || "", f.name || "", f.description || ""])));
    spec.append(h3("기술 스택"), table(["영역", "선택"], Object.entries(prd.tech_stack).filter(([, v]) => v)));
    if (prd.risks.length) spec.append(h3("위험 요소"), list(prd.risks));
    spec.append(h3("아키텍처"), el("p", "", arch.overview));
    if (arch.components.length) spec.append(h3("컴포넌트"), table(["이름", "책임"], arch.components.map((c) => [c.name || "", c.responsibility || ""])));
    if (arch.api.length) spec.append(h3("API"), table(["메서드", "경로", "설명"], arch.api.map((a) => [a.method || "", a.path || "", a.description || ""])));
    if (arch.data_model.length) spec.append(h3("데이터 모델"), table(["엔티티", "필드"], arch.data_model.map((d) => [d.entity || "", (d.fields || []).join(", ")])));
    spec.append(h3("실행 / 테스트"), list([...arch.run_commands, ...arch.test_commands]));

    const tree = $("file-tree"); tree.replaceChildren();
    sw.files.forEach((f) => {
      const li = el("li", "", f.path); li.dataset.path = f.path; li.setAttribute("role", "option"); li.tabIndex = 0;
      li.append(el("small", "", `${f.size.toLocaleString()}B`));
      const open = () => openFile(f.path);
      li.addEventListener("click", open); li.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
      tree.append(li);
    });
    switchTab("sw-summary");
    $("result").hidden = false;
    $("result").scrollIntoView({behavior: "smooth", block: "nearest"});
    const first = sw.files.find((f) => f.path === "README.md") || sw.files[0];
    if (first) openFile(first.path);
  }

  async function openFile(path) {
    document.querySelectorAll("#file-tree li").forEach((li) => li.classList.toggle("sel", li.dataset.path === path));
    $("code-title").textContent = path;
    try {
      const res = await fetch(`/api/jobs/${jobId}/file?path=${encodeURIComponent(path)}`);
      $("code").textContent = res.ok ? await res.text() : "(불러올 수 없습니다)";
    } catch (_) { $("code").textContent = "(불러올 수 없습니다)"; }
  }

  function switchTab(id) {
    document.querySelectorAll(".tab").forEach((t) => { const on = t.dataset.tab === id; t.classList.toggle("active", on); t.setAttribute("aria-selected", String(on)); });
    ["sw-summary", "sw-spec", "sw-code"].forEach((p) => { $(p).hidden = p !== id; });
  }

  /* ---------------------------------------------------------------- SSE */
  function connect() {
    source = new EventSource(`/api/jobs/${jobId}/events`);
    source.onopen = () => setConn("on", "실시간 연결됨");
    source.onmessage = (m) => {
      const ev = JSON.parse(m.data);
      if (ev.type === "agent") onAgent(ev);
      else if (ev.type === "log") logLine(ev);
      else if (ev.type === "done") { ev.software ? showSoftware(ev.software) : showLogistics(ev.summary, ev.downloads); end("완료"); }
      else if (ev.type === "error") { say(ev.message, true); end("오류로 종료", true); }
    };
    source.onerror = () => { if (source && source.readyState === EventSource.CLOSED) setConn("err", "연결 끊김"); else setConn("off", "재연결 중…"); };
  }

  function onAgent(ev) {
    const prev = states[ev.agent];
    states[ev.agent] = ev.state;
    if (WORKING.has(ev.state) && !WORKING.has(prev)) {          // 일을 시작하는 순간: 직전 담당자가 데이터를 넘겨준다
      if (lastTouched && lastTouched !== ev.agent) handoff(lastTouched, ev.agent);
      lastTouched = ev.agent;
    }
    setState(ev.agent, ev.state, ev.task);
    if (!["done", "skipped"].includes(ev.state) || ev.agent === "ceo") {
      const a = agentInfo[ev.agent];
      $("mon-agent").textContent = `${a.emoji} ${a.name} (상태: ${STATE_LABEL[ev.state]})`;
      $("mon-task").textContent = ev.task || "—";
    }
    $("mon-loop").textContent = `${ev.loop || 0} / ${MAX_LOOPS}`;
  }

  function end(text, bad) {
    if (source) { source.close(); source = null; }
    running = false; setConn(bad ? "err" : "off", text); refreshStart();
  }

  /* ---------------------------------------------------------------- 폼 / 모드 */
  function refreshStart() {
    let ok;
    if (mode === "logistics") ok = !!file;
    else ok = $("request").value.trim().length >= 10 && (factory.mode === "llm" || factory.mode === "demo");
    $("start").disabled = running || !ok;
  }

  async function loadAgents() {
    const res = await fetch(`/api/agents?mode=${mode}`);
    buildCards(await res.json());
    $("term").replaceChildren(el("div", "empty", mode === "software"
      ? "요구사항을 입력하고 '에이전트 팀 가동'을 누르면 로그가 여기에 실시간으로 표시됩니다."
      : "파일을 올리고 '에이전트 팀 가동'을 누르면 로그가 여기에 실시간으로 표시됩니다."));
  }

  async function setMode(m) {
    if (running || m === mode) return;
    mode = m;
    ["logistics", "software"].forEach((k) => { const b = $("mode-" + k), on = k === m; b.classList.toggle("active", on); b.setAttribute("aria-selected", String(on)); $("box-" + k).hidden = !on; });
    $("result").hidden = true; say("");
    await loadAgents();
    if (m === "software") await loadFactoryStatus();
    refreshStart();
  }

  async function loadFactoryStatus() {
    try { factory = await (await fetch("/api/factory/status")).json(); } catch (_) { factory = {mode: "disabled", hint: "상태를 확인할 수 없습니다."}; }
    const s = $("factory-status");
    const exec = {off: "테스트 실행 안 함(정적 점검만)", docker: "테스트는 네트워크 차단 Docker 샌드박스에서 실행", local: "⚠ 테스트를 이 서버에서 격리 없이 실행(local)"}[factory.exec] || "";
    const mcp = factory.mcp ? "MCP 경유 파일 기록" : "MCP 미사용";
    let text;
    if (factory.mode === "llm" && factory.provider === "ollama") text = `✅ 로컬 Ollama(${factory.model}) — 비용 0원, 데이터가 PC 밖으로 나가지 않습니다. 작은 모델은 느리고 품질이 낮을 수 있습니다.`;
    else if (factory.mode === "llm") text = `✅ Claude 모델(${factory.model})로 생성합니다. 호출 비용이 발생합니다.`;
    else if (factory.mode === "demo") text = `⚠ ${factory.hint}`;
    else text = `⛔ ${factory.hint}`;
    s.textContent = ["llm", "demo"].includes(factory.mode) ? `${text} · ${mcp} · ${exec}` : text;
    s.className = "factory-status " + (factory.mode === "llm" && factory.exec !== "local" ? "ok" : "warn");
  }

  async function submit(e) {
    e.preventDefault();
    if (running) return;
    const fd = new FormData();
    let url;
    if (mode === "logistics") { if (!file) return; fd.append("file", file); fd.append("instruction", $("instruction").value); url = "/api/jobs"; }
    else { fd.append("request", $("request").value); url = "/api/factory/jobs"; }
    if (source) { source.close(); source = null; }
    resetCards(); say("요청 전송 중…"); running = true; refreshStart();
    try {
      const res = await fetch(url, {method: "POST", body: fd});
      if (!res.ok) { let d = "작업 시작 실패"; try { d = (await res.json()).detail || d; } catch (_) {} throw new Error(d); }
      jobId = (await res.json()).job_id; say("에이전트 팀 가동 중…"); connect();
    } catch (err) { say(err.message, true); running = false; refreshStart(); }
  }

  function pick(f) {
    if (!f) return;
    file = f; $("drop").classList.add("has"); $("drop-label").textContent = `📄 ${f.name} (${(f.size / 1024).toFixed(1)} KB)`;
    say(""); refreshStart();
  }

  const drop = $("drop"), input = $("file");
  drop.addEventListener("click", () => input.click());
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
  input.addEventListener("change", () => pick(input.files[0]));
  ["dragover", "dragenter"].forEach((n) => drop.addEventListener(n, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((n) => drop.addEventListener(n, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => pick(e.dataTransfer.files[0]));
  $("job-form").addEventListener("submit", submit);
  $("request").addEventListener("input", refreshStart);
  $("mode-logistics").addEventListener("click", () => setMode("logistics"));
  $("mode-software").addEventListener("click", () => setMode("software"));
  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));
  $("term").addEventListener("scroll", () => { const t = $("term"); stick = t.scrollTop + t.clientHeight >= t.scrollHeight - 24; });
  EXAMPLES.forEach((t) => { const b = el("button", "chip", t.length > 34 ? t.slice(0, 33) + "…" : t); b.type = "button"; b.title = t; b.addEventListener("click", () => { $("request").value = t; refreshStart(); }); $("examples").append(b); });

  loadAgents().catch(() => say("에이전트 목록을 불러오지 못했습니다.", true));
})();
