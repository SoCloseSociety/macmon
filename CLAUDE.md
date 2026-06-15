# CLAUDE.md -- macmon

macmon is a 100% local macOS developer-monitor + system-cleaner CLI (CCleaner-Pro level).
Stack: Python 3.9+ / `typer` (CLI) / `rich` (TUI) / `psutil` / SQLite. Entry: `macmon.py`
(28+ `@app.command()` commands), logic in `modules/`. Install via `install.sh`
(venv at `~/.macmon/venv`, wrapper `/usr/local/bin/macmon`, config `~/.macmon/config.toml`).
No web server, no network surface -- everything runs against the local machine.

> Never use em dashes anywhere. Use `--`.

## Neo Connector (auto)
Ce projet expose `NEO_CONNECTOR.md` : le manifeste machine-lisible de TOUS ses
endpoints/auth/env, consommé par NeoBot pour se câbler automatiquement.
- IMPORTANT (macmon) : ce projet N'A PAS d'API HTTP. C'est un CLI/TUI local pur. Il NE
  DOIT PAS être câblé comme outils HTTP Neo. Le seul point d'intégration possible est
  d'exécuter le binaire `macmon` en shell sur la machine locale (ex: corps desktop Sentinel).
  Le manifeste documente la surface de commandes CLI comme interface de fait.
- RÈGLE : à chaque ajout/suppression/modif d'une commande CLI, d'une option, ou de la
  config, régénère le manifeste via `/neo-connector` (ou le prompt dans
  `.claude/skills/neo-connector`).
- Ne jamais éditer NEO_CONNECTOR.md à la main : il est généré.
- Le hook pre-commit (`.git/hooks/pre-commit`) avertit si le code change sans MAJ du manifeste.
