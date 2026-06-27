# État — cartographie case-level via dofus-map : ✅ LIVRÉE (2026-06-27)

La piste dofus-map `groupId=0` est **intégrée et validée**. Les vrais counts par
map remplacent la répartition sous-zone uniforme pour 77/85 ressources.

## Énigme des espaces : RÉSOLUE
Dans un coordspec `x:y1 y2 y3`, le `x` est suivi de **plusieurs y séparés par des
espaces**, tous avec le même count et le même x.
Ex. `4*0:9 -21+3:22 31` = count 4 aux cases (0,9),(0,-21),(3,22),(3,31).
Décodeur : `body.split('_')` → `count*spec+spec` → `spec = x:ys` → `ys.split()`.
Voir `scripts/build_dofusmap_counts.py:decode` + test `tests/test_dofusmap.py`.

## Ce qui a été fait
1. **`scripts/build_dofusmap_counts.py`** (nouveau, autonome, cache disque sous
   `data/cache/dofusmap/`) : crawl des 80 ressources via `groupId=0`, décodage,
   sortie `data/dofusmap_counts.json` keyé par slug du nom dofus-map. Inclut un
   contrôle d'overlap avec `world_maps.json` → **valide que les coords dofus-map ==
   coords DofusDB worldMap=1** (la plupart 90-100% in-world).
2. **Bridge** (dans `build_dofusdb_dataset.py:dofusmap_keys`) : `Bois de Frêne`↔
   `Frêne`, `Crabe`↔`Crabe Sourimi`, `Raie`↔`Raie Bleue`, etc. → 77 ressources
   matchées avec données case-level.
3. **Cells** (`build_dofusdb_dataset.py`) : vrais counts par map (filtrés aux maps
   worldMap=1 réelles) ; **fallback** répartition sous-zone pour les 8 ressources
   sans données dofus-map (Bois de Pin, Tulipe en papier, Cendrepierre, Cristal
   liquide/pliable, Pichon Magique/d'encre, + Bambou sombre dont les spawns sont
   tous hors worldMap=1).
4. **Cap `PER_MAP_CAP=10`** : la distribution des counts est saine (médiane 1,
   p90=3) mais une rare queue de maps-hub agrégées (max 105) ferait dégénérer la
   route en un seul écran → capée. Touche ~3% des entrées.

## Vérifs
- 17 tests verts + nouveau `tests/test_dofusmap.py` (décodeur, espaces, négatifs).
- `world_cells.json` : ~5700 → **3851 cells** (concentrées sur les vraies maps).
- Run `--all 1 --lambda 1` : route saine, leveling équilibré 5 métiers, plus de
  grab géant sur un hub.

## Pistes restantes (optionnel)
- **Bambou sombre & co hors worldMap=1** : leurs coords ne tombent pas dans notre
  graphe ; le fallback sous-zone les place quand même. Vérifier si ces zones
  (Frigost ?) sont sur un autre `worldMap` à intégrer.
- `base_xp` toujours **calibré communautaire** (DofusDB n'expose pas l'XP de
  récolte) — piste : table d'XP de récolte par ressource si une source fiable.
- Optimiseur : beam/lookahead, cibles/poids par métier (extensions déjà notées).
