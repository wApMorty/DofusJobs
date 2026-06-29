# DofusJobs — Instructions projet (Opus 4.8, effort élevé)

Optimiseur de routes de leveling pour les métiers de récolte Dofus (édition Unity).
Web app **stdlib uniquement** (aucune dépendance tierce au runtime). Voir `GOAL.md`.

## Protocole de langue (économie de tokens)
- **Raisonnement interne, scratchpad, et prompts envoyés aux sous-agents → en mandarin (中文).**
- **Tout ce qui m'est destiné — rapports, questions, plans, résumés — en français.**
- **Inchangés (jamais en mandarin)** : code, commentaires, messages de commit, échanges Ouroboros,
  ce fichier. Le repo est public ; le code reste lisible et conventionnel.

## Mémoire-d'abord (source de vérité)
La mémoire projet prime sur ce fichier pour l'état courant :
`~/.claude/projects/-home-wapmorty-projects-DofusJobs/memory/`.
- **Avant d'agir** : lire l'index `MEMORY.md`, puis rappeler la fiche pertinente
  (`dofusjobs-project.md` = décisions de spec + archi ; `ouroboros-install.md` = setup).
- **Après un fait non-évident** (décision de spec, piège, choix d'archi) : écrire/mettre à jour une
  fiche (frontmatter `name`/`description`/`type`) et ajouter sa ligne dans `MEMORY.md`. Ne pas
  dupliquer ici ce que la mémoire ou le code disent déjà.
- Une fiche peut être périmée : vérifier qu'un fichier/fonction cité existe encore avant de s'en servir.

## Commandes
- Tests (porte verte obligatoire) : `python3 -m unittest discover -s tests`  *(62 tests)*
- CLI : `python3 -m dofusjobs`
- Web : `python3 webapp/app.py` (http.server stdlib ; formulaire + `POST /api/plan`, param `engine=auto|beam|mcts`)
- Bench qualité de route (rate = valeur/écran, greedy/beam/mcts) : `python3 scripts/bench_routes.py`
- Simulation de dépletion (l'adaptatif anti-bot bat-il le moteur aveugle ?) : `python3 scripts/sim_depletion.py`
- Régénérer la table `engine=auto` (moteur+λ choisis selon le régime de niveaux ; **à refaire après
  toute reconstruction du dataset**) : `python3 scripts/bench_routes.py --emit-policy > data/engine_policy.json`
- Reconstruire le dataset (ordre impératif) :
  `python3 scripts/build_dofusmap_counts.py` **puis** `python3 scripts/build_dofusdb_dataset.py`

## Architecture (pointeurs — détail en mémoire)
- Moteur **primaire** : `dofusjobs/farmloop.py` (`FarmLoopFinder`, `plan_farm_route`, `plan_window`)
  — modèle **v4 SANS pods**, score = Σ %XP par map − λ·voyage, route gloutonne + simulation des
  montées de niveau + récolte « au passage ».
- **Planificateur MCTS/UCT** : `plan_window_mcts` (à côté de `plan_window`, jamais en remplacement —
  le beam reste le défaut et le repli). **Max-backup** (déterministe ⇒ on propage la meilleure route,
  pas une moyenne), récompense = **valeur à budget d'écrans fixe − λ·écrans** (orienteering budgété ;
  un rate brut ou une somme non bornée échouent — voir mémoire), rollout glouton plafonné, branchement
  limité. Garantie ≥ greedy par décision (rollout de Bertsekas). UI : `engine=mcts`. Détail/pièges en
  mémoire (`dofusjobs-project.md`).
- **Mode `engine=auto`** : `dofusjobs/engine_policy.py` (`resolve_engine`) choisit moteur+λ d'après le
  régime de niveaux (feature = niveau min + écart max−min, en buckets) via une table pré-calculée
  `data/engine_policy.json` (générée par `bench_routes.py --emit-policy`, objectif = vitesse %XP/écran).
  Lookup pur/déterministe, repli sur beam/λ=1 si table absente/clé manquante/tout-200. L'UI le
  ré-évalue à chaque case (niveaux courants). Le défaut UI est désormais `auto`.
- **Disponibilité adaptative (anti-bot)** : facteur `a∈(0,1]` par map (défaut 1), appris par EWMA des
  signalements du joueur (`AVAILABILITY_ALPHA=0.20`, `ewma_update`). `_cell_value` multiplie la **valeur
  de décision** par `a` ⇒ beam/mcts/find/auto en héritent par le seul chemin partagé. UI : bouton
  **Vide** (obs=0, `a×0.8`, ne récolte pas) à côté de Suivant (obs=1, remonte) / Sauter (obs neutre) ;
  persistance `localStorage` `dofusjobs.avail.v1`, posté dans `state` de `/api/plan`. Détail en mémoire.
- Le legacy v2 à pods (`optimizer.py`, `plan_route`, types `PlayerInput`/`RouteResult`, etc.) a été
  **supprimé le 2026-06-29** (audit ponytail). Le modèle `Resource` n'a plus `pods`/`resource_level`.
- Reste : `models.py`, `mapgraph.py` (graphe de vraies maps + BFS borné), `leveling.py` (table XP),
  `ingestion.py`, `webapp/app.py`.

## Invariants de spec — NE PAS régresser (déjà payés cher)
- **Pas de réintroduction des pods** dans le moteur/UI (v4 les a retirés volontairement).
- **Pénalité de voyage invariante en niveau** : pour la métrique `levels`, λ·voyage est converti en
  %-de-niveau au XP courant ⇒ test garde/skip ≈ `gain_xp > λ·voyage` (régression :
  `test_levels_metric_does_not_stall_at_high_level`). Ne pas comparer un λ·voyage brut à des %.
- **Build dataset SANS seuils ni caps** : placement primaire = positions case-par-case **dofus-map**
  (`data/dofusmap_counts.json`), intersectées avec la surface `worldMap=1`, comptes **bruts** (le cap
  `PER_MAP_CAP=10` a été retiré le 2026-06-28 — il écrasait la densité réelle des champs Paysan ; on
  accepte en contrepartie les hubs poisson/mine bruts, projections de spawns d'intérieur). Repli pour
  les ~5-7 ressources non couvertes par dofus-map = compte DofusDB `resourcesBySubarea` étalé sur les
  maps `worldMap=1` de la sous-zone, `(0,0)` exclu, dédup des sous-zones qui se chevauchent,
  apportionnement au prorata de la part surface.
- **Disponibilité n'escompte QUE la valeur, jamais le voyage** : `λ·voyage` n'est jamais multiplié par
  `a` ⇒ une map à `a` faible **sur le chemin** (`travel≈0`) reste récoltée gratuitement ; seul le
  *détour* vers une map botée est découragé. Le **XP réellement banké** (items / `harvest_coord` /
  `advance`) reste **plein** — `a` n'escompte que l'espérance de décision/aperçu. Dispo vide/tout-1 =
  **no-op byte-identique**. La planif est **pure** (ne mute pas le dict de dispo). Pas de seuil
  d'exclusion (`a` reste > 0). Tests : `tests/test_availability.py`.
- **Stdlib only** au runtime ; tests en `unittest`.
- Déterminisme : départ libre ancré dans la plus riche composante connexe ; sorties stables.

## Méthode de travail (effort élevé)
- Tâche non triviale → **mode plan** ; déléguer le fan-out de recherche à des sous-agents **Explore**
  et le design à un agent **Plan** (prompts en mandarin). Regrouper les appels d'outils indépendants
  en parallèle.
- Toujours finir par la **porte de tests verte** avant d'annoncer « terminé », avec la sortie réelle.
- Commits seulement si demandé ; e-mail `wApMorty@users.noreply.github.com` ; `.gitignore` exclut
  `.claude/` et `data/cache/`. Repo public : https://github.com/wApMorty/DofusJobs

## Ouroboros (Agent OS spec-first)
Binaire `~/.local/bin/ouroboros` ; MCP « ouroboros » enregistré ; runtime = CLI `claude`.
Cycle : **interview → seed → execute → evaluate → evolve**.
- Usage validé : **interview + seed (grade A) + evaluate**. La spec se construit ainsi.
- **Relance du mode autonome (`ooo auto`)** : autorisée, mais **encadrée** — l'essai précédent s'est
  **dégradé dans la boucle QA-repair et n'a produit aucun code**. Donc : restreindre le périmètre par
  run, poser des checkpoints, et **vérifier après coup que du code a réellement été écrit/qu'il passe
  les tests** ; sinon, **retomber sur l'implémentation manuelle à partir du Seed grade-A + `GOAL.md`**.
  Ne jamais laisser un run autonome écraser du code vert sans tests qui passent.

## Métiers (FR)
Bûcheron, Paysan, Alchimiste, Mineur, Pêcheur. Le XP de récolte est **fixe par ressource** ; le
niveau de métier ne fait que **débloquer** l'éligibilité (`required_level`), pas d'échelle d'over-level.
