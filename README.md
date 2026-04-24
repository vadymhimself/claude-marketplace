# vadymhimself-marketplace

Claude Code plugin marketplace. Ships plugins via two paths:

- **Claude Code CLI users** → add this marketplace via slash command, auto-updates on every push to `main`.
- **Cowork desktop users** → download the packaged `.plugin` file from Releases and install via the Customize UI.

---

## Install — for Cowork users (most teammates)

Cowork does **not** support the CLI `/plugin marketplace add` command. Install via the UI:

1. **Download the plugin file.** Grab `gigradar-gm.plugin` from the [latest release](https://github.com/vadymhimself/claude-marketplace/releases/latest) (or ask the admin for a direct Slack/Drive link).
2. **Open Cowork.** Click **Customize** (gear icon, top-right of the sidebar).
3. **Click "Browse plugins"** → **"Upload plugin file"** → select the `gigradar-gm.plugin` you just downloaded.
4. **Done.** Skills like `/customer-audit` and `/market-research` become available immediately in chat.

To update later: download the new `.plugin` from the latest GitHub release and re-upload (Cowork will replace the existing install).

## Install — for Claude Code CLI users

In your terminal (or Claude Code session), run:

```
/plugin marketplace add vadymhimself/claude-marketplace
/plugin install gigradar-gm@vadymhimself-marketplace
```

Updates ship automatically — Claude Code reads the commit SHA and pulls new versions at startup (no `version` field is pinned, so every push to `main` is a release). To force an immediate refresh:

```
/plugin marketplace update vadymhimself-marketplace
```

---

## Prerequisites — request credentials first

The `gigradar-gm` plugin talks to production data stores and needs **read-only** credentials for both. These are NOT bundled in the plugin for obvious reasons — you have to request them from the GigRadar admin before running any skill that touches data.

**Ask the admin for:**

1. **MongoDB read-only user** — connection string for the `gigradar-dev` database (role: read-only on the researcher scope). Used by the Mongo aggregation scripts (`proposals`, `opportunities`, `teams`, `leads.chats`, etc.).
2. **Elasticsearch read-only user** — user + password for the `metajob` index (role: `metajob-ro`). Used by KNN peer look-alikes and JD fetches.

Once you have them, export to your shell (or the Cowork workspace env) before invoking any skill:

```sh
export MONGO_URI='mongodb://<user>:<pw>@<host>:<port>/gigradar-dev?authSource=admin'
export ES_PASS='<password from admin>'
# optional overrides (defaults live in the plugin README):
# export ES_URL='https://<es-host>:9243'
# export ES_USER='researcher-prod'
# export MONGO_DB='gigradar-dev'
```

Full env-var table and defaults: see [`plugins/gigradar-gm/README.md`](plugins/gigradar-gm/README.md#environment-variables).

---

## Plugins in this marketplace

| Plugin | Description | Requires |
|---|---|---|
| [`gigradar-gm`](plugins/gigradar-gm) | GigRadar market research & growth insights — retro-first customer audits, peer look-alike KNN, and Upwork job-market reply-rate benchmarks for GM / Growth / Success Manager workflows. | MongoDB + ES read-only creds (ask admin) |

---

## Repo layout

```
claude-marketplace/
├── .claude-plugin/
│   └── marketplace.json        # marketplace manifest (for CLI users)
├── README.md                   # this file
├── scripts/
│   └── pack.sh                 # rebuild .plugin zip(s) for Cowork distribution
└── plugins/
    └── gigradar-gm/            # one plugin per directory
        ├── .claude-plugin/
        │   └── plugin.json     # NO "version" field — commit SHA drives updates
        ├── README.md
        ├── references/
        └── skills/
```

## Maintainer: how to ship an update

### For Claude Code CLI users
Just push to `main`. That's it. Claude Code picks up the new commit SHA at startup.

### For Cowork users
Cowork installs are snapshots of a `.plugin` zip at upload time — they don't auto-update. To ship an update to Cowork users:

1. `./scripts/pack.sh` — rebuilds `dist/gigradar-gm.plugin` from `plugins/gigradar-gm/`.
2. Create a new GitHub release (`gh release create vYYYY-MM-DD ./dist/gigradar-gm.plugin --notes "..."`).
3. Tell teammates in Slack: "New version up, grab it from Releases and re-upload."

### Adding a new plugin
Drop its source under `plugins/<name>/` (with a valid `.claude-plugin/plugin.json` that omits `"version"`), add an entry to `.claude-plugin/marketplace.json`, push. CLI users pick it up automatically; for Cowork users run `./scripts/pack.sh <name>` and cut a release.
