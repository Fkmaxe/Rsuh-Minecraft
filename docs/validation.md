# Validation — 13 novembre 2026

## Environnement

macOS ARM64, OpenJDK 25.0.1, Python 3.13.9. Backend Paper 1.21.11 build 132, Velocity 3.4.0 build 563, ViaVersion/ViaBackwards 5.12.2, BungeeGuard 1.4.0. Binaires téléchargés depuis leurs éditeurs et vérifiés par SHA-256. EULA explicitement acceptée par le propriétaire du projet.

## Tests automatisés

`python3 -m unittest discover -s tests -v` : **21 tests réussis** (dont l’annulation idempotente d’une partie déjà réglée et le nettoyage des dossiers de partie sans toucher aux dossiers partagés du lobby).

- Matchmaking des trois modes, groupes conservés, remplissage exact, refus des doubles inscriptions et des groupes trop grands.
- Retrait des joueurs hors ligne, saturation de capacité, requêtes concurrentes, échec d’un processus, conservation de la file et reprise après échec du lancement.
- Invitations d’équipe et d’amitié, consentement de l’invité, restrictions du chef.
- Rôles, refus d’escalade de privilège, authentification HTTP, validation des requêtes.
- Salon privé complet et démarrage réservé au créateur ; absence de points classés.
- Résultats atomiques/idempotents, rejet des statistiques invalides, persistance et annulation des parties interrompues.

`bash scripts/build.sh` : **compilation des deux plugins réussie**. La compilation ne constitue pas à elle seule un test de jeu ; elle est complétée par les tests ci-dessous.

## Déploiements réels par commande

Commandes exécutées contre le réseau lancé avec `--diagnostic` :

| Commande | Instance observée | Résultat |
|---|---|---|
| `python3 -m orchestrator.cli deploy-test --mode 1v1` | `rush-337a14af5dc5` | PASS |
| `python3 -m orchestrator.cli deploy-test --mode 2v2` | `rush-5b03217c1858` | PASS |
| `python3 -m orchestrator.cli deploy-test --mode 5v5` | `rush-5ffb615a15e2` | PASS |

Chaque commande a lancé un vrai processus Paper, attendu l’initialisation de Rush, obtenu une réponse de statut Minecraft (`Paper 1.21.11`, protocole 774), puis demandé l’arrêt et la libération de l’instance. Le 1v1 a été revérifié avec la configuration finale. Le dernier contrôle de santé confirme un lobby disponible et zéro partie active ; le proxy et le lobby ont émis leurs marqueurs de disponibilité sans erreur de démarrage.

## Parcours réel avec clients 1.9.4

`python3 scripts/smoke_network.py` : **PASS**, avec deux clients Mineflayer 4.33.0 en protocole 1.9.4 sur un réseau de test séparé. Le dernier parcours complet a validé : connexion, cadence 1.8 (un coup porté pendant l’invulnérabilité est ignoré), réduction d’armure 1.8 (plastron en fer : 3,04 dégâts), commandes amis/équipes, clic dans le menu, file automatique, déploiement, transferts, attaques répétées à dégâts complets, réapparition avec lit intact, achat et refus pour ressources insuffisantes, propulsion TNT sans dégâts directs, protection du lit allié, destruction du lit adverse, élimination, attribution du classement et retour au lobby. Une seconde partie privée a ensuite été déployée, jouée jusqu’à l’abandon d’un participant et terminée sans modifier le classement.

Instances de ce parcours : `rush-944363381dae` (classée) et `rush-cc3993ada2e0` (privée). Les événements enregistrent notamment deux coups à 4 dégâts, une explosion annulée et sa propulsion, puis les résultats des parties.

Le rapport du dernier test se trouve dans `runtime/smoke-report.json` : il donne les vérifications réussies et le dossier contenant les consoles, la base de test et les événements PvP. Ce rapport n’est écrit qu’en cas de succès de tous les contrôles du script.

Les essais ont permis de corriger le descripteur Velocity sous Java 25, le transfert d’identité pour les clients antérieurs à 1.13, la détection du protocole des serveurs dynamiques, l’annulation involontaire du clic gauche sur les lits et les dégâts des TNT sans propriétaire classées comme explosions de bloc par Paper.

## Interfaces, notifications et éditeur de maps

`python3 -m unittest discover -s tests` : **40 tests réussis**, dont les notifications (invitation avec boutons, refus, arrivée et départ d’équipe, présence des amis, invitations rappelées à la connexion, salon privé, variation d’Elo), les profils du TAB, les droits sur les maps, la validation des données envoyées par l’éditeur, l’enregistrement avec relance et position conservée, l’arrêt non propre qui conserve la map précédente, la fermeture d’un éditeur inactif et l’arrêt différé des instances.

`python3 scripts/smoke_network.py` vérifie en plus, avec les clients 1.9.4 : réception des notifications cliquables, menus et boutique par catégories, notification d’Elo en fin de partie, puis création d’une map depuis `classic`, édition (générateur, PNJ), enregistrement réel du monde, réouverture de l’éditeur, sortie, activation et **partie réelle sur la map enregistrée** (lits présents des deux côtés, 9 PNJ). Les fichiers de région enregistrés sont contrôlés : tailles multiples de 4 Kio, sans `playerdata`.

Ces essais ont corrigé deux défauts réels. Le premier : copier le monde d’un serveur en marche produisait des régions tronquées et emportait les données du joueur. L’enregistrement passe maintenant par un arrêt propre. Le second : un serveur qui s’arrête expulsait ses joueurs du réseau avant que le proxy les renvoie au lobby. L’arrêt est maintenant différé de 3 secondes.

Le rendu visuel (couleurs, scoreboard, titres) n’est pas vérifiable par les bots : à contrôler en jeu avec le client 1.9.4.

## Modération

`tests/test_moderation.py` (12 tests) couvre : refus des outils pour les joueurs, bannissement refusé à la connexion et expulsion, limite des modérateurs et protection du staff, expiration des mutes, échappement du texte des joueurs, freeze et alerte de déconnexion, spectateur admis seulement sur sa partie et retiré à la fin, vanish caché aux joueurs, signalements limités, chat staff, historique, mode hors ligne avec console.

Le test de bout en bout vérifie avec les clients 1.9.4 : disparition du TAB en vanish et réapparition, freeze avec commandes bloquées puis dégel, spec d'une vraie partie (le joueur ne voit pas le spectateur), `/invsee`, retour au lobby, mute appliqué (message non diffusé), signalement reçu par le staff, menu staff, bannissement avec expulsion puis reconnexion refusée.

## Historique des parties

`tests/test_history.py` (4 tests) vérifie le fichier d'une partie classée (statut, vainqueur, statistiques, Elo avant et après), la chronologie reconstruite depuis un journal d'événements, les freezes notés dans la partie, conservés en base avec leur durée et rétablis après redémarrage, l'annulation, la partie interrompue par un redémarrage, l'exclusion des tests de déploiement et la recherche par joueur. Le test de bout en bout contrôle le fichier d'une vraie partie classée (chronologie début, mort, lit détruit, fin ; Elo ±16) et celui d'une partie observée par le staff (spectateur, freeze, dégel, auteur).

## Limites à respecter

- Ces tests utilisent une authentification offline sur un proxy de test limité à `127.0.0.1`. Le réseau normal conserve l’authentification Microsoft/Mojang ; aucune connexion avec un compte officiel réel n’a été effectuée ici.
- Les positions et certains objets des bots sont préparés par commandes console. Leur simulation de déplacement est désactivée pour éviter les positions NaN émises par Mineflayer avec cette configuration ancienne. Le PvP serveur est réellement exécuté, mais le ressenti et les trajectoires doivent être vérifiés par des humains avec le client 1.9.4.
- Les modes 2v2 et 5v5 ont été testés dans le domaine et déployés réellement. Aucune partie complète avec quatre ou dix clients simultanés ni aucun test de charge prolongé n’a été réalisé.
- La carte est générée pour l’entraînement. Les maps Funcraft originales et un cahier de règles détaillé n’ont pas été fournis. Le rendu et les règles ne doivent pas être présentés comme une reproduction exacte de Funcraft.
- Le bonus de knockback en sprint n’est pas couvert par les bots, dont la physique est désactivée : le vérifier en jeu avec `--diagnostic` (champ `sprint` de `pvp_hit`).
- Les valeurs de knockback, la vitesse d’attaque, les ticks d’invulnérabilité et la propulsion TNT sont des réglages initiaux. Comparer les sensations avec des joueurs connaissant Funcraft avant le tournoi.
- La disponibilité multi-machine, le redémarrage à chaud des parties après un crash brutal du superviseur, l’anti-cheat, le matchmaking par Elo et la rotation/importation de maps ne sont pas implémentés.

Le socle d’entraînement et ses validations automatisées sont livrés. La fidélité au Rush Funcraft et la capacité de production doivent encore être validées sur le terrain.
