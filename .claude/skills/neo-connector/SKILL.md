---
name: neo-connector
description: Régénère NEO_CONNECTOR.md (manifeste de connexion pour NeoBot) en auditant ce repo.
---

Tu es en train d'auditer CE repo pour produire un manifeste de connexion machine-lisible
destiné à NeoBot (l'agent Neo de SoClose). NeoBot doit pouvoir appeler TOUTES les
fonctionnalités exposées par ce projet sans deviner. Ne rien inventer : tout doit être
prouvé par le code. Si une info est absente, écris "UNKNOWN -- <fichier où elle devrait être>".

CONTEXTE macmon : ce projet est un CLI/TUI macOS local (typer + rich + psutil), SANS API
HTTP. Vérifie quand même (au cas où ça change) qu'aucun serveur web / endpoint / webhook /
socket n'apparaît. Si toujours pur CLI, le manifeste doit le dire clairement et documenter
la surface de COMMANDES CLI comme interface de fait -- et préciser que ce projet NE DOIT PAS
être câblé comme outils HTTP Neo (seul un appel shell local est possible).

Étapes :
1. Détecte le type de projet et le framework (ici : typer CLI + rich TUI).
2. Trouve TOUTE la surface exposée :
   - HTTP/web : grep flask|fastapi|aiohttp|uvicorn|django|http.server|socket.listen|
     socket.bind|app.run|websocket -> s'il y a des routes, documente-les comme un vrai
     manifeste d'endpoints (méthode, chemin, auth, params, réponse, erreurs, async).
   - CLI : toutes les commandes typer (`@app.command()` dans macmon.py), leurs arguments
     et options, et ce qu'elles font.
   - Daemon/IPC/cron : autopilot (os.fork + PID file), launchd, etc.
3. Pour chaque endpoint HTTP (s'il y en a) : méthode, chemin complet, auth, params
   (body/query, types, requis), forme de réponse, codes d'erreur, long-running.
4. Liste les variables d'env nécessaires -- noms uniquement, JAMAIS les valeurs (ici : aucune).
5. Détecte la base URL de prod (ici : N/A -- pas d'API).
6. Note les flux multi-étapes (ici : seulement le daemon autopilot start/stop local).

Écris le résultat dans NEO_CONNECTOR.md à la racine, AVEC EXACTEMENT cette structure :

# NEO_CONNECTOR -- <nom du projet>
- service: <slug>
- base_url_prod: <url ou N/A>
- auth: <type: x-api-key | Bearer | cookie | none>
- env_required: [LISTE_DES_NOMS]
- generated_at: <laisser vide, NeoBot le datera>

## Endpoints
Pour CHAQUE endpoint HTTP, un bloc (méthode, path, auth, async, input, output, errors,
example_curl). S'il n'y a pas d'API HTTP : écris "None" et documente la surface CLI
dans une section "## CLI commands".

## Flows
Séquences multi-étapes (jobs async, polling, daemon).

## Gaps
Tout ce qui est UNKNOWN ou ambigu, avec le fichier à vérifier.

Termine par un récap : nombre d'endpoints HTTP (0 attendu), surface CLI, et le verdict
"NE PAS câbler comme outils HTTP Neo" si toujours pur CLI.
