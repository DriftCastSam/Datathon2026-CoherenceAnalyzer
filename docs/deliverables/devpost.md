# Compass : votre portefeuille dit-il ce que vous voulez ?

**Elevator pitch (182 caractères, limite Devpost : 200)**

> Votre portefeuille dit-il ce que vous voulez ? Compass vous montre, avec vos propres chiffres et vos propres mots, où il s'en écarte. L'IA écoute, des règles vérifient, vous décidez.

---

## Inspiration

Tout investisseur a une volonté : un horizon, un niveau de risque qu'il accepte, des objectifs, des valeurs. Il l'exprime une fois dans un questionnaire, parfois dans une note (« pas seulement de la tech »), puis il investit. Transaction après transaction, le portefeuille s'éloigne de cette volonté, et personne ne le lui montre.

Le cas qui nous a marqués, c'est **Robert** : 59 ans, risque déclaré faible à modéré, horizon de 4 ans. Robert veut mettre **10 % de son portefeuille dans NVDA**, financé par son fonds obligataire ZST. Ce n'est pas une mauvaise idée en soi. Le problème, c'est que cette transaction ne ressemble pas à la personne que Robert a dit être.

Lui répondre « oui » ou « non » ne sert à rien. Ce dont Robert a besoin, c'est d'un **miroir** : voir clairement, avec ses propres chiffres, l'écart entre ce qu'il a déclaré vouloir et ce que son portefeuille ferait. Ensuite, la décision lui appartient.

## Ce que fait Compass

Compass confronte un portefeuille, ou une transaction envisagée, à **la volonté de l'investisseur**, et lui montre où les deux ne concordent plus.

**1. Comprendre ce que l'investisseur veut.** Compass rassemble ce que l'utilisateur déclare (âge, horizon, objectifs, tolérance au risque, connaissances, exclusions) et ce qu'il exprime en chemin : ses notes libres, ses questions, ses transactions. Chaque geste devient un événement et affine le profil. Quand quelque chose reste ambigu, Compass pose une question fixe au lieu de supposer.

**2. Montrer l'écart, clairement.** Pour chaque point où le portefeuille s'éloigne de cette volonté, Compass indique :
- **quelle limite** est franchie, et pourquoi c'est la sienne (elle découle de ce qu'il a déclaré) ;
- **l'avant et l'après** de la transaction, côte à côte ;
- **ses propres mots** quand une note est contredite ;
- **la marge** restante avant la première limite franchie ;
- une **explication en langage clair**, dont chaque chiffre est vérifié.

**3. Laisser la décision à l'investisseur.** Compass décrit, il ne prescrit pas. Il ne dit jamais d'acheter, de vendre ou de rééquilibrer, et n'exécute aucune transaction. Ce qu'il ne sait pas, il le dit (« inconnu ») au lieu de le deviner.

### L'exemple de Robert

Robert veut passer NVDA de 0 % à 10 %. Compass répond **Incohérent**, et montre pourquoi :

| | Avant | Après | Sa limite |
|---|---|---|---|
| Poids de NVDA | 0 % | 10 % | max 8 % |
| Revenu fixe | 35 % | 25 % | min 30 % |
| Volatilité annuelle | 7,3 % | 9,8 % | max 9 % |
| Pire baisse historique | -7,7 % | -13,4 % | max -12 % |
| Part du risque tech | 8,4 % | 41,7 % | max 30 % |
| Valeur à l'horizon, scénario défavorable | 367 k$ | 317 k$ | plancher 360 k$ |

6 limites franchies, 2 avertissements IA, 1,5 % de marge avant le plancher. Robert voit exactement en quoi cette transaction l'éloigne du profil qu'il a déclaré.

### Deux autres écarts

- **Amira** accepte un risque élevé, mais pas n'importe lequel : sa concentration en tech contredit sa propre note « pas seulement de la tech », et sa part de crypto dépasse son plafond. Compass lui renvoie ses propres mots.
- **Mateo** a des exclusions ESG. Un titre du secteur de l'énergie reste **inconnu** tant qu'aucune étiquette sourcée ne le classe : Compass ne prétend pas savoir ce qu'il ne sait pas.

## Pourquoi on peut lui faire confiance

Pour montrer un écart à quelqu'un, il faut que cet écart soit vrai. Compass sépare donc strictement ce que l'IA fait de ce qui décide :

- **L'IA écoute :** des nœuds Amazon Bedrock lisent les notes, les questions et les intentions de transaction pour proposer ce qu'elles révèlent de la volonté de l'investisseur.
- **Une gate ne garde que ce qu'elle peut prouver :** chaque proposition doit citer un vrai événement ou un extrait exact des notes de l'utilisateur. Sinon, elle est rejetée et la valeur précédente est conservée.
- **Les règles fixent les limites :** les limites viennent uniquement des règles et de ce que l'utilisateur a déclaré, jamais de l'IA. Un test le garantit.
- **L'IA ne bloque jamais :** une vérification issue de l'IA peut avertir, pas déclarer une violation.
- **Les chiffres sont vérifiés :** chaque nombre de l'explication doit exister dans les faits calculés, sinon Compass utilise un gabarit.
- **Aucun conseil :** un Guardrail Bedrock refuse le conseil en placement personnalisé.

## Comment nous l'avons construit

- **Cœur en Python 3.11 :** règles, vérifications de cohérence, moteur de risque (5 000 trajectoires simulées, tests de résistance, transparence des FNB, frais) et simulateur de transaction, en fonctions pures et déterministes.
- **Amazon Bedrock (API Converse, un seul outil JSON)** pour six nœuds encadrés : comportement, qualité des questions, valeurs tirées des notes, intention de transaction, explication et cohérence interne. Chaque sortie passe par la gate.
- **AWS serverless, décrit en CloudFormation :** CloudFront sert le tableau de bord depuis S3 ; une Lambda (FastAPI) gère le profil, la revue du portefeuille et le what-if ; **AWS Step Functions** déroule le flux de transaction étape par étape, visible et rejouable ; S3 versionné et chiffré pour les profils et l'audit ; CloudWatch pour suivre les appels IA ; IAM en moindre privilège.
- **Reproductible :** réponses LLM en cache, température 0, graine fixe. Tout le pipeline et toute la suite de tests tournent hors ligne en mode `mock`.
- **Interface :** tableau de bord, questionnaire, clavardage what-if et vérification de plusieurs transactions à la fois.

## Les défis rencontrés

- **Montrer un écart sans faire la leçon.** La ligne est mince entre « cette transaction dépasse votre limite » et « vous devriez réduire ». Il a fallu un travail minutieux sur les prompts, un guardrail et un avertissement au texte fixe pour rester du côté de la description.
- **Comprendre la volonté sans l'inventer.** Les modèles citent volontiers des événements qu'on ne leur a jamais montrés. Nous avons resserré la gate pour qu'une proposition ne s'appuie que sur ce que le modèle a réellement vu.
- **Les notes chiffrées.** Quand un utilisateur écrit lui-même une limite dans une note, elle exprime clairement sa volonté, mais c'est l'IA qui la lit. Nous l'avons promue en limite **provisoire**, qui avertit seulement tant que l'utilisateur ne l'a pas approuvée.
- **Dire « inconnu » honnêtement.** Il est tentant de deviner si un titre relève de l'énergie ou de la tech. Nous avons choisi de répondre `unknown` tant qu'aucune étiquette sourcée et saisie par un humain n'existe.
- **Des chiffres justes dans le texte.** Une explication en langage clair ne vaut rien si un seul chiffre est faux. La vérification des nombres a demandé une tolérance d'arrondi, un nouvel essai et un gabarit de repli.
- **La plomberie serverless.** Permissions de la Function URL, preflight CORS et copie de travail sur S3 n'ont cassé qu'une fois déployés.

## Nos fiertés

- Le cas de Robert fonctionne de bout en bout : l'écart est montré, chiffré, expliqué et remis dans le contexte de son propre profil, sans oui ni non et sans conseil.
- Compass renvoie à l'investisseur **ses propres mots** quand son portefeuille les contredit.
- Une IA utile qui n'a jamais le dernier mot : une réponse rejetée laisse toujours l'état précédent intact et est consignée dans l'audit.
- Une suite de tests entièrement hors ligne qui vérifie chacune de ces garanties.

## Ce que nous avons appris

- Les investisseurs n'ont pas besoin qu'on décide à leur place, mais qu'on leur montre clairement où ils en sont par rapport à ce qu'ils veulent.
- « Inconnu » est une réponse valable, et elle inspire plus confiance qu'une supposition assurée.
- Encadrer un LLM, c'est surtout du code ordinaire : schémas, preuves, plafonds et solutions de repli.
- Si chaque chiffre vit dans les faits calculés, vérifier une explication devient simple.

## La suite pour Compass

- Tester avec de vrais investisseurs la façon dont ils lisent l'écart et la marge avant la première limite.
- Suivre l'écart dans le temps : alerter quand le portefeuille dérive lentement de la volonté déclarée, même sans transaction.
- Un parcours d'approbation des limites provisoires, pour que l'investisseur transforme ses propres notes en règles fermes.
- Des vues multicomptes (REER, CELI, non enregistré), en dollars canadiens.

---

*Simulation éducative avec des profils fictifs. Ceci n'est pas un conseil financier personnalisé. Aucune transaction n'est exécutée.*
