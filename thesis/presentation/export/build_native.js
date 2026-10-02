// Build an EDITABLE PowerPoint deck from deck.json (produced by parse_deck.py).
//
// Text, bullets and tables become real PowerPoint objects. Only the things
// PowerPoint has no native form for stay images: the ten thesis TikZ figures,
// the twelve display equations and the one inline diagram.
//
// Layout is measured, not guessed: each slide's blocks are stacked, their height
// estimated, and the body font stepped down until everything fits the text area.
// That is what keeps 56 slides free of overflow without hand-tuning each one.
//
// Run:  node thesis/presentation/export/build_native.js
const fs = require("fs");
const path = require("path");

const HERE = __dirname;
const DECK = path.resolve(HERE, "..");
const FIGS = path.join(HERE, "figs");
const OUT = path.join(DECK, "main-editable.pptx");

let pptxgen;
try { pptxgen = require("pptxgenjs"); }
catch { pptxgen = require(path.join(HERE, "node_modules", "pptxgenjs")); }
const { applyTheme } = require(path.join(
  process.env.PPTX_SKILL ||
  "C:/Users/KiraiEEE/.claude/skills/synced/228ae02b-0ff5-4243-ad93-d35f8bddc0fa_ec685b54-4c6c-4719-ae6d-094b1fa93d37/pptx",
  "scripts/apply_theme.js"));

// ---------------------------------------------------------------- theme
// The deck's own identity, taken from main.tex's \definecolor lines.
const THEME = {
  name: "ISSAT Defence",
  headFontFace: "Calibri",
  bodyFontFace: "Calibri",
  colors: {
    dk1: "262628", lt1: "FFFFFF",       // text1 / background1
    dk2: "072D5A", lt2: "DFE8F2",       // issatblue / lightblue
    accent1: "072D5A", accent2: "B08D3C", accent3: "1F77B4",
    accent4: "FF7F0E", accent5: "2CA02C", accent6: "D62728",
    hlink: "072D5A", folHlink: "5A5A5A",
  },
};
const BLUE = "072D5A", GOLD = "B08D3C", GRAY = "5A5A5A",
      LIGHT = "DFE8F2", TEXT = "262628", GREEN = "1C7C38";
const TEXCOLOR = {              // \definecolor names used by \textcolor in the deck
  issatblue: BLUE, issatgold: GOLD, accentgray: GRAY, darktext: TEXT,
  srpcolor: "1F77B4", ocpcolor: "FF7F0E", lspcolor: "2CA02C",
  ispcolor: "D62728", dipcolor: "9467BD", goodgreen: GREEN,
  "issatgold!80!black": "8C7030", "ispcolor": "D62728",
};

// ---------------------------------------------------------------- geometry
const W = 12192000 / 914400, H = 6858000 / 914400;   // 13.3333 x 7.5 in
const M = 0.5;                                        // side margin
const BODY_W = W - 2 * M;
const BODY_TOP = 1.12, BODY_BOT = 6.98;
const BODY_H = BODY_BOT - BODY_TOP;

const slides = JSON.parse(fs.readFileSync(path.join(HERE, "deck.json"), "utf8"));
const imgSize = {};
for (const f of fs.readdirSync(FIGS).filter(f => f.endsWith(".png"))) {
  const b = fs.readFileSync(path.join(FIGS, f));
  // PNG IHDR: width/height are big-endian uint32 at offsets 16 and 20
  imgSize[path.basename(f, ".png")] = { w: b.readUInt32BE(16), h: b.readUInt32BE(20) };
}

// ---------------------------------------------------------------- text helpers
function toRuns(rs, baseOpt) {
  return (rs || []).filter(r => r.text !== "").map(r => {
    const o = Object.assign({}, baseOpt);
    if (r.b) o.bold = true;
    if (r.i) o.italic = true;
    if (r.code) o.fontFace = "Consolas";
    if (r.sc) o.bold = true;
    if (r.sm) o.fontSize = Math.max(7, (o.fontSize || 12) - 3);   // \finding source line
    if (r.color && TEXCOLOR[r.color]) o.color = TEXCOLOR[r.color];
    return { text: r.text, options: o };
  });
}
const plain = rs => (rs || []).map(r => r.text).join("");

// Rough but consistent wrap estimate: Calibri averages ~0.48em per character.
function lines(text, widthIn, fontPt) {
  const perChar = (fontPt / 72) * 0.48;
  const cap = Math.max(8, Math.floor(widthIn / perChar));
  let n = 0;
  for (const para of String(text).split("\n")) n += Math.max(1, Math.ceil(para.length / cap));
  return n;
}
const lineH = pt => (pt / 72) * 1.25;

// ---------------------------------------------------------------- measuring
// Snippet PNGs are rendered at 300 dpi from a 10pt document, so their natural
// size in inches is px/300 and scales with the body font. Sizing an equation by
// column width instead would blow it up to several times its proper height.
function eqBox(b, sz, widthIn, pt) {
  const scale = (pt / 10) / 300;
  let w = sz.w * scale, h = sz.h * scale;
  const cap = b.eq ? widthIn : widthIn;      // never wider than its region
  if (w > cap) { h *= cap / w; w = cap; }
  return { w, h };
}

function blockHeight(b, widthIn, pt) {
  switch (b.t) {
    case "text":   return lines(plain(b.runs), widthIn, pt) * lineH(pt) + 0.06;
    case "list":   return b.items.reduce((s, it) =>
                     s + lines(plain(it), widthIn - 0.22, pt) * lineH(pt) + 0.07, 0) + 0.04;
    case "table":  return tableHeight(b, widthIn, pt);
    case "image": {
      const sz = imgSize[b.name]; if (!sz) return 0;
      return eqBox(b, sz, widthIn, pt).h + 0.1;
    }
    case "figure": {
      const sz = imgSize[b.name]; if (!sz) return 0;
      return Math.min(widthIn, BODY_W) * (sz.h / sz.w) + 0.1;
    }
    case "keynum": return keynumH(b, widthIn, pt);
    case "takeaway": return lines(plain(b.runs), widthIn - 0.3, pt) * lineH(pt) + 0.3;
    default: return 0;
  }
}
// Column widths used both for measuring and for drawing. They must agree, or a
// narrow first column wraps more than the estimate predicted and the table runs
// past whatever is placed under it.
function tableCols(b, widthIn) {
  const all = b.head.concat(b.rows);
  const cols = Math.max(1, ...all.map(r => r.reduce((s, c) => s + (c.span || 1), 0)));
  const unit = widthIn / (cols + 0.6);
  const colW = [];
  for (let i = 0; i < cols; i++) colW.push(i === 0 ? unit * 1.6 : unit);
  return { cols, colW };
}

function tableHeight(b, widthIn, pt) {
  const all = b.head.concat(b.rows);
  const { colW } = tableCols(b, widthIn);
  let h = 0;
  for (const row of all) {
    let mx = 1, ci = 0;
    for (const c of row) {
      const span = c.span || 1;
      let cw = 0;
      for (let k = 0; k < span && ci + k < colW.length; k++) cw += colW[ci + k];
      ci += span;
      mx = Math.max(mx, lines(plain(c.runs), Math.max(0.3, cw - 0.12), pt));
    }
    h += mx * lineH(pt) + 0.09;
  }
  return h + 0.12;
}

// A keynum box grows with its caption, so a long one is not clipped.
function keynumH(b, widthIn, pt) {
  const w = Math.min(widthIn, 3.5);
  const capPt = Math.max(7, pt - 3);
  return 0.40 + lines(plain(b.label), w - 0.2, capPt) * lineH(capPt) + 0.12;
}

// ---------------------------------------------------------------- drawing
function drawBlocks(slide, blocks, x, y, widthIn, pt) {
  let cy = y;
  for (const b of blocks) {
    const h = blockHeight(b, widthIn, pt);
    switch (b.t) {
      case "text":
        slide.addText(toRuns(b.runs, { fontSize: pt, color: TEXT }), {
          x, y: cy, w: widthIn, h, isTextBox: true, margin: 0,
          valign: "top", fontSize: pt, color: TEXT, lineSpacingMultiple: 1.0,
        });
        break;
      case "list": {
        const items = [];
        b.items.forEach((it, k) => {
          const rs = toRuns(it, { fontSize: pt, color: TEXT });
          rs.forEach((r, j) => {
            r.options = Object.assign({}, r.options, {
              bullet: j === 0
                ? (b.ordered
                    ? { type: "number", startAt: (b.start || 1) + k, indent: 18 }
                    : { code: "2022", indent: 14 })
                : false,
              breakLine: j === rs.length - 1 && k < b.items.length - 1,
              paraSpaceAfter: j === rs.length - 1 ? 4 : 0,
            });
            items.push(r);
          });
        });
        slide.addText(items, {
          x, y: cy, w: widthIn, h, isTextBox: true, margin: 0,
          valign: "top", fontSize: pt, color: TEXT,
        });
        break;
      }
      case "table": drawTable(slide, b, x, cy, widthIn, pt); break;
      case "image":
      case "figure": {
        const sz = imgSize[b.name];
        if (!sz) break;
        const box = b.t === "image" ? eqBox(b, sz, widthIn, pt)
                                    : { w: widthIn, h: widthIn * (sz.h / sz.w) };
        const iw = box.w, ih = box.h;
        slide.addImage({
          path: path.join(FIGS, b.name + ".png"),
          x: x + (widthIn - iw) / 2, y: cy, w: iw, h: ih,
          objectName: b.name, altText: b.name.replace(/_/g, " "),
        });
        break;
      }
      case "keynum": {
        const kw = Math.min(widthIn, 3.5), capPt = Math.max(7, pt - 3);
        slide.addShape("roundRect", {
          x, y: cy, w: kw, h,
          fill: { color: LIGHT }, line: { color: BLUE, width: 1 }, rectRadius: 0.06,
        });
        slide.addText(plain(b.big), {
          x: x + 0.07, y: cy + 0.05, w: kw - 0.14, h: 0.32,
          isTextBox: true, margin: 0, align: "center", valign: "bottom",
          fontSize: Math.max(13, pt + 3), bold: true, color: BLUE,
        });
        slide.addText(plain(b.label), {
          x: x + 0.07, y: cy + 0.38, w: kw - 0.14, h: h - 0.44,
          isTextBox: true, margin: 0, align: "center", valign: "top",
          fontSize: capPt, color: GRAY,
        });
        break;
      }
      case "takeaway":
        slide.addShape("roundRect", {
          x, y: cy, w: widthIn, h, fill: { color: "F7F0DE" },
          line: { color: GOLD, width: 1 }, rectRadius: 0.05,
        });
        slide.addText(toRuns(b.runs, { fontSize: pt, color: TEXT }), {
          x: x + 0.12, y: cy + 0.08, w: widthIn - 0.24, h: h - 0.16,
          isTextBox: true, margin: 0, valign: "top", fontSize: pt, color: TEXT,
        });
        break;
    }
    cy += h + 0.07;
  }
}

function drawTable(slide, b, x, y, widthIn, pt) {
  const all = b.head.concat(b.rows);
  const cols = Math.max(1, ...all.map(r => r.reduce((s, c) => s + (c.span || 1), 0)));
  const rows = all.map((row, ri) => row.map(c => {
    const head = ri < b.head.length;
    // rule under the LAST header row only: a border on an earlier one shows up as
    // stray part-width lines under spanning cells
    const lastHead = ri === b.head.length - 1;
    return {
      text: toRuns(c.runs, { fontSize: pt, color: head ? BLUE : TEXT, bold: head || undefined }),
      options: {
        colspan: c.span > 1 ? c.span : undefined,
        align: b.align[Math.min(row.indexOf(c), b.align.length - 1)] || "left",
        valign: "top",
        border: head
          ? [{ type: "none" }, { type: "none" },
             lastHead ? { pt: 1, color: BLUE } : { type: "none" }, { type: "none" }]
          : [{ type: "none" }, { type: "none" },
             { pt: 0.5, color: "DDDDDD" }, { type: "none" }],
      },
    };
  }));
  const { colW } = tableCols(b, widthIn);
  slide.addTable(rows, {
    x, y, w: widthIn, colW, fontSize: pt, color: TEXT,
    border: { type: "none" }, margin: [2, 4, 2, 2], autoPage: false,
  });
}

// ---------------------------------------------------------------- build
const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";
pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
pres.title = "Evaluating Coding Agent Quality";
pres.subject = "Application to Software Design Principles";
pres.author = "Akram Ben Fekih";

const FOOT = [
  { text: "Evaluating Coding Agent Quality",
    options: { x: 0.5, y: H - 0.38, w: 5, h: 0.25, fontSize: 9, color: GRAY,
               isTextBox: true, margin: 0 } },
];
pres.defineSlideMaster({
  title: "CONTENT",
  background: { color: "FFFFFF" },
  objects: [
    { text: { text: " ", options: FOOT[0].options } },
    { line: { x: M, y: 1.0, w: BODY_W, h: 0, line: { color: GOLD, width: 1.25 } } },
  ],
  slideNumber: { x: W - 1.1, y: H - 0.38, w: 0.6, h: 0.25,
                 fontSize: 9, color: GRAY, align: "right" },
});
pres.defineSlideMaster({
  title: "DIVIDER", background: { color: "FFFFFF" },
  objects: [{ text: { text: " ", options: FOOT[0].options } }],
  slideNumber: { x: W - 1.1, y: H - 0.38, w: 0.6, h: 0.25,
                 fontSize: 9, color: GRAY, align: "right" },
});
pres.defineSlideMaster({ title: "TITLE", background: { color: "FFFFFF" } });

let section = null;
let shrunk = 0;
slides.forEach((s, idx) => {
  const want = s.section ? s.section.replace(":", ".") : null;
  if (want && want !== section) {
    section = want;
    pres.addSection({ title: section });
  } else if (section === null) {
    section = "Opening";
    pres.addSection({ title: section });
  }

  if (s.kind === "plain" && idx === 0) {               // title slide
    const sl = pres.addSlide({ masterName: "TITLE", sectionTitle: section });
    sl.addText("Research Master's in Computer Science, Intelligent Pervasive Systems",
      { x: 1, y: 1.85, w: W - 2, h: 0.3, align: "center", fontSize: 12, color: GRAY, isTextBox: true });
    sl.addText("Evaluating Coding Agent Quality",
      { x: 1, y: 2.25, w: W - 2, h: 0.65, align: "center", fontSize: 36, bold: true, color: BLUE, isTextBox: true });
    sl.addText("Application to Software Design Principles",
      { x: 1, y: 2.92, w: W - 2, h: 0.4, align: "center", fontSize: 18, color: GRAY, isTextBox: true });
    sl.addShape("line", { x: W / 2 - 1.6, y: 3.45, w: 3.2, h: 0, line: { color: GOLD, width: 1.5 } });
    sl.addText("Akram Ben Fekih",
      { x: 1, y: 3.62, w: W - 2, h: 0.35, align: "center", fontSize: 17, color: TEXT, isTextBox: true });
    sl.addText("Supervised by Mrs. Maha Khemaja",
      { x: 1, y: 3.98, w: W - 2, h: 0.3, align: "center", fontSize: 12, color: TEXT, isTextBox: true });
    sl.addText("ISSAT Sousse, University of Sousse  ·  2025/2026",
      { x: 1, y: 4.3, w: W - 2, h: 0.3, align: "center", fontSize: 11, color: GRAY, isTextBox: true });
    sl.addNotes(`${idx + 1}/${slides.length}  Title`);
    return;
  }
  if (s.kind === "plain") {                            // "Thank you"
    const sl = pres.addSlide({ masterName: "TITLE", sectionTitle: section });
    sl.addText("Thank you", { x: 1, y: 3.1, w: W - 2, h: 0.8, align: "center",
      fontSize: 34, bold: true, color: BLUE, isTextBox: true });
    sl.addShape("line", { x: W / 2 - 1.2, y: 4.0, w: 2.4, h: 0, line: { color: GOLD, width: 1.5 } });
    sl.addNotes(`${idx + 1}/${slides.length}  Thank you`);
    return;
  }
  if (s.kind === "divider") {
    const sl = pres.addSlide({ masterName: "DIVIDER", sectionTitle: section });
    sl.addText(s.part, { x: 1, y: 3.0, w: W - 2, h: 0.32, align: "center",
      fontSize: 13, color: GRAY, isTextBox: true });
    sl.addText(s.title, { x: 1, y: 3.35, w: W - 2, h: 0.7, align: "center",
      fontSize: 28, bold: true, color: BLUE, isTextBox: true });
    sl.addShape("line", { x: W / 2 - 1.5, y: 4.15, w: 3.0, h: 0, line: { color: GOLD, width: 1.5 } });
    sl.addNotes(`${idx + 1}/${slides.length}  ${s.part}: ${s.title}`);
    return;
  }

  const sl = pres.addSlide({ masterName: "CONTENT", sectionTitle: section });
  const titleRuns = [{ text: s.title, options: { fontSize: 22, bold: true, color: BLUE } }];
  if (s.subtitle) {
    titleRuns.push({ text: "  " + s.subtitle,
      options: { fontSize: 12.5, bold: false, color: GRAY } });
  }
  sl.addText(titleRuns, { x: M, y: 0.33, w: BODY_W, h: 0.6, isTextBox: true,
    margin: 0, valign: "bottom" });
  sl.addNotes(`${idx + 1}/${slides.length}  ${s.title}${s.subtitle ? " — " + s.subtitle : ""}`);

  // fit: step the body size down until every region fits its height
  // Start generously and step down: a slide with little content then fills its
  // area instead of leaving the bottom third blank.
  let pt = 15.5;
  const fits = (p) => {
    const preH = s.pre.reduce((a, b) => a + blockHeight(b, BODY_W, p) + 0.07, 0);
    const postH = s.post.reduce((a, b) => a + blockHeight(b, BODY_W, p) + 0.07, 0);
    let colH = 0;
    for (const c of s.columns) {
      const cw = BODY_W * c.w;
      colH = Math.max(colH, c.blocks.reduce((a, b) => a + blockHeight(b, cw, p) + 0.07, 0));
    }
    return preH + colH + postH <= BODY_H;
  };
  while (pt > 7 && !fits(pt)) pt -= 0.5;
  if (pt < 10) shrunk++;

  let y = BODY_TOP;
  if (s.pre.length) {
    drawBlocks(sl, s.pre, M, y, BODY_W, pt);
    y += s.pre.reduce((a, b) => a + blockHeight(b, BODY_W, pt) + 0.07, 0);
  }
  if (s.columns.length) {
    const gap = 0.3;
    const total = s.columns.reduce((a, c) => a + c.w, 0);
    let cx = M;
    let maxH = 0;
    for (const c of s.columns) {
      const cw = (BODY_W - gap * (s.columns.length - 1)) * (c.w / total);
      drawBlocks(sl, c.blocks, cx, y, cw, pt);
      maxH = Math.max(maxH, c.blocks.reduce((a, b) => a + blockHeight(b, cw, pt) + 0.07, 0));
      cx += cw + gap;
    }
    y += maxH;
  }
  if (s.post.length) drawBlocks(sl, s.post, M, y, BODY_W, pt);
});

pres.writeFile({ fileName: OUT })
  .then(async f => {
    await applyTheme(f, THEME);
    console.log(`wrote ${f}  (${slides.length} slides, ` +
      `${(fs.statSync(f).size / 1048576).toFixed(1)} MB, ${shrunk} slides auto-shrunk to fit)`);
  })
  .catch(e => { console.error(e); process.exit(1); });
