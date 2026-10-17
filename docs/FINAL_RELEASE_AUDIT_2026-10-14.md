# EPGManager — Assemblée générale et audit de préversion finale
**Date**: 2026-10-14 — **Décision**: **NO-GO / release finale bloquée** jusqu'à résolution des P0.

## Périmètre et preuve

- Dépôt `wacayoub/EPGManager`, `main` et branche `fix/auto-mapping-native-epg-20261014`, PR #6 ouverte.
- Flux `wacayoub/EPG-Scrapers` : audits 48h par fournisseur + qualité par ID.
- Retours réels Vu+ Zero 4K / OpenATV 8.0.1 : 24 mappings OSN AUTO et 2 MANUAL ; Stage 1 et Stage 2 ont permis l'affichage réel des programmes OSN ; capture DMC HD avec trois fournisseurs.
- Build reproduit à blanc via `tools/audit_release_candidate.py --strict` et GitHub Actions `FINAL Release Preflight`.
- Aucun accès SSH direct au récepteur : tests Enigma2 (timer, lecture XMLTV, eEPGCache, reboot, opkg) restent à exécuter sur matériel. Une compilation GitHub ne vaut pas une certification finale.

## P0 : publier maintenant casserait l'installation

| ID | Blocage démontré | Action requise |
|---|---|---|
| P0-01 | `source/current.txt` pointe vers `EPGManager_source_v8.2.2-rc70.zip` tandis que `update.json` diffuse `2026.10.04-rc78`. | Reconstituer la source réelle rc78 **avec modifications connues du récepteur**, non pas repartir du ZIP rc70. |
| P0-02 | `source/overlay/ui/channel_mapping.py` assemblé : ~1601 lignes contre ~9962 dans `data/release-inspect/channel_mapping.py`. `_auto_batch_match` et `_refresh_cross_source_suggestions` absents. | Retirer l'overlay UI obsolète et assembler le code actuel complet ; tests sur 11 thèmes et navigation. |
| P0-03 | Assemblage configuré schéma `CONFIG_SCHEMA_VERSION=4`, snapshot rc78 schéma `15`. | Migration montante explicite, jamais une régression de schéma. |
| P0-04 | `core/direct_native_import.py` livré sans `_merge_persisted_auto_overrides` (Stage 1) ni protection `persisted_auto_keys` (Stage 2), contrairement à la correction fonctionnelle sur Vu+. | Intégrer les deux correctifs dans la vraie source et empêcher écrasement par Online Update. |
| P0-05 | Le GitHub Direct streaming et son contrôle dans `epg_cycle.py` sont aujourd'hui des hotfixes SSH, pas le code de l'IPK. | Intégration source officielle ; une seule lecture par feed ; plus de fallback local obsolète. |
| P0-06 | `github_match_health.py`, qualité par ID et drapeaux `SYNTHETIC` / `SUSPECT` non empaquetés. | Classer **programme authentique** avant `NO_EPG`, `GENERATED`, `REPEATED` ; provenance et horodatage explicites. |
| P0-07 | Le `build-release.yml` historique publiait après seule compilation de quelques fichiers sans regressions fonctionnelles. | Intégrer la barrière `tools/audit_release_candidate.py --strict` avant toute publication + tests CI intégration et migrations. |
| P0-08 | Pas de preuve qu'un IPK complet restaurera tous les modules installés. | Construire un **IPK staging** et comparer le contenu exact avec snapshot des modules du Vu+ avant installation. |
| P0-09 | Mise à jour force-reinstall `rc78` écrase potentiellement les hotfixes sur le Vu+. | Interdire temporairement force reinstall ; sauvegarder modules et mappings, rollback transactionnel. |
| P0-10 | `clear_before_import` n'est pas explicitement neutralisé dans l'assemblage. | Garantir aucun effacement global eEPGCache avant un import valide ; rollback sur échec feed. |
| P0-11 | Pas de certification de reprise après GUI restart / reboot / cron : source sélectionnée, mapping AUTO persisté, import actif. | Tests hardware de régression obligatoires : OSN MANUAL, OSN AUTO, DMC alternance et cinq autres sources. |

**Mesure** : Audit GitHub de l'assemblage du 14/10/2026, 84 modules Python compilent, mais 11 échecs fonctionnels/packaging. Compilation PASS ≠ release prête.

## P1 : sûreté, sécurité, données

| Domaine | Faille/risque et amélioration | Validation |
|---|---|---|
| Authentification paquet | HTTPS + SHA256 côté `update.json` protège de la corruption, pas d'une modification simultanée manifeste+IPK sur le même dépôt. Installer root depuis manifeste. | Limiter URL aux GitHub Releases du dépôt, SHA256/size obligatoires, version monotone, éventuellement signature indépendante. |
| Mise à jour réversible | `opkg --force-reinstall` ne propose pas de retour transactionnel prouvé sur erreur/timeout. `online_update.py` peut tuer `enigma2` avec `killall -9` en fallback. | Sauvegarde version, état et rollback testés, redémarrage propre uniquement. |
| Fluidité UI | `online_update.check_update` télécharge le manifeste dans l'appel UI, potentiellement jusqu'à timeout/fallback ; gros Smart Mapping (~10k lignes), beaucoup de `except Exception` génériques. | HTTP en thread, eTimer callback, instrumenter 95e percentile, cache court borné, journal erreurs. |
| Flux 48h | Les rapports 48h ne valident que la couverture temporelle. Ex. STC `DMC.eg@SD` 50 événements, **49 suspectés de titres génériques**. | Ajouter audits qualité par ID et `real_event_count`, ratio placeholders, répétitions, horizon/tz. |
| Sources critiques | Rapports du 14/10 : OSN 18 failed/101 ; ElCinema 13/97 ; Sport24 3/9 ; Tabie 4/9 ; beIN 1/68 ; Al Jazeera 2/5. | Ne pas masquer FAIL sous `sources OK`. Quarantaine des IDs vides. |
| Affichage Smart Mapping | Actuellement mapping propriétaire vert peut être affiché sans programme réellement importé. | 3 états distincts `mapped`, `XMLTV verified`, `eEPGCache now-next`. Vert seulement si événements ; jaune sinon, sans supprimer les droits manuels. |
| Priorité IDs | Départager exact identity, langue Nilesat/Badr et source officielle ; source qualité (pas score nom seul). | STC DMC ne remplace pas OSN/ElCinema authentique ; Abu Dhabi TV ne se base pas sur un cache local périmé. |
| Fiabilité import | Sources OFF, alias source_id, refs SRP absentes, rafraîchissement `channels.xml` pouvant évincer l'AUTO. | Contrôler plan final après filtres + réelle présence dans eEPGCache pour cinq chaînes/source. |
| Time zone | Corriger systématiquement XMLTV UTC, puis affichage Africa/Casablanca sur appareil ; sources ElCinema/Starzplay déjà signalées. | Fenêtres comparaison horaires 10 chaînes / sources, minuit et changement DST. |
| Programme généré | Baisser score ou exclure de l'auto mapping ; `SUSPECT` (diversité basse) = revue, `SYNTHETIC` (placeholder majoritaire) = rejet. | Cas DMC STC 49/50 ; qualité audit généré à chaque publication par source. |

## P2 : UI, performances, simplification

| Garder | Améliorer ou regrouper | Sortir du parcours utilisateur normal |
|---|---|---|
| Overview | Compteurs exacts : SAT total, IDs, programmes valides, **événements eEPGCache**, dernier succès, source WARN/FAIL | Widgets de diagnostic répétés en page accueil |
| Smart Mapping | 3 panneaux SAT/Receiver/Best Match ; un meilleur candidat + provenance NOW ; `[M]` verrou, `[A]` confirmé, `[A?]` jaune à vérifier ; override manuel accessible | Double liste ancienne des candidats, pages concurrentes de suggestions, scans réseau synchrones pendant navigation |
| Sources | Source détaillée via JAUNE/More ; priorité Morocco > beIN > OSN > Sport24 > ElCinema > OpenEPG > EPGShare ; vue audit 48h | Anciens générateurs embarqués, local scraping, sources tests 403/abandonnées du menu |
| Diagnostics | Fusionner `Duplicates`, `Zero EPG`, `Logs`, `System Check`, `Monitoring` dans **Diagnostics** avec sous-onglets | Actions doublonnées et boutons dont le résultat ne change rien |
| Settings | Quelques groupes : Import, Mapping, Sources, Performance, Appearance, About/Update ; conserver les 11 thèmes existants sans toucher au layout | Réglages legacy cachés, test flags, anciennes copies du même toggle, choix de source obsolètes |
| Maintenance | Une seule planification d'import après GitHub refresh ; backoff/offline/LKG ; gestion cache 1 source/refresh | Cron de scraping fournisseur sur Vu+ et refresh complet au démarrage |

Ne pas **supprimer automatiquement** les mappings, bouquets, logs, historique, sources utilisateur, XMLTV `.lastgood`, ni aucun thème sélectionné. Nettoyer d'abord par dépréciation/masquage et migration réversible.

## Assemblage cible / architecture finale

1. **One Source Tree** : base rc78 réellement publiée ou sauvegarde contrôlée du récepteur ; jamais reconstruire depuis ancien rc70. Intégrer proprement les patches (NativeImport, EPGCycle, Best Match, GUI) comme code source, supprimer les scripts hotfix du chemin de release (les conserver sous `tools/legacy/` pour dépannage).
2. **Un seul module mapping** : owner manuel prioritaire, owner AUTO exclusif, refs valides, source active ; exceptions `IGNORE` et `BLOCK` inviolables ; aucune recherche réseau pendant le déplacement du curseur.
3. **Un seul contrôleur import** : `GitHub XMLTV -> quality per ID -> selected source -> Native EPGImport -> eEPGCache` ; en absence de guide réel, proposer jaune / vérification. Statuts `ID_FOUND`, `REAL_EPG_FOUND`, `MAPPED`, `IMPORTED`, `EPG_NOW` indépendants.
4. **Un seul système de mise à jour** : IPK complet avec dépendances, hash/sig, backup, migration, rollback, redémarrage GUI.
5. **Une seule source de vérité qualité** : rapport 48h fraîcheur + rapport qualité `placeholder_counts`/répétition/horizon ; jamais considérer un slot rempli comme preuve de programme réel.

## Test final obligatoire sur Vu+ Zero 4K (OpenATV 8.0.1)

**Pré-installation** : sauvegarder plugin complet, config Enigma2, mappings et valeurs de version/schéma ; effectuer un dry run d'IPK `opkg`, comparer modules/sources ; test recovery.

**Après installation de candidate staging** :
- OSN : Movies Action et Family MANUAL intacts, Comedy / Horror / Premiere AUTO affichent un EPG NOW réel.
- DMC HD : STC 49/50 génériques classé `GENERATED` et ne gagne pas devant ElCinema/OSN ; un titre réellement présent s'affiche en NOW.
- Abu Dhabi TV HD : source avec programmes 48h valide privilégiée ; mauvaise source en jaune.
- Maroc : 2M, Al Aoula, Arryadia, Medi1 ; titres/arabe/horaires à Casablanca ; pas +1h.
- beIN : Sports, News et arabisation ; contrôle live vs synthetic.
- Sources : cinq chaînes **par source**, incluant au moins un ID sans EPG connu ; pas d'auto remapping automatique des manuels.
- Import : démarrage manuel, progression vraie, source confirmée, import eEPGCache ; retour de veille ; redémarrage GUI ; reboot ; prochain cron quotidien.
- Performance : mesurer RAM/CPU, temps ouverture, première réponse et navigation (cible interactive <300ms après cache chaud), usage réseau borné ; pas de GUI freeze.
- Maintenance : update GUI vers version candidate, vérification SHA/size, rollback et conservation mappings.

## Feuille de route et décision de release

| Phase | Actions | Critère |
|---|---|---|
| **F0 Prévention** | Garde `build-release.yml` + audit ZIP réel | NO-GO automatique sur ancien rc70 |
| **F1 Consolidation** | Reconstruire source officielle depuis rc78/hotfixes ; parité modules exacte ; version et schéma monotones | Audit final **0 P0** |
| **F2 Qualité** | Qualité EPG vraie par ID, source precedence, cas DMC/Abu Dhabi/OSN | Jeux de tests et 5 chaînes/source |
| **F3 UX** | Simplifier menus sans perdre fonctionnalités; thèmes testés | Navigation stable OpenATV, zéro fonction perdue |
| **F4 Staging** | IPK bêta candidat installé proprement, backup/restore | Récepteur confirmé + 24h cron |
| **F5 Final** | Publier tag stable, IPK SHA256/size, `update.json`, changelog et rollback | **GO** si tous les P0 sont fermés |

**NE PAS publier de version finale ni cliquer Force reinstall sur le récepteur avant F4/F5.**

Liens de suivi : [Branche fix AUTO EPG](https://github.com/wacayoub/EPGManager/tree/fix/auto-mapping-native-epg-20261014), [PR #6](https://github.com/wacayoub/EPGManager/pull/6), [Preflight](https://github.com/wacayoub/EPGManager/actions/workflows/final-release-preflight.yml).
