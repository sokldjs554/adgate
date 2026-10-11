// adgate 콘솔. 광고 문구는 신뢰하지 않는 입력이므로 DOM 은 textContent / createElement 로만 만든다(innerHTML 금지).
"use strict";

const $ = (s) => document.querySelector(s);
const FIELDS = [
  ["headline", "제목"], ["body", "본문"], ["cta", "CTA"], ["disclosure", "고지 문구"],
  ["image_text", "이미지 속 글자"], ["landing_text", "랜딩 페이지 텍스트"],
];
const OPTIONAL = new Set(["disclosure", "image_text", "landing_text"]);

const EXAMPLES = {
  "승인: 일반 앱": { category: "app", headline: "가계부 앱, 영수증은 사진 한 장으로", body: "영수증을 찍으면 항목이 자동으로 분류돼요.", cta: "앱 다운로드" },
  "반려: 무조건 승인": { category: "loan", headline: "무조건 승인, 오늘 바로 입금", body: "모바일로 3분이면 신청 끝.", cta: "신청하기",
    disclosure: "과도한 대출은 개인신용평점 하락의 원인이 될 수 있습니다. 금리는 연 7.9% ~ 17.9%이며 신용도에 따라 달라질 수 있습니다." },
  "반려: 띄어쓰기로 우회한 수익 보장": { category: "invest", headline: "소액 투자 시작하기", body: "누구나 쉽게 시작하는 해외 주식, 수 익 보 장 으로 걱정 없이 시작하세요.", cta: "시작하기",
    disclosure: "투자 상품은 원금 손실이 발생할 수 있습니다." },
  "승인: 같은 단어, 부정문": { category: "invest", headline: "해외 ETF 투자, 위험까지 투명하게", body: "이 상품은 수익을 보장하지 않으며 원금 손실 가능성이 있습니다.", cta: "설명서 보기",
    disclosure: "원금 비보장 상품입니다. 과거 수익률은 미래 수익률을 보장하지 않습니다." },
  "승인: 범위 표현 (최고 연 15.9%)": { category: "loan", headline: "직장인 신용대출, 금리 최고 연 15.9%", body: "금리는 신용도에 따라 달라질 수 있습니다.", cta: "한도 조회",
    disclosure: "과도한 대출은 개인신용평점 하락의 원인이 될 수 있습니다.", landing_text: "금리 연 6.5% ~ 15.9%. 한도는 심사 결과에 따라 결정됩니다." },
  "반려: 금융 필수 고지 누락": { category: "loan", headline: "직장인 신용대출 간편 신청", body: "서류 제출 없이 모바일에서 신청하세요.", cta: "신청하기" },
  "반려: 광고-랜딩 가격 불일치": { category: "ecommerce", headline: "프리미엄 요가 매트 월 9,900원 구독", body: "구독하면 매달 새 매트를 보내드려요.", cta: "구독하기",
    landing_text: "요가 매트 정기구독. 월 19,900원, 6개월 약정." },
  "사람 검토: 출처를 밝힌 1위": { category: "ecommerce", headline: "판매량 1위 (2026년 ○○리서치 조사 기준)", body: "믿고 쓰는 생활가전을 만나 보세요.", cta: "제품 보기" },
  "사람 검토: 근거 없는 긴급성": { category: "ecommerce", headline: "오늘만 이 가격! 놓치지 마세요", body: "겨울 코트 특가전을 만나 보세요.", cta: "특가 보기" },
  "반려: 질병 치료 표방": { category: "supplement", headline: "먹기만 하면 관절염 완치", body: "천연 추출물로 무릎이 가벼워집니다.", cta: "구매하기" },
};

const state = { run: null, rewrite: null };

// ---------------------------------------------------------------- DOM 도우미
function h(tag, attrs, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") e.className = v; else e.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c != null) e.append(c.nodeType ? c : document.createTextNode(String(c)));
  return e;
}
const clear = (node) => { while (node.firstChild) node.removeChild(node.firstChild); };

// ---------------------------------------------------------------- API
function token() {
  const role = $("#role").value;
  return role === "custom" ? $("#token").value.trim() : role;
}
// crypto.randomUUID 는 보안 컨텍스트(https, localhost)에서만 있다. 다른 주소로 열면 없으므로 getRandomValues 로 대신한다(독립 리뷰 L3).
function newKey() {
  if (globalThis.crypto && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const b = new Uint8Array(16);
  (globalThis.crypto || { getRandomValues: (a) => a.map(() => Math.floor(Math.random() * 256)) }).getRandomValues(b);
  return Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token()}`, ...(opts.headers || {}) },
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const e = new Error((data && (data.title || data.detail)) || `HTTP ${res.status}`);
    e.problem = data; e.status = res.status;
    throw e;
  }
  return data;
}
function showError(err) {
  const box = $("#error");
  if (!err) { box.hidden = true; return; }
  let msg = err.message;
  if (err.problem && err.problem.errors) msg += " — " + err.problem.errors.map((x) => x.loc.join(".")).join(", ");
  box.textContent = msg; box.hidden = false;
}

// ---------------------------------------------------------------- 폼
function readForm() {
  const c = {
    ad_id: "console-" + Date.now(),
    advertiser: { name: "콘솔 광고주", category: $("#category").value, verified: $("#verified").checked },
  };
  for (const [f] of FIELDS) {
    const v = $("#" + f).value;
    c[f] = OPTIONAL.has(f) ? (v.trim() === "" ? null : v) : v;
  }
  return c;
}
function fillForm(c) {
  $("#category").value = c.category || (c.advertiser && c.advertiser.category) || "app";
  $("#verified").checked = c.advertiser ? c.advertiser.verified : true;
  for (const [f] of FIELDS) $("#" + f).value = c[f] || "";
}

// ---------------------------------------------------------------- 하이라이트 (파이썬 오프셋은 코드포인트 기준)
function highlighted(text, findings) {
  const cps = Array.from(text);
  const spans = findings
    .filter((f) => f.start != null && f.end != null && f.severity !== "info")
    .sort((a, b) => a.start - b.start);
  const out = [];
  let pos = 0;
  for (const f of spans) {
    if (f.start < pos) continue; // 겹침은 건너뛴다
    if (f.start > pos) out.push(cps.slice(pos, f.start).join(""));
    const m = h("mark", { class: f.severity, title: `${f.rule_id} · ${f.code}` }, cps.slice(f.start, f.end).join(""));
    out.push(m);
    pos = f.end;
  }
  if (pos < cps.length) out.push(cps.slice(pos).join(""));
  return out;
}

// ---------------------------------------------------------------- 렌더
const LABEL = { APPROVE: ["승인", "approve"], REJECT: ["반려", "reject"], REVIEW: ["사람 검토 필요", "review"] };

function renderRun(run) {
  state.run = run;
  $("#empty").hidden = true; $("#result").hidden = false;
  const final = run.final_outcome;
  const [label, cls] = LABEL[final];
  const badge = $("#badge");
  badge.textContent = run.decided_by === "human" ? `${label} (사람 결정)` : label;
  badge.className = "badge " + cls;
  $("#state").textContent = run.state === "waiting_input" ? "사람 검토 대기 중" : run.decided_by === "human" ? "사람이 결정함" : "자동 판정";

  const fields = $("#fields"); clear(fields);
  for (const [f, name] of FIELDS) {
    const text = run.creative[f];
    if (!text) continue;
    const list = run.result.findings.filter((x) => x.field === f);
    fields.append(h("div", { class: "field" }, h("h4", {}, name), h("p", {}, highlighted(text, list))));
  }

  const ul = $("#reasons"); clear(ul);
  if (run.result.reasons.length === 0) ul.append(h("li", { class: "muted" }, "발견된 위반이 없습니다."));
  for (const r of run.result.reasons) {
    ul.append(h("li", { class: r.severity },
      h("div", {}, h("span", { class: "rid" }, `${r.rule_id} ${r.title}`), r.quote ? [" — ", h("span", { class: "q" }, `“${r.quote}”`)] : " — (광고 전체)"),
      h("div", {}, r.message),
      h("div", { class: "fix" }, "수정 방법: " + r.fix)));
  }
  $("#rewrite").hidden = run.final_outcome === "APPROVE";
  $("#rewriteBox").hidden = true; state.rewrite = null;
  loadEvents(run.run_id);
  updateReviewBox();
}

async function loadQueue() {
  const box = $("#queueBox");
  if (state.role !== "reviewer") { box.hidden = true; return; }
  try {
    const { items } = await api("/v1/reviews?state=waiting_input&limit=200");
    const ul = $("#queue"); clear(ul);
    if (items.length === 0) ul.append(h("li", { class: "muted" }, "대기 중인 건이 없습니다."));
    for (const it of items) {
      const btn = h("button", { type: "button" },
        h("span", {}, it.creative.headline || "(제목 없음)"),
        h("span", { class: "meta" }, `${it.submitter} · ${it.created_at.slice(11, 19)}`));
      btn.addEventListener("click", () => renderRun(it));
      ul.append(h("li", {}, btn));
    }
    box.hidden = false;
  } catch (e) { box.hidden = true; }
}

function updateReviewBox() {
  const run = state.run;
  $("#reviewBox").hidden = !(run && run.state === "waiting_input" && state.role === "reviewer");
}

async function loadEvents(id) {
  try {
    const { events } = await api(`/v1/reviews/${id}/events`);
    const ol = $("#events"); clear(ol);
    for (const e of events) {
      let detail = "";
      if (e.type === "stage") detail = ` ${e.payload.stage}` + (e.payload.count != null ? ` (${e.payload.count})` : "");
      else if (e.type === "resumed") detail = ` ${e.payload.reviewer}: ${e.payload.verdict}`;
      else if (e.payload.outcome) detail = ` ${e.payload.outcome}`;
      ol.append(h("li", {}, `${e.type}${detail}`));
    }
    $("#trace").hidden = false;
  } catch (_) { $("#trace").hidden = true; }
}

function renderRewrite(r) {
  const box = $("#rewriteBox"); clear(box);
  const statusText = { verified: "검증됨", partial: "부분 수정", unavailable: "자동 수정 불가" }[r.status];
  const cls = r.status === "verified" ? "approve" : r.status === "partial" ? "review" : "reject";
  box.append(h("div", { class: "head" }, h("h3", { class: "flush" }, "수정안"), h("span", { class: "badge " + cls }, statusText)));
  if (r.status === "unavailable") {
    box.append(h("p", { class: "muted" }, "자동으로 고칠 수 없는 위반입니다. 광고주가 직접 문구를 바꿔야 합니다."));
    if (r.unresolved.length) box.append(h("p", {}, "남은 위반: " + r.unresolved.join(", ")));
  } else {
    const diff = h("div", { class: "diff" });
    for (const a of r.applied) diff.append(h("div", {}, `${a.field}: `, h("del", {}, a.before || "(없음)"), " → ", h("ins", {}, a.after || "(삭제)")));
    box.append(diff);
    const v = r.verification;
    box.append(h("p", { class: "muted" },
      `재심사 결과 ${v.outcome_after} · 단어 보존 ${(v.retention * 100).toFixed(0)}% · 사라진 수치 ${v.lost_claims.length} · 새 수치 ${v.new_claims.length}`
      + (v.thin_headline ? " · 제목이 너무 짧아짐" : "")));
    if (r.unresolved.length) box.append(h("p", {}, "남은 위반: " + r.unresolved.join(", ")));
    box.append(h("div", { class: "actions" }, h("button", { id: "useRewrite", type: "button" }, "수정안을 폼에 채우기")));
    $("#useRewrite").addEventListener("click", () => fillForm(r.candidate));
  }
  box.hidden = false;
}

// ---------------------------------------------------------------- 이벤트
async function refreshMe() {
  try {
    const me = await api("/v1/me");
    state.role = me.role;
    $("#me").textContent = `${me.identity} (${me.role === "reviewer" ? "심사자" : "광고주"})`;
  } catch (e) { state.role = null; $("#me").textContent = "인증 실패: " + e.message; }
  updateReviewBox();
  loadQueue();
}

async function onSubmit() {
  showError(null);
  const btn = $("#submit"); btn.disabled = true;
  try {
    const run = await api("/v1/reviews", {
      method: "POST", body: JSON.stringify(readForm()),
      headers: { "Idempotency-Key": newKey() },
    });
    renderRun(run);
  } catch (e) { showError(e); } finally { btn.disabled = false; }
}

async function onRewrite() {
  if (!state.run) return;
  const btn = $("#rewrite"); btn.disabled = true;
  try { renderRewrite(await api(`/v1/reviews/${state.run.run_id}/rewrite`, { method: "POST" })); loadEvents(state.run.run_id); }
  catch (e) { showError(e); } finally { btn.disabled = false; }
}

async function onResume() {
  if (!state.run) return;
  showError(null);
  const verdict = document.querySelector("input[name=verdict]:checked").value;
  try {
    const run = await api(`/v1/reviews/${state.run.run_id}/resume`, {
      method: "POST", body: JSON.stringify({ verdict, reason: $("#reason").value }),
    });
    renderRun(run);
    loadQueue();
  } catch (e) { showError(e); }
}

function init() {
  for (const name of Object.keys(EXAMPLES)) $("#example").append(h("option", { value: name }, name));
  $("#example").addEventListener("change", (e) => { if (e.target.value) fillForm(EXAMPLES[e.target.value]); });
  $("#role").addEventListener("change", () => { $("#token").hidden = $("#role").value !== "custom"; refreshMe(); });
  $("#token").addEventListener("change", refreshMe);
  $("#submit").addEventListener("click", onSubmit);
  $("#rewrite").addEventListener("click", onRewrite);
  $("#resume").addEventListener("click", onResume);
  $("#refreshQueue").addEventListener("click", loadQueue);
  $("#clear").addEventListener("click", () => { fillForm({}); $("#example").value = ""; showError(null); });
  refreshMe();
}
document.addEventListener("DOMContentLoaded", init);
