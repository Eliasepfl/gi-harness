# LLM-as-a-Verifier — deep-read (arXiv 2607.05391)

> Lecture approfondie (14 juil. 2026). But : mesurer honnêtement l'intersection avec
> notre vérificateur physique code-only (SPEC_VERIFIER.md, G4_DESIGN.md). Consigne d'Elias :
> **ne pas forcer l'intersection** — « majoritairement orthogonal » est une conclusion valide.
> Le paper RÉSOUT (arXiv + repo GitHub réels, lus ; voir bloc HONNÊTETÉ pour le niveau de
> confiance de chaque chiffre). Ce que j'ai lu vs ce que j'infère est marqué partout.

- **Titre** : *LLM-as-a-Verifier: A General-Purpose Verification Framework*.
- **Auteurs** : Jacky Kwok, Shulu Li, Pranav Atreya, Yuejiang Liu, Yixing Jiang, Chelsea Finn,
  Marco Pavone, Ion Stoica, Azalia Mirhoseini (Stanford / UC Berkeley / NVIDIA).
- **arXiv** : 2607.05391 (v2 HTML lue). Catégories cs.AI / cs.CL / cs.LG / cs.MA / cs.RO.
- **Repo** : github.com/llm-as-a-verifier/llm-as-a-verifier — licence **MIT**, `pip install llm-verifier`.

---

## 1. Cadrage du problème (Abstract, §1)

Thèse : la **vérification** (déterminer si une solution est correcte) est un **nouvel axe de
scaling** au même titre que pre-training / post-training / test-time compute. Le framework fournit
un *feedback fin* pour des tâches **agentiques** (trajectoires longues : tool-use, code, robotique,
décision médicale) **sans entraînement supplémentaire**. Positionnement explicite (§9) contre
« LLM-as-a-Judge » : passer d'un **juge** (opinion discrète, score entier sur une réponse isolée)
à un **verifier** (score *continu*, sur la *trajectoire entière*).

## 2. Composants du framework (§3–4) — ce qui compte pour nous

**a) Score continu par espérance sur les logits (Eq. 3.1).** Au lieu de prendre le token de score
le plus probable, on calcule l'espérance sur la distribution des tokens de score :
`R(x,τ) = (1/CK) Σ_c Σ_k Σ_g p_θ(v_g | x,c,τ)·φ(v_g)`. Trois **axes de scaling** :
- **Granularité G** — G tokens de score ordonnés (ex. G=20) au lieu d'1. N'ajoute aucune
  information mais donne au décodeur un espace plus fin → meilleure séparation positif/négatif,
  moins d'ex-æquo (§4.1).
- **Évaluation répétée K** — moyenne de K passes indépendantes ; estimateur Monte-Carlo, variance
  en O(1/K) (§4.2).
- **Décomposition en critères C** — pour le code : *Specification / Output / Errors* jugés
  séparément puis agrégés (§4.3).
Scores normalisés [0,1] → préférences par **Bradley–Terry** (Eq. 3.2).

**b) Nature du verifier — POINT LOAD-BEARING pour nous.** Il est **prompt-based, pas
execution-grounded** : il *lit* les trajectoires candidates (texte / séquences d'actions) et note ;
il **n'exécute ni code ni tests**, n'appelle aucun oracle. **Reference-free** : à l'inférence, aucun
label de vérité-terrain, aucune solution de référence (§3.2). Requiert l'accès aux **logprobs des
tokens de score** (d'où l'exclusion des API fermées — limite auto-déclarée, §A).

**c) Ranking best-of-N — « Probabilistic Pivot Tournament » (PPT, Algo 1, §3.2).** Ring-pass sur
un cycle hamiltonien (chaque candidat une fois en position « A » et une fois en « B » → biais de
position annulé en espérance), sélection de pivots, rounds pivots. Coût O(Nk) au lieu de O(N²) du
round-robin.

**d) Modèles support.** Repo : Vertex AI (défaut **Gemini 2.5 Flash**) + serveurs OpenAI-compatibles
(vLLM), entrées **multimodales** (image). Verifiers par benchmark cités : Gemini 2.5 Flash (code),
un VLM Qwen (robotique). *[noms de versions exacts = faible confiance, voir HONNÊTETÉ]*

**e) Extension Claude Code (§6).** Proxy d'inférence entre client et provider : pour chaque requête,
dispatche N trajectoires candidates et sélectionne la meilleure via PPT ; UI web de suivi temps réel
via un signal de **progression** (Value-Order Correlation : scores croissants sur rollouts qui
réussissent, plats sur ceux qui stagnent). *[nom d'extension = faible confiance]*

## 3. Domaines & chiffres phares

**Verbatim de l'abstract (haute confiance)** — SOTA sans entraînement :
Terminal-Bench V2 **86.5 %**, SWE-Bench Verified **78.2 %**, RoboRewardBench **87.4 %**,
MedAgentBench **73.3 %**. En RL : feedback dense améliore l'efficacité-échantillon de **SAC**
(robotique) et **GRPO** (raisonnement mathématique).

**Détails via résumeur sur le HTML (confiance moyenne, non recoupés au PDF)** : baselines type
GPT-5.x / Opus 4.x battues de quelques points ; « Oracle Pass@K » ~98.9 % présenté comme *plafond
d'un verifier parfait* (headroom), pas comme baseline d'oracle-code ; réduction des ex-æquo (≈27 % →
0 %) par la granularité ; VOC de progression ~0.85–0.97.

## 4. Comparaison LLM-verifier vs oracle programmatique — le point décisif pour NOTRE thèse

**Il n'y a AUCUN duel tête-à-tête LLM-verifier vs vérificateur par exécution / oracle-code** dans le
paper. Le verifier est reference-free et opère précisément là où **aucun oracle-code bon marché
n'existe** : trajectoires de code open-ended, préférence robotique, décision médicale. Le « Oracle
Pass@K » est un *oracle hypothétique parfait* pour mesurer le headroom, pas un test-suite exécuté.
⇒ **Le paper ne fournit ni preuve pour ni preuve contre notre pari « oracle-code > jugement LLM »** :
il joue dans le régime complémentaire (pas d'oracle disponible), pas dans le nôtre (physique 2D
entièrement observable par code). C'est exactement la frontière de validité qu'OMNI-EPIC nomme
(« where information can be readily accessed through code », cf. SPEC_VERIFIER §Positionnement).

## 5. Limites que les auteurs admettent (§A)

1. **Accès aux logits** requis → exclut plusieurs modèles frontière (API restreintes).
2. Axes de scaling **non exhaustifs** ; la décomposition en critères est *hand-designed*, pourrait
   être apprise / générée par domaine.
3. RL **limité au single-turn**.

**Silence notable** : *aucune* discussion du **reward-hacking / robustesse adverse** — ils traitent
seulement le biais de position (annulé par le swap A/B de PPT). Le cas « une solution trafiquée qui
*paraît* correcte mais viole l'intention » n'est pas adressé. C'est **précisément la faille que nos
oracles-code existent pour éviter** (L2 `get_success` pur insensible au reward-hacking ; L4
anti-collusion ; référé G4 mécanique). Excellent point de contraste pour la soumission.

## 6. Maturité du code

MIT ; jeune (peu de commits, ~1 issue) ; API publique lisible : `select()` (best-of-N),
`compare()` (paire), `track()` / `ProgressTracker` (progression par pas). Modules : `fine_grained_reward.py`,
`pivot_tournament.py`, `progress.py`, `prompts.py`, `benchmarks.py`, `loaders.py`. « General-purpose »
concret = **méthode agnostique au domaine, mais rubriques spécifiques au domaine, écrites à la main**
(Specification/Output/Errors pour le code, progression pour la robotique).

---

## 7. Carte d'intersection (honnête, deux sens)

### 7A. Ce qui pourrait RENFORCER notre stack sans trahir la certification code-only
Principe de garde : un verifier LLM peut se faire berner — c'est exactement ce que nos oracles-code
évitent. Donc il n'est **acceptable QUE là où aucun oracle-code n'existe**, et **jamais comme
certificat** : seulement comme **signal consultatif** alimentant la boucle de réparation.

| Candidat | Ce qu'on adopterait | Coût | Mode d'échec (et où c'est tolérable) |
|---|---|---|---|
| **Rung L4 (contrat d'intention)** ★ | Leur verifier reference-free est *pile* l'outil pour « le jeu implémente-t-il ce que le prompt VOULAIT ? » — subjectif, sans oracle. Adopter score continu + décomposition en critères pour noter la fidélité prompt→design, **en complément** de notre `IFS_2D` déterministe (pas à sa place). | Accès logprobs (Gemini/vLLM) ; ~K passes. | Verifier berné = risque assumé **uniquement** au L4, où aucun code ne peut trancher ; reste advisory, ne casse jamais G0–G3. |
| **Notation du bloc DESIGN** | Scorer les 7 lignes DESIGN vs PROMPT (même juge reference-free). | 1 appel. | Faible enjeu (jamais un certificat) ; utile pour trier. |
| **Qualité visuelle des démos** | Aujourd'hui on juge avec NOS yeux (lecture de frames) ; leur verifier multimodal (repo accepte des images) pourrait scorer le rendu. | Multimodal. | C'est *pile* le jugement-pixels qu'on refuse pour la physique → cantonner au **cosmétique**, jamais à la correction physique. |
| **Diversité des prompts** | Score continu sur le corpus de prompts pour mesurer la couverture. | Léger. | Purement métrique interne. |
| **Cerveau d'attaquant Tier-2 (G4)** | VOC + score continu pour **prioriser** quelles hypothèses d'attaque rejouer d'abord. | Léger. | Compatible : chez nous le LLM **propose**, le référé **tranche** mécaniquement (G4 §1.1). Le verifier n'aide qu'à *ordonner*, il ne juge rien. |
| **Algo PPT (ranking)** | Réutilisable tel quel pour choisir le meilleur des N *jeux générés* quand le signal est subjectif (qualité de design), **indépendamment** de leur verifier. | Négligeable. | Aucun si le signal de tri reste advisory. |

### 7B. Ce qui ENTRE EN CONFLIT avec notre différenciateur
Leur pari : le **jugement LLM reference-free d'une trajectoire EST le verifier**. Notre pari : les
**prédicats code sur l'état-moteur SONT le certificat** ; un LLM ne juge jamais un résultat physique.
Opposition frontale sur les rungs physiques L0–L3 / G0–G3. **Formulation pour la soumission** :
« Là où un oracle-code existe (physique 2D, état-moteur intégralement observable), nous soutenons
que la *vérité d'état-moteur domine le jugement LLM* — et tous les résultats de LLM-as-a-Verifier
sont dans des domaines où *aucun oracle-code bon marché n'existe* (trajectoires de code ouvertes,
préférence robot, décision médicale). Les deux paris sont **complémentaires, pas concurrents** : ils
scalent la vérification là où l'oracle manque ; nous certifions là où l'oracle est présent. » À
souligner : leur **non-traitement du reward-hacking** est *exactement* notre motivation pour les
oracles-code (SPEC_VERIFIER L2/L4 ; G4 référé mécanique).

### 7C. Franchement orthogonal
- La contribution **RL (récompense dense pour SAC/GRPO)** — nous n'entraînons rien.
- Le **score continu par espérance de logits** comme technique de calibration — orthogonal à nos
  prédicats binaires purs.
- La **course au SOTA** sur Terminal-Bench / SWE-Bench / MedAgentBench — autre domaine.

---

## 8. Verdict + expériences

**Verdict : ADAPT (étroit, L4 + bloc DESIGN uniquement) + CITE (contrepoint « verifier
reference-free » qui aiguise notre positionnement).** Ni adoption en gros (ce serait trahir le
code-only sur la physique), ni ignorer (leur silence sur le reward-hacking et leur régime
« pas d'oracle » sont des arguments directs pour NOTRE thèse). Le mot « general-purpose » ne
recouvre pas notre cas : ils sont domain-agnostic *en méthode* mais reference-free *en jugement* —
là où nous avons justement un oracle.

**3 expériences petites, classées, jouables DANS ce harnais :**
1. **Sonde de fidélité prompt→design au L4 (la plus informative).** Faire noter par leur verifier
   (`compare`/`select`, ou une réimplémentation du score-continu + décomposition-en-critères) la
   fidélité PROMPT↔DESIGN des 6 jeux vitrine, puis **confronter** au jugement manuel d'Elias **et**
   à notre contrat observé déterministe (`IFS_2D`). Mesure clé : le juge LLM est-il d'accord avec
   l'oracle-code là où l'oracle existe ? Chaque désaccord = donnée directe « juge LLM vs oracle-code »
   → matière première de la soumission.
2. **Ex-æquo comme diagnostic de réparation.** Scorer en continu les N candidats de réparation d'UN
   jeu qui échoue ; tester si le classement prédit lequel passe G0–G3. Si oui → ordonner les
   candidats de réparation par ce score (économie d'itérations), le verdict restant 100 % code.
3. **Priorisation d'attaquant G4.** Utiliser le score continu / VOC pour **ordonner** les hypothèses
   d'attaque avant le rejeu mécanique ; mesurer le gain de finding-rate par appel LLM vs ordre
   aléatoire. Le référé reste mécanique (aucune concession à l'asymétrie générateur/vérifieur).

---

## 9. Bloc HONNÊTETÉ (lu vs inféré vs inaccessible)

**Lu réellement** : abstract arXiv (verbatim), texte intégral HTML v2 (via résumeur petit-modèle),
page/README GitHub (via résumeur). Fichiers projet lus directement : `SPEC_VERIFIER.md`,
`harness/gen/prompts/contract.md` & `design_block.md`, `notes/adversarial/G4_DESIGN.md`.

**Haute confiance (verbatim ou structurel)** : titre, auteurs, catégories ; les 4 chiffres phares
(86.5 / 78.2 / 87.4 / 73.3) ; licence MIT ; noms de l'API (`select/compare/track/ProgressTracker`) ;
nature **reference-free + prompt-based (pas d'exécution)** du verifier ; les **3 axes** (G/K/C) et
Bradley–Terry ; la limite « accès logits requis » ; **l'absence de duel vs oracle programmatique**.

**Confiance moyenne (résumeur sur HTML, non recoupé au PDF brut)** : valeurs des baselines et
« Oracle Pass@K », taux d'ex-æquo, corrélations VOC, pseudo-code exact de PPT, versions précises des
modèles verifiers (ex. « Qwen 3.x », « GPT-5.x », « Opus 4.x »), le nom de l'extension Claude Code,
le compte d'étoiles du repo. **Plausibles mais à re-vérifier au PDF** ; traiter les cellules de
tableau comme indicatives, pas comme citations.

**Inférence de ma part (étiquetée)** : le cadrage « complémentaires, pas concurrents » ; le fait que
L4 + bloc DESIGN soient le *seul* bon endroit d'adoption ; que leur silence sur le reward-hacking
appuie notre motivation oracle-code.

**Pas pu faire** : recouper les chiffres au niveau PDF ; exécuter le code du repo ; confirmer le
contenu exact des fichiers du repo au-delà du résumé README.

*Fichier : `notes/papers/LLM_AS_A_VERIFIER.md` — seul fichier créé par cette tâche.*
