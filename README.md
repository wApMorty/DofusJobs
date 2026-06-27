# DofusJobs — optimiseur de leveling des métiers de récolte (Dofus Unity)

Calcule la **route de leveling la plus rapide** : tu saisis ton niveau dans les 5
métiers de récolte, l'outil **note chaque map par la somme de %XP** qu'elle te
rapporte (toutes les ressources éligibles, à tes niveaux courants) et suit le
meilleur **score par écran** — **sans contrainte de pods**, en récoltant chaque
map entièrement et en simulant les paliers, pour monter tous tes métiers vers 200.

> Conçu via un workflow spec-first (Ouroboros) : l'interview a figé le modèle,
> puis l'app a été implémentée et testée à partir de cette spec.

## Ce que fait le modèle (v4 — sans pods)

> **v4** : on note **chaque map par la somme de %XP** qu'elle fournit et on suit
> la route la plus rapide (meilleur score/écran), **sans pods**. Pourquoi : pour
> *lever* un métier la contrainte réelle n'est pas l'inventaire (on vide à la
> banque) mais le **temps = déplacement**. L'ancien `/pods` divisait la valeur par
> le poids et écrasait le bois/minerai (XP faible, lourds) ; en l'enlevant, les
> bas métiers redeviennent prioritaires (gros %/récolte). À mesure qu'un métier
> monte, son %XP baisse → la route bascule et **équilibre tout vers 200**.


- **Données réelles (DofusDB + dofus-map)** : 85 ressources avec **niveau et pods
  authentiques** (DofusDB), placées au **niveau de la map** via les **vrais counts
  par map de dofus-map.com** (`getRessourceData.php?groupId=0`, carto monde entier
  d'une ressource en une requête). 70/85 ressources ont des counts case-level réels ;
  les 15 restantes (sans données dofus-map exploitables) retombent sur la répartition
  sous-zone `resourcesBySubarea` × `map-positions`. Voir `scripts/build_dofusmap_counts.py`
  (crawl+décodage) et `scripts/build_dofusdb_dataset.py` (bridge + assemblage).
- **Départ libre** : aucun ancrage. Le moteur **choisit le meilleur point de départ**
  (premier stop à coût de trajet nul), **dans la composante connexe la plus riche** —
  le monde a beaucoup d'îles reliées seulement par bateau/zaap (83 composantes), et
  une passe à pied ne peut pas en sortir ; sans cet ancrage le départ pouvait
  s'échouer sur une petite île. Tu peux aussi imposer un départ.
- **Graphe de la vraie carte du monde** : nœuds = ~6200 vraies maps (worldMap=1),
  arêtes = maps cardinalement adjacentes existantes → le trajet suit le **vrai
  layout des continents** (pas de marche à travers la mer). Chaque cellule (map)
  porte ses ressources ; le **score %XP** de chaque map dépend de ton niveau courant.
- **Simulation des paliers dans le plan** : en récoltant, ton XP de métier monte ;
  quand tu franchis un palier, les **ressources de tier supérieur se débloquent en
  cours de route** et le %XP du métier monté **baisse**, donc le chemin s'adapte et
  rééquilibre vers les métiers en retard. La sortie donne `start_levels`/`end_levels`.
- **Deux objectifs** (`metric`) :
  - **`levels` (défaut)** — maximise la somme des **% de niveau** gagnés. Comme un
    niveau coûte d'autant plus d'XP qu'on est haut, un métier avancé « rapporte »
    peu : l'optimiseur **équilibre la montée de tous les métiers en parallèle** et
    évite de gaspiller dans un job déjà haut.
  - **`xp`** — maximise l'XP brute totale (favorise les ressources/jobs avancés).
- **Pondération déplacement** : on choisit la map maximisant `score_map − λ·trajet`.
  `λ` est en **%XP par écran** (`λ=0` ignore le trajet, route très longue ; plus
  haut = route plus courte et dense).
- **XP de récolte fidèle au jeu** : XP **fixe par ressource** (pas de malus de
  sur-niveau en récolte) ; le niveau ne fait que **gater** l'éligibilité.
- **Sans pods** : aucune limite d'inventaire ; chaque map visitée est **récoltée
  entièrement**. La contrainte est le déplacement, pas le poids.
- **Déplacement case-par-case** : distance = **BFS** sur le graphe des vraies maps
  (1 unité = 1 transition d'écran), respectant le layout réel.

### Algorithme

Glouton séquentiel, sans pods : on part dans la **composante connexe la plus
riche** (départ libre), puis à chaque étape un **BFS borné** donne la distance aux
maps proches ; on va sur la map maximisant `Σ%XP − λ·trajet` (non encore visitée),
on la **récolte entièrement**, on **recalcule les niveaux** (un palier fait baisser
le %XP de ce métier → la route se rééquilibre), et on continue jusqu'à ce qu'aucune
map proche ne vaille le trajet. Déterministe (score, trajet, identifiants).

## Installation

**Aucune dépendance** : Python ≥ 3.10, bibliothèque standard uniquement.

```bash
cd DofusJobs
python3 -m unittest discover -s tests   # 43 tests
```

## Données

Rebuild (les deux scripts sont idempotents et mettent en cache dans `data/`) :

```bash
python3 scripts/build_dofusmap_counts.py   # counts par map (dofus-map, ~80 req. cachées)
python3 scripts/build_dofusdb_dataset.py   # catalogue DofusDB + bridge + cells
```

(`build_dofusmap_counts` n'a aucune dépendance ; `build_dofusdb_dataset` lit
`data/dofusmap_counts.json` s'il existe, sinon il retombe sur la répartition
sous-zone seule.) Artefacts :

- **`resources.json`** : 85 ressources récoltables — `job`, `required_level`,
  `pods` **authentiques DofusDB** (`api.dofusdb.fr`). `base_xp` = **calibré
  communautaire** (`xp ≈ 7 + 0.36·niveau`, ancré sur next-stage) car DofusDB
  n'expose pas l'XP de récolte.
- **`world_cells.json`** : ~3800 cellules (une par map portant des ressources). La
  quantité d'une ressource sur une map vient des **vrais counts dofus-map** (la
  distribution est saine : médiane 1, p90 3 spots/map). **Dé-agrégation des hubs**
  (`HUB_MAP_CEIL=30`) : dofus-map collapse parfois tout un réseau d'eau/mine sur
  quelques coords (les 4 coords d'Astrub portent ~100 de *chaque* poisson). Au lieu
  de capper (ce qui détruisait la vraie densité des champs — blé 26), on **répartit
  le surplus au-dessus de 30 sur les autres maps de la sous-zone DofusDB** : le
  **total est conservé** (aucune XP perdue) mais le hub redevient un **vrai tour
  multi-maps** au lieu d'un point fixe. La densité réelle (≤30, ex. blé 26) est
  laissée intacte ; seuls les vrais hubs sont étalés (~14 sous-zones, ~685 unités
  relocalisées). **Filtre intérieurs/sous-maps** : dofus-map projette les
  spawns d'intérieur (ex. truites/goujons des **égouts d'Astrub**) sur la coordonnée
  de surface parente, ce qui gonflait la case ET faussait la distance (il faudrait
  *explorer* l'intérieur). On ne garde donc une coord que si DofusDB confirme
  qu'elle appartient à une **sous-zone de la ressource** (`resourcesBySubarea`) — les
  sous-zones intérieures n'ayant pas de maps `worldMap=1`, leurs spawns sont écartés
  (~240 projections retirées). Les ressources sans données dofus-map exploitables
  gardent la **répartition sous-zone** (`resourcesBySubarea` réparti sur les
  `map-positions`).
- **`dofusmap_counts.json`** : counts bruts par map décodés de dofus-map, keyés par
  slug du nom dofus-map ; bridgés au catalogue par `build_dofusdb_dataset.py`
  (gère `Bois de Frêne`↔`Frêne`, `Crabe`↔`Crabe Sourimi`, `Raie`↔`Raie Bleue`).
- **`world_maps.json`** : ~6200 vraies maps du monde principal = les nœuds du graphe.
- **Table d'XP de métier** (`data/job_xp_table.json`), cap **niveau 200** :
  **formule fermée exacte** `cumulative(N) = 10·N·(N−1)` (chaque niveau coûte +20 XP
  que le précédent ; cumulée niv-200 = 398 000), d'après la courbe de
  [Dofus Pour Les Noobs](https://www.dofuspourlesnoobs.com/tableaux-dexpeacuterience.html).
  Pilote la simulation des paliers ET l'objectif « % de niveau ». Pour changer de
  courbe, remplace `formula` par un objet `anchors` `{niveau: xp_cumulée}` (alors
  interpolé linéairement).
- Lignes invalides (champ manquant, pods nuls, ressource inconnue) : **droppées
  et loguées** sur stderr.

## Utilisation

### Application web

```bash
python3 -m webapp.app --port 8000          # http://127.0.0.1:8000
```

Affiche la route ordonnée (maps), des **instructions de déplacement lisibles**
(`→×2 ↑` au lieu de coords brutes — le vrai chemin BFS entre deux stops, ordre
préservé), les **niveaux gagnés par métier**, les écrans parcourus, la **vitesse**
(%XP/écran) et le **score %XP** de chaque map. **Clic sur une coordonnée** = copie
la commande `/travel x y` (autopilote en jeu). Deux vues : onglet **Liste** (tout
d'un coup) et onglet **Interactif** (étape par étape, navigation Précédent/Suivant).

### Ligne de commande

```bash
python3 -m dofusjobs --lumberjack 9 --miner 10 --herbalist 62 --farmer 88 --fisherman 25
python3 -m dofusjobs --all 1 --lambda 0.5            # route plus longue et dense
python3 -m dofusjobs --all 50 --metric xp --json     # score = XP brut, sortie JSON
```

### API Python

```python
from dofusjobs import plan_farm_route
r = plan_farm_route(job_levels={"lumberjack": 9, "farmer": 88}, lambda_travel=1.0)
print(r.total_value, r.screens, r.rate, r.terminated)
print({j: (r.start_levels[j], r.end_levels[j]) for j in r.per_job})
print([s.world_coords for s in r.stops], r.stops[0].directions)
```

### API HTTP

`POST /api/route` — `job_levels`, `lambda_travel`, `metric` (`levels`/`xp`).
Réponse : `stops` (chacun : `world_coords`, `directions` ex. `["→×2","↑"]`,
`value`, `harvests`), `screens`, `rate`, `total_value`, `per_job`,
`start_levels`/`end_levels`.

## Structure

```
dofusjobs/
  models.py      # dataclasses (Resource, Cell, CellResource, ...)
  mapgraph.py    # graphe des vraies maps + BFS (coût + chemin + directions ↑↓←→ + composantes)
  leveling.py    # table XP<->niveau (pilote la simulation des paliers)
  farmloop.py    # moteur v4: score map = Σ%XP, route gloutonne sans pods + level-up sim
  optimizer.py   # (legacy v2/v3) moteur séquentiel sous pods, conservé pour `plan_route`
  ingestion.py   # chargement du dataset réel (resources / cells / maps)
  __main__.py    # CLI
webapp/app.py    # serveur web stdlib (form + /api/route)
scripts/build_dofusmap_counts.py  # crawl+décode les counts par map (dofus-map, cache)
scripts/build_dofusdb_dataset.py  # catalogue DofusDB + bridge dofus-map + cells
tests/           # unittest: route sans pods, gating, composante riche, paliers, déterminisme
data/            # resources.json, world_cells.json, world_maps.json, job_xp_table.json
```

## Limites connues / extensions

- **Granularité map (pas case-dans-la-map)** : on connaît la **map** qui porte la
  ressource (vrais counts dofus-map) mais pas la cellule exacte *dans* l'écran — non
  nécessaire pour un modèle de trajet inter-maps. Les 8 ressources sans données
  dofus-map (ex. Bois de Pin, Cristal liquide, Pichons) gardent la répartition
  sous-zone uniforme. Le count dofus-map est une densité (hubs dé-agrégés au-dessus
  de 30, total conservé), pas un nombre de spots garanti à l'instant T.
- `base_xp` est **calibré communautaire** (DofusDB n'a pas l'XP de récolte) ; la
  table d'XP de métier est la formule Dofus Pour Les Noobs.
- Sélection **gloutonne** (meilleur score/écran) + **BFS borné** (rayon `reach`) :
  rapide et déterministe, mais pas un optimum global — un **beam/lookahead** reste
  une extension. La route récolte chaque map **une fois** (single pass) ; pour une
  session répétée, relance à mesure que tes niveaux montent. L'ancien moteur sous
  pods reste disponible via `plan_route` (`dofusjobs/optimizer.py`).

## Licence

[MIT](LICENSE). Données de jeu issues de [DofusDB.fr](https://api.dofusdb.fr) et
[dofus-map.com](https://dofus-map.com) ; Dofus et ses contenus sont la propriété
d'Ankama. Projet non affilié, à but communautaire.
