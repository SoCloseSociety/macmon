# macmon -- Audit complet + nettoyage Mac (2026-07-03)

## Plan

- [x] Setup: venv Python 3.14 + requirements install + compile check
- [x] Smoke tests: --help, ps, config, health, network, clean --scan, startup --list, docker --status
- [x] Audit parallele des 17 modules -- 120 findings (11 critical, 21 high, 48 medium, 40 low)
- [x] Fix cleaner.py + privacy.py + utils.py (fait directement)
- [ ] Fix processes/dashboard, gc/dupes/disk, health/startup/uninstaller/config, security/network/docker, autopilot/scripts (5 agents en cours)
- [ ] Verifier les fixes en re-executant les commandes
- [ ] Nettoyer le Mac: clean --scan puis clean safe, gc, docker prune (avec confirmation), purge RAM

## Fixes appliques (cleaner/privacy/utils)

- _trash_or_rm ne bascule plus jamais silencieusement en suppression permanente (retourne bool)
- Xcode Archives / Maven repo / ~/.gem exclus du sweep auto (risky, opt-in via --module); gem_cache pointe sur ~/.gem/ruby
- Nettoyage navigateur par defaut = cache + crash uniquement (plus de Web Data/cookies/history par defaut)
- Refus de toucher aux BDD d'un navigateur en cours d'execution
- Cache HTTP principal Chromium (~/Library/Caches/<vendor>) desormais scanne
- Comptage freed reel (par chemin, seulement si suppression reussie) -- fini les "13 TB freed"
- Temp: seuil 3 jours + skip .lock/.pid/.sock/dotfiles; DiagnosticReports: fichiers individuels, skip dir systeme
- --scan ne mute plus rien (clipboard/recent/schedule gated)
- Schedule: sys.executable + unload avant load
- Recent items: fichiers .sfl2/.sfl3 modernes + confirmation
- Privacy: succes verifie (rc/existence), VACUUM quarantine DB, surrogateescape pour zsh_history, check Spotlight obsolete retire
- utils: format_duration corrige, confirm_action EOF-safe, osascript escape, dir_size hardlink-aware

## Etat du Mac observe (avant nettoyage)

- Swap: 92.9% utilise (14.9 GB) -- cause principale des lags
- RAM: 83.5% (9.5/24 GB), health score 76/100
- Docker: 43 conteneurs actifs, 90 images (85.4 GB, 38.9 GB recuperables), build cache 9.3 GB
- Cleaner scan: ~1.0 GB (caches, temp, logs VSCode)
- 6 startup items casses
- ~100 dossiers node_modules

## Review (2026-07-03)

### Audit: 120 findings, tous corriges
- 11 critical, 21 high, 48 medium, 40 low -- repartis sur les 17 modules + scripts
- 6 agents d'audit en parallele, puis 5 agents de fix + fixes directs (cleaner/privacy/utils/autopilot thermal)
- Verification: py_compile OK sur tout, smoke tests OK (ps, health --json, clean --scan, gc --scan, privacy --scan, network, security --firewall, startup, docker, auto --status, bigfiles, dupes fixtures)

### Bugs critiques corriges (extraits)
- cleaner/gc/dupes/uninstaller: echec Corbeille ne bascule plus en suppression permanente silencieuse
- clean --all ne supprime plus Xcode Archives / ~/.m2 / ~/.gem (risky = opt-in)
- security --block-ip: ancre pf dediee com.apple/250.macmon (plus d'ecrasement du ruleset) + validation ipaddress
- uninstaller/startup/quarantine: match exact au lieu de substring (plus de suppression d'apps voisines)
- sweep orphans: ppid==1 + sans terminal + hors /Applications (ne tue plus les apps GUI)
- zombies: SIGCHLD au parent au lieu de SIGTERM
- dashboard: plus de force_yes sur les raccourcis destructifs, confirmation double pour kill
- dupes --keep-in: survivant garanti par groupe, expanduser, hash vide exclu
- gc: node_modules imbriques exclus, signaux d'activite reels (git/lockfiles) pour staleness
- autopilot: PID file race corrige, cooldowns respectes, purge sudo -n verifie, temp reelle uniquement
- ps: CPU reel (double passe), RAM correcte, crash zombie corrige
- config --set: coercion int/float/bool correcte

### Nettoyage du Mac execute
- macmon clean --all -y: 1.4 GB (via Corbeille; HomeKit/CloudKit TCC skippes proprement)
- docker builder prune: 9.5 GB
- docker image prune (dangling): 2 MB
- npm cache clean: ~14.1 GB (15.4 -> 1.3 GB; reste root-owned, voir reco)
- brew cleanup -s: 763 MB
- TOTAL libere: ~25.8 GB

### Restant (decisions utilisateur)
- Swap 94.6% (16.1 GB): cause = 43 conteneurs Docker actifs (VM a 117% CPU / 2.8 GB RAM) + Chrome + VSCode. Stopper les compose projects inutilises ou redemarrer.
- docker image prune -a (34 GB), container prune (50 stoppes, 388 MB), volume prune (2.8 GB): destructif, a valider
- sudo chown -R 501:20 ~/.npm puis npm cache clean --force (reste du cache root-owned)
- macmon startup --broken: 6 items casses a examiner
- Vider la Corbeille pour materialiser le 1.4 GB
