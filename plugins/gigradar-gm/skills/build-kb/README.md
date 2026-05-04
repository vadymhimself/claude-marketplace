# Turn your folder of notes into a private AI-searchable library

Built for non-technical users. If you can copy-paste commands, you can do this.

**What you'll get:**
A folder of markdown / text / notes (PDFs aren't supported yet) becomes searchable by any AI assistant — semantically, like a brain that understands meaning rather than exact words. The content lives in your private Cloudflare account. Nothing on your laptop. Nothing in any GitHub repo.

**What this costs:**

- **Free tier** works for KBs under ~80 files. Past that you'll hit the 10,000 neurons/day Workers AI cap during the initial bootstrap.
- **Workers Paid ($5/mo)** is the realistic answer for any KB over ~100 files, OR if you don't want to wait a UTC day for cron to finish backfilling.

The math (number of files vs realistic time-to-fully-indexed, plus what each Cloudflare resource actually costs): see **`references/cost-and-sizing.md`**. Read it before bootstrapping if your corpus is over 80 files — it'll tell you whether to pay the $5 upfront.

Cloudflare may ask for a payment method on file even for free-tier-only usage.

**What you need:**
- A Cloudflare account ([sign up free](https://dash.cloudflare.com/sign-up))
- A computer with `bash`, `curl`, and `python3` installed (Mac and Linux ship with these; on Windows use **WSL** — see below)
- 15 minutes the first time

---

## Windows users: read this first

This skill uses bash scripts. Windows doesn't have bash natively. The fix:

1. Open PowerShell as Administrator
2. Run: `wsl --install`
3. Restart your computer
4. From the Start menu, open "Ubuntu" (it installed automatically)
5. Inside Ubuntu, your user home is `~/` — that's where `~/.kb-bootstrap.env` will live
6. Continue with the rest of these instructions inside the Ubuntu terminal

If you'd rather avoid WSL, ask someone with a Mac/Linux machine to run the bootstrap once for you, then they hand you the resulting `knowledge-base.skill` file.

---

## Step 1 — Set up Cloudflare

You need three things from Cloudflare: an API token, your account ID, and a Workers subdomain. We'll get all three now.

### 1A. Enable R2 (object storage)

Cloudflare's R2 product is what we'll store your files in. It's free for typical use, but it has to be turned on once.

1. Go to **[https://dash.cloudflare.com/?to=/:account/r2/overview](https://dash.cloudflare.com/?to=/:account/r2/overview)**
2. Click **Purchase R2** (despite the button name, the free tier is free — Cloudflare just may require a payment method on file)
3. Add a payment method if asked. You won't be charged unless you exceed the free tier.

If you skip this step, the bootstrap will fail with the message *"Please enable R2 through the Cloudflare Dashboard"*.

### 1B. Set up your Workers subdomain

This is the URL where your search worker will live (something like `myname.workers.dev`).

1. Go to **[https://dash.cloudflare.com/?to=/:account/workers/overview](https://dash.cloudflare.com/?to=/:account/workers/overview)**
2. If you've never used Workers, you'll be prompted to choose a subdomain. Pick anything memorable (your name, your company, anything). It's free.
3. Once set, your URL will be `<whatever-you-picked>.workers.dev`. Write it down.

### 1C. Find your account ID

1. Go to **[https://dash.cloudflare.com/](https://dash.cloudflare.com/)**
2. On the right sidebar, you'll see "Account ID". Copy it. It looks like `abc123def456789a012345678901bcde`.

### 1D. Mint an API token

1. Go to **[https://dash.cloudflare.com/profile/api-tokens](https://dash.cloudflare.com/profile/api-tokens)**
2. Click **Create Token**
3. Click **Get started** under "Custom token" (don't use the templates — they don't include all the permissions we need)
4. Name it `kb-bootstrap`
5. Add these **five** permission rows. For each one, pick the scope from the first dropdown and the level from the second:

   | Scope | Level |
   |---|---|
   | Account → Workers Scripts | **Edit** |
   | Account → Workers AI | **Read** |
   | Account → R2 Storage | **Edit** |
   | Account → Vectorize | **Edit** |
   | Account → Workers KV Storage | **Edit** |

6. Under **Account Resources**: pick "Include → All accounts" (or your specific account)
7. Optional **TTL**: 90 days is reasonable
8. Click **Continue to summary** → **Create Token**
9. **COPY THE TOKEN NOW.** Cloudflare won't show it again. If you close the page without copying, you'll have to make a new one.

### 1E. Save your config to a file

In your terminal:

```bash
cat > ~/.kb-bootstrap.env <<EOF
CLOUDFLARE_API_TOKEN=<paste-token-here>
CLOUDFLARE_ACCOUNT_ID=<account-id-from-step-1C>
KB_NAME=my-kb
KB_REGION=auto
WORKERS_SUBDOMAIN=<subdomain-from-step-1B>
EOF
chmod 600 ~/.kb-bootstrap.env
```

`KB_NAME` is whatever slug you want for THIS knowledge base. If you have multiple knowledge bases later, each gets its own slug. `auto` for region means Cloudflare picks the best region for you.

---

## Step 2 — Run the pre-flight check

This catches every common mistake before you actually deploy anything.

```bash
bash scripts/00_preflight.sh
```

Possible outcomes:

- **All green** — you're good to bootstrap.
- **Red item: token missing permission** — re-mint the token with the missing permission. Or click "Edit" on the existing token in the Cloudflare dashboard and add the row.
- **Red item: R2 not enabled** — go back to step 1A.
- **Red item: no Workers subdomain** — go back to step 1B.
- **Yellow: subdomain mismatch** — the dashboard's subdomain doesn't match what you put in the config file. Fix the config.

Re-run the preflight after fixing each issue until everything's green.

---

## Step 3 — Bootstrap your KB stack

```bash
bash scripts/bootstrap_all.sh
```

This takes about 2 minutes. It creates:

- A private storage bucket
- A vector database index (for semantic search)
- Two background workers (one indexes new content, one serves searches)
- A read-only access token for AI agents
- A pre-configured "consumer skill" you can install in any Claude Code plugin

Each step prints what it did. If something fails, the script tells you which step and why.

When it's done, you'll see:

```
═══════════════════════════════════════════════════════════
  Bootstrap complete.
═══════════════════════════════════════════════════════════
  Search Worker URL:  https://my-kb-search.<your-subdomain>.workers.dev
  Consumer token saved to: <path>/.token
  Consumer skill ready at: <path>/generated/knowledge-base/
```

---

## Step 4 — Organize your folder

The KB expects this structure:

```
my-research-folder/
├── topic-one/                  ← we call this a "bundle"
│   ├── README.md               ← describes what's in this bundle (REQUIRED)
│   ├── note1.md
│   └── note2.md
├── 2026-04-26_some-research/   ← optional date prefix for snapshots
│   ├── README.md
│   └── findings.md
└── another-topic/
    ├── README.md
    └── ...
```

Three rules:

1. **Files at the root don't get indexed individually** — only inside subfolders ("bundles").
2. **Every bundle (subfolder) needs a README.md** — this is the citation manual the AI agents read every time they cite content from that bundle. It tells them how to attribute the finding correctly.
3. **PDFs and binary files** — currently ignored by the indexer. Convert PDFs to markdown first if you want them searchable.

### "What goes in the README.md?"

Don't worry about writing it yourself the first time. Run:

```bash
bash scripts/generate_missing_readmes.sh /path/to/your/folder
```

This walks your folder and creates a stub `README.md` in every subfolder that doesn't have one. The stub has bracketed sections you fill in like a form:

- What is this bundle?
- What's in it?
- How do I cite findings from this bundle?
- What's off-limits to publish?
- Authority level
- Maintenance

Open each generated README, replace the bracketed text with your answers (it takes 5 minutes per bundle), and you're done.

---

## Step 5 — Upload your folder

```bash
bash scripts/05_sync_folder_to_r2.sh /path/to/your/folder
```

If any subfolder is still missing a README, the script will refuse and show you which ones. Fix those (or pass `--auto-readme` to generate stubs automatically) and re-run.

The upload takes a few minutes for hundreds of files. Each file is uploaded to your private storage bucket. Nothing is publicly readable.

After the upload, the indexer worker processes new files in the background — about 5 files per minute. It'll catch up to your full folder within an hour for typical KBs.

To watch progress:

```bash
bash scripts/poll_indexer_status.sh    # prints how many files are indexed vs total
```

---

## Step 6 — Use it

The bootstrap created a "consumer skill" at `generated/knowledge-base/` with the URL and access token already wired in. Drop this folder into any Claude Code plugin's `skills/` directory:

```bash
cp -r generated/knowledge-base/ ~/path/to/your/plugin/skills/
```

After the next time you start that plugin, AI agents in any session can run:

```bash
bash scripts/kb_search.sh --semantic "what I'm looking for"
bash scripts/kb_context_for.sh "path/to/file.md"
bash scripts/kb_manifest.sh
```

These calls go through your private worker, query your private storage, and return results without anything ever landing on the agent's machine.

To share access with a teammate, just hand them the `generated/knowledge-base/` folder. Nothing else to set up.

---

## Day-to-day operations

### Adding new content
Drop new files into your folder. Re-run `05_sync_folder_to_r2.sh`. The indexer picks up changes within 5 minutes.

### Editing existing content
Same. The indexer notices content changes via file etags and re-indexes only what changed.

### Removing content
Currently you have to delete from R2 directly (the sync only adds — it doesn't mirror deletes). If this matters for you, use the Cloudflare dashboard at *Storage → R2 → your bucket → delete*.

### Rotating the access token
If your consumer token leaks or you want to revoke it:

```bash
bash scripts/rotate_consumer_token.sh
```

This mints a new token, invalidates the old one, and regenerates the consumer skill. Distribute the new skill to teammates.

### Running multiple KBs
Each KB needs its own bootstrap. Just change `KB_NAME` in the env file and re-run. They're fully isolated.

---

## Troubleshooting

The full lookup is in `references/troubleshooting.md` — symptom → cause → fix, indexed by what you actually see. Quick highlights:

- **"Authentication error"** → token's missing a permission. Run `00_preflight.sh` to see which one.
- **"Please enable R2 through the Cloudflare Dashboard"** → R2 isn't enabled at the account level. Click *Purchase R2* at [https://dash.cloudflare.com/?to=/:account/r2/overview](https://dash.cloudflare.com/?to=/:account/r2/overview).
- **`/reindex` returns `quota_blocked: true`** → you've hit the Workers AI free-tier daily limit (~10K neurons / ~100 files of typical size). Wait for 00:00 UTC reset, OR upgrade Workers Paid ($5/mo).
- **Bootstrap finished but search returns nothing** → indexer cron hasn't run yet. Wait 5 minutes, or trigger manually: `curl -X POST https://<KB_NAME>-indexer.<WORKERS_SUBDOMAIN>.workers.dev/reindex`.
- **Semantic search works, keyword doesn't** → keyword is "semantic + literal-string filter," so it's stricter than you'd expect. Always try semantic first.
- **"I want to start over"** → `bash scripts/teardown.sh` (irreversible — deletes bucket, index, workers, KV namespaces).

For anything not covered above, the full troubleshooting reference handles every failure mode we hit during the GigRadar deploy. Open `references/troubleshooting.md` and `Ctrl+F` for the symptom you're seeing.

---

## Cost reality check

| Resource | Free tier limit | Typical usage | Cost |
|---|---|---|---|
| R2 storage | 10 GB | usually <1 GB | $0 |
| Vectorize vectors | 30 million | usually <100K | $0 |
| Workers AI | 10K embeddings/day | usually low | $0 |
| Workers requests | 100K/day | low | $0 |

Cloudflare may ask for a payment method on file even for free usage. They don't charge unless you exceed the free tier. In two years of running this kind of stack, GigRadar paid $0.

---

## Sharing this with another team

The whole setup is per-tenant. Hand them this folder, they follow these instructions in their own Cloudflare account. Their KB has nothing to do with yours.

Don't share your `~/.kb-bootstrap.env` or your consumer token — those grant access to YOUR data.
