import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const sharp = require("sharp");

const OUT = path.dirname(new URL(import.meta.url).pathname);
const C = {
  bg: "#F7F6F2",
  ink: "#132238",
  muted: "#617083",
  line: "#24364E",
  blue: "#DDE9FF",
  blueStrong: "#79A7FF",
  green: "#DDF3EA",
  greenStrong: "#58B896",
  amber: "#FFF0C7",
  amberStrong: "#E7B84B",
  purple: "#ECE5FF",
  purpleStrong: "#9B82E6",
  rose: "#FBE1E3",
  roseStrong: "#DE7D86",
  white: "#FFFFFF",
};

function esc(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function wrap(text, max = 24) {
  const words = String(text).split(/\s+/);
  const lines = [];
  let line = "";
  for (const word of words) {
    const next = line ? `${line} ${word}` : word;
    if (next.length > max && line) {
      lines.push(line);
      line = word;
    } else {
      line = next;
    }
  }
  if (line) lines.push(line);
  return lines;
}

function svgStart(w, h, title, subtitle = "") {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
  <title>${esc(title)}</title>
  <desc>${esc(subtitle)}</desc>
  <defs>
    <filter id="shadow" x="-20%" y="-20%" width="140%" height="140%">
      <feDropShadow dx="0" dy="3" stdDeviation="3" flood-color="#132238" flood-opacity="0.10"/>
    </filter>
    <marker id="arrow" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="${C.line}"/>
    </marker>
    <marker id="arrowBlue" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="${C.blueStrong}"/>
    </marker>
    <style>
      text { font-family: Inter, Arial, sans-serif; fill: ${C.ink}; }
      .title { font-size: 29px; font-weight: 700; }
      .subtitle { font-size: 16px; fill: ${C.muted}; }
      .label { font-size: 17px; font-weight: 650; }
      .small { font-size: 14px; fill: ${C.muted}; }
      .tiny { font-size: 12px; fill: ${C.muted}; }
      .lane { font-size: 15px; font-weight: 650; }
      .seq { font-size: 13px; }
    </style>
  </defs>
  <rect width="${w}" height="${h}" fill="${C.bg}"/>
  <text x="48" y="50" class="title">${esc(title)}</text>
  ${subtitle ? `<text x="48" y="78" class="subtitle">${esc(subtitle)}</text>` : ""}`;
}

function svgEnd() {
  return "</svg>";
}

function roundedBox(x, y, w, h, label, fill, opts = {}) {
  const lines = Array.isArray(label) ? label : wrap(label, opts.wrap ?? 24);
  const lineHeight = opts.lineHeight ?? 21;
  const fontClass = opts.fontClass ?? "label";
  const total = (lines.length - 1) * lineHeight;
  const startY = y + h / 2 - total / 2 + 6;
  const stroke = opts.stroke ?? C.line;
  const dash = opts.dash ? `stroke-dasharray="${opts.dash}"` : "";
  let out = `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${opts.rx ?? 16}" fill="${fill}" stroke="${stroke}" stroke-width="${opts.strokeWidth ?? 2}" ${dash} ${opts.shadow === false ? "" : 'filter="url(#shadow)"'}/>`;
  lines.forEach((line, i) => {
    out += `<text x="${x + w / 2}" y="${startY + i * lineHeight}" text-anchor="middle" class="${fontClass}">${esc(line)}</text>`;
  });
  if (opts.note) {
    out += `<text x="${x + w / 2}" y="${y + h - 13}" text-anchor="middle" class="tiny">${esc(opts.note)}</text>`;
  }
  return out;
}

function pill(x, y, w, label, fill = C.white, stroke = C.line) {
  return `<rect x="${x}" y="${y}" width="${w}" height="30" rx="15" fill="${fill}" stroke="${stroke}" stroke-width="1.5"/>
  <text x="${x + w / 2}" y="${y + 20}" text-anchor="middle" class="small">${esc(label)}</text>`;
}

function arrow(x1, y1, x2, y2, label = "", opts = {}) {
  const color = opts.color ?? C.line;
  const dash = opts.dash ? `stroke-dasharray="${opts.dash}"` : "";
  const marker = opts.blue ? "arrowBlue" : "arrow";
  let out = `<path d="M ${x1} ${y1} L ${x2} ${y2}" fill="none" stroke="${color}" stroke-width="${opts.width ?? 2.2}" stroke-linecap="round" ${dash} marker-end="url(#${marker})"/>`;
  if (label) {
    const mx = (x1 + x2) / 2;
    const my = (y1 + y2) / 2 - 8;
    out += `<rect x="${mx - Math.max(36, label.length * 3.8)}" y="${my - 12}" width="${Math.max(72, label.length * 7.6)}" height="19" rx="9" fill="${C.bg}"/>
    <text x="${mx}" y="${my + 2}" text-anchor="middle" class="tiny">${esc(label)}</text>`;
  }
  return out;
}

function elbowArrow(points, label = "", opts = {}) {
  const color = opts.color ?? C.line;
  const d = points.map((p, i) => `${i ? "L" : "M"} ${p[0]} ${p[1]}`).join(" ");
  const dash = opts.dash ? `stroke-dasharray="${opts.dash}"` : "";
  let out = `<path d="${d}" fill="none" stroke="${color}" stroke-width="${opts.width ?? 2.2}" stroke-linecap="round" stroke-linejoin="round" ${dash} marker-end="url(#arrow)"/>`;
  if (label) {
    const p = points[Math.floor(points.length / 2)];
    out += `<text x="${p[0] + 8}" y="${p[1] - 9}" class="tiny">${esc(label)}</text>`;
  }
  return out;
}

function group(x, y, w, h, label, fill = "none", stroke = C.line) {
  return `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="20" fill="${fill}" stroke="${stroke}" stroke-width="2" stroke-dasharray="8 7"/>
  <rect x="${x + 18}" y="${y - 14}" width="${Math.max(110, label.length * 9)}" height="28" rx="14" fill="${C.bg}"/>
  <text x="${x + 31}" y="${y + 5}" class="lane">${esc(label)}</text>`;
}

function actor(x, y, w, label, fill, endY = 760) {
  return `${roundedBox(x - w / 2, y, w, 54, label, fill, {wrap: 18, lineHeight: 18, fontClass: "lane", shadow: false, rx: 12})}
  <path d="M ${x} ${y + 54} L ${x} ${endY}" stroke="${C.muted}" stroke-width="1.5" stroke-dasharray="6 6"/>`;
}

function seqArrow(x1, x2, y, label, opts = {}) {
  const direction = x2 >= x1 ? 1 : -1;
  const start = x1 + direction * 8;
  const end = x2 - direction * 8;
  const color = opts.color ?? C.line;
  const dash = opts.dash ? `stroke-dasharray="6 5"` : "";
  return `<path d="M ${start} ${y} L ${end} ${y}" fill="none" stroke="${color}" stroke-width="2" ${dash} marker-end="url(#arrow)"/>
  <text x="${(x1 + x2) / 2}" y="${y - 8}" text-anchor="middle" class="seq">${esc(label)}</text>`;
}

async function write(name, svg, width = 1400) {
  const svgPath = path.join(OUT, `${name}.svg`);
  const pngPath = path.join(OUT, `${name}.png`);
  fs.writeFileSync(svgPath, svg);
  await sharp(Buffer.from(svg)).resize({ width }).png({ quality: 95 }).toFile(pngPath);
}

function planes() {
  let s = svgStart(1400, 900, "Convoy system planes", "One product experience, five explicit responsibility boundaries");
  s += group(42, 110, 1316, 128, "CONTROL PLANE", C.blue);
  s += roundedBox(80, 145, 220, 64, "Mission Control UI", C.white);
  s += roundedBox(350, 145, 220, 64, "Next.js APIs", C.white);
  s += roundedBox(620, 145, 240, 64, "Deployment Compiler", C.white);
  s += roundedBox(1080, 145, 220, 64, "Postgres", C.white, {note: "product + governance"});
  s += arrow(300, 177, 350, 177);
  s += arrow(570, 177, 620, 177);
  s += arrow(860, 177, 1080, 177, "records");

  s += group(42, 276, 1316, 128, "ORCHESTRATION PLANE", C.purple);
  s += roundedBox(120, 310, 260, 66, "Temporal Cloud", C.white, {note: "histories • timers • signals"});
  s += roundedBox(520, 310, 300, 66, "Convoy Coordinator", C.white, {note: "MissionWorkflow • AgentWorkflow"});
  s += roundedBox(1020, 310, 250, 66, "Mission Scheduler", C.white, {note: "deadline • budget • admission"});
  s += arrow(380, 343, 520, 343, "workflow tasks");
  s += arrow(820, 343, 1020, 343, "envelope");
  s += elbowArrow([[760,310],[760,244],[740,244],[740,209]], "start mission");

  s += group(42, 442, 1316, 135, "COMPUTE PLANE", C.green);
  s += roundedBox(110, 478, 250, 70, ["Root Agent", "Episode"], C.white, {note: "Fargate task"});
  s += roundedBox(470, 478, 250, 70, ["Child Agent", "Episode"], C.white, {note: "Fargate / Spot"});
  s += roundedBox(830, 478, 250, 70, ["Child Agent", "Episode"], C.white, {note: "Fargate / Spot"});
  s += roundedBox(1160, 490, 145, 46, "… N", C.white, {shadow: false});
  s += elbowArrow([[670,376],[670,430],[235,430],[235,478]], "RunTask");
  s += elbowArrow([[690,430],[595,430],[595,478]]);
  s += elbowArrow([[710,430],[955,430],[955,478]]);

  s += group(42, 617, 1316, 110, "POLICY + TOOL PLANE", C.amber);
  s += roundedBox(125, 648, 260, 54, "Policy / Model Gateway", C.white);
  s += roundedBox(525, 648, 210, 54, "Approval Service", C.white);
  s += roundedBox(860, 648, 210, 54, "Model Providers", C.white);
  s += roundedBox(1110, 648, 210, 54, "Enterprise Systems", C.white);
  s += arrow(385, 675, 525, 675, "gate");
  s += arrow(385, 675, 860, 675, "approved");
  s += arrow(1070, 675, 1110, 675);
  s += elbowArrow([[235,548],[235,648]], "all calls");
  s += elbowArrow([[595,548],[595,612],[255,612],[255,648]]);
  s += elbowArrow([[955,548],[955,612],[275,612],[275,648]]);

  s += group(42, 765, 1316, 95, "DATA PLANE", C.rose);
  s += roundedBox(150, 790, 250, 46, "S3 Mission Workspace", C.white, {shadow: false});
  s += roundedBox(560, 790, 250, 46, "DynamoDB Projection", C.white, {shadow: false});
  s += roundedBox(970, 790, 250, 46, "Audit + Metrics", C.white, {shadow: false});
  s += arrow(400, 813, 560, 813, "events");
  s += arrow(810, 813, 970, 813, "stream");
  return s + svgEnd();
}

function missionSequence() {
  let s = svgStart(1500, 1040, "Mission lifecycle — from request to evaluated result", "UML-style sequence: durable control, disposable compute");
  const xs = [105, 300, 505, 710, 930, 1140, 1350];
  const labels = ["User / Trigger", "Control Plane", "Temporal", "Coordinator", "Fargate Agent", "Gateway", "S3 + Projection"];
  const fills = [C.blue, C.blue, C.purple, C.purple, C.green, C.amber, C.rose];
  xs.forEach((x, i) => { s += actor(x, 105, 150, labels[i], fills[i], 970); });
  let y = 205;
  s += seqArrow(xs[0], xs[1], y, "1  create mission");
  y += 65; s += seqArrow(xs[1], xs[6], y, "2  persist spec + inputs");
  y += 65; s += seqArrow(xs[1], xs[2], y, "3  start MissionWorkflow");
  y += 65; s += seqArrow(xs[2], xs[3], y, "4  plan envelope");
  y += 65; s += seqArrow(xs[3], xs[4], y, "5  ECS RunTask");
  y += 65; s += seqArrow(xs[4], xs[6], y, "6  load checkpoint / workspace", {dash:true});
  y += 65; s += seqArrow(xs[4], xs[5], y, "7  model + tool action");
  y += 65; s += seqArrow(xs[5], xs[4], y, "8  approved result", {dash:true});
  y += 65; s += seqArrow(xs[4], xs[6], y, "9  checkpoint + artifacts");
  y += 65; s += seqArrow(xs[4], xs[3], y, "10  signed episode result");
  y += 65; s += seqArrow(xs[3], xs[2], y, "11  child / wait / complete");
  y += 65; s += seqArrow(xs[2], xs[1], y, "12  evaluated terminal outcome");
  s += `<rect x="250" y="172" width="1180" height="798" rx="20" fill="none" stroke="${C.blueStrong}" stroke-width="2" stroke-dasharray="10 8"/>
  <text x="270" y="1012" class="small">Temporal persists continuity; Fargate exists only during active episodes.</text>`;
  return s + svgEnd();
}

function recursiveTree() {
  let s = svgStart(1400, 860, "Recursive agents with bounded physical execution", "Agents may delegate freely; the scheduler controls what runs now");
  s += roundedBox(545, 110, 310, 76, ["Root: Renewal", "Commander"], C.purple, {note:"durable AgentWorkflow"});
  const supervisors = [
    [150, 280, "Risk Research", C.blue],
    [480, 280, "Account Operations", C.green],
    [810, 280, "Commercial Analysis", C.amber],
    [1140, 280, "Executive Comms", C.rose],
  ];
  supervisors.forEach(([x,y,l,f]) => {
    s += roundedBox(x - 120, y, 240, 68, l, f, {note:"child Agent"});
    s += arrow(700, 186, x, y, "", {width:2});
  });
  const leaves = [
    [95, 470, "Health score"], [245, 470, "Support history"],
    [420, 470, "Owner tasks"], [570, 470, "Adoption plan"],
    [750, 470, "ARR exposure"], [900, 470, "Forecast model"],
    [1080, 470, "Brief writer"], [1230, 470, "Customer note"],
  ];
  leaves.forEach(([x,y,l], i) => {
    const parent = supervisors[Math.floor(i/2)];
    s += roundedBox(x - 65, y, 130, 56, l, C.white, {fontClass:"small",wrap:14,shadow:false,rx:12});
    s += arrow(parent[0], 348, x, y, "", {width:1.8});
  });
  s += group(85, 610, 1230, 190, "MISSION SCHEDULER — PHYSICAL ADMISSION", "none", C.purpleStrong);
  s += roundedBox(130, 660, 250, 80, ["12 active", "Fargate episodes"], C.green, {note:"consumes concurrency"});
  s += roundedBox(475, 660, 250, 80, ["28 pending", "logical Agents"], C.purple, {note:"zero compute"});
  s += roundedBox(820, 660, 200, 80, ["$ budget", "tokens"], C.amber, {note:"shared envelope"});
  s += roundedBox(1090, 660, 180, 80, ["deadline", "slack"], C.rose, {note:"critical path"});
  s += arrow(380, 700, 475, 700, "completion returns slots");
  s += `<text x="700" y="830" text-anchor="middle" class="subtitle">Logical recursion is open-ended. Active compute is always bounded.</text>`;
  return s + svgEnd();
}

function scaleToZero() {
  let s = svgStart(1450, 920, "Weeks-long work without weeks-long compute", "An AgentWorkflow persists while bounded Agent Episodes come and go");
  const xs = [160, 440, 730, 1010, 1290];
  const labels = ["AgentWorkflow", "Fargate Episode A", "S3 Checkpoint", "Timer / Webhook", "Fargate Episode B"];
  const fills = [C.purple, C.green, C.rose, C.blue, C.green];
  xs.forEach((x,i)=>{ s += actor(x, 110, 185, labels[i], fills[i], 865); });
  let y = 220;
  s += seqArrow(xs[0], xs[1], y, "launch active work");
  y += 85; s += seqArrow(xs[1], xs[2], y, "persist plan + artifacts");
  y += 85; s += seqArrow(xs[1], xs[0], y, "waiting for external event");
  y += 55; s += `<rect x="320" y="${y}" width="250" height="44" rx="12" fill="${C.rose}" stroke="${C.roseStrong}" stroke-width="2"/>
  <text x="445" y="${y+28}" text-anchor="middle" class="lane">Episode A exits</text>`;
  y += 90; s += seqArrow(xs[0], xs[3], y, "register durable timer / signal");
  s += `<rect x="85" y="${y+36}" width="1040" height="82" rx="18" fill="${C.purple}" stroke="${C.purpleStrong}" stroke-width="2" stroke-dasharray="8 7"/>
  <text x="605" y="${y+69}" text-anchor="middle" class="label">Days or weeks pass</text>
  <text x="605" y="${y+95}" text-anchor="middle" class="small">Temporal state persists • zero agent tasks running • no idle compute spend</text>`;
  y += 165; s += seqArrow(xs[3], xs[0], y, "webhook or timer fires");
  y += 70; s += seqArrow(xs[0], xs[4], y, "launch from checkpoint");
  y += 70; s += seqArrow(xs[4], xs[2], y, "load + continue", {dash:true});
  return s + svgEnd();
}

function recovery() {
  let s = svgStart(1450, 900, "Interruption recovery and reconciliation", "A provider task can disappear without losing the logical Agent");
  const xs = [140, 380, 630, 880, 1120, 1340];
  const labels = ["Agent Episode", "S3", "EventBridge", "Reconciler", "Temporal", "Replacement"];
  const fills = [C.green, C.rose, C.blue, C.amber, C.purple, C.green];
  xs.forEach((x,i)=>{ s += actor(x, 110, 155, labels[i], fills[i], 840); });
  let y = 220;
  s += seqArrow(xs[0], xs[1], y, "periodic checkpoint");
  y += 80; s += seqArrow(xs[2], xs[0], y, "Spot interruption notice");
  y += 80; s += seqArrow(xs[0], xs[1], y, "final checkpoint");
  y += 70; s += `<path d="M ${xs[0]-46} ${y-22} L ${xs[0]+46} ${y+22} M ${xs[0]+46} ${y-22} L ${xs[0]-46} ${y+22}" stroke="${C.roseStrong}" stroke-width="5" stroke-linecap="round"/>
  <text x="${xs[0]}" y="${y+52}" text-anchor="middle" class="small">task exits</text>`;
  y += 90; s += seqArrow(xs[2], xs[3], y, "task STOPPED event");
  y += 75; s += seqArrow(xs[3], xs[4], y, "provider gone; retry Agent");
  y += 75; s += seqArrow(xs[4], xs[5], y, "launch new episode");
  y += 75; s += seqArrow(xs[5], xs[1], y, "load latest valid checkpoint", {dash:true});
  y += 55; s += `<text x="725" y="${y}" text-anchor="middle" class="subtitle">Same agent_id • new episode_id • no duplicate terminal state</text>`;
  return s + svgEnd();
}

function awsTopology() {
  let s = svgStart(1450, 930, "AWS deployment topology", "Private runners, explicit gateways, managed durable orchestration");
  s += roundedBox(50, 130, 220, 58, "Users + Webhooks", C.blue);
  s += roundedBox(1180, 130, 220, 58, "Temporal Cloud", C.purple, {note:"outside VPC"});
  s += group(45, 235, 1360, 630, "AWS ACCOUNT • us-west-2 • VPC ACROSS 2 AZs", "none", C.blueStrong);
  s += group(80, 285, 1290, 95, "PUBLIC SUBNETS", C.blue);
  s += roundedBox(135, 315, 260, 46, "Application Load Balancer", C.white, {shadow:false});
  s += roundedBox(530, 315, 260, 46, "Webhook Ingress", C.white, {shadow:false});
  s += roundedBox(970, 315, 260, 46, "NAT only if required", C.white, {shadow:false});
  s += arrow(270, 159, 265, 315, "TLS");
  s += arrow(160, 188, 660, 315, "signed events");

  s += group(80, 425, 1290, 155, "PRIVATE APPLICATION SUBNETS", C.purple);
  s += roundedBox(120, 468, 245, 70, "Control Plane", C.white, {note:"Next.js + product APIs"});
  s += roundedBox(445, 468, 245, 70, "Coordinator", C.white, {note:"Temporal workers"});
  s += roundedBox(770, 468, 245, 70, "Policy / Model Gateway", C.white, {note:"only external action path"});
  s += roundedBox(1095, 468, 220, 70, "Reconciler", C.white, {note:"ECS task state"});
  s += arrow(395, 338, 242, 468);
  s += arrow(790, 338, 242, 468);
  s += elbowArrow([[1300,159],[1030,159],[1030,425],[568,425],[568,468]], "outbound workflow poll");

  s += group(80, 625, 835, 155, "RESTRICTED RUNNER SUBNETS", C.green);
  s += roundedBox(120, 670, 220, 70, "Root Episode", C.white, {note:"no public IP"});
  s += roundedBox(390, 670, 220, 70, "Child Episodes", C.white, {note:"Fargate / Spot"});
  s += roundedBox(660, 670, 210, 70, "Hermetic World", C.white, {note:"eval only"});
  s += elbowArrow([[568,538],[568,605],[230,605],[230,670]], "ECS RunTask");
  s += elbowArrow([[588,605],[500,605],[500,670]]);
  s += arrow(610, 705, 770, 515, "all tools");
  s += arrow(340, 705, 770, 515);

  s += group(960, 625, 410, 155, "AWS PRIVATE ENDPOINTS", C.rose);
  s += roundedBox(990, 665, 150, 50, "S3", C.white, {shadow:false});
  s += roundedBox(1170, 665, 150, 50, "DynamoDB", C.white, {shadow:false});
  s += roundedBox(990, 725, 150, 38, "ECR + Logs", C.white, {shadow:false,fontClass:"small"});
  s += roundedBox(1170, 725, 150, 38, "Secrets + KMS", C.white, {shadow:false,fontClass:"small"});
  s += arrow(870, 705, 990, 690);
  s += arrow(870, 720, 1170, 690);
  s += roundedBox(1070, 835, 280, 54, "Enterprise Systems + Models", C.amber);
  s += elbowArrow([[895,538],[895,810],[1210,810],[1210,835]], "approved egress");
  s += `<text x="720" y="905" text-anchor="middle" class="small">Runner security groups: no inbound rules • egress only to gateway and approved AWS endpoints</text>`;
  return s + svgEnd();
}

function stateOwnership() {
  let s = svgStart(1400, 820, "One authority per kind of state", "Avoid split-brain orchestration by separating product, workflow, artifact, and projection data");
  s += roundedBox(530, 110, 340, 76, "Convoy Mission", C.blue, {note:"stable mission_id"});
  const stores = [
    [90, 330, 260, 120, "Postgres", C.blue, ["Product configuration", "Governance records", "Canonical Mission record"]],
    [405, 330, 260, 120, "Temporal", C.purple, ["Workflow progress", "Timers + signals", "Parent / child state"]],
    [720, 330, 260, 120, "S3", C.rose, ["Inputs + artifacts", "Checkpoints", "Immutable result bundle"]],
    [1035, 330, 260, 120, "DynamoDB", C.green, ["Live agent tree", "Recent events", "Rebuildable projection"]],
  ];
  stores.forEach(([x,y,w,h,l,f,notes]) => {
    s += `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="16" fill="${f}" stroke="${C.line}" stroke-width="2" filter="url(#shadow)"/>
    <text x="${x+w/2}" y="${y+34}" text-anchor="middle" class="label">${esc(l)}</text>`;
    notes.forEach((n,i)=>{s += `<text x="${x+w/2}" y="${y+63+i*18}" text-anchor="middle" class="tiny">${esc(n)}</text>`;});
    s += arrow(700,186,x+w/2,y,l==="DynamoDB"?"project":"");
  });
  s += group(180, 565, 1040, 160, "CONSISTENCY + RECOVERY", "none", C.line);
  s += pill(235, 610, 210, "idempotency keys", C.white);
  s += pill(475, 610, 210, "conditional writes", C.white);
  s += pill(715, 610, 210, "sequence-aware events", C.white);
  s += pill(955, 610, 210, "reconciliation", C.white);
  s += `<text x="700" y="690" text-anchor="middle" class="label">DynamoDB can be rebuilt. Temporal is never reconstructed from UI state.</text>
  <text x="700" y="770" text-anchor="middle" class="subtitle">Pointers cross stores; large payloads do not.</text>`;
  return s + svgEnd();
}

function policySequence() {
  let s = svgStart(1500, 990, "Every consequential action crosses the gateway", "UML-style sequence for authorization, credentials, approval, execution, and audit");
  const xs = [105, 300, 500, 700, 900, 1110, 1340];
  const labels = ["Agent Runner", "Gateway", "Policy Engine", "Approval", "Vault", "Connector", "Audit + Cost"];
  const fills = [C.green, C.amber, C.amber, C.rose, C.purple, C.blue, C.rose];
  xs.forEach((x,i)=>{s += actor(x,105,155,labels[i],fills[i],925);});
  let y = 215;
  s += seqArrow(xs[0],xs[1],y,"1  tool + args + capability");
  y+=65; s += seqArrow(xs[1],xs[2],y,"2  evaluate pinned policy");
  y+=65; s += seqArrow(xs[2],xs[1],y,"allow / deny / require approval",{dash:true});
  y+=65; s += seqArrow(xs[1],xs[3],y,"3  create approval request");
  y+=65; s += seqArrow(xs[3],xs[1],y,"approved or edited args",{dash:true});
  y+=65; s += seqArrow(xs[1],xs[4],y,"4  resolve credential reference");
  y+=65; s += seqArrow(xs[4],xs[1],y,"short-lived use inside gateway",{dash:true});
  y+=65; s += seqArrow(xs[1],xs[5],y,"5  perform approved call");
  y+=65; s += seqArrow(xs[5],xs[1],y,"bounded + sanitized response",{dash:true});
  y+=65; s += seqArrow(xs[1],xs[6],y,"6  immutable audit + usage");
  y+=65; s += seqArrow(xs[1],xs[0],y,"7  result, never credentials",{dash:true});
  s += `<rect x="220" y="170" width="1190" height="755" rx="20" fill="none" stroke="${C.amberStrong}" stroke-width="2" stroke-dasharray="10 8"/>
  <text x="815" y="965" text-anchor="middle" class="small">The runner can ask. Only the gateway can authorize and act.</text>`;
  return s + svgEnd();
}

function evalLoop() {
  let s = svgStart(1400, 850, "Hermetic evaluation and certified promotion", "The exact behavior-determining release tuple must pass before Production");
  s += roundedBox(75, 125, 250, 76, "Draft Agent Version", C.blue, {note:"prompt • model • grants"});
  s += roundedBox(415, 125, 250, 76, "Pinned Deployment", C.purple, {note:"policy • tools • runtime"});
  s += roundedBox(755, 125, 250, 76, "Hermetic World", C.green, {note:"isolated seeded state"});
  s += roundedBox(1095, 125, 230, 76, "Parallel Eval Runs", C.green, {note:"scripted approvals"});
  s += arrow(325,163,415,163);
  s += arrow(665,163,755,163);
  s += arrow(1005,163,1095,163);

  s += group(135, 315, 1130, 180, "LAYERED GRADERS", "none", C.purpleStrong);
  s += roundedBox(180, 360, 220, 78, ["End-state", "assertions"], C.blue, {note:"deterministic"});
  s += roundedBox(450, 360, 220, 78, ["Trajectory", "assertions"], C.amber, {note:"tools + arguments"});
  s += roundedBox(720, 360, 220, 78, ["Pinned LLM", "rubric"], C.purple, {note:"fuzzy quality"});
  s += roundedBox(990, 360, 220, 78, ["Human", "review"], C.rose, {note:"uncertain cases"});
  s += elbowArrow([[1210,201],[1210,285],[700,285],[700,315]], "evidence");

  s += roundedBox(185, 610, 310, 94, ["Certified Release", "Tuple"], C.green, {note:"all behavior inputs hashed"});
  s += roundedBox(545, 610, 310, 94, ["Promotion Gate", "exact tuple match"], C.amber, {note:"no stale certification"});
  s += roundedBox(905, 610, 310, 94, ["Production", "Deployment"], C.blue, {note:"canary • rollback"});
  s += elbowArrow([[700,495],[700,560],[340,560],[340,610]], "pass");
  s += arrow(495,657,545,657);
  s += arrow(855,657,905,657);
  s += elbowArrow([[700,495],[700,530],[200,530],[200,201]], "fail → revise", {dash:"7 6"});
  s += `<text x="700" y="785" text-anchor="middle" class="subtitle">Any change to model, prompt, tools, policy, evals, gateway, or runtime invalidates certification.</text>`;
  return s + svgEnd();
}

function dynamicExecutionGraph() {
  let s = svgStart(
    1450,
    930,
    "Static durable runtime, dynamic execution graph",
    "Compilation fixes the mission envelope; agents continuously discover the plan",
  );

  s += group(45, 120, 360, 690, "PREDEPLOYED TEMPORAL RUNTIME", C.purple);
  s += roundedBox(90, 170, 270, 78, "MissionWorkflow", C.white, {
    note: "budget • deadline • admission",
  });
  s += roundedBox(90, 300, 270, 78, "AgentWorkflow", C.white, {
    note: "checkpoint • children • mailbox",
  });
  s += roundedBox(90, 430, 270, 92, ["Deterministic", "intent interpreter"], C.white, {
    note: "versioned orchestration code",
  });
  s += roundedBox(90, 590, 270, 120, ["MissionSpec", "execution envelope"], C.blue, {
    note: "objective • policy • limits • refs",
  });
  s += arrow(225, 590, 225, 522, "starts with");

  s += group(455, 120, 470, 690, "ROLLING-HORIZON AGENT LOOP", C.green);
  s += roundedBox(520, 175, 340, 84, ["Launch bounded", "Agent Episode"], C.white, {
    note: "model + tools run in an Activity",
  });
  s += roundedBox(520, 325, 340, 98, ["Return typed", "next intent"], C.amber, {
    note: "result is recorded in history",
  });
  s += pill(505, 475, 170, "use_tool", C.white);
  s += pill(700, 475, 170, "spawn_agents", C.white);
  s += pill(505, 535, 170, "wait", C.white);
  s += pill(700, 535, 170, "replan", C.white);
  s += pill(505, 595, 170, "request_approval", C.white);
  s += pill(700, 595, 170, "complete", C.white);
  s += arrow(690, 259, 690, 325, "episode result");
  s += elbowArrow([[520, 374], [475, 374], [475, 217], [520, 217]]);
  s += elbowArrow([[405, 338], [455, 338], [455, 217], [520, 217]], "schedule");

  s += group(975, 120, 430, 690, "EMERGENT MISSION GRAPH", C.blue);
  s += roundedBox(1090, 170, 200, 68, "Root Agent", C.purple, {
    note: "known at start",
  });
  s += roundedBox(1005, 340, 170, 68, "Research Agent", C.blue, {
    note: "created at runtime",
  });
  s += roundedBox(1205, 340, 170, 68, "Ops Agent", C.green, {
    note: "created at runtime",
  });
  s += roundedBox(1005, 530, 170, 68, "Evidence Agent", C.amber, {
    note: "created later",
  });
  s += roundedBox(1205, 530, 170, 68, "Synthesis Agent", C.rose, {
    note: "created later",
  });
  s += arrow(1190, 238, 1090, 340);
  s += arrow(1190, 238, 1290, 340);
  s += arrow(1090, 408, 1090, 530);
  s += arrow(1290, 408, 1290, 530);
  s += elbowArrow([[860, 374], [950, 374], [950, 204], [1090, 204]], "admitted spawn");
  s += `<path d="M 1010 665 C 1080 620, 1260 620, 1370 680" fill="none" stroke="${C.blueStrong}" stroke-width="2" stroke-dasharray="8 7"/>
  <text x="1190" y="715" text-anchor="middle" class="label">Not known when the mission starts</text>
  <text x="1190" y="744" text-anchor="middle" class="small">The graph materializes from recorded episode decisions.</text>`;

  s += `<rect x="130" y="850" width="1190" height="50" rx="18" fill="${C.rose}" stroke="${C.roseStrong}" stroke-width="2"/>
  <text x="725" y="882" text-anchor="middle" class="label">Plans are mutable data. Agents never generate or deploy new Temporal workflow code mid-mission.</text>`;
  return s + svgEnd();
}

await write("01-convoy-system-planes", planes(), 1400);
await write("02-mission-lifecycle-sequence", missionSequence(), 1500);
await write("03-recursive-agent-admission", recursiveTree(), 1400);
await write("04-scale-to-zero-sequence", scaleToZero(), 1450);
await write("05-interruption-recovery-sequence", recovery(), 1450);
await write("06-aws-deployment-topology", awsTopology(), 1450);
await write("07-state-ownership", stateOwnership(), 1400);
await write("08-policy-gateway-sequence", policySequence(), 1500);
await write("09-hermetic-eval-promotion", evalLoop(), 1400);
await write("10-static-runtime-dynamic-graph", dynamicExecutionGraph(), 1450);

const contactNames = [
  "01-convoy-system-planes",
  "02-mission-lifecycle-sequence",
  "03-recursive-agent-admission",
  "04-scale-to-zero-sequence",
  "05-interruption-recovery-sequence",
  "06-aws-deployment-topology",
  "07-state-ownership",
  "08-policy-gateway-sequence",
  "09-hermetic-eval-promotion",
  "10-static-runtime-dynamic-graph",
];
const contactWidth = 1800;
const cellWidth = 600;
const cellHeight = 390;
const contactLayers = [];
for (let i = 0; i < contactNames.length; i += 1) {
  const thumb = await sharp(path.join(OUT, `${contactNames[i]}.png`))
    .resize({ width: 570, height: 355, fit: "contain", background: C.bg })
    .png()
    .toBuffer();
  contactLayers.push({
    input: thumb,
    left: (i % 3) * cellWidth + 15,
    top: Math.floor(i / 3) * cellHeight + 15,
  });
}
await sharp({
  create: {
    width: contactWidth,
    height: cellHeight * Math.ceil(contactNames.length / 3),
    channels: 4,
    background: C.bg,
  },
}).composite(contactLayers).png().toFile(path.join(OUT, "contact-sheet.png"));

console.log("Generated 10 SVG and PNG architecture diagrams plus contact sheet.");
