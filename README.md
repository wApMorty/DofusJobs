# DofusJobs — optimiseur de leveling des métiers de récolte (Dofus Unity)

Calcule un **chemin de farm optimisé pour une passe** : tu saisis ton niveau (ou
ton XP) dans les 5 métiers de récolte et ta **limite de pods**, l'outil renvoie
l'ordre des cellules à visiter qui **maximise l'XP** sous contrainte de pods, **en
pénalisant les déplacements**.

> Conçu via un workflow spec-first (Ouroboros) : l'interview a figé le modèle,
> puis l'app a été implémentée et testée à partir de cette spec.

## Ce que fait le modèle (v2)

- **Données réelles (DofusDB + dofus-map)** : 85 ressources avec **niveau et pods
  authentiques** (DofusDB), placées au **niveau de la map** via les **vrais counts
  par map de dofus-map.com** (`getRessourceData.php?groupId=0`, carto monde entier
  d'une ressource en une requête). 70/85 ressources ont des counts case-level réels ;
  les 15 restantes (sans données dofus-map exploitables) retombent sur la répartition
  sous-zone `resourcesBySubarea` × `map-positions`. Voir `scripts/build_dofusmap_counts.py`
  (crawl+décodage) et `scripts/build_dofusdb_dataset.py` (bridge + assemblage).
- **Départ libre** : aucun ancrage. Le moteur **choisit le meilleur point de départ**
  (premier stop à coût de trajet nul). Tu peux aussi imposer un départ.
- **Graphe de la vraie carte du monde** : nœuds = ~6200 vraies maps (worldMap=1),
  arêtes = maps cardinalement adjacentes existantes → le trajet suit le **vrai
  layout des continents** (pas de marche à travers la mer). Chaque cellule (map)
  porte ses ressources ; l'XP/pods disponibles **dépendent de ton niveau courant**.
- **Simulation des paliers dans le plan** : en récoltant, ton XP de métier monte ;
  quand tu franchis un palier, les **ressources de tier supérieur se débloquent en
  cours de route** et le chemin s'adapte. Les level-ups sont reportés dans la sortie
  (`level_ups`, avec ce qui est débloqué et à quel stop).
- **Deux objectifs** (`metric`) :
  - **`levels` (défaut)** — maximise la somme des **% de niveau** gagnés. Comme un
    niveau coûte d'autant plus d'XP qu'on est haut, un métier avancé « rapporte »
    peu : l'optimiseur **équilibre la montée de tous les métiers en parallèle** et
    évite de gaspiller dans un job déjà haut.
  - **`xp`** — maximise l'XP brute totale (favorise les ressources/jobs avancés).
- **Pondération déplacement** : `score = objectif − λ·total_travel_cost`. `λ` règle
  l'agressivité anti-déplacement (`λ=0` ignore le trajet) ; son unité suit
  l'objectif (XP/écran ou %-niveau/écran).
- **XP de récolte fidèle au jeu** : XP **fixe par ressource** (pas de malus de
  sur-niveau en récolte) ; le niveau ne fait que **gater** l'éligibilité.
- **Pods = contrainte dure** : `0 ≤ pods_used ≤ pods_limit`, pas de retour banque.
- **Déplacement case-par-case** : distance = **BFS** sur le graphe des vraies maps
  (1 unité = 1 transition d'écran), respectant le layout réel.

### Algorithme

Comme les niveaux montent en cours de passe, ce qui est récoltable dépend de
l'ordre → optimisation **séquentielle** : à chaque étape, **un BFS borné** depuis
la position courante donne la distance à toutes les maps proches, on prend la
récolte éligible **la plus efficace en XP/pod** (trajet intégré à la valeur), on
récolte, puis on **recalcule le niveau** ; franchir un palier ouvre de nouveaux
candidats. Départ libre = premier lot sans trajet. La passe reste **locale** (rayon
borné) — réaliste et rapide. Déterministe (efficacité, valeur, trajet, identifiants).

## Installation

**Aucune dépendance** : Python ≥ 3.10, bibliothèque standard uniquement.

```bash
cd DofusJobs
python3 -m unittest discover -s tests   # 36 tests
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

Affiche le chemin ordonné (maps), des **instructions de déplacement lisibles**
(`→×2 ↑` au lieu de coords brutes — le vrai chemin BFS entre deux stops,
ordre préservé), l'XP totale et par métier, les pods, le coût de déplacement, **les
level-ups en cours de passe** (et ce qu'ils débloquent). **Clic sur une coordonnée**
= copie la commande `/travel x y` dans le presse-papier (autopilote en jeu). Deux
vues : onglet **Liste** (toutes les instructions d'un coup) et onglet **Interactif**
(étape par étape, en grand, avec navigation Précédent/Suivant et barre de progression).

### Ligne de commande

```bash
python3 -m dofusjobs --pods 600 --lambda 1 --all 1                 # bas niveau: voir les unlocks
python3 -m dofusjobs --pods 500 --lumberjack 60 --miner 1          # metric=levels (défaut): équilibre
python3 -m dofusjobs --pods 500 --lumberjack 60 --miner 1 --metric xp   # XP brut: gave le job avancé
python3 -m dofusjobs --pods 300 --all 50 --start 10,-20 --json     # départ imposé + JSON
```

### API Python

```python
from dofusjobs import plan_route
r = plan_route(job_levels={"lumberjack": 60, "miner": 1}, pods_limit=500,
               lambda_travel=1.0, metric="levels")   # ou metric="xp"
# ou en XP exact : plan_route(job_xp={"lumberjack": 100421}, pods_limit=500)
print(r.total_levels_gained, r.levels_gained,
      [s.cell_id for s in r.route],
      [(lu.job_id, lu.to_level, lu.unlocked) for lu in r.level_ups])
```

### API HTTP

`POST /api/route` — `job_levels` **ou** `job_xp`, `pods_limit`, `lambda_travel`,
`metric`, `start_coords` (optionnel `[x,y]`). Réponse : route (chaque stop porte
`directions`, ex. `["→×2","↑"]`), métriques, `level_ups`,
`start_levels`/`end_levels`.

## Structure

```
dofusjobs/
  models.py      # dataclasses (Resource, Cell, CellResource, RouteResult, LevelUp)
  mapgraph.py    # graphe des vraies maps + BFS (coût + chemin + directions ↑↓←→)
  leveling.py    # table XP<->niveau (pilote la simulation des paliers)
  optimizer.py   # moteur séquentiel: efficacité XP/pod + simulation level-up + BFS borné
  ingestion.py   # chargement du dataset réel (resources / cells / maps)
  __main__.py    # CLI
webapp/app.py    # serveur web stdlib (form + /api/route)
scripts/build_dofusmap_counts.py  # crawl+décode les counts par map (dofus-map, cache)
scripts/build_dofusdb_dataset.py  # catalogue DofusDB + bridge dofus-map + cells
tests/           # unittest: pods, gating, free start, objectif pondéré, level-up, déterminisme
data/            # resources.json, world_cells.json, world_maps.json, job_xp_table.json
```

## Limites connues / extensions

- **Granularité map (pas case-dans-la-map)** : on connaît la **map** qui porte la
  ressource (vrais counts dofus-map) mais pas la cellule exacte *dans* l'écran — non
  nécessaire pour un modèle de trajet inter-maps. Les 8 ressources sans données
  dofus-map (ex. Bois de Pin, Cristal liquide, Pichons) gardent la répartition
  sous-zone uniforme. Le count dofus-map est une densité (capée à 10), pas un nombre
  de spots garanti à l'instant T.
- `base_xp` est **calibré communautaire** (DofusDB n'a pas l'XP de récolte) ; la
  table d'XP de métier est la formule Dofus Pour Les Noobs.
- Sélection **greedy par efficacité** + **BFS borné** (rayon `reach`, passe locale).
  Un **beam/lookahead** et un mode **cibles/poids par métier** restent des extensions.
- Pas de retour banque ni de re-planification live (choix « simulation dans le plan »).

## Licence

[MIT](LICENSE). Données de jeu issues de [DofusDB.fr](https://api.dofusdb.fr) et
[dofus-map.com](https://dofus-map.com) ; Dofus et ses contenus sont la propriété
d'Ankama. Projet non affilié, à but communautaire.
