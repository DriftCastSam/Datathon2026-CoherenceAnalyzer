"""Generate one-pager.html with its inline SVG visuals.

Robert's figures come from the offline mock run of his trade (no LLM needed for any number):
  COHERENCE_ANALYZER_STORE_DIR=.cache/store COHERENCE_ANALYZER_LLM=mock \
    python -m coherence_analyzer.cli check fixtures/robert --intent in_0001 --auto-confirm
(checks_current / checks_proposed H3, H4, H7, H8, H9, H10 and headroom.max_weight_pct). Re-run it and update ROWS if
the engine or the fixtures change.
Run: python docs/deliverables/make_onepager.py && docs/deliverables/build_pdfs.sh
"""
from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent

NAVY, INK, MUTED, LABEL, LINE, RED, BLUE, GREEN = ("#0b2a4a", "#172b3f", "#5c6b7a", "#4d6a87", "#dde4ec",
                                                    "#de171e", "#0b6bcb", "#1a7f47")
BEFORE = "#aeb8c7"

# (label, before, after, limit, kind of limit, formatter)
pct = lambda v: f"{v * 100:.1f}".replace(".", ",").replace(",0", "") + " %"
cad = lambda v: f"{v / 1000:.0f} k$"
ROWS = [
    ("Poids de NVDA", 0.0, 0.10, 0.08, "max", pct),
    ("Revenu fixe", 0.35, 0.25, 0.30, "min", pct),
    ("Volatilité annuelle", 0.0727, 0.0983, 0.09, "max", pct),
    ("Pire baisse historique", -0.077, -0.134, -0.12, "max", pct),
    ("Part du risque tech", 0.0843, 0.4173, 0.30, "max", pct),
    ("Valeur à l'horizon, scénario défavorable", 366719.0, 316971.0, 360000.0, "plancher", cad),
]


def chart() -> str:
    W, row, top = 372, 29, 22
    lx, tx0, tx1 = 0, 140, 270
    H = top + row * len(ROWS) + 4
    s = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img" '
         f'aria-label="Avant et après la transaction, pour chaque limite franchie">']
    # legend
    s.append(f'<circle cx="{tx0 + 4}" cy="8" r="4" fill="{BEFORE}"/><text class="lbl" x="{tx0 + 12}" y="11">avant</text>'
             f'<circle cx="{tx0 + 52}" cy="8" r="4.5" fill="{RED}"/><text class="lbl" x="{tx0 + 60}" y="11">après</text>'
             f'<rect x="{tx0 + 98}" y="2" width="2" height="12" fill="{NAVY}"/><text class="lbl" x="{tx0 + 105}" y="11">votre limite</text>')
    for i, (label, b, a, lim, kind, fmt) in enumerate(ROWS):
        y = top + i * row + row / 2
        lo, hi = min(b, a, lim), max(b, a, lim)
        pad = (hi - lo) * 0.12
        lo, hi = lo - pad, hi + pad
        X = lambda v: tx0 + (v - lo) / (hi - lo) * (tx1 - tx0)
        lines = [label] if len(label) < 26 else [label[:label.rfind(" ", 0, 26)], label[label.rfind(" ", 0, 26) + 1:]]
        for j, t in enumerate(lines):
            dy = (j - (len(lines) - 1) / 2) * 11 + 3.5
            s.append(f'<text x="{lx}" y="{y + dy:.1f}" font-size="9.6" style="fill:{INK}">{t}</text>')
        s.append(f'<line x1="{tx0}" y1="{y}" x2="{tx1}" y2="{y}" stroke="{LINE}" stroke-width="5" stroke-linecap="round"/>')
        # the side of the limit that breaks it
        if kind == "max" and lim >= 0:
            s.append(f'<line x1="{X(lim)}" y1="{y}" x2="{tx1}" y2="{y}" stroke="#fbdcdc" stroke-width="5"/>')
        else:
            s.append(f'<line x1="{tx0}" y1="{y}" x2="{X(lim)}" y2="{y}" stroke="#fbdcdc" stroke-width="5"/>')
        s.append(f'<line x1="{X(b):.1f}" y1="{y}" x2="{X(a):.1f}" y2="{y}" stroke="{RED}" stroke-width="2" opacity=".55"/>')
        s.append(f'<rect x="{X(lim) - 1:.1f}" y="{y - 8}" width="2" height="16" rx="1" fill="{NAVY}"/>')
        s.append(f'<circle cx="{X(b):.1f}" cy="{y}" r="4" fill="{BEFORE}" stroke="#fff" stroke-width="1.5"/>')
        s.append(f'<circle cx="{X(a):.1f}" cy="{y}" r="4.8" fill="{RED}" stroke="#fff" stroke-width="1.5"/>')
        s.append(f'<text x="{tx1 + 10}" y="{y - 1}" font-size="9.6" font-weight="600" style="fill:{INK}">{fmt(b)} → {fmt(a)}</text>')
        s.append(f'<text class="lbl" x="{tx1 + 10}" y="{y + 9.5}">{kind} {fmt(lim)}</text>')
    s.append("</svg>")
    return "\n".join(s)


def badge(x, y, w, tag, title, sub, color=NAVY, hot=False):
    stroke = BLUE if hot else LINE
    return (f'<rect x="{x}" y="{y}" width="{w}" height="40" rx="4" fill="#fff" stroke="{stroke}"/>'
            f'<rect x="{x + 7}" y="{y + 8}" width="24" height="24" rx="4" fill="{color}"/>'
            f'<text x="{x + 19}" y="{y + 23.5}" text-anchor="middle" font-size="8.6" font-weight="700" style="fill:#fff">{tag}</text>'
            f'<text x="{x + 37}" y="{y + 17}" font-size="9.8" font-weight="600" style="fill:{NAVY}">{title}</text>'
            f'<text x="{x + 37}" y="{y + 30}" font-size="8.4" style="fill:{MUTED}">{sub}</text>')


def aws_map() -> str:
    W, H = 372, 172
    a = lambda d: f'<path d="{d}" stroke="#9aabbd" stroke-width="1.2" fill="none" marker-end="url(#m)"/>'
    s = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">',
         '<defs><marker id="m" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">'
         '<path d="M0,0 L10,5 L0,10 z" fill="#9aabbd"/></marker></defs>',
         f'<rect x="0" y="0" width="{W}" height="{H}" rx="6" fill="#f7f9fc" stroke="{LINE}"/>',
         f'<text x="10" y="14" font-size="8.6" font-weight="600" style="fill:{LABEL}">AWS · serverless</text>']
    s.append(badge(8, 24, 112, "CF", "CloudFront", "interface"))
    s.append(badge(8, 74, 112, "λ", "Lambda", "API, profil", hot=True))
    s.append(badge(8, 124, 112, "SF", "Step Functions", "transaction"))
    s.append(badge(140, 50, 112, "BR", "Bedrock", "6 nœuds encadrés", BLUE))
    s.append(badge(140, 112, 112, "GR", "Guardrails", "refus du conseil", RED))
    s.append(badge(264, 24, 100, "S3", "S3", "profils, audit", LABEL))
    s.append(badge(264, 74, 100, "CW", "CloudWatch", "appels IA", LABEL))
    s.append(badge(264, 124, 100, "IAM", "IAM", "moindre privilège", LABEL))
    s.append(a("M64,64 L64,72"))
    s.append(a("M120,94 L138,74"))
    s.append(a("M120,144 C130,144 130,80 138,78"))
    s.append(a("M196,90 L196,110"))
    s.append(a("M120,86 C190,40 230,40 262,44"))
    s.append("</svg>")
    return "\n".join(s)


def loop() -> str:
    """The core: the profile refinement loop (flux A), drawn as a closed cycle with the gate's reject branch."""
    W, H = 380, 214
    U, R, L, G = BLUE, GREEN, LABEL, RED
    s = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img" '
         f'aria-label="Boucle d\'affinage du profil">',
         '<defs><marker id="lm" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">'
         f'<path d="M0,0 L10,5 L0,10 z" fill="{BLUE}"/></marker>'
         '<marker id="lr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">'
         f'<path d="M0,0 L10,5 L0,10 z" fill="{RED}"/></marker></defs>']
    w, h, xs, y1, y2 = 108, 50, [4, 136, 268], 8, 146

    def n(x, y, c, k, t, a, b=""):
        out = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" fill="#fff" stroke="{LINE}"/>'
               f'<rect x="{x}" y="{y}" width="{w}" height="3" rx="1" fill="{c}"/>'
               f'<text x="{x + 8}" y="{y + 17}" font-size="9.8" font-weight="600" style="fill:{NAVY}">{k} · {t}</text>'
               f'<text x="{x + 8}" y="{y + 30}" font-size="8.6" style="fill:{MUTED}">{a}</text>')
        if b:
            out += f'<text x="{x + 8}" y="{y + 41}" font-size="8.6" style="fill:{MUTED}">{b}</text>'
        return out

    def ar(d, red=False):
        dash = ' stroke-dasharray="4 3"' if red else ""
        return (f'<path d="{d}" stroke="{RED if red else BLUE}" stroke-width="1.4" fill="none" '
                f'marker-end="url(#{"lr" if red else "lm"})"{dash}/>')
    s.append(n(xs[0], y1, U, "1", "Profil", "déclarations, notes,", "questions, transaction"))
    s.append(n(xs[1], y1, L, "2", "L'IA propose", "comportement, valeurs,", "connaissances"))
    s.append(n(xs[2], y1, G, "3", "Gate", "preuve réelle ou rejet ;", "IA plafonnée à warning"))
    s.append(n(xs[2], y2, R, "4", "Limites", "recalculées par règles,", "jamais par l'IA"))
    s.append(n(xs[1], y2, R, "5", "Portefeuille", "confronté au profil :", "violations, marge"))
    s.append(n(xs[0], y2, U, "6", "À clarifier", "questions fixes,", "jamais générées"))
    cy, cy2 = y1 + h / 2, y2 + h / 2
    s.append(ar(f"M{xs[0] + w},{cy} L{xs[1] - 2},{cy}"))
    s.append(ar(f"M{xs[1] + w},{cy} L{xs[2] - 2},{cy}"))
    s.append(ar(f"M{xs[2] + w / 2 + 18},{y1 + h} L{xs[2] + w / 2 + 18},{y2 - 2}"))
    s.append(ar(f"M{xs[2]},{cy2} L{xs[1] + w + 2},{cy2}"))
    s.append(ar(f"M{xs[1]},{cy2} L{xs[0] + w + 2},{cy2}"))
    s.append(ar(f"M{xs[0] + w / 2},{y2} L{xs[0] + w / 2},{y1 + h + 2}"))
    s.append(f'<text x="{xs[0] + w / 2 + 6}" y="{(y1 + h + y2) / 2 - 2}" font-size="8.6" style="fill:{BLUE}">chaque réponse =</text>'
             f'<text x="{xs[0] + w / 2 + 6}" y="{(y1 + h + y2) / 2 + 9}" font-size="8.6" style="fill:{BLUE}">un nouvel événement</text>')
    # gate reject branch, back into the gate's own input
    gx = xs[2] + w / 2 - 22
    s.append(ar(f"M{gx},{y1 + h} C{gx},{y1 + h + 30} {xs[1] + w / 2},{y1 + h + 30} {xs[1] + w / 2},{y1 + h + 2}", red=True))
    s.append(f'<text x="{(gx + xs[1] + w / 2) / 2}" y="{y1 + h + 40}" text-anchor="middle" font-size="8.6" style="fill:{RED}">'
             f'rejet : valeur précédente gardée, audit</text>')
    s.append(f'<text x="{W / 2 + 4}" y="{y2 - 18}" text-anchor="middle" font-size="11" font-weight="600" style="fill:{NAVY}">'
             f'relancée à chaque événement</text>')
    s.append("</svg>")
    return "\n".join(s)


ICON = {
    "lock": '<path d="M5 9V6a4 4 0 0 1 8 0v3" fill="none" stroke="currentColor" stroke-width="1.6"/><rect x="3" y="9" width="12" height="8" rx="2" fill="currentColor"/>',
    "check": '<circle cx="9" cy="9" r="8" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M5 9.2l2.6 2.6L13 6.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
    "quote": '<path d="M2 4h14v9H8l-4 3v-3H2z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M5 7.5h8M5 10h5" stroke="currentColor" stroke-width="1.4"/>',
    "hash": '<path d="M7 2L5 16M13 2l-2 14M2.5 6.5h13M2 11.5h13" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
    "tag": '<path d="M2 2h7l7 7-7 7-7-7z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><circle cx="6" cy="6" r="1.5" fill="currentColor"/>',
    "no": '<circle cx="9" cy="9" r="8" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M3.5 14.5l11-11" stroke="currentColor" stroke-width="1.6"/>',
}


def icon(name, color):
    return f'<svg viewBox="0 0 18 18" width="18" height="18" style="color:{color};flex:none">{ICON[name]}</svg>'


PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Compass</title>
<link rel="stylesheet" href="print.css">
<style>
  html,body{font-size:10.6px}
  h1{font-size:25px;margin-bottom:5px}
  .lead{font-size:12px}
  h2{font-size:13px;margin-bottom:6px}
  .core{display:grid;grid-template-columns:1.22fr 1fr;gap:10px;margin-top:5mm}
  .core .card{padding:10px 12px}
  .cap{font-size:9.6px;color:var(--muted);line-height:1.45;margin-bottom:6px}
  .ctx p{font-size:9.4px;color:var(--muted);margin-bottom:3px}
  .ctx li{font-size:9.4px;color:var(--muted);margin:2px 0} .ctx b{font-size:9.4px}
  .hero{display:grid;grid-template-columns:1.32fr 1fr;gap:10px}
  .ask{font:500 13.5px/1.3 var(--f);color:var(--navy);margin:2px 0 1px}
  .kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}
  .kpi{padding:8px 10px}
  .kpi .v{font:500 22px/1.05 var(--f);font-variant-numeric:tabular-nums}
  .kpi .c{font-size:9px;color:var(--muted);margin-top:2px;line-height:1.25}
  .side{display:flex;flex-direction:column;gap:7px}
  .persona{padding:9px 11px} .side .persona{flex:1}
  .persona h3{display:flex;justify-content:space-between;align-items:center;gap:6px;font-size:10.8px}
  .persona p{font-size:9.4px;color:var(--muted);line-height:1.4}
  .bottom{display:grid;grid-template-columns:1.32fr 1fr;gap:10px}
  .guar{display:grid;grid-template-columns:1fr;gap:5px}
  .g1{display:flex;gap:8px;align-items:center;padding:5px 8px;border:1px solid var(--line);border-radius:4px;background:var(--rowTint)}
  .g1 svg{width:16px!important;height:16px!important}
  .g1 p{font-size:9.2px;color:var(--muted);line-height:1.3} .g1 b{font-size:9.8px;display:block}
  .sect{margin-top:5mm}
</style>
</head>
<body>
<section class="page">
  <div class="hdr"><div class="wm"><svg viewBox="0 0 24 24" width="26" height="26"><circle cx="12" cy="12" r="10.5" fill="none" stroke="#0b2a4a" stroke-width="1.8"/><path d="M12 4.5l2.6 7.5h-5.2z" fill="#de171e"/><path d="M12 19.5l-2.6-7.5h5.2z" fill="#0b2a4a"/><circle cx="12" cy="12" r="1.4" fill="#fff"/></svg><span>Compass</span></div><span>Compass · Datathon PolyFinances 2026</span></div>

  <h1>Votre portefeuille dit-il ce que <em>vous</em> voulez ?</h1>
  <p class="lead">Compass montre à l'investisseur, avec ses propres chiffres et ses propres mots, où son portefeuille
  s'écarte de ce qu'il veut. L'IA écoute, une gate ne garde que ce qu'elle peut prouver, les règles vérifient,
  et la décision reste la sienne. Jamais de conseil, jamais d'exécution.</p>

  <div class="core">
    <div class="card">
      <h2>1 · Comprendre ce que l'investisseur veut</h2>
      <p class="cap">Déclarations, notes, questions, transactions : chaque geste affine le profil. L'IA propose ce qu'il
      révèle, la gate filtre, les règles recalculent les limites et le portefeuille y est confronté. Ce qui reste flou
      revient à l'investisseur sous forme de question, jamais de supposition.</p>
      <div class="legend"><span class="u">utilisateur</span><span class="r">code déterministe</span><span class="l">Amazon Bedrock</span><span class="g">gate</span></div>
      {loop}
    </div>
    <div class="card">
      <h2>2 · Un écart qu'on peut croire</h2>
      <p class="cap">Pour montrer un écart, il doit être vrai : rien de l'IA ne touche le profil ni le verdict sans la gate.</p>
      <div class="guar">
        <div class="g1">{i_quote}<div><b>Preuves réelles</b><p>Un événement ou une citation exacte, sinon rejet</p></div></div>
        <div class="g1">{i_lock}<div><b>Limites hors IA</b><p>Règles et déclarations seulement</p></div></div>
        <div class="g1">{i_check}<div><b>IA plafonnée</b><p>Avertit, ne bloque jamais</p></div></div>
        <div class="g1">{i_hash}<div><b>Chiffres vérifiés</b><p>Absent des faits : un nouvel essai, puis un gabarit</p></div></div>
        <div class="g1">{i_tag}<div><b>Origine étiquetée</b><p>Fait, hypothèse ou inférence</p></div></div>
        <div class="g1">{i_no}<div><b>Aucun conseil</b><p>Guardrail Bedrock, aucune exécution</p></div></div>
      </div>
    </div>
  </div>

  <h2 class="sect">L'écart, montré à l'investisseur</h2>
  <div class="hero">
    <div class="card" style="padding:10px 12px">
      <span class="tag" style="--c:var(--blue)">Robert · 59 ans · risque déclaré faible à modéré · horizon 4 ans</span>
      <p class="ask">« Je veux mettre 10 % de mon portefeuille dans NVDA. »</p>
      <p class="muted" style="margin-bottom:5px">Financé par son fonds obligataire ZST. Verdict : <span class="chip v">Incohérent</span></p>
      {chart}
    </div>
    <div class="side">
      <div class="kpis">
        <div class="card kpi"><div class="v" style="color:var(--red)">6</div><div class="c">limites franchies</div></div>
        <div class="card kpi"><div class="v" style="color:var(--label)">2</div><div class="c">avertissements IA</div></div>
        <div class="card kpi"><div class="v" style="color:var(--blue)">1,5 %</div><div class="c">marge avant le plancher</div></div>
      </div>
      <div class="card persona">
        <h3>Amira <span class="chip v">Crypto au-dessus du plafond</span></h3>
        <p>Risque élevé accepté, mais pas tout : Compass lui renvoie sa propre note « pas seulement de la tech ».</p>
      </div>
      <div class="card persona">
        <h3>Mateo <span class="chip w">Inconnu, pas supposé</span></h3>
        <p>Exclusions ESG : un titre énergie reste « inconnu » tant qu'aucune étiquette sourcée ne le classe.</p>
      </div>
    </div>
  </div>

  <div class="bottom sect">
    <div>
      <h2>Pour mesurer et montrer l'écart</h2>
      <div class="card ctx">
        <p>Ces briques chiffrent l'écart et le rendent lisible pour l'investisseur.</p>
        <ul>
          <li><b>Moteur de risque :</b> 5 000 trajectoires simulées, stress tests, avant / après</li>
          <li><b>Simulateur de transaction :</b> marge avant la première limite franchie</li>
          <li><b>Explication en langage clair :</b> rédigée après le verdict, jamais avant</li>
          <li><b>Interface :</b> tableau de bord, questionnaire, what-if</li>
          <li><b>Données :</b> titres, FNB en transparence, frais, étiquettes sourcées</li>
        </ul>
      </div>
    </div>
    <div>
      <h2>Sur AWS</h2>
      <div class="card" style="padding:5px">{aws}</div>
    </div>
  </div>

  <p class="disclaimer" style="position:absolute;left:14mm;right:14mm;bottom:8mm">Simulation éducative avec des profils fictifs. Ceci n'est pas un conseil financier personnalisé. Aucune transaction n'est exécutée.</p>
</section>
</body>
</html>
"""


def main() -> None:
    html = PAGE.replace("{chart}", chart()).replace("{aws}", aws_map()).replace("{loop}", loop())
    for name, color in [("lock", NAVY), ("check", GREEN), ("quote", BLUE), ("hash", NAVY), ("tag", LABEL), ("no", RED)]:
        html = html.replace("{i_" + name + "}", icon(name, color))
    (HERE / "one-pager.html").write_text(html, encoding="utf-8")


if __name__ == "__main__":
    main()
