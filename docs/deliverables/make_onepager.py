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
  .ps{display:grid;grid-template-columns:1fr 1.2fr;gap:10px;margin-top:5mm}
  .ps .card{padding:11px 13px}
  .pt{display:flex;gap:9px;align-items:flex-start;margin-top:7px}
  .pt .d{flex:none;width:18px;height:18px;border-radius:50%;color:#fff;font:600 9.5px/18px var(--f);text-align:center}
  .pt b{display:block;font-size:10.6px;margin-bottom:1px}
  .pt p{font-size:9.8px;color:var(--muted);line-height:1.45}
  .prob .d{background:var(--red)} .sol .d{background:var(--blue)}
  .flow{display:grid;grid-template-columns:repeat(6,1fr);gap:9px}
  .flow .card{padding:8px 9px;position:relative}
  .flow .card p{font-size:9.3px;color:var(--muted);line-height:1.35}
  .flow .card:not(:last-child)::after{content:"›";position:absolute;right:-5px;top:50%;transform:translate(50%,-50%);color:var(--label);font-size:14px}
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
  .guar{display:grid;grid-template-columns:repeat(2,1fr);gap:6px}
  .g1{display:flex;gap:7px;align-items:center;padding:6px 9px}
  .g1 svg{width:16px!important;height:16px!important}
  .g1 p{font-size:9.2px;color:var(--muted);line-height:1.3} .g1 b{font-size:9.8px;display:block}
  .sect{margin-top:5mm}
</style>
</head>
<body>
<section class="page">
  <div class="hdr"><div class="wm"><svg viewBox="0 0 24 24" width="26" height="26"><circle cx="12" cy="12" r="10.5" fill="none" stroke="#0b2a4a" stroke-width="1.8"/><path d="M12 4.5l2.6 7.5h-5.2z" fill="#de171e"/><path d="M12 19.5l-2.6-7.5h5.2z" fill="#0b2a4a"/><circle cx="12" cy="12" r="1.4" fill="#fff"/></svg><span>Compass</span></div><span>Compass · Datathon PolyFinances 2026</span></div>

  <h1>Votre transaction est-elle cohérente avec <em>votre</em> profil ?</h1>
  <p class="lead">Compass vérifie, avant qu'il agisse, si le portefeuille ou la transaction d'un investisseur autonome
  respecte le profil qu'il a lui-même déclaré, et lui explique pourquoi en langage clair.</p>

  <div class="ps">
    <div class="card prob">
      <h2>Le problème</h2>
      <div class="pt"><span class="d">1</span><div><b>Personne ne vérifie la cohérence</b><p>En courtage direct, l'ordre est exécuté tel quel. Le profil déclaré à l'ouverture du compte n'est plus jamais confronté aux décisions.</p></div></div>
      <div class="pt"><span class="d">2</span><div><b>Le comportement dérive</b><p>Effet de mode, concentration sur un titre : l'investisseur prend plus de risque que ce qu'il a lui-même dit pouvoir supporter.</p></div></div>
      <div class="pt"><span class="d">3</span><div><b>Un chatbot n'est pas la réponse</b><p>Une IA générale invente des chiffres et glisse vers le conseil personnalisé : inacceptable en finance réglementée.</p></div></div>
    </div>
    <div class="card sol">
      <h2>Notre solution</h2>
      <div class="pt"><span class="d">1</span><div><b>Le profil devient la référence</b><p>Ses déclarations (horizon, objectifs, tolérance et capacité au risque, connaissances, valeurs) sont traduites par des règles en limites personnelles, chacune traçable jusqu'au champ qui la fixe.</p></div></div>
      <div class="pt"><span class="d">2</span><div><b>Chaque décision est mesurée</b><p>Un moteur de risque (5 000 trajectoires simulées, stress tests) compare le portefeuille avant et après la transaction à ces limites, et mesure la marge.</p></div></div>
      <div class="pt"><span class="d">3</span><div><b>L'IA lit et explique, sous contrôle</b><p>Amazon Bedrock lit le comportement, les notes et l'intention ; chaque réponse est vérifiée. L'IA peut avertir, jamais bloquer, et chaque chiffre expliqué est vérifié.</p></div></div>
    </div>
  </div>

  <h2 class="sect">Comment ça marche</h2>
  <div class="legend"><span class="u">utilisateur</span><span class="r">code déterministe</span><span class="l">Amazon Bedrock</span><span class="g">contrôle</span></div>
  <div class="flow">
    <div class="card role u"><span class="tag">1 · Profil</span><p>Déclarations, historique, portefeuille</p></div>
    <div class="card role l"><span class="tag">2 · Lecture IA</span><p>Comportement, questions, notes, intention</p></div>
    <div class="card role g"><span class="tag">3 · Gate</span><p>Preuves réelles, sinon rejet</p></div>
    <div class="card role r"><span class="tag">4 · Limites</span><p>Calculées par règles, jamais par l'IA</p></div>
    <div class="card role r"><span class="tag">5 · Contrôles</span><p>Avant / après, marge</p></div>
    <div class="card role l"><span class="tag">6 · Explication</span><p>Chiffres vérifiés, Guardrail</p></div>
  </div>

  <h2 class="sect">Exemple</h2>
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
        <p>Risque élevé accepté, mais pas tout : sa concentration tech contredit sa propre note « pas seulement de la tech ».</p>
      </div>
      <div class="card persona">
        <h3>Mateo <span class="chip w">Inconnu, pas supposé</span></h3>
        <p>Exclusions ESG : un titre énergie reste « inconnu » tant qu'aucune étiquette sourcée ne le classe.</p>
      </div>
    </div>
  </div>

  <div class="bottom sect">
    <div>
      <h2>Garanties</h2>
      <div class="guar">
        <div class="card g1">{i_lock}<div><b>Limites hors IA</b><p>Règles et déclarations seulement</p></div></div>
        <div class="card g1">{i_check}<div><b>IA plafonnée</b><p>Avertit, ne bloque jamais</p></div></div>
        <div class="card g1">{i_quote}<div><b>Preuves réelles</b><p>Événement ou citation exacte</p></div></div>
        <div class="card g1">{i_hash}<div><b>Chiffres vérifiés</b><p>Absent des faits = rejeté</p></div></div>
        <div class="card g1">{i_tag}<div><b>Origine étiquetée</b><p>Fait, hypothèse ou inférence</p></div></div>
        <div class="card g1">{i_no}<div><b>Aucun conseil</b><p>Ni recommandation, ni exécution</p></div></div>
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
    html = PAGE.replace("{chart}", chart()).replace("{aws}", aws_map())
    for name, color in [("lock", NAVY), ("check", GREEN), ("quote", BLUE), ("hash", NAVY), ("tag", LABEL), ("no", RED)]:
        html = html.replace("{i_" + name + "}", icon(name, color))
    (HERE / "one-pager.html").write_text(html, encoding="utf-8")


if __name__ == "__main__":
    main()
