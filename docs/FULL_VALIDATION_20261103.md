# Validation complète — 3 novembre 2026

Contrôle terminé à **15:27 UTC / heure locale du Maroc**, sur le snapshot
`8f68c44bd9ce8c9f6d34871d062fb1da8f8edf22`.
Les résultats, identifiants sans programmes, empreintes et preuves GitHub sont
dans [le rapport détaillé](../reports/full-validation-20261103.json).

## Collectes et publications

Le [contrôle séquentiel GitHub](https://github.com/wacayoub/EPGManager/actions/runs/41658269105)
a exécuté les **14 collectes**, une par une, avec succès. **13 nouvelles
publications** ont ensuite passé la vérification de leur commit producteur,
archive gzip, SHA-256, taille, manifeste, index TXT et programmes XMLTV.
La reconstruction dérivée MENA a également réussi.

Le workflow complet est **FAIL** : Tabie a conservé son dernier flux valide
(`KEEP_LKG`), car le nouveau candidat ne couvre que 6,08 heures futures, sous le
seuil de huit heures. Ses réponses pour les jours suivants répètent la date
courante ; ces lignes sont rejetées. Ce refus reste visible et ne certifie pas
une nouvelle publication.

| Source | Prochaine cible locale, 4 novembre | Chaînes avec EPG futur / catalogue | Résultat |
| --- | --- | --- | --- |
| AlKass | 00:17 | 8 / 8 | Publication vérifiée |
| Tabie | 00:30 | 4 / 4 | Dernier flux conservé ; horizon court |
| OSN | 01:40 | 80 / 100 | Publication vérifiée ; couverture partielle et horaires concurrents |
| beIN | 02:22 | 65 / 68 | Publication vérifiée ; couverture partielle |
| STC TV | 02:37 | 125 / 149 | Publication vérifiée ; couverture partielle |
| STARZPLAY | 03:07 | 118 / 120 | Publication vérifiée ; couverture partielle |
| Shahid | 03:39 | 71 / 71 | Publication vérifiée |
| Rotana | 03:50 | 8 / 9 | Publication vérifiée ; couverture partielle |
| Al Jazeera | 04:12 | 2 / 5 | Publication vérifiée ; couverture partielle |
| ElCinema | 04:20 | 95 / 97 | Publication vérifiée ; couverture partielle |
| Dubai+ | 04:44 | 9 / 9 | Publication vérifiée ; chevauchement Noor Dubai |
| Sport24 | 06:03 | 7 / 9 | Publication vérifiée ; couverture courte et partielle |
| Tunisia TV | 06:35 | 2 / 2 | Publication vérifiée |
| Maroc | 07:07 | 12 / 15 | Publication vérifiée ; données estimées signalées |

Ces horaires utilisent `Africa/Casablanca`. GitHub peut retarder leur exécution.
Les 14 workflows sont actifs, sans erreur de configuration détectée. Les
collectes planifiées de Tabie, OSN et ElCinema sont observées ; celles des onze
autres workflows ne le sont pas encore. Un lancement manuel réussi ne prouve
pas la livraison automatique de demain. Aucun horaire supplémentaire n'est ajouté.

Sport24 conserve ses contrôles stricts : ses trois donneurs exacts OSN/Dubai ne
passent pas le contrôle requis de 48 heures. Aucune extension n'est inventée.
Le chevauchement de vingt minutes de Noor Dubai reste signalé ; les horaires
contradictoires de l'éditeur ne sont pas arbitrairement décalés.

OSN présente aussi sept chevauchements sur Nicktoons et cinq sur OSN iQIYI.
Des épisodes identiques apparaissent avec des débuts différents de cinq à
quinze minutes. Le collecteur réunit les grilles Android et Legacy ; la provenance
précise de ces lignes concurrentes ne peut pas être établie à partir du XMLTV
final seul. La vérification directe des deux API de plateforme reçoit HTTP 403
depuis cet environnement. Ces horaires restent à revoir, sans suppression ou
décalage fondé uniquement sur le titre commun. Les 80 chaînes avec EPG futur
ne constituent donc pas 80 grilles certifiées correctes.

Le Maroc garde ses quinze identifiants. AFLAM, Arryadia HD1 et Arryadia TNT
n'ont pas de programmes futurs vérifiés. Grand Format sur HD2/HD3 et le mode
Smart/Botola de la chaîne HD principale restent explicitement signalés comme
déclarés ou estimés, avec leurs règles existantes.

## Archives de secours et corrections

Les **112 archives** du catalogue précédent ont été intégralement décompressées
et analysées en streaming : intégrité de compression, racine XMLTV, identifiants,
orphelins, titres, intervalles et présence de programmes futurs. Les empreintes
SHA-256 du XML décompressé et les compteurs figurent source par source dans le
rapport. Ce contrôle ne certifie pas les horaires éditoriaux par rapport aux
émissions réelles ni la compatibilité matérielle.

Résultats avant correction : 11 archives sans anomalie dans ces contrôles et
avec EPG futur pour tous leurs identifiants ; 57 couvertures partielles ;
38 avec des intervalles à revoir ; trois avec des incohérences de catalogue ;
trois sans aucun programme futur. Les 2 192 intervalles invalides sur
2 222 180 programmes restent signalés. Les données utiles des éditeurs tiers
ne sont pas supprimées en bloc à cause de quelques lignes erronées.

Corrections appliquées :

- Retrait de `Egypt-EPGShare / EG1` : 238 chaînes et 1 279 programmes expirés,
  sans EPG futur. L'autre source `EGYPT / OpenEPG` reste disponible.
- Retrait de `Pakistan / PK1` : huit chaînes, aucun programme.
- Migration de `Philippines / PH1`, vide, vers `PH2`, présent dans l'index de
  l'éditeur : 111 chaînes, 5 228 programmes, dont 4 198 futurs couvrant 56 chaînes,
  sans intervalle invalide, titre manquant ou anomalie d'identifiant constaté.
  Cette source reste partielle.

Le catalogue contient désormais **110 sources de secours et 14 sources directes**.
Les anciennes URL PH1 sont converties en PH2, sans changer l'identifiant
`ext_philippines` ni modifier les définitions installées. Les anciennes
sélections EG1/PK1 sont filtrées et refusées au téléchargement avec leur vraie
raison de retrait. Les tests couvrent cette migration et les doublons.

Trente-trois archives dépassent le budget XML canonique Vu+ de 12 MiB. Elles ont
néanmoins été contrôlées intégralement, avec une limite locale distincte de
256 MiB. Ce dépassement appelle une revue ; il ne prouve pas un échec matériel.

## Validation logicielle et limites Vu+

Les **310 tests outils et six tests de migration/publication** passent localement.
La [CI de référence](https://github.com/wacayoub/EPGManager/actions/runs/41659002869)
est PASS sur Python 3.13 et 3.14 ; la correction du catalogue passe également par
la CI de sa pull request. Les seize horaires et les quatorze bundles publiés
passent `tools/verify_unified_repository.py`.

La branche `mena-data` reste au commit
`f02d3be8c7bf5bb9d9022cedce3212fbf1116e67`. Ses octets compressés ont été extraits
directement de Git et contrôlés hors récepteur. Le contrôle de libération est
**FAIL** : les 551 identifiants combinés n'ont plus d'EPG futur et la médiane
future réelle est de zéro heure. Le manifeste d'octobre ne constitue pas une
preuve de fraîcheur. Voir aussi [le contrôle canonique](VU_RELEASE_NO_GO_20261103.md).

Le nouveau flux dérivé sur `main` contient 515 chaînes et 31 579 programmes,
dont 50 chaînes sans EPG futur. Il reste `READY_WITH_WARNINGS` et dépasse le
budget XML canonique (14 653 475 octets). Il ne remplace pas une publication
canonique certifiée.

**Vu+ : NO_GO.** Aucun accès matériel n'a pu être testé : seule l'indication
« Vu+ » a été fournie, sans adresse IP ou nom réseau. Aucun import,
rafraîchissement, redémarrage ni changement de mapping du récepteur n'est effectué.

L'ancien dépôt EPG-Scrapers est archivé après migration de son historique.
Sa suppression définitive reste empêchée par l'absence du droit OAuth
`delete_repo` ; il n'est pas présenté comme supprimé.
