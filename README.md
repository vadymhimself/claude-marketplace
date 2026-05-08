# vadymhimself-marketplace

Personal Claude Code plugin marketplace. Add it once, then install or remove plugins as you need. Updates ship with every push to `main` — no version bumps required.

## Install (for teammates)

Open Claude Code (or Cowork), then run these two slash commands — first add the marketplace, then install the plugin you want from it:

```
/plugin marketplace add vadymhimself/claude-marketplace
/plugin install gigradar-gm@vadymhimself-marketplace
```

That's it. Skills inside the plugin (e.g. `/customer-audit`, `/market-research`) become available immediately.

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
# export ES_URL='https://prod-search-deployment.es.us-west-2.aws.found.io:9243'
# export ES_USER='researcher-prod'
# export MONGO_DB='gigradar-dev'
```

Full env-var table and defaults: see [`plugins/gigradar-gm/README.md`](plugins/gigradar-gm/README.md#environment-variables).

## Update

Updates ship automatically at Claude Code startup. To force a refresh right now:

```
/plugin marketplace update vadymhimself-marketplace
```

Because none of the plugins pin a `version` field, Claude Code uses the git commit SHA to detect updates — every push to `main` is a release.

## Plugins in this marketplace

| Plugin | Description | Requires |
|---|---|---|
| [`gigradar-gm`](plugins/gigradar-gm) | GigRadar market research & growth insights — retro-first customer audits, peer look-alike KNN, and Upwork job-market reply-rate benchmarks for GM / Growth / Success Manager workflows. | MongoDB + ES read-only creds (ask admin) |

## Repo layout

```
claude-marketplace/
├── .claude-plugin/
│   └── marketplace.json        # marketplace manifest
├── README.md                   # this file
└── plugins/
    └── gigradar-gm/            # one plugin per directory
        ├── .claude-plugin/
        │   └── plugin.json     # NO "version" field — commit SHA drives updates
        ├── README.md
        ├── references/
        └── skills/
```

Adding a new plugin: drop its source under `plugins/<name>/` (with a valid `.claude-plugin/plugin.json` that omits `"version"`), add an entry to `.claude-plugin/marketplace.json`, and push. Installed users pick it up automatically.
