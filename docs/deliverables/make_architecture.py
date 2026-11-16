"""Generate architecture-aws.html (the AWS architecture document) with its inline SVG diagrams.

Every service and link drawn here exists in deploy/aws/coherence-analyzer.cfn.json, deploy/cloudfront.yaml or
deploy/aws/flow_b.asl.json; the loop, gate and explanation steps follow docs/index.html. Run: python docs/deliverables/make_architecture.py && docs/deliverables/build_pdfs.sh
"""
from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent

COL = {"net": "#0b2a4a", "store": "#4d6a87", "comp": "#0b2a4a", "ai": "#0b6bcb", "int": "#0b2a4a",
       "sec": "#de171e", "mgmt": "#4d6a87", "user": "#0b6bcb", "rule": "#1a7f47", "llm": "#4d6a87",
       "gate": "#de171e", "grey": "#9aabbd"}


def box(x, y, w, h, tag, color, title, sub="", sub2=""):
    """Service tile: white card, coloured badge with a short tag, title and up to two sub-lines."""
    out = [f'<rect class="box" x="{x}" y="{y}" width="{w}" height="{h}" rx="4"/>',
           f'<rect x="{x + 11}" y="{y + 10}" width="28" height="28" rx="5" fill="{color}"/>',
           f'<text x="{x + 25}" y="{y + 28.5}" text-anchor="middle" font-size="10" font-weight="700" '
           f'style="fill:#fff">{tag}</text>',
           f'<text class="ttl" x="{x + 47}" y="{y + 21}">{title}</text>']
    if sub:
        out.append(f'<text class="sub" x="{x + 47}" y="{y + 35}">{sub}</text>')
    if sub2:
        out.append(f'<text class="sub" x="{x + 11}" y="{y + 54}">{sub2}</text>')
    return "\n".join(out)


def arrow(d, label="", lx=0, ly=0, hot=False, anchor="middle", dash=False):
    cls = "arrow hot" if hot else "arrow"
    marker = "url(#ah)" if hot else "url(#a)"
    extra = ' stroke-dasharray="4 3"' if dash else ""
    out = f'<path class="{cls}" d="{d}" marker-end="{marker}"{extra}/>'
    if label:
        out += f'<text class="lbl" x="{lx}" y="{ly}" text-anchor="{anchor}">{label}</text>'
    return out


def num(x, y, n):
    return (f'<circle cx="{x}" cy="{y}" r="8" fill="#0b2a4a"/>'
            f'<text x="{x}" y="{y + 3.5}" text-anchor="middle" font-size="9.5" font-weight="700" style="fill:#fff">{n}</text>')


DEFS = """<defs>
<marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
<path d="M0,0 L10,5 L0,10 z" fill="#9aabbd"/></marker>
<marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
<path d="M0,0 L10,5 L0,10 z" fill="#0b6bcb"/></marker>
</defs>"""


def overview() -> str:
    W, H = 760, 600
    s = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">', DEFS]
    # AWS cloud zone
    s.append('<rect class="cloud" x="140" y="8" width="612" height="584" rx="10"/>')
    s.append('<text x="156" y="28" font-size="10.5" font-weight="700" style="fill:#0b2a4a">AWS Cloud · une région</text>')
    # zone: edge / web
    s.append('<rect class="zone" x="152" y="44" width="380" height="104" rx="8"/>')
    s.append('<text class="lbl" x="162" y="58">Stack site</text>')
    # zone: IAM boundary around both Lambdas
    s.append('<rect class="zone" x="346" y="186" width="186" height="298" rx="8" style="stroke:#de171e"/>')
    s.append('<text class="lbl" x="354" y="200" style="fill:#de171e;font-weight:600">IAM · moindre privilège</text>')

    # user
    s.append('<rect class="box" x="8" y="236" width="118" height="92" rx="6"/>')
    s.append(f'<rect x="8" y="236" width="118" height="3" rx="1" fill="{COL["user"]}"/>')
    s.append('<text class="ttl" x="20" y="258">Utilisateur</text>')
    s.append('<text class="sub" x="20" y="273">navigateur</text>')
    s.append('<text class="sub" x="20" y="287">app.html (Compass)</text>')
    s.append('<text class="sub" x="20" y="306">Démo / CLI</text>')
    s.append('<text class="sub" x="20" y="319">aws stepfunctions</text>')

    # row 1: CloudFront -> S3 site
    s.append(box(166, 70, 160, 66, "CF", COL["net"], "Amazon CloudFront", "HTTPS · HTTP/2-3 · cache", "OAC (SigV4) · en-têtes sécurité"))
    s.append(box(360, 70, 160, 66, "S3", COL["store"], "S3 · bucket site", "privé · SSE-AES256", "app.html, js/, css/, données"))
    # row 2: Function URL -> API Lambda
    s.append(box(166, 236, 160, 66, "URL", COL["comp"], "Lambda Function URL", "HTTPS · CORS", "/api · /site-api"))
    s.append(box(360, 214, 160, 80, "λ", COL["comp"], "Lambda · API", "FastAPI + Mangum", "Python 3.11 · 1 024 Mo · 29 s"))
    s.append('<text class="sub" x="371" y="284">Flux A (profil), C (revue), what-if</text>')
    # row 3: Step Functions -> Flow B Lambda
    s.append(box(166, 392, 160, 80, "SF", COL["int"], "AWS Step Functions", "machine à états Flow B", "B1 → B9 · retry · catch"))
    s.append('<text class="sub" x="177" y="463">branche parallèle B5</text>')
    s.append(box(360, 392, 160, 80, "λ", COL["comp"], "Lambda · Flow B", "1 état = 1 invocation", "Python 3.11 · 2 048 Mo · 900 s"))
    s.append('<text class="sub" x="371" y="463">flux transaction (B)</text>')

    # right column
    s.append(box(574, 70, 166, 66, "GR", COL["sec"], "Bedrock Guardrails", "sujet refusé : conseil", "en placement personnalisé"))
    s.append(box(574, 186, 166, 80, "BR", COL["ai"], "Amazon Bedrock", "API Converse · 1 outil JSON", "Claude Sonnet 5.5 (live)"))
    s.append('<text class="sub" x="585" y="257">nœuds N1 à N6</text>')
    s.append(box(574, 320, 166, 80, "S3", COL["store"], "S3 · bucket données", "versionné · SSE · TLS seul", "préfixe ca/ : profiles, events,"))
    s.append('<text class="sub" x="585" y="391">portfolios, reports, audit</text>')
    s.append(box(574, 440, 166, 66, "CW", COL["mgmt"], "CloudWatch Logs", "1 groupe par Lambda", "latence, tokens, cache LLM"))

    # deploy strip
    s.append(box(166, 516, 354, 62, "CFN", COL["mgmt"], "AWS CloudFormation", "2 stacks · deploy.sh : code → S3 → deploy", ""))
    s.append('<text class="sub" x="213" y="566">sorties : ApiFunctionUrl, StateMachineArn, BucketName</text>')

    # arrows
    s.append(arrow("M126,256 C146,256 146,103 164,103", hot=True))
    s.append(num(146, 180, 1))
    s.append(arrow("M326,103 L358,103", "OAC", 342, 97))
    s.append(arrow("M126,272 L164,270", hot=True))
    s.append(num(146, 262, 2))
    s.append(arrow("M326,262 L358,258", hot=True))
    s.append(arrow("M126,312 C146,312 146,430 164,430", hot=True))
    s.append(num(146, 372, 3))
    s.append(arrow("M326,430 L358,430", "invoke", 342, 424))
    # lambdas -> bedrock
    s.append(arrow("M520,240 L572,228"))
    s.append(arrow("M520,412 C556,412 556,250 572,250"))
    # lambdas -> s3 data
    s.append(arrow("M520,276 C540,276 540,350 572,350"))
    s.append(arrow("M520,440 C548,440 548,372 572,372"))
    # -> logs
    s.append(arrow("M520,462 L572,470"))
    # bedrock <-> guardrail
    s.append(arrow("M657,184 L657,138"))
    s.append('<text class="lbl" x="664" y="164">explainer (N5)</text>')
    s.append('<text class="lbl" x="664" y="175">seulement</text>')
    # cfn -> stacks (dashed)
    s.append(arrow("M520,546 C560,546 560,546 572,520", dash=True))
    s.append("</svg>")
    return "\n".join(s)


def state(x, y, w, h, color, title, sub="", kind="task"):
    if kind == "choice":
        cx, cy = x + w / 2, y + h / 2
        return (f'<path d="M{cx},{y} L{x + w},{cy} L{cx},{y + h} L{x},{cy} z" fill="#fff" stroke="#c9d0db"/>'
                f'<text class="ttl" x="{cx}" y="{cy - 1}" text-anchor="middle" font-size="10.5">{title}</text>'
                f'<text class="sub" x="{cx}" y="{cy + 11}" text-anchor="middle">{sub}</text>')
    return (f'<rect class="box" x="{x}" y="{y}" width="{w}" height="{h}" rx="6"/>'
            f'<rect x="{x}" y="{y}" width="{w}" height="4" rx="2" fill="{color}"/>'
            f'<text class="ttl" x="{x + w / 2}" y="{y + 22}" text-anchor="middle" font-size="10.5">{title}</text>'
            f'<text class="sub" x="{x + w / 2}" y="{y + 36}" text-anchor="middle">{sub}</text>')


def flow_b() -> str:
    W, H = 760, 300
    R, L, G, U = COL["rule"], COL["llm"], COL["gate"], COL["user"]
    s = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">', DEFS]
    w, h, y1, y2 = 118, 48, 28, 170
    xs = [10, 160, 310, 460, 620]
    s.append(state(xs[0], y1, w, h, L, "B1 parse intent", "N4 intent_parser + gate"))
    s.append(state(xs[1], y1 - 6, w, 60, R, "B2 confirmé ?", "Choice", kind="choice"))
    s.append(state(xs[2], y1, w, h, L, "B3 refresh", "Flux A : N1–N3 + gate"))
    s.append(state(xs[3], y1, w, h, R, "B4 apply", "portefeuille proposé"))
    # B5 parallel
    s.append(f'<rect class="zone" x="{xs[4] - 6}" y="{y1 - 18}" width="{w + 12}" height="{h + 70}" rx="8" style="stroke:{COL["int"]}"/>')
    s.append(f'<text class="lbl" x="{xs[4]}" y="{y1 - 6}" style="fill:{COL["int"]};font-weight:600">B5 analyse · Parallel</text>')
    s.append(state(xs[4], y1, w, 40, R, "actuel", "engine.analyse"))
    s.append(state(xs[4], y1 + 48, w, 40, R, "proposé", "même graine"))
    s.append(state(xs[1], y1 + 92, w, 40, U, "Awaiting confirmation", "Pass · brouillon"))
    # row 2
    s.append(state(xs[4], y2, w, h, R, "B6 checks + diff", "contrôles durs/souples"))
    s.append(state(xs[3], y2 - 6, w, 60, R, "B7 violations ?", "Choice", kind="choice"))
    s.append(state(xs[2], y2 + 72, w, h, R, "B7 resize", "marge de la transaction"))
    s.append(state(xs[1], y2, w, h, L, "B8 explain", "N5 + numcheck"))
    s.append(state(xs[0], y2, w, h, R, "B9 save", "rapport + audit → S3"))
    s.append(f'<rect class="box" x="{xs[0]}" y="{y2 + 74}" width="{w}" height="40" rx="6" style="stroke:{G}"/>'
             f'<text class="ttl" x="{xs[0] + w / 2}" y="{y2 + 92}" text-anchor="middle" font-size="10.5" style="fill:{G}">FlowBFailed</text>'
             f'<text class="sub" x="{xs[0] + w / 2}" y="{y2 + 105}" text-anchor="middle">Catch de chaque tâche</text>')
    cy = y1 + h / 2
    s.append(arrow(f"M{xs[0] + w},{cy} L{xs[1] - 2},{cy}"))
    s.append(arrow(f"M{xs[1] + w},{cy} L{xs[2] - 2},{cy}", "oui", xs[1] + w + 16, cy - 5))
    s.append(arrow(f"M{xs[1] + w / 2},{y1 + 54} L{xs[1] + w / 2},{y1 + 90}", "non", xs[1] + w / 2 + 6, y1 + 76, anchor="start"))
    s.append(arrow(f"M{xs[2] + w},{cy} L{xs[3] - 2},{cy}"))
    s.append(arrow(f"M{xs[3] + w},{cy} L{xs[4] - 8},{cy}"))
    s.append(arrow(f"M{xs[4] + w / 2},{y1 + 108} L{xs[4] + w / 2},{y2 - 2}"))
    cy2 = y2 + h / 2
    s.append(arrow(f"M{xs[4]},{cy2} L{xs[3] + w + 2},{cy2}"))
    s.append(arrow(f"M{xs[3]},{cy2} L{xs[1] + w + 2},{cy2}", "non", xs[2] + w / 2, cy2 - 5))
    s.append(arrow(f"M{xs[3] + w / 2},{y2 + 54} L{xs[3] + w / 2},{y2 + 96} L{xs[2] + w + 2},{y2 + 96}", "oui", xs[3] + w / 2 + 6, y2 + 78, anchor="start"))
    s.append(arrow(f"M{xs[2] + w / 2},{y2 + 72} L{xs[1] + w / 2 + 20},{y2 + h + 2}"))
    s.append(arrow(f"M{xs[1]},{cy2} L{xs[0] + w + 2},{cy2}"))
    s.append(arrow(f"M{xs[0] + w / 2},{y2 + h} L{xs[0] + w / 2},{y2 + 72}", "erreur", xs[0] + w / 2 + 6, y2 + 64, anchor="start", dash=True))
    s.append("</svg>")
    return "\n".join(s)


def node(x, y, w, h, color, title, l1="", l2="", aws=""):
    """Pipeline step: white card, coloured top rule, title, two lines, AWS badge at the bottom."""
    out = [f'<rect class="box" x="{x}" y="{y}" width="{w}" height="{h}" rx="4"/>',
           f'<rect x="{x}" y="{y}" width="{w}" height="3" rx="1" fill="{color}"/>',
           f'<text class="ttl" x="{x + 10}" y="{y + 19}">{title}</text>']
    if l1:
        out.append(f'<text class="sub" x="{x + 10}" y="{y + 33}">{l1}</text>')
    if l2:
        out.append(f'<text class="sub" x="{x + 10}" y="{y + 45}">{l2}</text>')
    if aws:
        bw = 9 + 5.1 * len(aws)
        out.append(f'<rect x="{x + 10}" y="{y + h - 19}" width="{bw:.0f}" height="13" rx="6.5" fill="#e8f1fb"/>'
                   f'<text x="{x + 15}" y="{y + h - 9.5}" font-size="8.4" font-weight="600" style="fill:#0b6bcb">{aws}</text>')
    return "\n".join(out)


def profile_loop() -> str:
    W, H = 760, 322
    R, L, G, U = COL["rule"], COL["llm"], COL["gate"], COL["user"]
    s = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">', DEFS]
    xs, w, h, y1, y2 = [8, 160, 312, 464, 616], 136, 86, 14, 214
    s.append(node(xs[0], y1, w, h, U, "Utilisateur", "questionnaire, notes,", "questions, transaction", "app.html"))
    s.append(node(xs[1], y1, w, h, U, "Événements", "question, quiz, note,", "profile_edit, order_intent", "S3 · events"))
    s.append(node(xs[2], y1, w, h, R, "A1 · Règles", "champs rule: dérivés", "horizon, capacité, risque", "Lambda"))
    s.append(node(xs[3], y1, w, h, L, "A2 · Lecteurs IA", "N1 comportement, N2", "questions, N3 notes", "Bedrock"))
    s.append(node(xs[4], y1, w, h, G, "A3 · Gate", "abstention, preuves,", "bornes, pas limité", "Lambda"))
    s.append(node(xs[4], y2, w, h, R, "A4 · Règles (bis)", "score de connaissances", "avec N2 s'il est actif", "Lambda"))
    s.append(node(xs[3], y2, w, h, R, "A5 · Limites + trace", "règles et déclaré seuls ;", "N3 chiffré : provisoire", "Lambda"))
    s.append(node(xs[2], y2, w, h, R, "A6 · Nouvelle version", "schéma validé, diff,", "audit du run", "S3 · profiles, audit"))
    s.append(node(xs[1], y2, w, h, L, "Cohérence interne", "C1–C5 (code) + N6 (IA)", "→ info ou warning", "Lambda + Bedrock"))
    s.append(node(xs[0], y2, w, h, U, "Points à clarifier", "questions fixes,", "jamais générées", "app.html"))
    cy = y1 + h / 2
    for i in range(4):
        s.append(arrow(f"M{xs[i] + w},{cy} L{xs[i + 1] - 2},{cy}", hot=(i == 0)))
    cy2 = y2 + h / 2
    for i in range(4, 0, -1):
        s.append(arrow(f"M{xs[i]},{cy2} L{xs[i - 1] + w + 2},{cy2}"))
    gx = xs[4] + w / 2
    s.append(arrow(f"M{gx},{y1 + h} L{gx},{y2 - 2}", "accepté ou borné", gx - 6, 140, anchor="end"))
    s.append(f'<text class="lbl" x="{gx - 6}" y="152" text-anchor="end" style="fill:#de171e">rejet : valeur précédente gardée,</text>')
    s.append(f'<text class="lbl" x="{gx - 6}" y="163" text-anchor="end" style="fill:#de171e">rejet écrit dans l\'audit</text>')
    ux = xs[0] + w / 2
    s.append(arrow(f"M{ux},{y2} L{ux},{y1 + h + 2}", "chaque réponse", ux + 6, 148, hot=True, anchor="start"))
    s.append(f'<text class="lbl" x="{ux + 6}" y="159">= un nouvel événement</text>')
    s.append('<text x="345" y="150" text-anchor="middle" font-size="12.5" font-weight="600" style="fill:#0b2a4a">Boucle d\'affinage du profil (flux A)</text>')
    s.append('<text class="sub" x="345" y="165" text-anchor="middle">relancée à chaque événement · IA seulement si son entrée a changé</text>')
    s.append("</svg>")
    return "\n".join(s)


def lanes() -> str:
    W, H = 760, 200
    R, L, G, T = COL["rule"], COL["llm"], COL["gate"], "#4d6a87"
    s = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">', DEFS]
    w, h, y1, y2 = 136, 70, 10, 112
    s.append(node(8, y1, w, h, R, "Verdict + sections", "décidés par le code,", "avant tout appel IA", "Lambda"))
    s.append(state(166, y1 + 5, 114, 60, R, "Section ok ?", "statut pass", kind="choice"))
    s.append(node(304, y1, w, h, L, "N5 · essai 1", "faits de la section seuls", "Guardrail actif", "Bedrock + Guardrail"))
    s.append(node(464, y1, w, h, G, "Vérificateur V1–V5", "références, chiffres,", "couverture, ton, forme", "Lambda"))
    s.append(node(616, y1, w, h, R, "Texte retenu", "mode llm ou llm_retry", "segments fait / inférence", ""))
    s.append(node(464, y2, w, h, L, "N5 · essai 2", "mêmes faits + liste", "des échecs", "Bedrock + Guardrail"))
    s.append(node(616, y2, w, h, G, "V1–V5 (bis)", "toutes les règles,", "tous les échecs listés", "Lambda"))
    s.append(node(168, y2, 112, h, T, "Gabarit", "texte déterministe,", "passe toujours", ""))
    cy = y1 + h / 2
    s.append(arrow(f"M144,{cy} L166,{cy}"))
    s.append(arrow(f"M280,{cy} L302,{cy}", "non", 291, cy - 5))
    s.append(arrow(f"M224,{y1 + 65} L224,{y2 - 2}", "oui", 230, 96, anchor="start"))
    s.append(arrow(f"M440,{cy} L462,{cy}"))
    s.append(arrow(f"M600,{cy} L614,{cy}", "ok", 607, cy - 5))
    s.append(arrow(f"M532,{y1 + h} L532,{y2 - 2}", "échec", 538, 96, anchor="start", dash=True))
    s.append(arrow(f"M600,{y2 + h / 2} L614,{y2 + h / 2}"))
    s.append(arrow(f"M684,{y2} L684,{y1 + h + 2}", "ok", 690, 96, anchor="start"))
    s.append(arrow(f"M684,{y2 + h} L684,{H - 6} L224,{H - 6} L224,{y2 + h + 2}", "échec, ou blocage du Guardrail", 450, H - 10, dash=True))
    s.append('<text class="lbl" x="372" y="104" text-anchor="middle" font-weight="600" style="fill:#0b2a4a">× 5 facteurs en parallèle</text>')
    s.append('<text class="lbl" x="372" y="117" text-anchor="middle">objectif, horizon, risque,</text>')
    s.append('<text class="lbl" x="372" y="128" text-anchor="middle">contraintes, préférences</text>')
    s.append('<text class="lbl" x="372" y="142" text-anchor="middle">2 appels au plus par section</text>')
    s.append("</svg>")
    return "\n".join(s)



def funnel() -> str:
    W, H = 760, 184
    R, L, G, U = COL["rule"], COL["llm"], COL["gate"], COL["user"]
    s = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">', DEFS]
    steps = [
        (L, "Réponse IA", "un outil JSON,", "contexte limité", "Bedrock", ""),
        (G, "Contrat", "schéma JSON", "validé", "Lambda", "nouvel essai, puis rejet"),
        (G, "Gate", "preuves réelles,", "bornes, pas limité", "Lambda", "valeur précédente gardée"),
        (R, "Autorité", "warning au plus,", "limites hors IA", "Lambda", "plafonné à warning"),
        (G, "Vérificateur", "V1–V5 sur", "le texte", "Lambda", "nouvel essai, puis gabarit"),
        (G, "Guardrail", "refus du conseil", "personnalisé", "Bedrock", "gabarit, sans nouvel essai"),
        (U, "Rapport", "verdict, faits,", "texte vérifié", "S3 · audit", ""),
    ]
    w, h, y1, y2 = 96, 72, 8, 122
    for i, (c, t, a, b, aws, rej) in enumerate(steps):
        x = 8 + i * 108
        s.append(node(x, y1, w, h, c, t, a, b, aws))
        if i < len(steps) - 1:
            s.append(arrow(f"M{x + w},{y1 + h / 2} L{x + 106},{y1 + h / 2}", hot=True))
        if rej:
            s.append(arrow(f"M{x + w / 2},{y1 + h} L{x + w / 2},{y2 - 2}", dash=True))
            s.append(f'<rect x="{x}" y="{y2}" width="{w}" height="40" rx="4" fill="#fef6f6" stroke="#f3c9ca"/>')
            words, lines, cur = rej.split(), [], ""
            for wd in words:
                if len(cur) + len(wd) > 17:
                    lines.append(cur.strip()); cur = ""
                cur += wd + " "
            lines.append(cur.strip())
            for j, ln in enumerate(lines[:2]):
                s.append(f'<text x="{x + w / 2}" y="{y2 + 17 + j * 12}" text-anchor="middle" font-size="9" style="fill:#de171e">{ln}</text>')
    s.append(f'<text class="lbl" x="8" y="{H - 4}">En rouge : ce qui se passe quand une couche refuse. L\'état précédent n\'est jamais effacé.</text>')
    s.append("</svg>")
    return "\n".join(s)


PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Compass · Architecture AWS</title>
<link rel="stylesheet" href="print.css">
<style>
  .n{display:inline-block;width:15px;height:15px;border-radius:50%;background:var(--navy);color:#fff;text-align:center;
     font:600 9px/15px var(--f);margin-right:5px}
  .sec{margin-top:10px}
  .intro{color:var(--muted);font-size:11px;margin:0 0 8px;max-width:170mm}
  .table td.aws{white-space:nowrap;color:var(--blue);font-weight:600}
  .never li{list-style:none;position:relative;padding-left:2px}
  .never li::before{content:"✕";position:absolute;left:-13px;color:var(--red);font-weight:600}
  .grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
  .grid4 .card p{font-size:9.5px;color:var(--muted)}
</style>
</head>
<body>

<!-- 1 · AWS map -->
<section class="page">
  <div class="hdr"><div class="wm"><svg viewBox="0 0 24 24" width="26" height="26"><circle cx="12" cy="12" r="10.5" fill="none" stroke="#0b2a4a" stroke-width="1.8"/><path d="M12 4.5l2.6 7.5h-5.2z" fill="#de171e"/><path d="M12 19.5l-2.6-7.5h5.2z" fill="#0b2a4a"/><circle cx="12" cy="12" r="1.4" fill="#fff"/></svg><span>Compass</span></div><span>Compass · Architecture AWS</span></div>
  <h1>Architecture AWS</h1>
  <p class="lead" style="margin-bottom:4mm">Le cœur de Compass tient en deux pièces : une <b>boucle agentique</b> qui affine le profil
  à chaque événement, et une <b>couche de sécurité IA</b> entre ce profil et le portefeuille. Amazon Bedrock propose, une gate
  ne garde que ce qu'elle peut prouver, et le code fixe les limites et le verdict. Le reste de l'architecture (interface,
  flux transaction, explication) est du contexte. Serverless, décrite en CloudFormation.</p>
  <div class="grid2" style="margin-bottom:5mm">
    <div class="card role l"><span class="tag">Cœur 1 · page 2</span><p class="muted">Boucle d'affinage du profil : l'IA propose, la gate filtre, les règles recalculent, et ce qui reste flou revient à l'utilisateur.</p></div>
    <div class="card role g"><span class="tag">Cœur 2 · page 3</span><p class="muted">Sécurité IA : dix couches entre l'IA et le profil ; une réponse rejetée laisse toujours l'état précédent intact.</p></div>
  </div>
  <div class="card" style="padding:8px">{overview}</div>
  <div class="grid3" style="margin-top:9px">
    <div class="card"><h3><span class="n">1</span>Interface</h3><p class="muted">CloudFront sert <code>app.html</code>. Le bucket du site est privé, lisible par CloudFront seul (OAC).</p></div>
    <div class="card"><h3><span class="n">2</span>Profil et revue</h3><p class="muted">Lambda Function URL : questionnaire, boucle de profil, revue de portefeuille, what-if.</p></div>
    <div class="card"><h3><span class="n">3</span>Transaction</h3><p class="muted">Machine à états Step Functions : chaque étape visible, rejouable, tracée.</p></div>
  </div>
  <div class="pn">1 / 5</div>
</section>

<!-- 2 · profile loop -->
<section class="page">
  <div class="hdr-thin"></div>
  <h2 style="margin-top:4mm">Cœur 1 · La boucle d'affinage du profil</h2>
  <p class="intro">C'est la boucle agentique de Compass. Le profil n'est jamais réécrit par l'IA. Chaque événement relance la boucle : les règles recalculent, les lecteurs IA proposent,
  la gate accepte, borne ou rejette, puis les limites sont reconstruites à partir des règles et des valeurs déclarées.
  Ce qui reste ambigu revient à l'utilisateur sous forme de questions fixes ; sa réponse est un nouvel événement, et le profil s'affine à chaque tour.</p>
  <div class="legend"><span class="u">utilisateur / données</span><span class="r">code déterministe</span><span class="l">Amazon Bedrock</span><span class="g">gate</span></div>
  <div class="card" style="padding:8px">{loop}</div>

  <h2 class="sec">Les lecteurs IA et leur gate</h2>
  <div class="card" style="padding:4px 8px">
  <table class="table">
    <tr><th>Nœud</th><th>Lit</th><th>Propose</th><th>Gate : condition d'acceptation</th></tr>
    <tr><td>N1 comportement</td><td>Questions et intentions des 30 derniers jours (20 max), aucun champ du profil</td><td>Niveau de risque observé, confiance ≤ 0,8</td><td><code>event_id</code> réellement montrés ; un niveau de variation par run au plus ; avertit seulement si confiance ≥ 0,5</td></tr>
    <tr><td>N2 questions</td><td>Chaque question posée (option, désactivée par défaut)</td><td>Sous-scores ; le score est calculé par le code</td><td>Cite son propre événement, citation exacte, effet ≤ ±5 points</td></tr>
    <tr><td>N3 valeurs</td><td>Notes libres de l'utilisateur</td><td>Règles d'exclusion ou de plafond, avec citation</td><td>Citation mot pour mot dans les notes ; règle chiffrée = limite <b>provisoire</b>, plafonnée à warning jusqu'à l'approbation</td></tr>
    <tr><td>N4 intention</td><td>Le texte de la transaction</td><td>Brouillon du formulaire</td><td>Achat seulement, ticker réel, 0 &lt; poids ≤ 100, financement valide ; sinon formulaire vide. L'utilisateur confirme.</td></tr>
    <tr><td>N6 cohérence</td><td>Champs déclarés + constats C1–C5</td><td>Jusqu'à 5 tensions entre champs</td><td>2 champs réels ou plus, citations exactes, sans chiffre ni conseil ; ne change jamais le verdict</td></tr>
  </table>
  </div>
  <div class="grid4 sec">
    <div class="card role g"><span class="tag">Abstention</span><p>« Je ne sais pas » est une réponse valide : l'ancienne valeur reste.</p></div>
    <div class="card role g"><span class="tag">Preuves</span><p>Preuve inventée = réponse entière rejetée.</p></div>
    <div class="card role g"><span class="tag">Bornes</span><p>Énumérations, plages et pas de variation revérifiés par le code.</p></div>
    <div class="card role r"><span class="tag">Estampille</span><p><code>by: llm:&lt;nœud&gt;</code>, version du prompt, modèle, hash d'entrée.</p></div>
  </div>
  <div class="pn">2 / 5</div>
</section>

<!-- 3 · AI safety (core 2) -->
<section class="page">
  <div class="hdr-thin"></div>
  <h2 style="margin-top:4mm">Cœur 2 · La sécurité IA entre profil et portefeuille</h2>
  <p class="intro">Aucune couche ne suffit seule. Chacune suppose que la précédente a échoué ; une réponse rejetée laisse toujours l'état précédent intact.</p>
  <div class="card" style="padding:8px;margin-bottom:9px">{funnel}</div>
  <div class="card" style="padding:4px 8px">
  <table class="table">
    <tr><th>Couche</th><th>Mécanisme</th><th>Où sur AWS</th></tr>
    <tr><td>1 · Entrée</td><td>Chaque nœud ne voit que son contexte : N1 ne voit aucun champ du profil (pas d'ancrage), N5 ne voit que les faits de sa section.</td><td class="aws">Lambda</td></tr>
    <tr><td>2 · Contrat</td><td>Un seul outil par nœud, son schéma JSON comme sortie ; validation, un nouvel essai avec l'erreur, puis <code>LLMOutputInvalid</code>.</td><td class="aws">Bedrock Converse</td></tr>
    <tr><td>3 · Gate</td><td>Abstention, preuves réelles, bornes, pas de variation limité, estampille. Rejet = valeur précédente + entrée d'audit.</td><td class="aws">Lambda</td></tr>
    <tr><td>4 · Autorité</td><td>L'IA ne produit que <code>warning</code>, <code>info</code> ou <code>unknown</code>. Les limites viennent des règles et du déclaré ; seule exception, une règle chiffrée des notes, provisoire et plafonnée à warning.</td><td class="aws">Lambda · test CI</td></tr>
    <tr><td>5 · Ordre</td><td>Verdict et marge calculés avant toute rédaction : un texte ne peut pas changer un résultat.</td><td class="aws">Lambda · Step Functions</td></tr>
    <tr><td>6 · Rédaction</td><td>Vérificateur V1–V5, un nouvel essai, puis gabarit déterministe.</td><td class="aws">Lambda</td></tr>
    <tr><td>7 · Guardrail</td><td>Sujet refusé « recommandation de placement personnalisée » sur l'explication ; blocage = gabarit, sans nouvel essai.</td><td class="aws">Bedrock Guardrails</td></tr>
    <tr><td>8 · Texte fixe</td><td>Titre, marge de la transaction et avertissement légal sont des gabarits.</td><td class="aws">Lambda</td></tr>
    <tr><td>9 · Traçabilité</td><td>Chaque appel journalisé : nœud, modèle, version du prompt, hash d'entrée, cache, latence, tokens. Audit par run.</td><td class="aws">CloudWatch · S3 audit/</td></tr>
    <tr><td>10 · Reproductibilité</td><td>Réponses en cache par hash d'entrée, graine fixe ; la démo et les tests rejouent les réponses enregistrées.</td><td class="aws">S3 · mode mock</td></tr>
  </table>
  </div>

  <div class="grid2 sec">
    <div class="card">
      <h2>Ce que l'IA ne peut jamais faire</h2>
      <ul class="never">
        <li>Produire une violation ou changer un verdict</li>
        <li>Fixer une limite à pleine force sans l'accord de l'utilisateur</li>
        <li>Écrire un chiffre absent des faits</li>
        <li>Citer une preuve qui n'existe pas</li>
        <li>Recommander, classer des choix ou proposer une « prochaine étape »</li>
        <li>Exécuter une transaction</li>
      </ul>
    </div>
    <div class="card">
      <h2>Faits, hypothèses, inférences</h2>
      <p class="muted">Chaque élément du rapport porte son origine : déclaré par l'utilisateur, calculé par une règle (<code>rule:</code>),
      ou inféré par l'IA (<code>llm:</code>, avec sa confiance et ses preuves). Les hypothèses de l'équipe (dérive, calibrations)
      sont étiquetées comme telles, dans le rapport comme dans l'interface. Ce qui ne peut pas être vérifié est listé, pas supposé.</p>
    </div>
  </div>
  <div class="pn">3 / 5</div>
</section>

<!-- 4 · context: trade flow + explanation loop -->
<section class="page">
  <div class="hdr-thin"></div>
  <h2 style="margin-top:4mm">Contexte · Le flux transaction (Step Functions)</h2>
  <div class="legend"><span class="r">code déterministe</span><span class="l">Bedrock, puis contrôle</span><span class="u">attente utilisateur</span><span class="g">échec</span></div>
  <div class="card" style="padding:8px">{flow_b}</div>
  <p class="muted" style="margin-top:6px;font-size:9.6px">Chaque tâche invoque la Lambda Flow B avec le nom de l'étape. B3 rejoue la boucle de profil.
  L'intention n'est utilisée qu'une fois confirmée. La marge (B7) est un fait calculé, jamais une suggestion.</p>

  <h2 class="sec">Contexte · La boucle d'explication</h2>
  <p class="intro">Le verdict est fixé avant tout appel à l'IA. Chaque facteur est rédigé à part, à partir de ses seuls faits, vérifié, puis
  réessayé une fois ; sinon un gabarit prend le relais. Le titre, la marge et l'avertissement ne sont jamais rédigés par l'IA.</p>
  <div class="card" style="padding:8px">{lanes}</div>
  <div class="grid4 sec" style="grid-template-columns:repeat(5,1fr)">
    <div class="card role g"><span class="tag">V1 · Références</span><p>Chaque segment cite un contrôle, un fait ou un événement réel.</p></div>
    <div class="card role g"><span class="tag">V2 · Chiffres</span><p>Chaque nombre existe dans les faits (tolérance d'arrondi).</p></div>
    <div class="card role g"><span class="tag">V3 · Couverture</span><p>Chaque violation et avertissement est mentionné.</p></div>
    <div class="card role g"><span class="tag">V4 · Ton</span><p>Aucune formule de conseil, FR ou EN : « vous devriez », « envisagez »…</p></div>
    <div class="card role g"><span class="tag">V5 · Forme</span><p>Nombre de segments, type, longueur.</p></div>
  </div>
  <div class="pn">4 / 5</div>
</section>

<!-- 5 · services, IAM, deployment -->
<section class="page">
  <div class="hdr-thin"></div>
  <h2 style="margin-top:4mm">Contexte · Services</h2>
  <div class="card" style="padding:4px 8px">
  <table class="table">
    <tr><th>Service</th><th>Ressources</th><th>Rôle</th></tr>
    <tr><td>Amazon Bedrock</td><td><code>ModelArn</code></td><td>API Converse, un outil JSON par nœud. Six nœuds : comportement, questions, valeurs, intention, explication, cohérence.</td></tr>
    <tr><td>Bedrock Guardrails</td><td><code>ExplainerGuardrail</code></td><td>Refus du conseil en placement personnalisé, sur l'explication.</td></tr>
    <tr><td>AWS Lambda</td><td><code>ApiFunction</code> · <code>FlowBFunction</code></td><td>API HTTP (FastAPI via Mangum, Function URL) et étapes du flux transaction. Python 3.11, même code qu'en local.</td></tr>
    <tr><td>AWS Step Functions</td><td><code>FlowBStateMachine</code></td><td>Flux transaction B1 à B9, analyse parallèle, <em>retry</em> et <em>catch</em> par tâche.</td></tr>
    <tr><td>Amazon S3</td><td><code>DataBucket</code> · <code>SiteBucket</code></td><td>Événements, profils versionnés, portefeuilles, rapports, audit sous <code>ca/</code> ; fichiers du site.</td></tr>
    <tr><td>Amazon CloudFront</td><td><code>SiteDistribution</code></td><td>Interface en HTTPS, HTTP/2 et 3, cache, en-têtes de sécurité.</td></tr>
    <tr><td>Amazon CloudWatch</td><td><code>ApiLogGroup</code> · <code>FlowBLogGroup</code></td><td>Journal de chaque appel LLM.</td></tr>
    <tr><td>AWS IAM</td><td><code>AppAccessPolicy</code></td><td>Un rôle par Lambda, un pour la machine à états.</td></tr>
    <tr><td>CloudFormation</td><td>2 stacks</td><td><code>deploy/cloudfront.yaml</code> · <code>deploy/aws/coherence-analyzer.cfn.json</code></td></tr>
  </table>
  </div>

  <div class="grid2 sec">
    <div class="card">
      <h2>Sécurité de l'infrastructure</h2>
      <ul>
        <li><code>bedrock:InvokeModel</code> sur le modèle configuré seulement ; <code>ApplyGuardrail</code> sur le guardrail du stack.</li>
        <li>S3 : lecture, écriture et listage limités au préfixe <code>ca/</code>.</li>
        <li>Chaque Lambda n'écrit que dans son groupe de logs ; la machine à états n'invoque que la Lambda Flow B.</li>
        <li>Buckets privés, chiffrés, versionnés ; TLS obligatoire ; site lisible par CloudFront seul.</li>
        <li>Aucun identifiant dans le dépôt.</li>
      </ul>
    </div>
    <div class="card">
      <h2>Déploiement</h2>
      <p class="muted"><code>deploy/aws/deploy.sh</code> : paquet de code vers S3, puis <code>aws cloudformation deploy</code>.</p>
      <table class="table" style="margin-top:6px">
        <tr><th>Mode</th><th>Usage</th></tr>
        <tr><td><code>mock</code></td><td>Réponses enregistrées : tests et démo hors ligne</td></tr>
        <tr><td><code>record</code></td><td>Appelle Bedrock et enregistre</td></tr>
        <tr><td><code>live</code></td><td>Sur AWS : Bedrock et S3</td></tr>
      </table>
    </div>
  </div>

  <div class="grid2 sec">
    <div class="card">
      <h2>Coûts</h2>
      <ul>
        <li>Paiement à l'usage : aucun serveur toujours allumé.</li>
        <li>Lecteurs IA appelés seulement si leur entrée a changé ; le cache évite de repayer un appel.</li>
        <li>CloudFront en classe de prix 100.</li>
      </ul>
    </div>
    <div class="card">
      <h2>Limites connues</h2>
      <ul>
        <li>Historique de 5 ans ; la dérive est une hypothèse, pas une prévision.</li>
        <li>Biais de survivance dans l'univers de titres.</li>
        <li>Limites calibrées par l'équipe, pas réglementaires (KYC AMF/OCRI hors périmètre).</li>
      </ul>
    </div>
  </div>

  <p class="disclaimer" style="position:absolute;left:14mm;right:14mm;bottom:14mm">Simulation éducative avec des profils fictifs. Ceci n'est pas un conseil financier personnalisé. Aucune transaction n'est exécutée.</p>
  <div class="pn">5 / 5</div>
</section>

</body>
</html>
"""


def main() -> None:
    html = (PAGE.replace("{overview}", overview()).replace("{flow_b}", flow_b())
            .replace("{loop}", profile_loop()).replace("{funnel}", funnel()).replace("{lanes}", lanes()))
    (HERE / "architecture-aws.html").write_text(html, encoding="utf-8")


if __name__ == "__main__":
    main()
