/* 오피스 직원 아바타(SVG) — 12개 에이전트별 캐릭터/책상/소품.
 * 모든 색은 속성(fill/stroke)으로 지정하고 움직임은 dashboard.css 의 클래스로 제어한다 (CSP: 인라인 style 금지). */
(() => {
  const SKIN = ["#f4d2b3", "#efc39b", "#f7dcc4", "#e6b88c"];
  const HAIRC = ["#1c1a21", "#2a2118", "#15151b", "#3b2b21"];

  const hair = {
    short: {back: () => "", front: (c) => `<path d="M58 59C56 38 68 31 80 31C93 31 104 38 102 59C99 49 92 45 80 45C68 45 61 49 58 59Z" fill="${c}"/>`},
    side: {back: () => "", front: (c) => `<path d="M57 60C55 37 70 30 83 31C96 32 105 41 102 60C98 48 88 43 75 46C67 48 61 52 57 60Z" fill="${c}"/><path d="M68 37C80 34 93 39 99 48" stroke="#fff" stroke-opacity=".14" fill="none" stroke-width="2"/>`},
    bob: {back: (c) => `<path d="M55 68C51 36 68 29 80 29C93 29 109 36 105 68L105 82C99 82 98 74 98 64L62 64C62 74 61 82 55 82Z" fill="${c}"/>`,
          front: (c) => `<path d="M58 57C60 42 70 38 80 38C92 38 101 43 102 57C94 49 66 49 58 57Z" fill="${c}"/>`},
    bun: {back: () => "", front: (c) => `<circle cx="80" cy="26" r="9" fill="${c}"/><path d="M58 59C56 38 68 31 80 31C93 31 104 38 102 59C99 49 92 45 80 45C68 45 61 49 58 59Z" fill="${c}"/>`},
    ponytail: {back: (c) => `<g class="ponytail"><path d="M99 44C113 46 116 66 107 80C109 66 105 56 97 52Z" fill="${c}"/></g>`,
               front: (c) => `<path d="M58 59C56 38 68 31 80 31C93 31 104 38 102 59C99 49 92 45 80 45C68 45 61 49 58 59Z" fill="${c}"/>`},
    messy: {back: () => "", front: (c) => `<path d="M57 59C53 45 59 34 66 36L68 27L76 34L82 25L88 34L96 29L97 38C105 40 105 50 103 59C99 50 92 46 80 46C68 46 61 50 57 59Z" fill="${c}"/>`},
    long: {back: (c) => `<path d="M54 60C47 90 50 108 58 119L102 119C110 108 113 90 106 60C104 36 92 28 80 28C68 28 56 36 54 60Z" fill="${c}"/>`,
           front: (c) => `<path d="M58 57C60 42 70 38 80 38C92 38 101 43 102 57C94 49 66 49 58 57Z" fill="${c}"/>`},
  };

  /* ---------- 벽/배경 소품 (왼쪽) ---------- */
  const WALL = {
    window: () => `<rect x="8" y="14" width="46" height="40" rx="2" fill="#bfe0f7" class="win"/><path d="M8 54V40L18 34L26 42L36 30L46 40L54 36V54Z" fill="#7ea4c9"/><rect class="cloud" x="14" y="20" width="14" height="5" rx="2.5" fill="#fff"/><path d="M31 14V54M8 34H54" stroke="#e8eef7" stroke-width="2"/>`,
    wbNotes: () => `<rect x="6" y="14" width="50" height="40" rx="2" fill="#f7f9fd" stroke="#c9cfe3"/><g class="sticky"><rect x="11" y="19" width="11" height="11" fill="#ffd966" transform="rotate(-4 16 24)"/><rect x="26" y="21" width="11" height="11" fill="#ff9eb5" transform="rotate(3 31 26)"/><rect x="40" y="19" width="11" height="11" fill="#9ee0a8" transform="rotate(-2 45 24)"/></g><path class="wb-line" d="M11 40H48M11 46H36" stroke="#4f46e5" stroke-width="2" stroke-linecap="round" fill="none" pathLength="100"/>`,
    wbDiagram: () => `<rect x="6" y="14" width="50" height="40" rx="2" fill="#f7f9fd" stroke="#c9cfe3"/><rect x="11" y="19" width="14" height="9" rx="2" fill="#cfe0ff" stroke="#4f46e5"/><rect x="36" y="19" width="14" height="9" rx="2" fill="#cfe0ff" stroke="#4f46e5"/><rect x="23" y="38" width="16" height="9" rx="2" fill="#d7f5e3" stroke="#16a34a"/><path class="wb-line" d="M25 24H36M18 28L28 38M43 28L35 38" stroke="#4f46e5" stroke-width="1.8" fill="none" stroke-linecap="round" pathLength="100"/>`,
    wbWire: () => `<rect x="6" y="14" width="50" height="40" rx="2" fill="#f7f9fd" stroke="#c9cfe3"/><rect x="12" y="19" width="38" height="6" rx="1" fill="#e5e9f7"/><rect x="12" y="29" width="16" height="18" rx="1" fill="#e5e9f7"/><rect x="32" y="29" width="18" height="8" rx="1" fill="#e5e9f7"/><path class="wb-line" d="M10 17H52V50H10Z" stroke="#ec4899" stroke-width="1.6" fill="none" pathLength="100"/>`,
    clock: () => `<circle cx="30" cy="34" r="19" fill="#fff" stroke="#c9cfe3" stroke-width="3"/><path d="M30 19V22M30 46V49M15 34H18M42 34H45" stroke="#8a94ab" stroke-width="2"/><g class="clock-hand"><path d="M30 34V23" stroke="#1f2937" stroke-width="2.2" stroke-linecap="round"/></g><path d="M30 34L38 38" stroke="#dc2626" stroke-width="1.8" stroke-linecap="round"/><circle cx="30" cy="34" r="2" fill="#1f2937"/>`,
    kanban: () => `<rect x="6" y="14" width="50" height="40" rx="2" fill="#f7f9fd" stroke="#c9cfe3"/><path d="M23 16V52M40 16V52" stroke="#dfe3f2"/><g class="kcard"><rect x="9" y="20" width="12" height="8" fill="#ffd966"/><rect x="26" y="20" width="12" height="8" fill="#9ee0a8"/><rect x="43" y="20" width="10" height="8" fill="#9ec8ff"/></g><rect x="9" y="32" width="12" height="8" fill="#ff9eb5"/><rect x="26" y="32" width="12" height="8" fill="#9ee0a8"/>`,
    shelf: () => `<rect x="8" y="16" width="46" height="40" fill="#c8a87c"/><rect x="10" y="18" width="42" height="17" fill="#e9dcc3"/><rect x="10" y="38" width="42" height="16" fill="#e9dcc3"/><g><rect x="12" y="22" width="5" height="13" fill="#4f46e5"/><rect x="18" y="25" width="5" height="10" fill="#16a34a"/><rect x="24" y="21" width="5" height="14" fill="#f59e0b"/><rect x="30" y="24" width="5" height="11" fill="#ec4899"/></g><circle cx="44" cy="46" r="6" fill="#4ade80"/><rect x="41" y="48" width="6" height="6" fill="#b45309"/>`,
    rack: () => `<rect x="10" y="12" width="40" height="44" rx="2" fill="#2b3245"/><g fill="#1b2130"><rect x="13" y="16" width="34" height="8"/><rect x="13" y="27" width="34" height="8"/><rect x="13" y="38" width="34" height="8"/></g><circle class="led l1" cx="18" cy="20" r="1.6" fill="#34d399"/><circle class="led l2" cx="24" cy="20" r="1.6" fill="#fbbf24"/><circle class="led l3" cx="18" cy="31" r="1.6" fill="#34d399"/><circle class="led l4" cx="24" cy="31" r="1.6" fill="#38bdf8"/><circle class="led l5" cx="18" cy="42" r="1.6" fill="#f87171"/><circle class="led l2" cx="24" cy="42" r="1.6" fill="#34d399"/>`,
  };

  /* ---------- 모니터 (오른쪽, 정면) ---------- */
  const MON = {
    code: (k) => `<g class="code-lines"><rect x="115" y="68" width="18" height="2.4" fill="#7dd3fc"/><rect x="118" y="73" width="26" height="2.4" fill="#a7f3d0"/><rect x="118" y="78" width="14" height="2.4" fill="#fde68a"/><rect x="115" y="83" width="24" height="2.4" fill="#c4b5fd"/><rect x="118" y="88" width="20" height="2.4" fill="#7dd3fc"/><rect x="115" y="93" width="28" height="2.4" fill="#a7f3d0"/><rect x="118" y="98" width="16" height="2.4" fill="#fde68a"/></g>`,
    chart: () => `<g><rect class="bar b1" x="116" y="80" width="6" height="8" fill="#38bdf8"/><rect class="bar b2" x="125" y="76" width="6" height="12" fill="#34d399"/><rect class="bar b3" x="134" y="71" width="6" height="17" fill="#fbbf24"/><rect class="bar b4" x="143" y="74" width="6" height="14" fill="#f472b6"/></g>`,
    shield: () => `<path class="shield" d="M132 67L145 72V80C145 86 140 90 132 93C124 90 119 86 119 80V72Z" fill="#34d399" fill-opacity=".85"/><path d="M126 80L131 85L139 75" stroke="#0f1626" stroke-width="2.2" fill="none" stroke-linecap="round"/>`,
    checks: () => `<g><rect x="116" y="69" width="34" height="3" fill="#475569"/><rect x="116" y="77" width="34" height="3" fill="#475569"/><rect x="116" y="85" width="34" height="3" fill="#475569"/></g><path class="tick t1" d="M144 70L146 72L150 67" stroke="#34d399" stroke-width="1.8" fill="none"/><path class="tick t2" d="M144 78L146 80L150 75" stroke="#34d399" stroke-width="1.8" fill="none"/><path class="tick t3" d="M144 86L146 88L150 83" stroke="#fbbf24" stroke-width="1.8" fill="none"/>`,
    doc: () => `<g class="doc-lines"><rect class="dl d1" x="116" y="69" width="34" height="2.6" fill="#cbd5e1"/><rect class="dl d2" x="116" y="75" width="28" height="2.6" fill="#cbd5e1"/><rect class="dl d3" x="116" y="81" width="32" height="2.6" fill="#cbd5e1"/><rect class="dl d4" x="116" y="87" width="20" height="2.6" fill="#cbd5e1"/></g>`,
    rocket: () => `<g class="rocket"><path d="M132 66C137 71 137 80 132 86C127 80 127 71 132 66Z" fill="#e5e7eb"/><circle cx="132" cy="75" r="2.3" fill="#38bdf8"/><path d="M127 82L123 88L128 86ZM137 82L141 88L136 86Z" fill="#f87171"/><path class="flame" d="M130 86L132 93L134 86Z" fill="#fbbf24"/></g><path d="M116 94H148" stroke="#475569" stroke-width="2"/>`,
    palette: () => `<circle class="pd p1" cx="122" cy="76" r="4.5" fill="#f87171"/><circle class="pd p2" cx="133" cy="72" r="4.5" fill="#fbbf24"/><circle class="pd p3" cx="144" cy="76" r="4.5" fill="#34d399"/><circle class="pd p4" cx="127" cy="86" r="4.5" fill="#38bdf8"/><circle class="pd p5" cx="139" cy="86" r="4.5" fill="#a78bfa"/>`,
    gauge: () => `<path d="M118 90A14 14 0 0 1 146 90" stroke="#475569" stroke-width="4" fill="none"/><path d="M118 90A14 14 0 0 1 126 78" stroke="#34d399" stroke-width="4" fill="none"/><path d="M126 78A14 14 0 0 1 138 78" stroke="#fbbf24" stroke-width="4" fill="none"/><g class="needle"><path d="M132 90L132 77" stroke="#f1f5f9" stroke-width="2" stroke-linecap="round"/></g><circle cx="132" cy="90" r="2.5" fill="#f1f5f9"/>`,
  };

  /* ---------- 책상 위 소품 (왼쪽) ---------- */
  const DESK = {
    mug: () => `<rect x="20" y="86" width="12" height="13" rx="2" fill="#fff" stroke="#c9cfe3"/><path d="M32 89C37 89 37 96 32 96" stroke="#c9cfe3" stroke-width="2" fill="none"/><g class="steam"><path d="M23 83C21 80 25 78 23 75M28 83C26 80 30 78 28 75" stroke="#fff" stroke-opacity=".7" stroke-width="1.6" fill="none" stroke-linecap="round"/></g>`,
    clipboard: () => `<rect x="18" y="78" width="22" height="22" rx="2" fill="#b98a5d"/><rect x="21" y="81" width="16" height="17" fill="#fff"/><rect x="24" y="76" width="10" height="5" rx="1.5" fill="#64748b"/><path d="M23 86H35M23 91H31" stroke="#cbd5e1" stroke-width="1.6"/><path class="clip-check" d="M31 93L33 95L37 89" stroke="#16a34a" stroke-width="2" fill="none" stroke-linecap="round"/>`,
    tablet: () => `<rect x="16" y="82" width="28" height="18" rx="2" fill="#2b3245"/><rect x="18" y="84" width="24" height="14" fill="#0f1626"/><circle class="pd p1" cx="23" cy="91" r="2.4" fill="#f87171"/><circle class="pd p2" cx="30" cy="91" r="2.4" fill="#fbbf24"/><circle class="pd p3" cx="37" cy="91" r="2.4" fill="#34d399"/><path class="stylus" d="M44 80L36 94" stroke="#f1f5f9" stroke-width="2" stroke-linecap="round"/>`,
    book: () => `<path d="M16 98L30 94L44 98V86L30 82L16 86Z" fill="#fff" stroke="#c9cfe3"/><path d="M30 82V94" stroke="#c9cfe3"/><g class="page"><path d="M30 83L42 86V96L30 93Z" fill="#f1f5f9" stroke="#c9cfe3"/></g><path class="pen" d="M42 80L48 94" stroke="#2563eb" stroke-width="2.2" stroke-linecap="round"/>`,
    magnifier: () => `<g class="mag"><circle cx="30" cy="86" r="8" fill="#bfe0f7" fill-opacity=".5" stroke="#64748b" stroke-width="2.4"/><path d="M36 92L44 100" stroke="#64748b" stroke-width="3.2" stroke-linecap="round"/></g>`,
    lamp: () => `<ellipse class="lamp-glow" cx="30" cy="80" rx="13" ry="11" fill="#fde68a" fill-opacity="0"/><path d="M22 99H38M30 99V86M30 86L24 78" stroke="#64748b" stroke-width="2.4" stroke-linecap="round" fill="none"/><path d="M19 74L30 70L33 80L22 83Z" fill="#f59e0b"/>`,
    wrench: () => `<g class="wrench"><path d="M24 98L36 86" stroke="#94a3b8" stroke-width="4" stroke-linecap="round"/><circle cx="38" cy="84" r="5" fill="none" stroke="#94a3b8" stroke-width="3"/></g>`,
  };

  const SPECS = {
    ceo:         {skin: 0, hair: 0, style: "side",     shirt: "#2f3b52", acc: "tie", tie: "#c0392b", wall: "window",    mon: "chart",   desk: "mug"},
    supervisor:  {skin: 2, hair: 1, style: "bob",      shirt: "#2a7a72", glasses: true,           wall: "kanban",    mon: "checks",  desk: "clipboard"},
    ideation:    {skin: 1, hair: 2, style: "messy",    shirt: "#f2b632",                           wall: "wbNotes",   mon: "doc",     desk: "lamp"},
    architecture:{skin: 2, hair: 3, style: "bun",      shirt: "#4a6fd1",                           wall: "wbDiagram", mon: "chart",   desk: "mug"},
    uiux:        {skin: 0, hair: 0, style: "long",     shirt: "#e0679a",                           wall: "wbWire",    mon: "palette", desk: "tablet"},
    development: {skin: 3, hair: 2, style: "messy",    shirt: "#5b6b8c", headset: true,            wall: "shelf",     mon: "code",    desk: "mug"},
    qa:          {skin: 1, hair: 1, style: "short",    shirt: "#3f9d6b", glasses: true,           wall: "shelf",     mon: "checks",  desk: "magnifier"},
    refinement:  {skin: 0, hair: 3, style: "short",    shirt: "#e9863a",                           wall: "clock",     mon: "code",    desk: "wrench"},
    security:    {skin: 3, hair: 0, style: "short",    shirt: "#27272f", acc: "collar",             wall: "rack",      mon: "shield",  desk: "mug"},
    devops:      {skin: 2, hair: 2, style: "long",     shirt: "#2aa7a1", headset: true,            wall: "rack",      mon: "rocket",  desk: "mug"},
    docs:        {skin: 1, hair: 3, style: "side",     shirt: "#9a7bd0", glasses: true,           wall: "shelf",     mon: "doc",     desk: "book"},
    loop:        {skin: 0, hair: 1, style: "ponytail", shirt: "#d6a23a",                           wall: "clock",     mon: "gauge",   desk: "mug"},
  };

  function render(key, label) {
    const s = SPECS[key];
    if (!s) return "";
    const skin = SKIN[s.skin], hc = HAIRC[s.hair], h = hair[s.style];
    const darker = "#00000022";
    const glasses = s.glasses ? `<g fill="none" stroke="#2b3245" stroke-width="1.6"><circle cx="71" cy="63" r="6.5"/><circle cx="89" cy="63" r="6.5"/><path d="M77.5 63H82.5"/></g><path class="glint" d="M67 60L70 58" stroke="#fff" stroke-width="1.4" stroke-linecap="round"/>` : "";
    const headset = s.headset ? `<path d="M57 60C55 38 105 38 103 60" stroke="#3b4258" stroke-width="3.4" fill="none"/><rect x="52" y="56" width="7" height="12" rx="3" fill="#3b4258"/><rect x="101" y="56" width="7" height="12" rx="3" fill="#3b4258"/><path d="M55 68C56 78 66 80 72 78" stroke="#3b4258" stroke-width="2" fill="none"/><circle cx="73" cy="78" r="2.6" fill="#ef4444" class="mic"/>` : "";
    const tie = s.acc === "tie" ? `<path d="M76 86L84 86L82 92L86 108L80 112L74 108L78 92Z" fill="${s.tie}"/><path d="M74 86L80 90L86 86L80 84Z" fill="#fff"/>` : "";
    const collar = s.acc === "collar" ? `<path d="M70 86L80 96L90 86L86 84L80 90L74 84Z" fill="#fff" fill-opacity=".9"/>` : "";
    const lanyard = `<path d="M72 86L78 99M88 86L82 99" stroke="#2563eb" stroke-width="1.4"/><rect x="76" y="98" width="8" height="6" rx="1" fill="#fff" stroke="#2563eb" stroke-width=".8"/>`;
    return `<svg class="scene" viewBox="0 0 160 120" role="img" aria-label="${label || key} 직원" focusable="false">
<rect class="wall" width="160" height="100"/><rect class="baseboard" y="94" width="160" height="6"/>
<g class="wall-prop">${(WALL[s.wall] || (() => ""))()}</g>
<g class="monitor"><rect x="110" y="62" width="44" height="32" rx="3" fill="#2b3245"/><rect class="screen" x="113" y="65" width="38" height="26" rx="1.5"/><g class="screen-content">${(MON[s.mon] || (() => ""))()}</g><rect x="128" y="94" width="8" height="5" fill="#2b3245"/><rect x="120" y="98" width="24" height="2.5" rx="1" fill="#2b3245"/></g>
<g class="char">
${h.back(hc)}
<path class="torso" d="M42 120C42 98 58 86 80 86C102 86 118 98 118 120Z" fill="${s.shirt}"/>
${tie}${collar}${lanyard}
<rect x="73" y="76" width="14" height="13" rx="4" fill="${skin}"/><path d="M73 84Q80 90 87 84V89H73Z" fill="${darker}"/>
<g class="head">
<ellipse cx="58.5" cy="64" rx="3.2" ry="4.6" fill="${skin}"/><ellipse cx="101.5" cy="64" rx="3.2" ry="4.6" fill="${skin}"/>
<ellipse cx="80" cy="62" rx="21" ry="23" fill="${skin}"/>
${h.front(hc)}
<path d="M66 56Q71 53 76 55.6M84 55.6Q89 53 94 56" stroke="${hc}" stroke-width="1.9" fill="none" stroke-linecap="round"/>
<g class="eyes"><ellipse class="eye" cx="71" cy="63" rx="2.5" ry="3" fill="#20202a"/><ellipse class="eye" cx="89" cy="63" rx="2.5" ry="3" fill="#20202a"/><circle cx="72" cy="61.8" r=".9" fill="#fff"/><circle cx="90" cy="61.8" r=".9" fill="#fff"/></g>
<ellipse cx="64" cy="70" rx="4" ry="2.4" fill="#f472b6" fill-opacity=".25"/><ellipse cx="96" cy="70" rx="4" ry="2.4" fill="#f472b6" fill-opacity=".25"/>
<path class="mouth" d="M75 72Q80 76 85 72" stroke="#8a4a3f" stroke-width="1.8" fill="none" stroke-linecap="round"/>
<path class="mouth-big" d="M73 71Q80 81 87 71Z" fill="#a8443f"/>
${glasses}${headset}
</g></g>
<rect class="desk" x="0" y="100" width="160" height="20"/><rect class="desk-edge" x="0" y="100" width="160" height="3"/>
<g class="desk-prop">${(DESK[s.desk] || (() => ""))()}</g>
<rect class="kbd" x="60" y="102" width="40" height="7" rx="2" fill="#3b4258"/><g class="keys" fill="#7c86a3"><rect class="key k1" x="64" y="104" width="4" height="2"/><rect class="key k2" x="71" y="104" width="4" height="2"/><rect class="key k3" x="78" y="104" width="4" height="2"/><rect class="key k4" x="85" y="104" width="4" height="2"/><rect class="key k5" x="92" y="104" width="4" height="2"/></g>
<g class="arms"><path d="M48 100Q50 92 62 98" stroke="${s.shirt}" stroke-width="9" fill="none" stroke-linecap="round"/><path d="M112 100Q110 92 98 98" stroke="${s.shirt}" stroke-width="9" fill="none" stroke-linecap="round"/>
<circle class="hand hl" cx="64" cy="100" r="4.6" fill="${skin}"/><circle class="hand hr" cx="96" cy="100" r="4.6" fill="${skin}"/></g>
<g class="fx fx-think"><circle cx="108" cy="46" r="2" fill="#fff"/><circle cx="116" cy="38" r="3" fill="#fff"/><g><rect x="112" y="14" width="40" height="22" rx="11" fill="#fff" stroke="#c9cfe3"/><circle class="td t1" cx="123" cy="25" r="2.4" fill="#8b5cf6"/><circle class="td t2" cx="132" cy="25" r="2.4" fill="#8b5cf6"/><circle class="td t3" cx="141" cy="25" r="2.4" fill="#8b5cf6"/></g></g>
<g class="fx fx-zzz"><text x="108" y="38" font-size="14" font-weight="700" fill="#94a3b8">Z</text><text x="119" y="28" font-size="10" font-weight="700" fill="#94a3b8">z</text></g>
<g class="fx fx-alert"><circle cx="112" cy="32" r="10" fill="#ef4444"/><rect x="110.6" y="25" width="2.8" height="9" rx="1.2" fill="#fff"/><circle cx="112" cy="38" r="1.6" fill="#fff"/></g>
<g class="fx fx-done"><path d="M112 30L116 34L124 24" stroke="#16a34a" stroke-width="3.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path class="spark s1" d="M104 22L106 26L110 28L106 30L104 34L102 30L98 28L102 26Z" fill="#fbbf24"/><path class="spark s2" d="M130 40L131.5 43L134.5 44.5L131.5 46L130 49L128.5 46L125.5 44.5L128.5 43Z" fill="#fbbf24"/></g>
</svg>`;
  }

  window.Avatars = {render, SPECS};
})();
