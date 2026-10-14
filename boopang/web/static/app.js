(() => {
  const $ = (id) => document.getElementById(id);
  const LABELS = {READY:"출고대기",SHIPPING:"배송중",DELIVERED:"배송완료",PARTIAL:"부분입고",BACKORDER:"입고대기",
    INSPECT_PENDING:"검수대기",HOLD:"보류(오류)",CANCELLED:"취소",RETURNED:"반품"};
  let current = null;

  const won = (s) => Number(s).toLocaleString("ko-KR") + "원";
  const el = (tag, text, cls) => { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; };
  const say = (t, err) => { $("msg").textContent = t; $("msg").className = err ? "error" : ""; };

  async function upload(file, fmt) {
    const fd = new FormData(); fd.append("file", file);
    const url = fmt ? "/api/convert?format=" + fmt : "/api/process";
    const res = await fetch(url, {method: "POST", body: fd});
    if (!res.ok) { let d = "처리 실패"; try { d = (await res.json()).detail || d; } catch (_) {} throw new Error(d); }
    return res;
  }

  function card(label, value) { const c = el("div", undefined, "card"); c.append(el("b", value), el("span", label)); return c; }

  function table(id, headers, rows, numCols) {
    const t = $(id); t.replaceChildren();
    const thead = el("thead"), tr = el("tr");
    headers.forEach((h, i) => tr.append(el("th", h, numCols.includes(i) ? "num" : "")));
    thead.append(tr); t.append(thead);
    const tb = el("tbody");
    rows.forEach((r) => { const row = el("tr"); r.forEach((cell, i) => {
      const td = el("td", cell.text, (numCols.includes(i) ? "num " : "") + (cell.cls || "")); row.append(td); }); tb.append(row); });
    t.append(tb);
  }

  function render(d) {
    const s = d.summary;
    $("cards").replaceChildren(card("라인", s.line_count), card("주문", s.order_count),
      card("오류", s.issues.ERROR), card("경고", s.issues.WARN),
      card("공급가", won(s.supply_amt)), card("부가세", won(s.vat_amt)),
      card("배송비", won(s.shipping_fee)), card("총 합계", won(s.total_amt)));
    $("badges").replaceChildren(...Object.entries(s.order_status).map(([k, v]) => el("span", `${LABELS[k]} ${v}`, "badge s-" + k)));
    table("orders", ["주문번호", "상태", "라인", "공급가", "부가세", "배송비", "합계"],
      d.orders.map((o) => [{text:o.order_id}, {text:LABELS[o.status], cls:"badge s-" + o.status}, {text:o.line_count},
        {text:won(o.supply_amt)}, {text:won(o.vat_amt)}, {text:won(o.shipping_fee)}, {text:won(o.total_amt)}]), [2,3,4,5,6]);
    const rank = {ERROR:0, WARN:1, INFO:2};
    const issues = [...d.issues].sort((a, b) => rank[a.severity] - rank[b.severity] || a.row - b.row);
    table("issues", ["행", "심각도", "코드", "메시지", "주문번호", "SKU"],
      issues.map((i) => [{text:i.row}, {text:i.severity, cls:"sev-" + i.severity}, {text:i.code}, {text:i.message}, {text:i.order_id}, {text:i.sku}]), [0]);
    $("result").hidden = false;
  }

  async function handle(file) {
    if (!file) return;
    current = file; say("처리 중…");
    try { render(await (await upload(file)).json()); say(file.name + " 처리 완료"); }
    catch (e) { $("result").hidden = true; say(e.message, true); }
  }

  async function download(fmt) {
    if (!current) return;
    try {
      const blob = await (await upload(current, fmt)).blob();
      const a = el("a"); a.href = URL.createObjectURL(blob);
      a.download = {csv:"wms_lines.csv", json:"wms_orders.json", xlsx:"inspection_report.xlsx"}[fmt];
      a.click(); URL.revokeObjectURL(a.href);
    } catch (e) { say(e.message, true); }
  }

  const drop = $("drop"), input = $("file");
  drop.addEventListener("click", () => input.click());
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") input.click(); });
  input.addEventListener("change", () => handle(input.files[0]));
  ["dragover", "dragenter"].forEach((n) => drop.addEventListener(n, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((n) => drop.addEventListener(n, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => handle(e.dataTransfer.files[0]));
  document.querySelectorAll(".export button").forEach((b) => b.addEventListener("click", () => download(b.dataset.fmt)));
  const tabs = {orders: $("tab-orders"), issues: $("tab-issues")};
  Object.entries(tabs).forEach(([k, b]) => b.addEventListener("click", () => {
    Object.entries(tabs).forEach(([kk, bb]) => { bb.classList.toggle("active", kk === k); $(kk).hidden = kk !== k; });
  }));
})();
