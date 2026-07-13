# Spec consolidée — Vérificateur physique code-only en boucle fermée

> Document de travail (13 juil. 2026). Synthèse des 4 lectures approfondies :
> GameGen-Verifier (arXiv 2605.07442), PhyScensis (ICLR 2026, 2602.14968),
> ScriptDoctor (IEEE CoG 2025, 2506.06524) + OMNI-EPIC (ICLR 2025, 2405.15568),
> SimWorld Studio (2605.09423) + Intent Fidelity (2605.09360).
> ⚠️ Les constantes marquées `[ing.]` sont des choix d'ingénierie à calibrer — les papiers ne les fournissent pas.

## Positionnement (pour le README de soumission)

Aucun système publié ne ferme la boucle de correction physique sans VLM :
- Code2Worlds → VLM-Motion Critic sur vidéo rendue.
- SimWorld Studio → pile hybride, moitié VLM sur screenshots.
- GameGen-Verifier → juge principal = VLM sur screenshots (son abstract laisse croire l'inverse ; le texte complet le contredit).
- OMNI-EPIC → rejette explicitement les VLM (« not yet accurate enough ») et valide le succès en code, mais ne vérifie pas la *cohérence physique* de la scène.

Notre vérificateur est **100 % assertionnel** : il n'interroge que l'état du moteur. Divergence délibérée et défendable : en 2D simulée, l'état vrai est intégralement observable par code (positions, vitesses, contacts) — le domaine de validité idéal identifié par OMNI-EPIC lui-même (« where information can be readily accessed through code »).

Justification empirique de la couche intention : ~40 % des sorties « réparées par exécution seule » tournent encore avec une physique fausse (FalseExec, baseline Exec-Repair du papier Intent Fidelity : 40.0 / 40.0 / 39.1 % selon le modèle). Exécutabilité et fidélité d'intention sont séparables.

## Vue d'ensemble — entonnoir à 5 couches (coût croissant, arrêt au premier échec)

```
scène candidate (code généré par LLM)
  → L0 statique      (sandbox + scene graph)         ~ms
  → L1 settling      (simulation sans input)         ~0.1 s
  → L2 objectif      (get_success bien formée)       ~ms
  → L3 solvabilité   (injection d'état + recherche)  ~s
  → L4 intention     (contrat gelé vs observé)       ~s
  → verdict 4-aire + feedback JSON → boucle de réparation
```

Verdict 4-aire (AWM) : `COMPLETED / PARTIAL / AGENT_ERROR / ENV_ERROR` — la boucle de réparation sait quoi régénérer.

---

## L0 — Statique (sandbox + scene graph) [SimWorld, ScriptDoctor]

**Sandbox (prérequis absolu — le code est généré par LLM, donc non fiable) :**
- Exécution en sous-processus dédié, jamais dans l'orchestrateur.
- Timeout dur (kill), cap mémoire. Sous Windows : watchdog + kill ; idéalement WSL2/Docker.
- AST-scan avant exécution : whitelist d'imports (`pymunk`, `math`, `numpy`) ; rejet de `os`, `sys`, `subprocess`, `open`, `socket`, `eval`, `__import__`. (Équivalent durci du parseur Lark de ScriptDoctor : valider la forme avant d'exécuter.)
- Espace physique neuf + seed fixe + dt fixe par unité de vérification → déterminisme, parallélisation triviale.

**Checks sur le scene graph (avant simulation) :**
| # | Check | Calcul | Seuil |
|---|---|---|---|
| C1 | No-collision initial | profondeur de pénétration par paire (`shapes_collide`) | fail si d > 0.5 px `[ing.]` |
| C2 | In-bounds | AABB ⊆ rectangle-monde | strict |
| C3 | Counts | nb d'entités par type vs spec | exact |
| C4 | Entités nommées | chaque entité requise existe avec un handle stable | exact |

## L1 — Settling (stabilité sous simulation) [PhyScensis]

Le *drop-and-settle* EST le solveur physique (MVP PhyScensis) : pas d'optimisation continue, on laisse la physique résoudre.

- Simuler **300 steps à dt=1/60 s** (~5 s simulées) sans aucun input `[ing.]`.
- Par objet censé être au repos : Δpos < 2 % de sa taille caractéristique `[ing.]` (proxy papier : Settle Distance ≈ 1 mm), Δangle < 0.15 rad, |v| et |ω| finales < 1e-2 `[ing.]`.
- **Prédicats relationnels qui doivent survivre au settling** (analogues 2D de PhyScensis) :
  - `CONTACT(A,B)` : `shapes_collide` / arbiters non vides.
  - `ON(A,B)` : contact + normale ~verticale (|n.x| < 0.3) + CoM de A projeté dans l'intervalle de contact [x_min, x_max] (analogue 2D de « CoM dans l'enveloppe convexe ») + support_ratio optionnel.
  - `IN(A,cont)` : AABB de A ⊂ intérieur + contact avec le fond + pas de pénétration des parois.
- **Oracles invariants toujours actifs** (indépendants de tout LLM — filet de sécurité) :
  - C8 pas de NaN/explosion (|v| < v_max, positions finies),
  - C9 pénétration max en cours de rollout < seuil,
  - C10 repos atteint si la commande l'implique (KE totale < ε),
  - C11 KE non croissante sous damping (invariant métamorphique).
- Option si temps : score de stabilité probabiliste — K=8-16 perturbations gaussiennes sur (x, y, θ, masse, friction), score = fraction stable `[ing.]`. À couper en premier si le budget serre.

## L2 — Objectif (`get_success`) [OMNI-EPIC]

- Le LLM génère 3 artefacts **séparés** : `build_scene(space) -> refs`, `get_success(space, refs) -> bool`, `available_actions`.
- `get_success` = **prédicat binaire pur** : lit l'état, ne modifie jamais `space`, ne participe à aucun coût/reward → insensible au reward hacking (distinction load-bearing d'OMNI-EPIC).
- Vocabulaire d'état : `body.position/velocity/angle`, distances à seuil, contacts via collision handlers qui posent des flags (patron `all(self.debris_placed) and norm(...) < 1.0` d'OMNI-EPIC, transposé).
- Sanity checks L2 : `get_success` ne doit PAS être vraie à t=0 (objectif déjà atteint = scène dégénérée) ni invariablement fausse sur états injectés triviaux.

## L3 — Solvabilité [GameGen-Verifier + ScriptDoctor]

**L3a — Tests par injection d'état (règles, pas atteignabilité).** Keypoints = triplets de Hoare `(P, a, Q)` extraits par LLM sous contraintes C1 constructibilité / C2 bornage / C3 vérifiabilité, puis ancrés en unités exécutables. Le LLM émet le triplet ; le jugement est du code pur (**divergence assumée vs le juge VLM du papier**).
- Injection : `set_state`/`teleport`/`spawn` + `space.reindex_shapes_for_body` obligatoire après téléportation.
- Interaction bornée : `apply(action)` puis `step(n)`, n ≤ 240 `[ing.]`.
- Sémantique de **falsification** : un keypoint échoué = témoin falsifiant, propagé aux keypoints avals qui en dépendent (non exécutés). Un pass global ≠ preuve, = « aucune violation exposée ».
- **Mitigation de la faille dominante** (complétude d'extraction) : bibliothèque de templates de keypoints par type de mécanique (contact-déclencheur, transition de phase, persistance d'état, invariant physique) en plus des keypoints générés.

**L3b — Oracle d'atteignabilité (escalade, arrêt au premier succès) :**
1. **Géométrique** : grille/PRM filtrée par `point_query`/`segment_query` → A* de l'agent vers la région-succès. Positif = solvable (borne inf) ; négatif ≠ preuve → escalader.
2. **Kinodynamique** : best-first sur états quantifiés (ε spatial, δ vitesse — équivalent continu des « unique nodes »), macro-actions = action discrète + N steps ; heuristique = distance A* du niveau 1 ; plafonds durs : 50-200k nœuds `[ing.]` (analogue du 1M de ScriptDoctor), timeout mural 5-30 s.
3. **Fallback RRT/random-shooting** si branchement explosif — oracle incomplet, documenté comme tel.
- **Filtre anti-trivialité** (généralise le « >10 coups » de ScriptDoctor) : rejeter si solution < K macro-actions ou diversité d'actions trop faible `[ing.]`.
- Retour : `{solvable, solution_len, nodes_expanded, wall_time, solution_trace}` — la trace sert aussi à L4.

## L4 — Intention (contrat gelé vs contrat observé) [Intent Fidelity + SimWorld]

Le différenciateur. Répond à la limite auto-déclarée de ScriptDoctor : « les jeux les plus complexes tendent à être solvables *à cause* de leurs mécaniques cassées ».

- **Intent Compiler** : appel LLM dédié qui ne voit QUE la commande texte (jamais le code de scène) → contrat = liste de propriétés typées, chacune instanciant un **template d'une bibliothèque figée et auditée** (count, on_top_of, stack_vertical, in_bounds, comes_to_rest, stable_under_gravity, must_move, must_pass_through_zone, …). Le contrat est **gelé** (hash) avant génération de la scène.
- **Contrat observé** : reconstruit **déterministiquement** depuis le scene graph + rollout + trace L3b, par du code pur. Aucun LLM dans le jugement (analogue de la table Kernel→opérateur du papier).
- **Score** : `IFS_2D = 1 − Σ w_j·1[fail_j] / Σ w_j` ; poids forts sur le structurel (comptes, relations), faibles sur le quantitatif. Seuil τ = 0.85, N_max = 2 raffinements, **regression guard** (garder le meilleur candidat). Rapport de violation dans les noms de variables du candidat.
- **Anti-collusion, par ordre de priorité** (1+2+3 = 80 % de la garantie ; 7 quasi gratuit ; 6 = démo forte) :
  1. Séparation informationnelle stricte : le Scene Coder ne voit jamais le contrat.
  2. Checks non génératifs : le LLM sélectionne des templates, n'écrit jamais le code d'un check.
  3. Jugement = comparaison contrat gelé vs contrat observé, 100 % code.
  4. (polish) Familles de modèles différentes pour Compiler et Coder.
  5. (polish) Quorum k-échantillons sur l'extraction du contrat.
  6. Agent de propriétés adverse : cherche une propriété impliquée par la commande que la scène viole hors contrat.
  7. Oracles invariants C8-C11 toujours actifs, qu'aucune commande ne peut désactiver.
- Limite à documenter honnêtement (héritée du papier) : IFS = fidélité structurelle (« la scène fait ce que la commande décrit »), pas un certificat que la commande décrit une physique réaliste.

## Boucle de réparation [OMNI-EPIC + ScriptDoctor + SimWorld]

- Budget : compilation ≤ 5 itérations (traceback complet en feedback) ; budget global 3-5 par scène `[ing.]` (ScriptDoctor : 10 ; retries empiriques PhyScensis : ~1.04 ± 1.41 → plafond bas réaliste).
- Échec terminal → **jeter la scène et régénérer une nouvelle tâche** (OMNI-EPIC), pas d'acharnement.
- Le feedback JSON (verdict 4-aire + checks par couche + `hint` en langage naturel + `free_regions` pour reproposer) **réintègre la trajectoire comme observation suivante** de l'agent codeur (SimWorld) — un seul canal.

## Contrat SDK (exigences du vérificateur sur le moteur)

```python
class SceneSDK:
    # introspection white-box
    def list_entities(self) -> list[str]; def get(self, name) -> EntityView
    # injection d'état (L3a)
    def set_state(self, name, *, pos=None, vel=None, angle=None, **fields)
    def teleport(self, name, pos)          # + reindex obligatoire
    def spawn(self, kind, *, pos, **f) -> str; def destroy(self, name)
    def set_flag(self, key, value); def get_flag(self, key)
    # interaction bornée
    def apply(self, action); def step(self, n=1, dt=None)
    # requêtes déterministes (base des assertions — jamais de pixels)
    def query(self, name) -> dict          # pos, vel, contacts, sleeping
    def contacts(self, a, b) -> bool; def events(self) -> list[dict]
    def reset(self, seed)                  # isolation + déterminisme
```

Enveloppe jouable : Gymnasium 5-tuple `(obs, reward, terminated, truncated, info)`, observation **vectorielle** depuis le scene graph (pas de pixels), `info["contract_satisfied"]` évalué à chaque pas → la vérification reste fermée aussi à l'exécution.

## Constantes récapitulatives (toutes `[ing.]`, à calibrer)

| Paramètre | Valeur initiale | Origine |
|---|---|---|
| Settling | 300 steps @ 1/60 s | PhyScensis (non chiffré ; proxy Settle Distance ~1 mm) |
| Δpos toléré | 2 % taille objet | « no large displacement » |
| Vitesses repos | < 1e-2 | équivalent robuste de KE≈0 |
| Interaction bornée L3a | ≤ 240 steps | C2 « courte » non chiffrée dans le papier |
| Nœuds kinodynamique | 50-200k | ScriptDoctor : 1M (à réduire) |
| Anti-trivialité | ≥ K macro-actions | ScriptDoctor : > 10 coups |
| IFS : τ / N_max | 0.85 / 2 | Intent Fidelity (verbatim) |
| Réparation compile / globale | ≤ 5 / 3-5 | OMNI-EPIC / ScriptDoctor |
| Stabilité probabiliste | K = 8-16 (optionnel) | PhyScensis (non chiffré) |
