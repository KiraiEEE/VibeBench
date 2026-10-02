// Convert the XeLaTeX/Beamer defence deck to .pptx.
//
// The deck's design, TikZ figures and spacing were checked slide by slide in the
// PDF, so this preserves them exactly: every slide becomes one full-bleed 1920x1080
// image on a 16:9 PowerPoint slide. The text is therefore not editable in
// PowerPoint; main.tex stays the source. Slide titles drive the PowerPoint outline,
// the section list, each image's alt text and the speaker notes.
//
// Run:  node thesis/presentation/export/to_pptx.js        (from the repo root)
//
// Needs: pdftoppm (Poppler, ships with MiKTeX), python, and pptxgenjs. If
// pptxgenjs is not resolvable, `npm install pptxgenjs` in this directory first.
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const HERE = __dirname;
const DECK = path.resolve(HERE, "..");
const PDF = path.join(DECK, "main.pdf");
const OUT = path.join(DECK, "main.pptx");

// LAYOUT_WIDE writes sldSz 12192000 x 6858000 EMU. Derive the extent from those
// rather than the rounded 13.333in, which lands 305 EMU short of the right edge.
const EMU_PER_IN = 914400, CX = 12192000, CY = 6858000;
const W = CX / EMU_PER_IN, H = CY / EMU_PER_IN;

let pptxgen;
try {
  pptxgen = require("pptxgenjs");
} catch {
  pptxgen = require(path.join(HERE, "node_modules", "pptxgenjs"));
}

if (!fs.existsSync(PDF)) {
  throw new Error(`${PDF} not found. Build the deck first: latexmk -xelatex main.tex`);
}

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "beamer-pptx-"));
try {
  console.log("rendering slides...");
  execFileSync("pdftoppm",
    ["-png", "-scale-to-x", "1920", "-scale-to-y", "1080", PDF, path.join(tmp, "s")],
    { stdio: "inherit" });

  console.log("reading slide titles...");
  const titlesJson = path.join(tmp, "titles.json");
  execFileSync("python", [path.join(HERE, "slide_titles.py"), "--pdf", PDF,
                          "--tex", path.join(DECK, "main.tex"), "--out", titlesJson],
    { stdio: "inherit" });
  const entries = JSON.parse(fs.readFileSync(titlesJson, "utf8"));

  const imgs = fs.readdirSync(tmp).filter(f => f.endsWith(".png")).sort();
  if (imgs.length !== entries.length) {
    throw new Error(`${imgs.length} slide images but ${entries.length} titles`);
  }

  const pres = new pptxgen();
  pres.layout = "LAYOUT_WIDE";
  pres.title = "Evaluating Coding Agent Quality";
  pres.subject = "Application to Software Design Principles";
  pres.author = "Akram Ben Fekih";

  let section = null;
  imgs.forEach((img, i) => {
    const { title, divider } = entries[i];
    // Only a \divider page opens a section. "Backup: why recall..." is a content slide.
    if (divider) {
      const m = /^(Part \d+|Backup):\s*(.*)$/.exec(title);
      section = m ? `${m[1]}. ${m[2]}` : title;
      pres.addSection({ title: section });
    } else if (section === null) {
      section = "Opening";
      pres.addSection({ title: section });
    }
    const slide = pres.addSlide({ sectionTitle: section });
    slide.background = { color: "FFFFFF" };
    slide.addImage({
      path: path.join(tmp, img),
      x: 0, y: 0, w: W, h: H,
      altText: title,
      objectName: `slide-${String(i + 1).padStart(2, "0")}`,
    });
    slide.addNotes(`${i + 1}/${imgs.length}  ${title}`);
  });

  return pres.writeFile({ fileName: OUT }).then(f => {
    console.log(`wrote ${f}  (${imgs.length} slides, ` +
                `${(fs.statSync(f).size / 1048576).toFixed(1)} MB)`);
    fs.rmSync(tmp, { recursive: true, force: true });
  });
} catch (e) {
  fs.rmSync(tmp, { recursive: true, force: true });
  throw e;
}
