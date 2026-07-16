# MORNING REPORT — nuit du 2026-07-16

## 🏆 VERDICT FINAL (fix3db, funnel entièrement réparé)
- **PREMIER JEU 3D CERTIFIÉ**: `a_3d_game_fly_a_small_craft_through_a_se` —
  COMPLETED, witness 239 ticks, 4 corrections. Capture GIF en cours
  (~/orcd/scratch/gi/demos/first3d/). Toute la chaîne de la nuit y mène.
- parking 3D: ENV_ERROR — dead actions steer_left/right (5 contextes probés):
  candidat n°1 du difficulty/liveness travail 3D (le steering d'une voiture
  3D immobile ne fait rien: même classe que 'brake', peut-être probe à
  étendre OU vraie voiture cassée — à trancher).
- drone 3D: UNSOLVED — 0/2088 épisodes au premier milestone: design trop dur,
  le difficulty auto-tuner (piste E) est maintenant LE chantier.

## 🎯 L'histoire de la nuit en une ligne
Le biais 2D était NOTRE bug (un "2D" hardcodé dans le premier message) ; une
fois tué, le modèle écrit du 3D immédiatement — et la 3D a révélé deux couches
d'infra jamais testées (wire JSON non-fini: FIXÉ ; déterminisme physique 3D:
agent en cours).

## Chronologie des preuves (tout poussé sauf mention)
1. long3d (tes prompts longs): 0/3 "3D" explicites -> 3D. Fidélité prompt 79%.
   -> LONG3D_GAP_ANALYSIS.md: le gap le plus flagrant = dimensionnalité,
   cause = `_first_user_msg` hardcodait "Design an original 2D physics game".
2. FIX du hardcode + préservation des attempts mid-run -> poussé (96 verts).
3. fix2d A/B: **3/3 jeux Node3D** — mais 2 VERIFY_ERROR (wire) + 1 INVALIDATED
   (mes pushes mid-run — règle arbre-gelé re-apprise, gel appliqué ensuite).
4. FIX wire: `_f`/`_num` sérialisent inf/nan -> null (serve_game.gd) — poussé
   (92 verts). Premier bug de l'ère 3D.
5. fix3d (relance propre, gel des pushes): 3/3 3D, zéro erreur infra-wire.
   Échecs = vrai game design 3D:
   - fly-rings: oscillation difficulté (trop dur 0/1440 -> trop facile
     single-action) -> besoin du difficulty auto-tuner (piste E).
   - parking + drone: **non-déterminisme 3D** (deltas 0.046 / 5.9e-05) —
     probablement PhysicsServer3D threading = à PINNER CÔTÉ HOST.
     -> AGENT EN COURS (worktree, fix + tests + DETERMINISM_3D.md).
   - drone: stuck classique left_start->weaving_spires (le diagnostic
     checkpoint-pair marche en 3D).

## Livrables de la nuit (tous poussés)
- notes/CREATIVE_DIRECTIONS.md (agent Fable): flagship **THE ATLAS** (carte QD
  sur comportement certifié; breeding/genesis ciblé cellules vides).
- **Atlas MVP construit + mergé**: harness/atlas/*, atlas.svg (notes/
  atlas_mvp.svg), COVERAGE 25% (9/36), frontières vides nommées; 16 tests.
- notes/LONG3D_GAP_ANALYSIS.md (fidélité 79%, taxonomie, pistes A-F).
- notes/engines/VARIETY_FORCING.md: verdict menu (seul l'axe dimension passe
  ta barre anti-hardcoding), reco staged: pin-dimension Stage 0 -> QD Stage 1.
- notes/engines/ASSET_CREATION_3D.md + godotworld/mesh_lib.gd: voiture 72 tris
  en pur GDScript, preuve harness/demo/mesh_proof.png; reco = bibliothèque
  dresser routée par route_assets (Blender/MCP rejetés, raisons données).

## DÉCISIONS QUI T'ATTENDENT (Elias)
1. **Atlas breeding/genesis** (la moitié créative active): go/no-go.
2. **Pin-dimension Stage 0** sur prompts silencieux (tirage seedé 50/50):
   le "honor named dim" est déjà prouvé par fix3d; le tirage est ton appel.
3. **mesh_lib -> dresser**: intégrer les proxys voiture/spire/anneau au
   visual_dress (petit chantier, preuve déjà rendue).
4. Piste D (plafond modèle GLM vs Opus): l'A/B n'est plus bloqué par la
   dimension; utile surtout si le design 3D reste faible après le fix
   déterminisme + tuner difficulté.

## File d'attente technique
- Agent déterminisme 3D (en cours) -> merger à l'atterrissage.
- Difficulty auto-tuner (piste E) — 3/6 long3d + fly-rings 3D le réclament.
- harden --g3 (num_envs=8, jobs séparés) sur les certifiés récents.
- Captures des certifiés long3d (maze, platformer) -> day3.
- Fixes: skills-key dans le JSON résultat; port serve éphémère (bind 0).
- Swarm adopts restants: analyzer-errors -> hint G0; géométrie vérité-moteur.
