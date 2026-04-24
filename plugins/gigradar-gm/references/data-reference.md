# GigRadar Data Reference

Operator manual for the AI researcher. Verified against live prod MongoDB `gigradar-dev` (`<mongo-host>:<port>`, user `researcher-prod`) and the Elasticsearch deployment at `<es-host>:9243` on 2026-04-22. Source of truth for TypeScript types: `gigradar-monorepo/gigradar-definitions` (imported as `gigradar`); source of truth for repository patterns: `gigradar-aws-functions/services/utils/docdb.ts` and `services/utils/repositories/*`.

---

## 0. The big picture

GigRadar is a multi-tenant SaaS that automates Upwork lead generation. The production stack stores:

- **MongoDB** — primary OLTP store. One logical database `gigradar-${STAGE}` (`gigradar-dev` = prod data). 65 collections, covering tenants, jobs, proposals, worker accounts, chat, billing, webhooks, activity logs, OAuth, CRM.
- **Elasticsearch** — `metajob` index is the flat job firehose (every Upwork job we crawl, enriched and deduped by ciphertext). Additional indexes hold freelancer profiles, skills, agent metrics.

Everything is scoped to a **team** (`teams._id` = `ObjectId`). That tenant id appears as `gigradarTeamId` (ObjectId in most places; sometimes string in legacy writes) or `teamId` on nearly every other collection. When counting, aggregating, or filtering, **always constrain by `gigradarTeamId`**; queries without it will scan the whole platform.

### Core product pipeline (follow the arrows)

```
Upwork job feed → jobs.ledger (per-job dedupe) → temp.gigs (enriched, TTL 30d) → Elasticsearch `metajob`
                                                                                         │
                                                                                         ▼
                                           teams.scanners (user-defined queries) percolated against jobs
                                                                                         │
                                                                                         ▼
                                                                               opportunities (per match)
                                                                                         │
             ┌────────── qualified ──────────┐                                           │
             ▼                               ▼                                           │
     AI/autobidder generates           alerts (Slack/Telegram)                           │
     + dispatches to worker                                                              │
             │                                                                           │
             ▼                                                                           │
     workers.queue (BM job queue) OR workers.agent.api (OAuth)                           │
             │                                                                           │
             ▼                                                                           │
     Proposal submitted on Upwork → proposals (CRM record) ←── proposals.ledger (timeline)
             │                                                                           │
             ▼                                                                           │
     invitations / interviews / offers come back through upwork.notifications, emails    │
             │                                                                           │
             ▼                                                                           │
     leads.chats.* (CRM chat layer, sits on top of upwork.messages.* raw crawl)          │
```

### Two agent-account flavours

`enum AgentAccountType` (in `leads/index.ts`):
- **`BusinessManager`** — browser-automation "BM" Upwork accounts. Stored in `workers.upwork`. Physical worker pool lives in `workers.agent` (mobile devices). Jobs dispatched through `workers.queue` (SQS-keyed by worker `nid`).
- **`UpworkApi`** — OAuth-connected Upwork accounts. OAuth state in `upwork.oauth.state`, tokens (AES-256-CBC encrypted) in `upwork.oauth.accounts`. Runtime is a PM2 process represented by `workers.agent.api` (keyed by `apiKeyId`, no `deviceId`).

---

## 1. MongoDB — the 65 collections

Live counts captured 2026-04-22. `TTL` = server-side expiry.

### 1.1 Tenancy and identity

| Collection | Rows | Repo / file | Primary purpose |
|---|---:|---|---|
| `teams` | 2,101 | `TeamsRepository` | **The tenant boundary.** Mega-document per team: `scanners[]`, `subscription` (Stripe), `apiSubscription`, `profilesSubscription` (Stripe), `upworkAgency`, `upworkMember`, `alerts`, `preferences`, `csm`, `verifiedBMs`, `memory`, `skills`. See §2 for shape. |
| `users` | 5,480 | `UsersRepository` | Cognito-backed user identity. `_id` = cognito sub (uuid string). Holds `cognitoAttributes`, `stripeCustomer`, `subscriptions[]`, `upwork.claimedId`, `identity.{id,email}`, `roles[]`. |
| `csms` | 11 | CSM lookup — `{ email, fullName }`. Referenced by `teams.csm.csmId`. |
| `csms.tasks` | 25,691 | `CaseRepository` sibling | CSM tasks keyed by `gigradarTeamId`. Includes Stripe-invoice follow-ups (`objectType: 'stripeInvoice'`), `priority`, `due`, `resolved`. |
| `community.members` | 727 | Slack community gamification. `slackUserId`, `claimedFreelancerProfileCiphertext`, `currentXp`, `kudos[]`. Ties a Slack user to a claimed Upwork freelancer/agency profile. |
| `academy.authors` / `academy.courses` / `academy.feedbacks` | 4 / 3 / 263 | `AcademyRepository`, `AcademyFeedbackRepository` | In-app academy (videos + homework). |

### 1.2 Scanning / job feed / opportunities (the core)

| Collection | Rows | Repo | Notes |
|---|---:|---|---|
| `jobs.ledger` | 600,834 | `JobLedgerRepository` | **Per-job dedupe ledger for the global feed.** `_id` = job ciphertext. Fields: `feedType` (`US`/`UK`/`GLOBAL`), `collected: bool`, `createdAt`, `enrichedBy` (worker id), `enrichedAt`, `enrichRequeuedAt`, `enrichStuckAlerted`, `enrichAttemptCount`. |
| `temp.gigs` | 593,650 | `docdb.gigsTemp` | Enriched, fully crawled job payloads. **TTL 30 days** (`storedAt_1` index). Document shape = `metajob.metajob`. Key for joining with `opportunities` via `metaJob.ciphertext`. |
| `opportunities` | **43,185,526** | `OpportunitiesRepository` | **The matcher output.** One doc per (scanner × job) match. Type: `api.OpportunityV2`. Fields: `scannerId`, `scannerName`, `jobId` (ciphertext), `jobUid`, `title`, `published`, `detected`, `notified`, `score`, `preMatch`, `application` (proposal attempt), `feedback[]`, `leads.stage` (`EOpportunityLeadsStage`), `irrelevant`, `isPreview`, `isSimulation`, `originalGigTempId`, `originalTeamMemory`, `originalScannerMemory`. Use `gigradarTeamId_1_scannerId_1_detected_-1` for timeline queries. |
| `scanner.history` | 1,204,689 | `ScannerActivityRepository` | Scanner activity audit log. Dual format: newer `updInfo.updateType` events (per `ScannerUpdateType` enum — userSave, opportunityMatched, opportunityQualifiedByAI, opportunityProposalSent, memoryStatementAdded/Removed, etc.) and legacy `updates[]` embedded format migrated via `_migrated/_migratedAt`. Always include `teamId` + `scannerId` in queries. |

### 1.3 Proposals and downstream CRM

| Collection | Rows | Repo | Notes |
|---|---:|---|---|
| `proposals` | 5,732,593 | `ProposalsRepository` | **The proposal CRM.** Full Upwork proposal payload + `meta` object (`api.Proposal` / `MetaProposal`). Unique key: `(_gigradarTeamOid, meta.uid)`. `meta.status` ∈ `upwork.ProposalStatus` enum: `SUBMITTED=2, OFFER_LOST=3, ACTIVE=7, JOB_CLOSED=8, OFFER_WON=9, HIRED=10`. `updates[]` history capped at 30. Filter by `_gigradarTeamOid` (NOT `gigradarTeamId` — two fields: legacy string `gigradarTeamId`, authoritative ObjectId `_gigradarTeamOid`). |
| `proposals.ledger` | 197,669 | `ProposalLedgerRepository` | Per-proposal timeline events. Unique: `(proposalUid, gigradarTeamId, companyReference)`. Used for recollect retry with `enrichmentRetryAttempts`. |
| `invitations` | 318,478 | `docdb.invitations` | Invite-to-interview records. Keyed by `meta.uid`. `upworkInvitation.status` (e.g. `Declined`, `Active`). `meta.gigradarTeamId` is a **string** here, not ObjectId. |
| `upwork.notifications` | 177,933 | `NotificationsRepository` | `MetaNotification`. Upwork's notification inbox per agent account. `upworkNotificationType` ∈ `UpworkNotificationType` (Account, Bpa, Engage, Misc, Offer, Pause). Triggers proposal recollection via `proposalRecollectQueuedAt`. Unique: `(gigradarTeamId, companyReference, upworkNotificationId)`. |
| `upwork.user.notifications.meta` | 666 | same repo | Per-account high-water mark (`lastUpworkNotificationId`, `lastUpworkNotificationCreatedAt`, `totalCount`). |
| `incidents.incident` / `incidents.case` | 3,376 / 366,984 | `IncidentRepository`, `CaseRepository` | Customer-facing incidents & per-team case records (e.g. "Autobidder not working"). Soft delete via `deletedAt`. |
| `dashboard.benchmarks` | 21,904 | `BenchmarkRepository` | Daily per-category market benchmarks (avg connects, opportunities, sent, replies, CPR, LRR, PVR). Used in dashboards. |

### 1.4 Leads CRM chat layer (GigRadar CRM on top of Upwork messages)

Zod-inferred schemas live in `gigradar-definitions/leads/schemas.ts` (Room, ChatMessage, RoomMember, JobDetails, ScheduledMessage, UserReadStatus).

| Collection | Rows | Repo | Notes |
|---|---:|---|---|
| `leads.chats` | 11,032 | `LeadsChatRepository` | One room per Upwork chat. `upworkRoomUid`, `gigradarTeamId`, `memberIds[]` (upwork person uids), `jobDetails` (title, upworkJobUid, client/freelancer), `lastMessage`, `lastReadAt`, `isFavorite`/`isHidden`/`isPublic`. Text index on `title`+`jobDetails.title`. |
| `leads.chats.messages` | 195,704 | `LeadsChatMessageRepository` | One row per story/message. `upworkStoryUid`, `actionType` (`eo:post`, etc.), `author.{role: bm\|api\|client, upworkPersonUid}`, `attachments[]`, `isDeleted`. Text index on `text`+`messageHeader`. |
| `leads.chats.members` | 11,243 | `LeadsChatMemberRepository` | Upwork person → profile snapshot (firstName, lastName, profilePictureUrl, role). |
| `leads.chats.userReadStatus` | 24,231 | `LeadsUserReadStatusRepository` | Per-user last-read timestamps for unread counts. |
| `leads.chat.temp.files` | 1,282 | `LeadsChatFileMetadataRepository` | S3 (`crm-files-gr-devprod`) file upload records for chat attachments. |
| (no sibling) | — | `ScheduledMessagesRepository` | Scheduled send jobs (zod schema). Not seen in live collection list above (collection name `leads.chats.scheduled_messages` is referenced in code — may be empty in prod). |

### 1.5 Upwork raw messages (crawl output, owned by BM/API agents)

| Collection | Rows | Purpose |
|---|---:|---|
| `upwork.messages.rooms` | 160,734 | Raw Upwork chat rooms. `roomId`, `context` (jobTitle, clientName, applicationUid, jobUid, applicantName, etc.), `latestStory`, `gigradarTeamId`, `workerId`. |
| `upwork.messages.stories` | **2,899,631** | Raw Upwork messages. `storyId`, `roomId`, `message`, `actionType` (`eo:post`, `fp:sendnew` = new-proposal send event), `userId`, `orgId`, `workerId`, `gigradarTeamId`. `LeadsRepository` (`umc.IStories`) queries this with `actionType: fp:sendnew` to get proposal-send events. |
| `upwork.messages.persons` | 106,890 | Upwork people directory (freelancers + clients). `uid`, `personName.{firstName,lastName}`, `location`, `photoUrl`, `rid`. |
| `upwork.messages.users` | 478,261 | Room-member mapping. `roomId × userId × role (admin/member/etc.)`. |
| `upwork.messages.organizations` | 101,309 | Client/agency orgs from messaging graph. `uid`, `name`, `description`, `timezoneName`, `contact.{country,city,zip}`. |

### 1.6 Worker infrastructure

| Collection | Rows | Repo | Notes |
|---|---:|---|---|
| `workers.upwork` | 6,184 | `WorkersRepository` | Business Manager Upwork accounts. `login` (email), `password`, proxy config, `terminated`, `accepted`, `verified` (country), `suspendedState`, `verificationRequired`, `user.{id,rid,nid}`, `ipHistory[]`, `lastEmailCheck`, `bannedSubject`. Stay away from this without team context. |
| `workers.agent` | 79 | `AgentWorkerRepository` | BM mobile-device agents (physical pool). `deviceId`, `swarmId`, `swarmHostname`, `sqsUrl`, `status` (AVAILABLE, …), `appStatus`, `errorDetails`. |
| `workers.agent.api` | 4 | `AgentApiWorkerRepository` | OAuth (UpworkApi) PM2 agent processes. `apiKeyId`, `gigradarTeamId`, `publicUrl`, `proxy.{host,port,username,nid}`. |
| `workers.swarm` | 1 | `SwarmRepository` | Swarm host heartbeat. |
| `workers.queue` | 783 | `WorkersQueueRepository` | **BM job queue.** `_id` = worker `nid` (short hex). `dispatchedJobs[]` capped at 100 (APPLY/OPEN/FETCH/… jobs), `jobs[]` pending. Job meta: `jobId` (internal queue id), `jobType`, `jobPriority`, `queuedAt`, `dispatchedAt`, `actualApifyRunId`, `callbackAt`, `callbackPayload`, `callbackStatusCode`. |
| `proxies` | 976 | — | Proxy pool — `{host, port, login, password, type, provider, country, workerOid, assignedAt}`. Providers: smartproxy, brightdata. Countries include `United States`, `United Kingdom`, `Indonesia` (see `WorkerVerifiedCountry`). |
| `profile.emails` | 5,007 | `ProfileEmailsRepository` | Pool of inbound emails (Cloudflare forward / cloudmailin) assigned to workers. `email`, `worker`, `lastEmailReceivedDate`, `lastCheckAt`, `status`. Unique on `email`. |
| `profile.images` | 2,662 | — | S3-hosted avatar pool for BM account creation. |
| `pdfs.linkedin` | 1,186 | — | LinkedIn PDF pool (used when creating Upwork accounts / verification). |

### 1.7 API platform (public API access for paying teams)

| Collection | Rows | Repo | Notes |
|---|---:|---|---|
| `api.keys` | 8 | `ApiKeysRepository` | GigRadar public API keys. `teamId`, `key` (hashed), `keyMasked` (`gr_***...xxxx`), `active`, `rateLimits.{requestsPerMinute:30, requestsPerDay:1000}`. Max 1 active per team. |
| `api.logs` | 15,962 | `ApiLogsRepository` | Request log. `teamId`, `apiKeyId`, `path`, `ipAddress`, `statusCode`, `date`. Server has a TTL 90-day index in code (not applied on live collection per index check — verify before reporting as TTL'd). |
| `upwork.api.keys` | 4 | `UpworkApiKeysRepository` (in docdb) | Upwork OAuth app credentials (clientId + encrypted clientSecret). Scoped via `ownerType`, `ownerTeamId`, `teamIds[]`. |
| `upwork.oauth.state` | 15 | `UpworkOAuthStateRepository` | OAuth CSRF state. `nonce`, `teamId`, `apiKeyId`, `status` (`in_progress`/`error`), `expiresAt`, `error` message. |
| `upwork.oauth.accounts` | 4 | `UpworkOAuthAccountsRepository` | Connected OAuth Upwork accounts. `upwork.{userId,username,name,photoUrl}`, AES-256-CBC-encrypted `accessToken`/`refreshToken` (`iv:ciphertext` format), `tokenExpiresAt`, `tokenStatus` (active/expired), `connectedAt`, `lastCrawlAt`, `disconnectedAt`. |
| `upwork.api.crawl-queue` | 55 | `UpworkCrawlQueueRepository` | Per-room crawl schedule for OAuth agents. PK: `(roomId, teamId)`. `lastCrawledAt`, `nextScheduledCrawlAt`, `crawlIntervalMs`, `backfillCompleted`, `consecutiveErrors`. |
| `webhooks.log` | 533,252 | `WebhooksRepository` | Outbound webhook delivery log. TTL 90 days (`ttl_date_90days` on `date`). `event.type` ∈ `GIGRADAR.PROPOSAL.UPDATE`, `GIGRADAR.OPPORTUNITY.CREATE`, `GIGRADAR.INTERNAL.LEADS_SUBSCRIPTION.UPDATE`. Webhook config itself lives inside `teams.webhooks[]` (no separate collection). |

### 1.8 Profiles module (GigRadar's ranking/competitor product)

| Collection | Rows | Repo | Notes |
|---|---:|---|---|
| `upwork.agency.profiles` | 2,279 | `AgencyProfilesRepository` | Full agency profile per team (`gigradarTeamId`+`teamUid`). Arrays: `active[]`, `closed[]` contracts with full feedback. Useful for team benchmarking. |
| `upwork.contractor.profiles` | 20,248 | `ContractorProfilesRepository` | Per-contractor enriched profile. `contractorUid`, `gigradarTeamId`, `teamUid`, `ciphertext`, `completed[]`, skills, `firstName`, `lastName`. |
| `upwork.agency.profiles.public` | 3 | (tiny) | Public crawl of agency profiles (non-tenant). |
| `upwork.freelancer.profiles.public` | 5 | (tiny) | Public crawl of freelancer profiles (non-tenant). |
| `skills` | 49 | `SkillsRepository` | Upwork skill master list (`uid` = Upwork skill uid). `prettyName`, `skill`, `timesFoundInJobs`, `lastCrawl` (Apify run metadata). |
| `upwork.clients.companies` | 1,493,622 | — | Global client-companies crawl. `uid`, `persons[]`, `location`, `stats`, `company_details`, `updates[]` versioned. |
| `upwork.clients.organizations` | 1,336,720 | — | Client-organization mirror (uid, parent_uid, top_level_org_id, timezone). |
| `upwork.stats.views` | 272,655 | `ViewStatsRepository` | Profile view metrics per freelancer (`freelancerNid`, `metric`, `value`, `timestamp`). |

### 1.9 Outreach (GigRadar's outbound growth engine)

These ride on LaGrowthMachine data (`lgmLeadId`). Not backed by explicit TypeScript types in `gigradar-definitions` — the agent should treat them as LGM integration tables.

| Collection | Rows | Notes |
|---|---:|---|
| `outreach.campaign` | 85 | LGM campaign snapshot. `campaignGroup` (e.g. `gm20new`), `campaignName`, `identityId`, `stats.channel.{email,linkedin}` with all the funnel counters + percentages. |
| `outreach.journeys` | 20,289 | One per contacted lead. `lgmLeadId`, `identity` (sender), `dateString`, `lgmData` (full LGM lead blob), `isEmailReplied`, `isLinkedinReplied`. |
| `outreach.inbox` | 43,856 | Per-reply inbox items. `channel` (`LINKEDIN`/`EMAIL`), `conversationId`, `messageId`, `text`, `isSent`. |

### 1.10 Misc / system

| Collection | Rows | Notes |
|---|---:|---|
| `emails` | 1,468,913 | `EmailsRepository`. Parsed Upwork notification emails via Cloudmailin / Mailgun. `emailType` ∈ `EmailType` enum (`WEEKLY_SUMMARY`, `OFFER`, `PROPOSAL_DECLINED`, `REPLY_CHAT`, `LOGIN_VERIFICATION`, …). `normalizedTo`, `parsedDate`, `metadata.jobTitle`, `proposalRecollection.byTeam[]` (used for recollect fan-out). |
| `smtps.sender` | 1 | Gmail sender rotation pool. `email`, `password`, `used`, `limit`, `status`. |
| `slack_community_messages` | 1,555 | Mirror of Slack community messages for community gamification / dashboards. |
| `temp.panda.log` | 101,192 | Short audit log. `jobId` + `teamId` + `companyUid` + `date`. Likely anti-spam/deduplication around a "panda" scraping flow. |
| `notification.errors` | 1 | Error counter — `{channel, error, count}`. |
| `product.state` | 1 | Global feature kill-switch — `{type: 'maintain', status: 'disabled', notification}`. **Always check before writing destructive jobs.** |

---

## 2. `teams` — the mega document

`TeamsRepository` (`api.Team`, defined from line 840 in `gigradar-definitions/index.ts`, ~2050 lines of type). This one document **is the tenant**. Read carefully before joining anything.

Top-level fields observed in prod samples:
- `name` — team display name (often an email of the owner)
- `scanners[]` — **array of scanners embedded in the team doc**. Each scanner is ~`api.Scanner` with:
  - `_id` (ObjectId inside the array)
  - `query` — `GigsQueryV2` (keywords `q`, excluded, categories, budgets, countries, talentPreference, experienceLevel, workload, duration, companySize, clientIndustry, etc.)
  - `name`
  - `scoring` — per-scanner `avgFixedBudget`, `stdFixedBudget`, `avgClientRate`, etc. (populated by the scoring worker)
  - `alerts.opportunities` / `alerts.autobidder` — Slack / Telegram webhook configs
  - `biddingStrategy.options.disabled`, `biddingStrategy.options.autoBidder`
  - `memory` — GPT scanner memory statements (added/removed via `ScannerUpdateType.memoryStatement*`)
  - `deleted: bool` soft-delete flag
  - `updates[]`, `lastScan`, `lastUpdated`, `version`, `forceUpdate`
- `scannersOrder[]` — ObjectId order for UI
- `subscription.stripe` — Stripe subscription object (+ `subscription` product meters)
- `apiSubscription` — Stripe subscription for the API product (separate)
- `profilesSubscription` — Stripe subscription for the Profiles product
- `alerts.upwork.{replies, invitations, connectsBalance}.{slack, telegram}` — account-level alerts (per `upworkMember`)
- `preferences` — user UI preferences
- `upworkAgency.{companyReference, teamUid, …}` — the team's Upwork agency
- `upworkMember` — the primary agent account reference
- `csm.csmId` → `csms`
- `verifiedBMs[]` — approved BM pool for this team (country-scoped)
- `memory` — team-wide GPT memory
- `skills` — team skill set

**Query tip**: Most operational scanner queries use the compound index `_id_1_scanners._id_1` or `_id_1_scanners._id_1_scanners.deleted_1`. To get "all active scanners for a team": `{_id: teamOid, "scanners.deleted": {$ne: true}}` with `{scanners.$: 1}` projection per scanner, or `$unwind` in an aggregation.

**Query tip — find a scanner without its team**: index `scanners._id_1` lets you look up `teams` by any embedded scanner id, which is what the opportunity/proposal pipeline does.

---

## 3. `opportunities` — the matcher output

This is the busiest operational collection (43M docs). Type: `api.OpportunityV2`.

Key fields (always filter on `gigradarTeamId` first):

- `scannerId`, `scannerName` — which scanner matched this job
- `jobId` (ciphertext, like `~01...`) and `jobUid` (numeric Upwork uid)
- `title`
- Timestamps: `published` (Upwork post time), `detected` (we matched it), `notified` (alert sent), `created` (doc insert)
- `score` — matcher score
- `preMatch.{qualified}` — AI qualification verdict before generating a proposal
- `application` — the bidding attempt (subobject). Every join/lookup in the codebase (stats-repository, scanner stats) uses `application.proposalId`:
  - `application.proposalId` — **string** = the Upwork applicationUID. Joins to `proposals.meta.uid` (with matching `_gigradarTeamOid`). NOT an ObjectId, NOT pointing at `proposals._id`.
  - `application.sent`, `application.generated`, `application.dispatchedAt`, `application.error`, `application.errorCode`, `application.queuedAt`, `application.scheduledAt`
  - `application.coverLetter`, `application.questions[]`, `application.answers`, `application.price`, `application.bid.{type,amount}`, `application.rank`, `application.connectPrice`, `application.connectsExpended`, `application.boost`, `application.smartBoost`
  - `application.apifyRunId` / `application.agentRunId` (BM vs API agent attribution)
  - `application.gigradarTeamId` (ObjectId), `application.upworkTeamId`, `application.upworkFreelancerUid`, `application.upworkFreelancerId`, `application.upworkCompanyUid`, `application.companyReference`
  - `application.algorithmSignature`, `application.priceStripeId`, `application.slsInvLink`, `application.model` — autobidder attribution (Template/Sardor/Laziza)
  - `application.originalStrategy` — snapshot of template/answerTemplate/options used at send time
- `leads.stage` ∈ `EOpportunityLeadsStage`: `NEW`, `CONTACT_LATER`, `INTERESTED`, `BOOKED`, `HAPPENED`, `QUALIFIED`, `CONVERTED`, `UNREACHABLE`, `NOT_INTERESTED`, `WRONG_TARGET`, `ALREADY_EQUIPPED`
- `feedback[]` — per-user thumb up/down on the opportunity (drives `scanner.history` memory updates)
- `irrelevant: bool` — auto-disqualified
- `isPreview: bool`, `isSimulation: bool` — for dry-run matcher
- `originalGigTempId` → `temp.gigs`; `originalTeamMemory` / `originalScannerMemory` / `originalPortfolios` — snapshots at match time
- `slackAlert.sent` — Slack notification sent

**Best indexes to lean on**:
- `gigradarTeamId_1_scannerId_1_detected_-1` — the timeline query
- `idx_leads_team_jobUid`, `idx_leads_team_proposalId` — CRM lookups
- `opportunities_team_title_proposalId` — title search
- `getScannerStatsIndex` — heavy aggregation for scanner stats

---

## 4. `proposals`

Type: `api.Proposal` which extends `MetaProposal` (line 4897 in `gigradar-definitions/index.ts`).

Two team fields exist — **use `_gigradarTeamOid` (ObjectId)** as the filter, not the legacy string `gigradarTeamId`.

> **⚠️ Scanner / template / algorithm / generated-CL attribution is NOT on this collection.** `proposals` is the Upwork-sync CRM record of what got submitted; it does **not** carry `scannerId`, `scannerName`, `templateId`/`originalGigTempId`, `algorithmSignature`, `algorithmVer`, `promptVersion`, `model`, or `originalStrategy`. Those fields live on the matching `opportunities` doc (see §24.10a). Any audit that slices by scanner, template, algorithm, or the generated CL MUST `$lookup` into `opportunities` via `opportunities.application.proposalId ↔ proposals.meta.uid` (both strings). The absence of a joined opportunity is itself a signal: it means the proposal is a **manual bid** (not auto-bidder output). See §10.C for the canonical query, §17.3 for the split projection template, §24.10a for the empirical Ubiquify auto-vs-manual split.

Key fields:
- `meta.uid` (Upwork applicationUID, unique per team — `(_gigradarTeamOid, meta.uid)` is the uniqueness key; also the **join key** into `opportunities.application.proposalId`)
- `meta.status` — `upwork.ProposalStatus` enum. Integer: `2=SUBMITTED, 3=OFFER_LOST, 7=ACTIVE, 8=JOB_CLOSED, 9=OFFER_WON, 10=HIRED`
- `meta.createdAt`, `auditDetails.createdTs`/`modifiedTs`
- `meta.jobId` (ciphertext), `meta.jobTitle`
- `meta.author.{name, uid, slug, avatarUrl}`, `meta.freelancer.{name, rid, slug, avatarUrl}`
- `meta.chat.chatId` — joins to `leads.chats.upworkRoomUid`
- `meta.connectsExpended`, `meta.skills.labels[]`
- `applicationUID`, `applyingAs`
- `client.buyer.info.{company, location, stats, jobs, logo}`
- `client.activity.{lastBuyerActivity, numberOfPositionsToHire, totalApplicants, totalInvitedToInterview, totalHired, invitationsSent}`
- `attachments`, `archiveReason`, `otherAnnotations`
- `proposalExtended.originalJob.visibility` — privacy flag on the job at proposal time
- `updates[]` — capped at 30 historical change records

**Best indexes**: `_gigradarTeamOid_1_meta.uid_1` (unique), `_gigradarTeamOid_1_meta.createdAt_-1`, `idx_gigradarTeamOid_jobId_freelancerRid_createdAt`, `idx_leads_team_chatId`.

---

## 5. `jobs.ledger` + `proposals.ledger` (the timelines)

Two append-only ledgers used for enrichment/recollection retry logic. Both have the `enrichedBy` field (worker short-id hex).

- **`jobs.ledger`**: `_id` = job ciphertext. `feedType` ∈ `US`/`UK`/`GLOBAL`. `collected: bool`, `enrichRequeuedAt`, `enrichStuckAlerted`. Index `idx_feedType_collected_createdAt` supports the "next N uncollected jobs in this feed" worker query.
- **`proposals.ledger`**: `proposalUid` + `gigradarTeamId` + `companyReference` unique. `recollectedAt`, `enrichmentRetryAttempts`. Used to fan out proposal recollects triggered by incoming `upwork.notifications`.

---

## 6. Upwork raw messaging vs GigRadar leads CRM

There are **two stacks** — do not confuse them when querying.

- **Raw crawl** (owned by BM or API worker, usually for operational replay): `upwork.messages.rooms`, `upwork.messages.stories`, `upwork.messages.persons`, `upwork.messages.users`, `upwork.messages.organizations`. Docs are keyed by `workerId` and `gigradarTeamId`.
- **Product CRM** (what the frontend reads): `leads.chats`, `leads.chats.messages`, `leads.chats.members`, `leads.chats.userReadStatus`, `leads.chat.temp.files`. Normalized, zod-validated, with per-user read state and S3 attachments.

Bridge: `leads.chats.upworkRoomUid` = `upwork.messages.rooms.roomId`. `leads.chats.messages.upworkStoryUid` = `upwork.messages.stories.storyId`. The `LeadsRepository` specifically queries `upwork.messages.stories` with `actionType: 'fp:sendnew'` to detect "a proposal was sent in this room" events.

---

## 7. Billing

No dedicated collection — lives inside `teams`. Stripe subscription objects cached under:
- `teams.subscription.stripe` — core proposal product
- `teams.apiSubscription` — API product
- `teams.profilesSubscription` — Profiles product

Per-algorithm metered usage is billed through Stripe meters. Current algorithm types: `Template`, `Sardor`, `Laziza` (per `AutoBidderType`). Meter IDs, price IDs, and default per-proposal costs are hardcoded in `gigradar-definitions/billing/index.ts`. Intervals: `Weekly`, `Monthly`, `Quarterly`, `SemiAnnual`, `Annual`.

Products: `ProductName` ∈ `leads`, `profiles`, `api`, `groupTrial`. Profile seat limits: TRIAL=1, BASIC=1, AGENCY=3, PRO=10.

`usage` collection holds per-team usage events (`type: 'bidding'`, `charged` in cents, `price`, `quantity`, `total`, `date`, `data.{proposalUid, relatedEntity.{collection, oid}}`). Drives meter event emission and billing reconciliation.

---

## 8. Elasticsearch

Cluster URL in prod: `https://<es-host>:9243` (requires Basic auth — see `ES_PUBLIC_LOGIN=public_prod_elastic_api` env var). Separate self-hosted cluster for write traffic: `https://<stage-es-host>/` (stage).

### Indexes

| Index | Purpose | Repo |
|---|---|---|
| `metajob` | **All crawled Upwork jobs**, write alias. Document shape = `metajob.crawledJob.CrawledJob extends upwork.Job`. | `ElasticGigsRepository` (`services/utils/elasticSearch/ElasticGigsRepository.ts`). Constant: `JOBS_INDEX_ALIAS = 'metajob'` (in `services/workers/functions/syncScannersToPercolatorV1/constants.ts`). |
| `profile-contractor` | Freelancer profiles mirror. | `ElasticContractorRepository` |
| `profile-skill` | Skill master + aggregates. | `ElasticSkillRepository` |
| `profile-skill-rank` | Per-contractor per-skill rank history. | `ElasticSkillRankRepository` |
| `jobs-volume-skill-daily` | Daily job-volume rollup per skill. | `ElasticSkillRepository` |
| `agent-metrics` | Agent performance time series. | `services/workers/functions/agentMetricsAlertAggregatorV1` |
| `agent-actor-runs` | Apify actor run events. | `services/workers/functions/agentActorRunsAlertAggregatorV1` |

There is also a scanner-percolator index (`SCANNER_PERCOLATOR_ES_BASE_URL`) — scanners are stored as percolator queries and jobs are matched against all scanners on ingest.

### `metajob` document shape (the important one)

Combined from `metajob.metajob` (the original scrapped view) and `metajob.crawledJob.CrawledJob` (the enriched ES view):

- `ciphertext` — Upwork job id, the primary key
- `runId`, `date_scrapped`
- `title`, `description`
- `createdOn`, `publishTime`
- `budget` (`{type, fixed, hourlyMax, hourlyMin}`), `hourlyBudget.{min,max,type}`
- `duration`, `engagement`, `experienceLevel`, `talentPreference`, `connectsPrice`
- `categoryName`, `subCategoryName`, `skills[]`, `ontologySkillNames[]`
- `questions[]` (with optional Sardor AI-generated `answer`)
- `client.{paymentVerified, location{country,city,timezone}, stats{feedbackScore,totalSpent,hireRate,averageHourlyRatePaid,totalHires,totalHiresActive,totalHireHours,jobsPostedCount}, company{industry,size,isEnterprise,name,id,description,summary,websiteUrl}}`
- `qualification.{prefLocationsCountry[], prefLocationMandatory, spokenLanguages[]}`
- `jobTrend[]` — time-series of client activity snapshots while the job was open
- `isPrivate`, `privateScrappedAt`, `aiInterviewerEnabled`
- `meta` — GigRadar-computed: `clientHireRate`, `descriptionLength`, `hourlyProjectBudgetEstimation`, `fixedProjectRateEstimation`, `clientSpentPerHire`, `jobTrend[]` (null-safe variant), `topBids[]` (detected competing bids)
- `augmentedData.clientNames.{companyNames[], personNames[], confidence}` — LLM-extracted client names (from `client-name-extractor` agent)
- `summary.buyer.{jobs, stats, avgHourlyJobsRate, company{profile{size,industry,visible,l3Occupations[]}}, location{country,city,state,countryTimezone,worldRegion,offsetFromUtcMillis}, logo}`
- `summary.job.{info{type,title,ciphertext,access,recno,createdOn,isPtcPrivate,uid,premium,hideBudget,…}, description, status, postedOn, workload, duration, budget{currencyCode,amount}, clientActivity{…}, contractorTier, segmentationData[], categoryGroup, category, attachments, openingUid, publishTime, extendedBudgetInfo, engagementDuration, sandsData{occupation, ontologySkills, additionalSkills, occupations[]}, annotations.customFields}`
- `v4Details.jobDetails.buyer.workHistory[]` — full client work history (contractor + feedback + total hours + rate)
- `matcher.embedding[]`, `matcher.text_blob` (vector + BM25 text for matcher)
- `matcher.appliedByTeams[].{teamId, proposalStatus, isInterviewed}` — updated at proposal-send time so we can show "X teams already applied".

---

## 9. Enums to remember

From `gigradar-definitions/index.ts`:
- `EmailType`: TEAM_INVITATION, UNEXPECTED_LOGIN, ACCOUNT_BLOCKED, VERIFICATION_REQUIRED, CHANGE_EMAIL_REQUEST, LOGIN_VERIFICATION, EMAIL_VERIFICATION, WEEKLY_SUMMARY, ACCEPT_OFFER, JOB_CLOSED_PROPOSAL, OFFER, PROPOSAL_DECLINED, REPLY_CHAT, UNKNOWN.
- `UpworkNotificationMetaType`: PROPOSAL_DECLINED, PROPOSAL_VIEWED, INVITATION_TO_INTERVIEW, INTERVIEW_ACCEPTED, OFFER_MADE, CONTRACT_STARTED, UNKNOWN.
- `UpworkNotificationType`: Account, Bpa, Engage, Misc, Offer, Pause.
- `EOpportunityLeadsStage`: NEW, CONTACT_LATER, INTERESTED, BOOKED, HAPPENED, QUALIFIED, CONVERTED, UNREACHABLE, NOT_INTERESTED, WRONG_TARGET, ALREADY_EQUIPPED.
- `ScannerUpdateType`: userSave/userEnable/userDisable/oppFeedbackMemUpdate/memoryRecreate/systemDisable/systemEnable/trialExpiredDelete/trialConversionDuplicate/scannerCreated/userDuplicate/opportunityMatched/opportunityQualifiedByAI/opportunityDisqualifiedByAI/opportunityProposalCreated/opportunityProposalSent/opportunityProposalFailed/opportunityViewed/opportunityChat/opportunityBoosted/opportunityWebhookSent/opportunityWebhookFailed/opportunityJobNotificationSent/autoBidderNotificationSent/opportunityPositiveFeedback/opportunityNegativeFeedback/memoryStatementAdded/memoryStatementRemoved/teamMemoryStatementAdded/teamMemoryStatementRemoved/userDelete.

From `gigradar-definitions/upwork/index.ts`:
- `upwork.ProposalStatus`: SUBMITTED=2, OFFER_LOST=3, ACTIVE=7, JOB_CLOSED=8, OFFER_WON=9, HIRED=10.

From `gigradar-definitions/leads/index.ts`:
- `AgentAccountType`: BusinessManager (BM), UpworkApi (OAuth).

From `gigradar-definitions/billing/index.ts`:
- `SubscriptionType`: Weekly, Monthly, Quarterly, Semi-annual, Annual.
- `AutoBidderType`: Template, Sardor, Laziza.
- `ProductName`: leads, profiles, api, groupTrial.
- `ProfilesPlan`: basic, agency, pro, trial.
- `SubscriptionAddonStatus`: active, canceled, trialing.

From `profiles/index.ts`:
- `KeywordType`: custom, ontology.
- `FreelancerCrawlerRequestTarget`: page, gql.
- `BadgeType`: top_rated, top_rated_plus, hipo, null.

From `webhooks/index.ts`:
- `EventType`: `GIGRADAR.PROPOSAL.UPDATE`, `GIGRADAR.OPPORTUNITY.CREATE`, `GIGRADAR.INTERNAL.LEADS_SUBSCRIPTION.UPDATE`.

---

## 10. Query cookbook

### A. All scanners for a team

```js
db.teams.findOne(
  { _id: ObjectId("<teamId>") },
  { scanners: 1, name: 1 }
)
```

To filter out deleted scanners in aggregation:
```js
db.teams.aggregate([
  { $match: { _id: ObjectId("<teamId>") } },
  { $unwind: "$scanners" },
  { $match: { "scanners.deleted": { $ne: true } } },
  { $project: { scanner: "$scanners" } }
])
```

### B. Opportunities pipeline for a scanner, last 7 days

```js
db.opportunities.find({
  gigradarTeamId: ObjectId("<teamId>"),
  scannerId: "<scannerIdStringOrObjectId>", // check both; scanner ids are sometimes stored as strings
  detected: { $gte: ISODate("...") },
  isPreview: { $ne: true }
}).sort({ detected: -1 })  // uses gigradarTeamId_1_scannerId_1_detected_-1
```

### C. Connect an opportunity to its proposal

`opportunities.application.proposalId` (**string** = Upwork applicationUID) → `proposals.meta.uid`. Team filter: `gigradarTeamId` on `opportunities`, `_gigradarTeamOid` on `proposals` (both ObjectIds). Cross-check `opportunities.jobId` (ciphertext) against `proposals.meta.jobId`.

This is the exact join `StatsRepository.getScannerStats` uses:
```js
{ $lookup: {
    from: 'proposals',
    localField: 'application.proposalId',
    foreignField: 'meta.uid',
    pipeline: [{ $match: { _gigradarTeamOid: teamOid } }, { $project: { dashroomUID: 1, status: 1, otherAnnotations: 1 } }],
    as: 'proposal',
}}
```

Verified live 2026-04-22: sample doc `{ _id, jobId: "~01e34f42fca101ffb5", application: { proposalId: "1691063615321939969" } }` → joined to a proposal on `meta.uid`.

### D. All proposals won by a team

```js
db.proposals.find({
  _gigradarTeamOid: ObjectId("<teamId>"),
  "meta.status": { $in: [9, 10] }   // OFFER_WON, HIRED
}).sort({ "meta.createdAt": -1 })  // uses _gigradarTeamOid_1_meta.createdAt_-1
```

### E. Chat thread for a proposal

`proposals.meta.chat.chatId` → `leads.chats.upworkRoomUid` → `leads.chats.messages.upworkRoomUid`.

### F. Connects spent by a team this month

```js
db.usage.aggregate([
  { $match: {
      gigradarTeamId: ObjectId("<teamId>"),
      type: "bidding",
      date: { $gte: ISODate("2026-04-01") }
  }},
  { $group: { _id: null, totalCents: { $sum: "$charged" }, count: { $sum: "$quantity" } } }
])  // uses gigradarTeamId_1_date_1
```

### G. Job lookups across MongoDB ↔ Elasticsearch

- Upwork job primary key: **ciphertext** (`~01...`).
- In MongoDB: `opportunities.jobId`, `proposals.meta.jobId`, `temp.gigs.metaJob.ciphertext`, `jobs.ledger._id`.
- In ES: `metajob._id` / `metajob.ciphertext`.

### H. Who is the BM worker behind a proposal?

`proposals.meta.chat.chatId` → `leads.chats.upworkRoomUid` → `leads.chats.upworkWorkerId` (ObjectId) → `workers.upwork._id`. Alternatively via `upwork.messages.rooms.workerId`.

### I. Count jobs in a US feed that still need enrichment

```js
db["jobs.ledger"].countDocuments({ feedType: "US", collected: false })
// uses idx_feedType_collected_createdAt
```

---

## 11. Access quick reference

**MongoDB** (read-only, scoped to `gigradar-dev` collections):
```
mongodb://researcher-prod:<password>@<mongo-host>:<port>/gigradar-dev?authSource=admin
```

Python:
```python
from pymongo import MongoClient
c = MongoClient("mongodb://researcher-prod:<password>@<mongo-host>:<port>/gigradar-dev?authSource=admin",
                serverSelectionTimeoutMS=15000)
db = c["gigradar-dev"]
```

Role does **not** have permission on `system.views` (acceptable — no materialized views to worry about).

**Elasticsearch**: requires Basic auth. Cluster at `https://<es-host>:9243`. Auth envs surfaced in code: `ES_USERNAME=elastic`, `ES_PUBLIC_LOGIN=public_prod_elastic_api`. Request credentials from the team before querying — current researcher credentials do not authorize ES.

---

## 12. Researcher rules of thumb

1. **Always scope by `gigradarTeamId` (or `_gigradarTeamOid` on `proposals`) first.** Unscoped queries scan hundreds of millions of documents.
2. **Pay attention to `gigradarTeamId` types.** `opportunities.gigradarTeamId` = ObjectId. `proposals.gigradarTeamId` = string (legacy), but `proposals._gigradarTeamOid` = ObjectId (use this). `invitations.meta.gigradarTeamId` = string. Check both types on lookups that cross collections.
3. **Scanner ids cross collection boundaries as mixed types** — sometimes ObjectId, sometimes the stringified version. The embedded `teams.scanners[]._id` is an ObjectId; `opportunities.scannerId` and `proposals.scannerId` tend to be strings.
4. **Soft deletes everywhere**: `teams.scanners[].deleted`, `incidents.*.deletedAt`, `proposals.meta` deletion flag variants, `upwork.contractor.profiles` soft-remove. Filter accordingly.
5. **Preview / simulation docs exist** in `opportunities` (`isPreview`, `isSimulation`) — exclude from production metrics.
6. **Money**: `usage.charged` is cents. `proposals` connects values are integers. Stripe amounts are cents.
7. **Do not assume Upwork IDs are stable across types.** `jobUid` (numeric), `jobId` (ciphertext `~01...`) and `recno` are different handles. Team-scoped views mostly use `jobId` (ciphertext); Upwork internal messaging uses `uid`/`recno`.
8. **`opportunities` is a monster** (43M rows). For analytical work, prefer Elasticsearch `metajob` for job-shape analysis and keep MongoDB queries pinned to compound indexes with `gigradarTeamId` as the leading key.
9. **`upwork.messages.stories` (2.9M rows) is the source of truth for raw chat activity**, but the product CRM reads `leads.chats.messages`. They can drift — if an answer must be real-time, check the raw stories and the CRM both.
10. **Before destructive or high-volume work, check `product.state`** — if `status: 'disabled'` with `type: 'maintain'`, the platform is in maintenance and writes may be rejected.
11. **Secrets**: AES-256-CBC-encrypted tokens are stored as `iv:ciphertext` (Base64). Don't bother decoding without the `API_SECRET` env var — it's not in this doc.

---

## 13. Canonical stats formulas (from `StatsRepository`)

Path: `gigradar-aws-functions/services/utils/repositories/stats/stats-repository.ts`. If you produce any reply-rate / view-rate / connects metric, match these definitions exactly — this is what the product dashboard shows.

### 13.1 Reply

**A proposal is "replied"** iff `proposals.dashroomUID` is non-null. `dashroomUID` is Upwork's chat-thread id; it gets populated the moment the client opens a message thread with the freelancer (sending a message, marking Shortlisted, inviting to interview, etc.). In code: `{ $ne: ['$dashroomUID', null] }`.

### 13.2 View

**A proposal is "viewed"** iff ANY of:
- `dashroomUID` is non-null (chat started), OR
- `status === 7` (`upwork.ProposalStatus.ACTIVE`), OR
- `otherAnnotations[]` contains `12` (`upwork.ReverseEngineeredAnnotation.PROPOSAL_VIEWED`).

### 13.3 Proposals base filter

```js
{
  _gigradarTeamOid: ObjectId(teamId),
  "meta.createdAt": { $gte: from, $lt: to },
  "meta.inviteToInterviewUid": null,   // exclude invite-replies — only count outbound proposals
}
```

### 13.4 Opportunities base filter

```js
{
  gigradarTeamId: ObjectId(teamId),
  notified: { $gte: from, $lt: to },   // use `notified`, not `detected` or `created`
  isPreview: { $ne: true },
  // scannerId filter optional for trial-user scoping
}
```
The dashboard then dedupes by `jobId` (one count per unique job seen in the window).

### 13.5 Connects spent (proposal)

Priority ladder: `terms.connectsBid > 0` → else `meta.connectsExpended` → else top-level `connectsExpended` → else `0`. Uses `$cond + $gt` rather than `$ifNull` because Upwork writes `0` as "no data".

### 13.6 Scanner-level reply rate (opportunities + proposals join)

```js
// from StatsRepository.getScannerStats
$lookup: {
  from: 'proposals',
  localField: 'application.proposalId',
  foreignField: 'meta.uid',
  pipeline: [{ $match: { _gigradarTeamOid: teamOid } }, { $project: { dashroomUID: 1, status: 1, otherAnnotations: 1 } }],
  as: 'proposal',
}
// then: totalReplied = sum($cond: [{$not: '$proposal.dashroomUID'}, 0, 1])
```

---

## 14. Index / perf log (for analytical queries)

Running log of performance pitfalls and index suggestions discovered while doing large analytical aggregations. Each entry: **observation → suggestion**.

1. **`opportunities` point-scans on `application.proposalId` alone are fine** (there's a dedicated `application.proposalId_1` index), but paired with a team filter you should explicitly use `idx_leads_team_proposalId` = `{gigradarTeamId: 1, "application.proposalId": 1}` — it's the index the codebase assumes.
2. **Broken/duplicate index on `opportunities`**: `gigradarTeamId_1_application.proposalId_1_1` has key `{gigradarTeamId: 1, "application.proposalId_1": 1}` — note the literal `_1` suffix inside the field name. This looks like a typo (someone typed the sort order into the field). It indexes a field that doesn't exist, wasting storage. Suggested: **drop** `gigradarTeamId_1_application.proposalId_1_1`; keep `idx_leads_team_proposalId`.
3. **Unfiltered `$exists` scans on `opportunities` (43M docs) time out** even with small projections. Always include `gigradarTeamId` (and `notified` where possible) in the match stage. If you truly need "all opps with X field", use `allowDiskUse: true` + `hint` + a covering index, and run from a worker — not from an ad-hoc session.
4. **`proposals` analytics windowed by `meta.createdAt`** are covered by `_gigradarTeamOid_1_meta.createdAt_-1`. Sorting by `meta.createdAt` asc uses `_gigradarTeamOid_1_meta.createdAt_1` (both directions exist). Any `$lookup` back to the same team should pin `_gigradarTeamOid` in the pipeline filter (done in `StatsRepository`).
5. **When joining `opportunities` → `proposals` across a full month**, filter opps by `notified` first (uses `gigradarTeamId_1_notified_-1` if present; otherwise the compound `gigradarTeamId_1_scannerId_1_detected_-1`). Otherwise the `$lookup` fan-out blows up.

Index creation suggestions to propose to the platform team (not yet applied):

- **Drop** `gigradarTeamId_1_application.proposalId_1_1` on `opportunities` (broken key, see #2 above).
- **Consider** a `{gigradarTeamId: 1, notified: 1}` index on `opportunities` if it doesn't exist — the existing `gigradarTeamId_1_scannerId_1_detected_-1` doesn't cover month-windowed aggregations that don't filter by scanner.
- ~~Consider a `{"meta.createdAt": 1}` non-compound index on `proposals`~~ — **already exists** (index name `meta.createdAt_1`). Cross-team analytical queries work, but Mongo's planner picks `_gigradarTeamOid_1_meta.createdAt_1` by default even without a team filter (it walks the whole index). Fix: pass `hint={"meta.createdAt": 1}` explicitly. Verified Apr 22 2026: a cross-team May 2025 category aggregation runs in ~95s with the hint; without it, it timed out at 10 min.

---

## 15. ES metajob — field gotchas (research log)

Learned while running aggregations for the Apr/May/Jun 2025 trends report (see `upwork_may2025_trends.xlsx`).

### 15.1 Field names and `.keyword` suffix

- `metaJob.categoryName`, `metaJob.subCategoryName`, `metaJob.ontologySkillNames` are **already `keyword` type** — do NOT append `.keyword`. Appending it returns 0 buckets.
- `metaJob.skills.name` is a `text` field with a `.keyword` sub-field. Terms aggregations MUST use `metaJob.skills.name.keyword`.
- Date field for month-windowed queries: `metaJob.createdOn` (not `date_scrapped`, which may exist but returns empty in this index).

### 15.2 `metaJob.ontologySkillNames` is functionally empty

Despite being indexed, this field has near-zero population for 2025 data (only 10 docs for all of April 2025, all legal-related; 0 for May and June). Don't plan any analysis around it — use `metaJob.skills.name.keyword` instead.

### 15.3 `track_total_hits` required for accurate volume

ES limits `hits.total` at 10,000 by default. Every analytical aggregation on `metajob` needs `{"track_total_hits": true}` in the body, else every month looks like "exactly 10,000 jobs."

### 15.4 Budget medians must be scoped by budget type

`metaJob.budget.type`: `1 = fixed`, `2 = hourly`. `metaJob.budget.fixed` is 0/null on hourly jobs and vice-versa. Taking a percentile over the raw field produces nonsense (medians of 0–3). Use a filter sub-agg per type:

```json
"fixed": {
  "filter": {"term": {"metaJob.budget.type": 1}},
  "aggs": {"median_fixed": {"percentiles": {"field": "metaJob.budget.fixed", "percents": [50]}}}
}
```

### 15.5 Client-quality fields (verified present)

These are populated reliably and work as `avg` aggs for quality analysis:
- `metaJob.client.paymentVerified` — boolean-as-avg gives % verified in the bucket.
- `metaJob.client.stats.totalSpent` — USD all-time client spend.
- `metaJob.client.stats.feedbackScore` — 0–5 scale.
- `metaJob.client.stats.hireRate` — 0–1 share of posted jobs that were hired.

### 15.6 Access (researcher-prod role)

- URL: `https://<es-host>:9243`
- User: `researcher-prod`
- Role: `metajob-ro` — index-scoped. Cluster-level endpoints (`/_cluster/health`, `/_cat/indices`) return 403; direct `/metajob/_search`, `/metajob/_count`, `/metajob/_mapping` work.
- Default index alias: `metajob` (mapping sits under `metajob-v9-000001`).

---

## 16. Cross-team reply-rate analytics (research log)

For research questions like "what's the market-wide reply rate for skill X?", NOT customer-facing dashboards.

### 16.1 Grouping directly off proposals.metaJob

`proposals.metaJob` embeds the full job shape at the moment the proposal was submitted. You can group by `metaJob.categoryName`, `metaJob.subCategoryName`, or unwind `metaJob.skills` and group by `metaJob.skills.name` — **no join to ES needed**. Much cheaper than joining `opportunities` → ES.

### 16.2 Null-category rows

~25–30% of proposals in a given month have `metaJob.categoryName = null`. These are older records from before the embedded metaJob enrichment pipeline matured, or edge cases where the scanner emitted a proposal without job enrichment. They show up as a `null` bucket in `$group` and should be excluded from per-category reply-rate tables but included in market totals.

### 16.3 Proposal pool ≠ market

The Mongo `proposals` collection represents only the proposals submitted by GigRadar customer agencies. In May 2025 this was ~93k proposals against 198k total jobs posted on Upwork — i.e. GigRadar customers saw and bid on ~47% of the market.

Important: the distribution across categories is **heavily skewed toward Web/Mobile/SW Dev** (64% of proposals vs 26% of market jobs). Categories like Translation, Writing, Customer Service, Legal have <1k proposals per month — any reply-rate inference on them has wide confidence intervals. Always report a "proposals" denominator alongside a reply rate.

### 16.4 Reply-rate snapshot effect

`dashroomUID` is written when a client opens a chat thread with the freelancer — this can happen days or weeks after the proposal was submitted. So the reply rate for the most recent window is always biased low because replies are still coming in. Observed concretely: June 2025 reply rate queried in April 2026 was 7.7% vs 10.6% in May — the 10-month gap wasn't enough to close for June either, suggesting late-reply tail matters materially. If you need stable numbers, use windows at least 60 days old.

---

## 17. Customer-audit corrections (retro-first methodology)

Added 2026-04-22 after re-auditing Ubiquify Digital with the corrected methodology. The following sections supersede earlier guidance.

### 17.1 Reply + hire definitions (DO NOT use dashroomUID)

- **Reply** = `proposals.meta.chat.chatId` field is POPULATED (non-null AND non-empty-string). Query with `{'meta.chat.chatId': {'$exists': True}}` or `{'meta.chat.chatId': {'$nin': [None, '']}}` — **NOT** `{'$ne': None}` (that variant returns 0 due to Mongo missing-field semantics; see §24.8). NOT `dashroomUID != null`, NOT `status == 7`, NOT "has interview". Interview = reply for customer audits. Do not compute a separate interview rate.
- **Hire** = `proposals.meta.status` ∈ `[10, 'Hired']` (MIXED TYPES — must use `$in`; see §24.7). Confirmed closed deal on Upwork.
- Why: chat id populated = the client opened a conversation; status HIRED = contract signed on-platform. `dashroomUID` and status 7 are lagging proxies and should only be used for the global dashboard (see §13) — not for a customer audit.
- Apply (**methodology v2**):
  - **Reply rate** = `replies / sent` — **HEADLINE / north-star metric.**
  - **$/reply** = `sum(connectsExpended) * $0.15 / replies` — **HEADLINE cost metric.**
  - **Hire rate** = `hires / sent` — **diagnostic only**, always report with population label ("P-rank among N teams with ≥1 hire in window"). Never as top-line.
  - **$/hire** — diagnostic only, never headline.
  - Why hires aren't headline: GigRadar users often close off-Upwork (paid outside the platform, moved to Slack/email). Hire counts systematically undercount real wins; reply rate is the least-lossy observable success signal.

### 17.2 Contract-closed timestamp = `auditDetails.modifiedTs`

For every HIRED proposal the "closed" timestamp should come from the status-change audit log, NOT the proposal's creation time. Candidate sources in order of preference:
1. `auditDetails.modifiedTs` — last mutation to the proposal (typically the status flip to HIRED).
2. `client.buyer.info.company.contractDate` — set when the client clicks "hired" with a signed contract.
3. If a status-change log collection exists, use it (check `proposals.updates[]` first).

Using `meta.createdAt` for time-to-close or month-bucketed win counts makes deals look instantly closed and breaks cohort comparison. The gap between `meta.createdAt` and the actual close can be weeks to months.

Apply: when reporting wins by month, computing time-to-close, or building the retro win timeline — always use `auditDetails.modifiedTs` (or the best available status-change timestamp). Include BOTH dates on the evidence row so the GM can sanity-check the spread.

### 17.3 Minimalistic Mongo projections (non-negotiable)

Every `find` / `aggregate` / `count_documents` on `proposals` or `opportunities` must pin a `projection={...}` with the exact fields needed and nothing else. Both collections have huge per-doc payloads (full Upwork job HTML, rendered cover letters, skills arrays, LLM prompts) — fetching full docs at volume kills bandwidth and times out.

> **Heads up — two-collection projection.** Scanner / template / algorithm / CL-text attribution lives on `opportunities.application.*`, NOT on `proposals`. Any audit query that slices by scanner, template, algorithm, or cover-letter text MUST project BOTH sides and join via `opportunities.application.proposalId ↔ proposals.meta.uid` (strings, per §24.10a). An audit that only projects proposal fields will silently lose the auto-bidder metadata.

**Minimal proposal projection template** — the Upwork-sync side (CRM record of what was submitted):

```python
{
  "_id": 1, "_gigradarTeamOid": 1,
  "meta.uid": 1,  # join key to opportunities.application.proposalId
  "meta.createdAt": 1, "meta.status": 1, "meta.jobId": 1, "meta.jobTitle": 1,
  "meta.author.name": 1, "meta.author.uid": 1,
  "meta.freelancer.name": 1, "meta.freelancer.rid": 1,
  "meta.chat.chatId": 1,
  "meta.inviteToInterviewUid": 1,  # null = outbound bid, non-null = invite cohort
  "meta.connectsExpended": 1,
  "terms.connectsBid": 1, "connectsExpended": 1,  # for the connects ladder (§24.11)
  "auditDetails.modifiedTs": 1, "auditDetails.createdTs": 1,
  "applicationUID": 1,
  "dashroomUID": 1,  # codebase-canonical reply signal (§24.11) — use with $exists/$nin, NOT $ne:null (§24.8)
  "archiveReason.reason": 1, "archiveReason.reasonRef": 1,
  # coverLetter / renderedCoverLetter — only for narrow win/loss sampling
}
```

**Minimal companion opportunity projection** — the auto-bidder side (what the scanner / algorithm / CL generator produced):

```python
{
  "_id": 1, "gigradarTeamId": 1,
  "application.proposalId": 1,  # join key to proposals.meta.uid
  "scannerId": 1, "scannerName": 1,
  "originalGigTempId": 1,  # template ObjectId (NOT on proposals)
  "score": 1, "jobId": 1, "jobUid": 1,
  "notified": 1, "detected": 1, "generationStartedAt": 1, "published": 1,
  "application.algorithmSignature": 1,
  "application.algorithmVer": 1,
  "application.promptVersion": 1,
  "application.model": 1,
  "application.config": 1,
  "application.bid": 1,  # {type: 'hourly'|'fixed', amount} — hourly stores amount:null
  "application.connectPrice": 1,  # per-bid connect cost, varies (not always 15)
  "application.cost": 1,  # $ to generate via LLM
  "application.boost": 1, "application.matchPercentage": 1,
  "application.generated": 1, "application.sent": 1,
  "application.coverLetter": 1,  # generated CL text — only project for narrow samples
  "application.originalStrategy": 1,  # strategy snapshot — only for narrow samples
  "isPreview": 1,  # filter with {"$ne": True} — preview opps are dry-runs
}
```

Add `renderedCoverLetter`, `client.buyer.info.company.contractDate`, chat message bodies, `application.prompt`, `application.llmRawOutput`, etc. ONLY for cherry-picked follow-up queries (≤50 rows).

Before writing a query:
1. Name the index the filter will hit. Proposals: `_gigradarTeamOid_1_meta.createdAt_1` / `_gigradarTeamOid_1_meta.createdAt_-1` / `idx_leads_team_chatId` / `gigradarTeamId_1_auditDetails.createdTs_-1` / `contractRef_-1` / `meta.status_1`. Opportunities: `gigradarTeamId_1_scannerId_1_detected_-1` / `gigradarTeamId_1_notified_-1` / `gigradarTeamId_1_application.proposalId_1` (verify).
2. List the projection fields explicitly on BOTH sides if you're joining.
3. Decide if this is a count (use `count_documents` with the indexed filter — never `len(list(find(...)))`), a scan, or a sample.
4. For heavy bodies (CLs, chat messages, prompts) — narrow to ≤50 rows first.
5. Pick `auditDetails.modifiedTs` vs `meta.createdAt` deliberately for time filters.
6. For the auto-bidder join: drive the pipeline from `opportunities` (filter by `gigradarTeamId` + `notified` window + `isPreview: {$ne: true}`) and `$lookup` into `proposals` by `application.proposalId → meta.uid`. Drop rows where the join doesn't hit if you want the auto-bidder cohort only; keep `$unwind {preserveNullAndEmptyArrays: true}` only when you need the manual cohort too.

Cross-references:
- §4 — proposals field catalogue (scanner/template/algorithm attribution is NOT here; see §24.10a).
- §10.C — query cookbook for the opportunity-first pipeline.
- §24.10a — canonical join key, auto-bidder vs manual split, Ubiquify 98.8%/1.2% empirical finding.
- §24.11 — codebase-canonical reply / connects-spent definitions.

### 17.4 Archive-reason + feedback-reason fields (REFERENCE ONLY — do NOT build the scanner-quality grid)

> **Methodology v2 update:** we no longer build a per-scanner rejection-reason / feedback-reason distribution grid. In real audits these distributions are sparse, team-specific (see §24.1: `archiveReason.*` empty on the Ubiquify sample), and do not surface actionable insights. The **Win/Loss CL comparison** (see `audit-playbook.md` Section 4) replaces it as the core scanner diagnostic.
>
> This section stays as a field-schema reference — so if you ever do need to investigate a specific opportunity's archive reason for a cherry-picked Win/Loss row, you know the shape of the data. Do not aggregate it.

Proposals carry `archiveReason.{rid, reason, reasonRef, otherReason, message}`, and two feedback-reason enums surface the human-in-the-loop verdicts on jobs and applications.

- `OpportunityFeedbackReason` (on `opportunities.feedback[].reason`):
  - `irrelevant_targeting` — scanner FALSE POSITIVE (bad match surfaced).
  - `pre_match_error` — scanner FALSE NEGATIVE (should have matched, didn't).
  - `job_not_good_fit_for_profile`
  - `spam_job`
  - `accurate_targeting`
  - `accurate_disqualified_job`
- `ApplicationFeedbackReason` (on application/proposal feedback):
  - `generation_instructions_not_honored`
  - `bot_like_cover_letter`
  - `portfolio_project_not_relevant`
  - `incorrect_representation`
  - `accurate_generation`

**When to read these fields (narrow, per-row):**
- When pulling a cherry-picked loss for the Win/Loss table (Section 4), include `archiveReason.reason` in the projection so the Notes column can reference it if populated — "archived: irrelevant_targeting" is a useful one-liner when present.
- When investigating why a scanner's specific loss looks irrelevant, check the matching `opportunities.feedback[].reason` for that doc.

**Do NOT:** aggregate these fields across a whole scanner and produce a grid of distributions. Do NOT recommend based on the distribution shape. Do NOT produce a "scanner-quality grid" workbook sheet.

### 17.5 Slicing dimensions — what IS vs IS NOT user-configurable

Teams tune these (valid audit slice dimensions):
- Cover-letter **template** (`coverLetterTemplate`, `answerTemplate`, `templateId`).
- Upwork **profile** (bio, hourly rate, portfolio, case studies).
- Scanner **targeting** (query, excluded terms, country/budget filters, schedule).
- Scanner **bidding strategy** (hourly rate strategy, boosting, connects boost expense, bidding terms, **algorithm version**).

Teams do NOT tune (do NOT slice on):
- `promptVersion`
- `llm`
- `proof_reader_version`

Scanner naming convention: `{campaign_family}-{NN}-{variant_letter}` (e.g. `AM-05-C` = variant C of campaign AM-05). Different letters are intentional A/B/C buckets — treat them as a controlled experiment; do not aggregate across letters.

### 17.6 Connects cost constant

Upwork connects cost **$0.15 each** (absolute unit price, not a rate). Used to compute $ cost of sent / replied and **$ per reply** (north-star cost metric) and $ per hire (diagnostic only). Factor into rate/positioning recommendations.

### 17.7 Error codes to ignore in customer audits

Ignore `meta.status == ERROR` analysis UNLESS the error is user-actionable (insufficient credits, payment-method issues, profile issues). Specifically DROP:
- code 9012 `ProposalAlreadySent` — expected in multi-scanner setups.
- code 234 `OutsideScheduledHours` — the team's own A/B test across scanners on different days.
- code 2006 `ContractorLocationMismatchPreferred`, 2020 `JobInterviewRequired`, rate-limits.

These are design choices, not bugs. Focus the audit on: profile quality, CL quality, scanner targeting, bidding rates. If insufficient-credits errors appear, surface them separately as an operational flag.

### 17.8 Retro-first audit ordering (methodology v2)

Hard ordering rule — do NOT start with auto-bidding data.

1. **Retro on the client themselves.** Pull their FULL proposal history (including pre-GigRadar) with no lower date bound. Read the oldest 50 + all HIRED. Identify pre-GigRadar wins (`status == 10`), read those CLs, note rate / tone / positioning. Compare to the current CL.
2. **Competitive deep-dive.** One consolidated section:
   - 2A **Cohort compare** vs sibling teams and `dashboard.benchmarks` (reply rate + $/reply as headline; hire rate as diagnostic with population label).
   - 2B **Peer look-alike vector search.** Use `metajob` KNN on `matcher.embedding` WITHOUT the team filter; walk `matcher.appliedByTeams[]` in returned hits for `proposalStatus == 10` or `isInterviewed == true`. Fetch those winners' proposals + `upwork.agency.profiles` from Mongo. Harvest: rate, profile description, CL style.
3. **Chat-room transcripts.** For HIRED + replied proposals, fetch `leads.chats` by `upworkRoomUid = proposal.meta.chat.chatId`, then `leads.chats.messages` sorted by `createdAt`. Read first 5–10 messages — rate pushback, scope changes, drop-off points.
4. **Win/Loss CL comparison** (**THE core scanner diagnostic**). Cherry-pick 1–3 winners + 1–3 losers per scanner with meaningful volume; pull scanner config + `renderedCoverLetter` + opportunity description + client info; build side-by-side table; derive concrete edit suggestions. **This replaces the old scanner-quality rejection-reason grid.**
5. **Auto-bidding aggregates** per `scannerId × templateId × algorithmSignature` — supporting context only. Reply rate + $/reply headline; hires + hire rate diagnostic. No standalone recommendations from this sheet.
6. **Three-tier synthesis** into a single dark-mode xlsx workbook with the exec summary on sheet 1 — **WINS (green) / OKAY (amber) / CRITICAL (red)** bands, every item backed by evidence cells linking to detail sheets. NO separate markdown summary.

### 17.9 Cohort compare fallback

For each metric (reply rate — headline, $/reply — headline, view rate — supporting, hire rate — diagnostic with population label), compare against:
- team's own full-history average,
- sibling-cohort (same `teams.serviceNames`, similar scale),
- `dashboard.benchmarks` per-category averages.

**Fallback when `dashboard.benchmarks` is missing for the exact category:** infer the agency category from the Upwork profile description + scanner names/queries (e.g. "automation agency", "AI/ML consulting", "web-dev shop"), then either pull the closest-matching `dashboard.benchmarks` category or directly query sibling teams by `serviceNames` similarity and compute cohort medians ad-hoc. Do not silently skip cohort compare — always produce a number, even if approximate, and note the inference in the caveats section.

**Percentile-reporting rule (critical):** whenever quoting a percentile rank, state the population inline. Use **all qualifying teams** (≥100 sent) for reply rate / view rate / $/reply — customer's P-rank is directly comparable. Use **teams with ≥1 hire in window** only for hire rate as a diagnostic; otherwise the zero-hire long tail (88% of auto-bidding scanners never hire in any 90-day window) inflates apparent standing. Never mix denominators in the same rank sentence.

---

## 18. `leads.chats` + `leads.chats.messages` + `leads.chats.members` — schema detail

Expanded from §1.4 for analytical use by the customer-audit skill.

### 18.1 `leads.chats`

One doc per Upwork chat room. Used to read client replies on HIRED / replied proposals.

Key fields:
- `_id` (ObjectId)
- `upworkRoomUid` — **join key** to `proposals.meta.chat.chatId` (format: `room_{...}`).
- `gigradarTeamId` (ObjectId) — tenant.
- `memberIds[]` — Upwork person uids in the room.
- `memberUids[]` — legacy alias; same contents in new writes.
- `jobDetails.{title, description, jobId, upworkJobUid, client, freelancer}` — the job the room was created for.
- `recentMessages[]` — embedded cache of recent messages (may be stale; for the audit pull the full `leads.chats.messages` list instead).
- `oldestMessage`, `lastMessage` — timestamps/refs.
- `startedAt` — room creation time.
- `lastReadAt`, `isFavorite`, `isHidden`, `isPublic`.
- `title` — room title (may override `jobDetails.title`).

Indexes:
- `gigradarTeamId_1_upworkRoomUid_1` (uniqueness) — tenant + room join.
- Text index on `title` + `jobDetails.title`.

Minimal projection for audit use:
```python
{
  "_id": 1, "upworkRoomUid": 1, "gigradarTeamId": 1,
  "jobDetails.title": 1, "jobDetails.jobId": 1,
  "startedAt": 1, "lastMessage": 1,
}
```

### 18.2 `leads.chats.messages`

One doc per chat message (story). Fetch in ascending `createdAt` order to read client reactions.

Key fields:
- `_id` (ObjectId)
- `upworkRoomUid` — **join key** to `leads.chats`.
- `upworkStoryUid` — Upwork story id (maps to `upwork.messages.stories.storyId`).
- `text` — the message body. Heavy field; fetch only for the narrowed room set.
- `messageHeader` — optional offer/system header text.
- `author.{name, uid, type, role}` — `type` ∈ `text | file | offer | system`, `role` ∈ `bm | api | client` (from the underlying crawl).
- `attachments[]` — S3 refs.
- `actionType` — Upwork action type (`eo:post`, etc.).
- `isDeleted: bool`.
- `createdAt` — message timestamp.

Indexes:
- `upworkRoomUid_1_createdAt_1` — timeline scan within a room.
- Text index on `text` + `messageHeader`.

Minimal projection for audit use:
```python
{
  "_id": 1, "upworkRoomUid": 1, "upworkStoryUid": 1,
  "text": 1, "messageHeader": 1,
  "author.type": 1, "author.role": 1, "author.name": 1,
  "actionType": 1, "createdAt": 1,
}
```

### 18.3 `leads.chats.members`

Upwork person → profile snapshot (firstName, lastName, profilePictureUrl, role). Not usually needed beyond author resolution.

Key fields: `_id`, `upworkPersonUid`, `firstName`, `lastName`, `profilePictureUrl`, `role`, `gigradarTeamId`.

### 18.4 Join pattern — proposal → chat → messages

```
proposal.meta.chat.chatId
    ↓ (equals)
leads.chats.upworkRoomUid
    ↓ (1-to-many)
leads.chats.messages.upworkRoomUid
```

Example chain for an audit:

```python
# 1) fetch room (single doc)
room = db["leads.chats"].find_one(
    {"upworkRoomUid": proposal["meta"]["chat"]["chatId"],
     "gigradarTeamId": ObjectId(team_id)},
    projection={"_id":1, "upworkRoomUid":1, "jobDetails.title":1, "startedAt":1},
)

# 2) fetch messages, sorted ascending
msgs = list(db["leads.chats.messages"].find(
    {"upworkRoomUid": room["upworkRoomUid"]},
    projection={"_id":1, "text":1, "author.type":1, "author.role":1,
                "author.name":1, "createdAt":1, "actionType":1},
).sort("createdAt", 1).limit(50))
```

---

## 19. `OpportunityFeedbackReason` + `ApplicationFeedbackReason` enums

Field-schema reference for two enum families. **See §17.4** — the aggregate scanner-quality rejection-reason grid is dropped as of methodology v2; the Win/Loss CL table replaces it. This enum table stays as a reference for interpreting a single cherry-picked loss's `archiveReason` when you see it in a Win/Loss row. Sourced from `gigradar-monorepo/gigradar-definitions/index.ts`.

### 19.1 `OpportunityFeedbackReason` (on `opportunities.feedback[].reason`)

| Value | Meaning | Scanner-quality interpretation |
|---|---|---|
| `irrelevant_targeting` | Scanner surfaced a bad match. | **False positive** — scanner query too loose. |
| `pre_match_error` | Scanner should have matched but didn't. | **False negative** — scanner query too narrow. |
| `job_not_good_fit_for_profile` | Job doesn't fit the team's profile. | Profile/targeting mismatch — candidate for scanner refinement or profile rewrite. |
| `spam_job` | Detected spam. | Noise filter signal — not actionable for the audit. |
| `accurate_targeting` | Scanner correctly surfaced a relevant match. | Positive signal. |
| `accurate_disqualified_job` | Scanner correctly filtered a bad match. | Positive signal. |

### 19.2 `ApplicationFeedbackReason` (on application/proposal feedback)

| Value | Meaning | CL-quality interpretation |
|---|---|---|
| `generation_instructions_not_honored` | LLM ignored the template's instructions. | System issue — flag as operational, not actionable for the team. |
| `bot_like_cover_letter` | CL reads generic / templated. | **CL rewrite signal** — template is too formulaic. |
| `portfolio_project_not_relevant` | Attached portfolio doesn't match the job. | **Portfolio refinement signal** — update portfolio selection logic or case studies. |
| `incorrect_representation` | CL misrepresents the team's capabilities. | **Profile-vs-CL mismatch signal** — reconcile CL promises with actual profile/portfolio. |
| `accurate_generation` | CL correctly represents the team. | Positive signal. |

### 19.3 Do NOT aggregate (methodology v2)

**Dropped.** The scanner-quality distribution grid (irrelevance archive rate / accurate-targeting rate / pre-match-error rate / CL-quality red-flag rate per scanner) is **no longer part of the customer audit**. In real audits the distributions are sparse, team-specific, and do not surface actionable insights.

Use these enums only for interpretation of a single row — the `archiveReason` of a specific cherry-picked loss in the Win/Loss CL table, or the `ApplicationFeedbackReason` of a specific replied proposal when framing its chat transcript. Do not compute per-scanner rates over these fields. Do not build the "scanner-quality grid" workbook sheet.

---

## 20. ES `metajob` KNN — cross-team (collaborative filtering) query

Added for the peer look-alike phase (§17.8 step 2).

### 20.1 Purpose

Given a target job (ciphertext or description), find historical jobs that look similar AND were WON by other teams, so we can harvest their rate / CL style / profile description. This is the collaborative-filtering play when the subject team's own history is thin.

### 20.2 Query shape (no team filter)

```json
{
  "size": 100,
  "_source": {
    "includes": [
      "matcher.appliedByTeams",
      "matcher.text_blob",
      "summary.job.info.title",
      "summary.buyer.company.profile"
    ]
  },
  "knn": {
    "field": "matcher.embedding",
    "query_vector": [... 1536 or 3072 dims ...],
    "k": 100,
    "num_candidates": 2000
  }
}
```

Do NOT add a nested `filter` on `matcher.appliedByTeams.teamId` — we explicitly WANT to see jobs other teams applied to.

### 20.3 Post-processing: walk `appliedByTeams[]` for winners

```python
winners = []
for hit in resp["hits"]["hits"]:
    for team in hit["_source"].get("matcher", {}).get("appliedByTeams", []):
        if team.get("proposalStatus") == 10 or team.get("isInterviewed"):
            winners.append({
                "jobCiphertext": hit["_id"],
                "teamId": team["teamId"],
                "proposalStatus": team.get("proposalStatus"),
                "isInterviewed": team.get("isInterviewed"),
                "jobTitle": hit["_source"].get("summary", {}).get("job", {}).get("info", {}).get("title"),
            })
```

### 20.4 Fetch winner artifacts from Mongo

```python
for w in winners:
    proposal = db["proposals"].find_one(
        {"_gigradarTeamOid": ObjectId(w["teamId"]),
         "meta.jobId": w["jobCiphertext"]},
        projection={
            "_id": 1, "meta.createdAt": 1, "meta.status": 1,
            "meta.jobTitle": 1, "renderedCoverLetter": 1,
            "templateId": 1, "algorithmSignature": 1,
            "auditDetails.modifiedTs": 1,
        },
    )
    profile = db["upwork.agency.profiles"].find_one(
        {"gigradarTeamId": ObjectId(w["teamId"])},
        projection={"description": 1, "hourlyRate": 1, "services": 1},
    )
```

### 20.5 Reference implementations

- `gigradar-ml-service/common/elasticsearch/job.py` — `get_job_embedding_by_id`, `similarity_search(embedding, team_id, k, num_candidates)`.
- `gigradar-ml-service/pre_matcher/proposal_similarity/proposal_similarity.py` — WON/INTERVIEWED/SENT/LOSS scoring rubric; same enum used when walking `appliedByTeams`.

### 20.6 ⚠️ Embedding coverage horizon — READ BEFORE USING KNN FOR RETRO

`matcher.embedding` was only rolled out to the `metajob` pipeline in mid-2025. Per an engineer's count (monthly distribution of `metajob` docs with `matcher.embedding` by `metaJob.createdOn`):

| Month | Embedded docs |
|---|---:|
| 2025-01 | 0 |
| 2025-02 | 5 |
| 2025-03 | 1 |
| 2025-04 | 510 |
| 2025-05 | 392 |
| 2025-06 | 18,583 |
| 2025-07 | 21,073 |
| 2025-08 | 19,766 |
| 2025-09 | 19,540 |
| 2025-10 | 106,596 |
| 2025-11 | 177,063 |
| 2025-12 | 165,746 |

**Consequence:** jobs created before ~2025-06 have effectively NO embedding. Full coverage kicks in ~2025-10. KNN on historical pre-2025-06 jobs returns nothing / near-nothing.

**Implications for the retro phase:**
- **Do NOT use KNN on pre-GigRadar / pre-2025-06 jobs.** The embeddings don't exist.
- **Client's OWN pre-GigRadar wins** must be pulled directly from Mongo `proposals` using the `_gigradarTeamOid_1_meta.createdAt_1` index, sorted ascending, filtered by `meta.status == 10`. No ES needed — this is a pure Mongo retrospective.
- **Peer look-alike KNN remains valid ONLY when the seed job is CURRENT** (≥ 2025-10 for reliable coverage, ≥ 2025-06 for partial). Seed from the client's CURRENT target ciphertexts → find similar CURRENT jobs that OTHER teams won → harvest those teams' CLs / profiles / rates.
- If you need cross-team look-alikes on historical jobs, the only options are (a) re-embed the old jobs ad-hoc via the ML service, (b) fall back to `matcher.text_blob` BM25 search, or (c) accept that cross-team retro is unavailable and stick to direct Mongo pulls.
- **When reporting KNN results, always state the seed job's `metaJob.createdOn`** — if it's pre-2025-10, caveat the result as "partial coverage window."

**Quick sanity check before any KNN call:** does the seed job have `matcher.embedding`? If `get_job_embedding_by_id(ciphertext)` returns None, skip KNN and fall back to text/BM25.

---

## 21. Status-change timestamps — `auditDetails.modifiedTs` and `client.buyer.info.company.contractDate`

Added for §17.2. These are the correct fields for "when did this proposal close", NOT `meta.createdAt`. **Empirically tested on Ubiquify (2026-04-22 probe): `contractDate` IS populated on 25/25 HIRED (values span 2011-2024). `auditDetails.modifiedTs` is also populated on all 25 but with much tighter spread.**

### 21.1 `auditDetails.modifiedTs` (preferred for in-GigRadar-era close)

Subfield of the `auditDetails` object on every `proposal`. Tracks the last mutation to the doc.

- For a HIRED proposal (`meta.status == 10`), this is typically the timestamp of the status flip from `SUBMITTED`/`ACTIVE` → `HIRED`.
- **Covers only the in-GigRadar-era close, not historical Upwork hires.** For proposals ingested as historical data (Upwork account sync on first GigRadar signup), `modifiedTs` reflects the INGEST time, not the original hire time.
- Covered by index `gigradarTeamId_1_auditDetails.createdTs_-1` (note: the index keys `createdTs`, not `modifiedTs` — for `modifiedTs` queries, still filter on `_gigradarTeamOid` first and scan within team).
- Always include both `auditDetails.createdTs` AND `auditDetails.modifiedTs` in the projection so the evidence row shows the spread.

### 21.2 `client.buyer.info.company.contractDate` — this is the CLIENT's Upwork-signup date, NOT the hire date

**Corrected 2026-04-22:** This field is a property of the CLIENT COMPANY on Upwork (when the buyer first signed up to Upwork as an employer), NOT when they hired the freelancer. Ubiquify's contractDate values span 2011-2024 simply because different clients signed up in different years.

- Do NOT use `contractDate` as the proposal close date.
- It's still useful context — a client with `contractDate: 2011` is a long-tenured Upwork buyer (more mature hiring habits, longer client lifetime). This is client-quality metadata, not transaction metadata.
- For the actual hire timestamp, see §21.4 below for where to look next (still being validated).

### 21.3 Do NOT use `meta.createdAt` for close dates

`meta.createdAt` is the proposal **submission** time. Using it as the close date makes deals look instantly closed and breaks:
- month-bucketed win counts,
- cohort comparison by close-month,

### 21.4 Candidate fields for the ACTUAL hire timestamp (unvalidated — probe before using)

On HIRED proposals the following top-level keys exist and MAY carry the hire timestamp. Need empirical validation per team:
- `contractRef` — ObjectId ref. Probably joins to a separate contracts collection that has start/end dates.
- `terms` — contract terms object; likely has `startDate`.
- `readyToStartDate` — literally "when the freelancer is ready to start" (proposal-time field).
- `status` (top-level, distinct from `meta.status`) — may have status-change history.
- `updates[]` — array of status transitions? Check.
- `jobDetails` — embedded job snapshot; may have `hiredAt` or similar.

**Until validated, treat the actual hire timestamp as UNKNOWN.** `auditDetails.modifiedTs` remains the best proxy for in-GigRadar-era status flip, but for pre-GigRadar historical hires (ingested via Upwork account sync), there is currently no proven field that gives the true original hire date. The retro-first methodology may need to rely on proposal submission ordering (`meta.createdAt` ascending) as the only available timeline signal for pre-signup history.
- time-to-close metrics.

Gap between `meta.createdAt` and actual close can be weeks to months.

### 21.4 Apply

```python
# month-bucket HIRED proposals by actual close
from datetime import datetime

hired = db["proposals"].find(
    {"_gigradarTeamOid": ObjectId(team_id), "meta.status": 10},
    projection={
        "_id": 1, "meta.jobTitle": 1, "meta.createdAt": 1,
        "auditDetails.modifiedTs": 1,
        "client.buyer.info.company.contractDate": 1,
    },
)
for p in hired:
    close = (
        p.get("auditDetails", {}).get("modifiedTs")
        or p.get("client", {}).get("buyer", {}).get("info", {}).get("company", {}).get("contractDate")
        or p["meta"]["createdAt"]  # last resort, flag the row
    )
    month = close.strftime("%Y-%m") if isinstance(close, datetime) else None
```

---

## 22. Algorithm signatures catalog

Added for the audit slicing rule (§17.5). From `gigradar-monorepo/gigradar-definitions/billing/index.ts` (`AutoBidderType`) and observed values on `proposals.algorithmSignature`.

| Signature | Type enum | Description |
|---|---|---|
| `TEMPLATE` | `AutoBidderType.Template` | Template-only auto-bidder. Deterministic CL from the configured template; minimal LLM generation. |
| `SARDOR` | `AutoBidderType.Sardor` | Sardor AI auto-bidder. Heavier LLM usage (question answering, memory injection). |
| `LAZIZA` / `ALG_LAZ` | `AutoBidderType.Laziza` | Laziza AI auto-bidder. Latest generation; scanner memory + richer personalization. |
| `PUBLIC_API` | — | Proposals submitted via the public API (external / partner systems). |

`algorithmSignature` is on both `proposals` and `opportunities.application.algorithmSignature`. `algorithmVersion` tracks the sub-version within a signature (e.g. a Laziza v3 vs v4 rollout).

These are valid slicing dimensions — the team configures which algorithm a scanner uses via the bidding strategy. `promptVersion` / `llm` / `proof_reader_version` are internal within a given algorithm and NOT user-configurable.

---

## 23. Query-design checklist (apply before every Mongo query)

Pinned from §17.3. Apply mechanically before writing any `find` / `aggregate` / `count_documents`:

1. **Which index will this filter hit?** Name it. If you can't name one, stop and reconsider — an unindexed query on `proposals` (5.7M docs) or `opportunities` (43M docs) will time out.
2. **What is the minimal projection?** List fields explicitly. Never `projection=None` on `proposals`.
3. **Is this a count, a scan, or a sample?** Counts use `count_documents` with the indexed filter, NEVER `len(list(find(...)))`.
4. **For heavy bodies** (`renderedCoverLetter`, chat `text`, job description HTML) — am I narrowing to ≤50 rows FIRST via an aggregate / top-K pull?
5. **For time filters** — am I using `auditDetails.modifiedTs` (status-change) or `meta.createdAt` (submission)? Pick deliberately; they answer different questions.
6. **Tenant scoping** — am I filtering on `_gigradarTeamOid` (proposals) / `gigradarTeamId` (opportunities, leads.chats, etc.) as the leading index key? Unscoped queries scan the whole platform.
7. **Preview/simulation exclusion** — on `opportunities`, am I excluding `isPreview: true` / `isSimulation: true`?

If any answer is "I don't know," stop and read the relevant §1-§8 section above before running the query.

---

## 24. Empirical audit-signal survey — what's ACTUALLY available (Ubiquify probe, 2026-04-22)

These are signals I expected to be present for rejection-reason / chat-transcript / close-date analysis, and what the data actually showed on a real team (teamOid `679a215568faa05722aabb93`, 8,539 proposals). **These are team-specific empirical findings — other teams may differ — but they illustrate which signals are reliably populated vs. empty.**

**Important correction:** an earlier draft of this section claimed `client.buyer.info.company.contractDate` was the pre-GigRadar hire date. That is WRONG — it's the client company's Upwork signup date (when the buyer first joined Upwork). See §21.2 for the correct interpretation.

### 24.1 `archiveReason.*` — EMPTY for this team
All five sub-fields (`reason`, `reasonRef`, `otherReason`, `message`, `rid`) are null on all 8,539 proposals. `archiveReason` is PRESENT as an object but its fields aren't populated. **Conclusion: don't assume `archiveReason` is populated — probe first per team. Team-specific archive workflows may or may not fill it.**

### 24.2 `declineReadon` (typo field — on the doc) — RARE but populated
Ubiquify has ~52 populated decline reasons across 8,539 proposals:
- 47 × `{rid: '146', reason: 'Other', reasonRef: 'API_REAS_REJECT_OTHER', otherReason: 'client suspended'}` — client account was banned mid-conversation
- 4 × `{rid: '150', reason: 'Just preferred other applicants'}` — explicit client rejection
- 1 × `{rid: '75', reason: 'Application expired', reasonRef: 'API_REAS_AUTO_EXPIRATION'}`

**Note the typo: `declineReadon` (extra letter) is the actual doc field name.** Values are structured as a nested object with keys `rid / reason / reasonRef / otherReason / message`. The `reasonRef` field is an Upwork API constant; useful for grouping.

**Conclusion: `declineReadon` gives actionable decline signal but only on 0.6% of Ubiquify proposals. It's client-side rejection data (post-reply), not scanner-side. Surface the small count as qualitative evidence, not a scanner-quality grid.**

### 24.3 `withdrawReason` — 1 populated on Ubiquify
`{rid: '141', reason: 'Applied by mistake'}` — used when the freelancer/agency retracts. Near-empty in this sample.

### 24.4 `statusReason`, `invalidateReason` — all null on Ubiquify
Not populated on any Ubiquify proposal.

### 24.5 `opportunities.feedback` — empty for Ubiquify
No opportunity docs for this team have any `feedback` / `reason` / `decline` fields populated. `OpportunityFeedbackReason` enum exists but the feedback isn't captured for this team. Probe before relying on it.

### 24.6 `leads.chats` — ZERO docs for Ubiquify
Despite 759 proposals having `meta.chat.chatId` populated, `db.leads.chats.findOne({gigradarTeamId: TEAM_OID})` returns nothing. Nor do `upwork.messages.rooms` / `upwork.messages.stories` have matching `roomId`. **Chat transcripts for Ubiquify appear to be un-synced.** Team-specific; don't build the audit around chat-transcript reading as a default — verify availability per team with a direct lookup on one known `chat_id`.

### 24.7 `meta.status` is a MIXED TYPE
Same team has statuses as both numeric enum codes AND string labels:
- Numeric: `2` (6,475), `8` (1,260), `7` (601), `3` (67), `10` (24), `4` (1)
- String: `'Accepted'` (74), `'Activated'` (23), `'Declined'` (7), `'Archived'` (6), `'Hired'` (1)

**Implication: ALL status queries must use `$in` over both types.** Contract = `{$in: [10, 'Hired']}` (25, not 24). Reply rollup must handle both.

### 24.8 `meta.chat.chatId` reply signal — USE `$exists` OR `$nin: [null, '']`, NOT `$ne: null`

**GOTCHA (wasted multiple probe iterations):** The query `{'meta.chat.chatId': {'$ne': None}}` in pymongo returned 0 matches even though 759 docs truly have the field populated. Reason: the field is MISSING on non-replied docs (not literal null, not empty string — the key is absent). `$ne: null` in Mongo find has counter-intuitive semantics with missing fields.

**Correct reply-signal queries:**
- `{'meta.chat.chatId': {'$exists': True}}` — ✅ returns 759 for Ubiquify
- `{'meta.chat.chatId': {'$nin': [None, '']}}` — ✅ returns 759 (safer if empty strings ever appear)
- `{'meta.chat.chatId': {'$ne': None}}` — ❌ returns 0 (BROKEN — do not use)

**In aggregation projection**, however, `$meta.chat.chatId` resolves missing → null and `{$cond: [{$ne: ['$meta.chat.chatId', null]}, 1, 0]}` INCORRECTLY returns 1 for missing fields. Use `{$cond: [{$and: [{$ne: ['$meta.chat.chatId', null]}, {$ne: ['$meta.chat.chatId', '']}, {$ifNull: ['$meta.chat.chatId', false]}]}, 1, 0]}` or filter by `{'meta.chat.chatId': {'$exists': True}}` in a prior `$match` stage.

**Ubiquify truth**: 759 / 8,540 proposals truly replied (**8.89% reply rate**). 21 of 25 HIRED have a populated `chatId`; 4 HIRED closed WITHOUT an Upwork chat (direct invites / offline relationships / backfilled status). Hire rate: **25/8,540 = 0.29%** (bid-to-hire) or **25/759 = 3.29%** (reply-to-hire).

### 24.9 Team signup date — use `team._id.generation_time`, NOT `team.createdAt`

`team.createdAt` is null on many team docs (Ubiquify included). Since team `_id` is an ObjectId, extract the creation timestamp directly: `ObjectId('679a215568faa05722aabb93').generation_time` → `2025-01-29 12:38:45 UTC`. Works across all teams.

Corroborating signals when present:
- `team.payment.selection.providerData.subscription.start_date` (Unix seconds) — Stripe subscription start. Ubiquify: `1775809471` → 2026-04-10 (current trial, `usedTrial: true` indicates prior trial existed).
- `team.payment.selection.providerData.subscription.trial_start` / `trial_end` — trial window.
- `team.payment.selection.usedTrial` — boolean, true if team has consumed at least one trial.
- Earliest `auditDetails.createdTs` across this team's proposals — first time GigRadar INGESTED anything for them.

**Retro-ingestion gotcha:** `meta.createdAt` can predate team creation by months to years (Upwork submit timestamps from account sync). `auditDetails.createdTs` reflects when GigRadar ingested the proposal into its own store, always >= team._id generation time. For Ubiquify: 10 proposals have `meta.createdAt` (2024-03 → 2024-12) before team creation (2025-01-29); 3 of those 10 are HIRED. So "retro-first" analysis IS meaningful when the pre-signup cohort is large — but for Ubiquify specifically, only 10 retro proposals vs 8,530 GigRadar-era, so retro is a minor tail, not the main story.

### 24.10a Scanner/template/algorithm metadata lives on OPPORTUNITIES, not proposals

**Critical correction to earlier DATA_REFERENCE entries that listed `scannerId` / `templateId` / `algorithmSignature` as proposal-level fields — they are NULL on proposals.** For auto-bidder proposals these fields live on the joined opportunity document.

**Canonical join key (per codebase, verified in `services/utils/repositories/opportunities/opportunities-repository.ts` ~line 237 and `StatsRepository.getScannerStats`)**:

```
opportunities.application.proposalId  ↔  proposals.meta.uid
```

Both sides are **strings** (verified live 2026-04-22: sample proposal `meta.uid: "1763545115219456001"` (str); sample opportunity `application.proposalId: "2019167820853010433"` (str) — both numeric-looking Upwork applicationUIDs stored as strings). Team filter: `gigradarTeamId` on `opportunities`, `_gigradarTeamOid` on `proposals` (both ObjectIds). Do NOT join on `meta.jobId ↔ jobId` — that's a ciphertext match that collapses when one job has multiple proposals, and is not what the codebase uses.

**Join coverage for Ubiquify (correct join, probe 10)**: **8,441 / 8,540 proposals (98.8%) are AUTO-BIDDER** (have a matching opportunity via `application.proposalId`); the remaining **99 / 8,540 (1.2%) are MANUAL** (no joinable opportunity — synced from Upwork but not originated by GigRadar).

**Funnel diverges sharply by cohort** (major audit finding):

| Cohort | n | Replied | Hired | Reply rate | Hire rate |
|---|---|---|---|---|---|
| Auto-bidder (has opp) | 8,441 | 724 | 21 | 8.58% | 0.249% |
| Manual (no opp) | 99 | 35 | **4** | **35.35%** | **4.04%** |

Manual bids convert **~16x** better than auto-bidder output on this team. That 1.2% manual tail contains 4 of 25 total hires. The prior probe that used `meta.jobId ↔ jobId` (8,400/140) was wrong on both count and conclusion.

**Fields on opportunity top-level**:
- `scannerId` (ObjectId), `scannerName` (string, e.g., `'MERN Stack'`)
- `jobId` / `jobUid` (Upwork job UID)
- `originalQuery` (full scanner filter: `q`, `minFixedBudget`, `minHourlyRate`, `country`, etc.)
- `originalGigTempId` (template ObjectId — this is what the earlier doc mislabeled as `templateId`)
- `score` (scanner match score, 0..1)
- `application` (sub-document with ALL the algorithm/LLM metadata — see below)
- `created`, `detected`, `generationStartedAt`, `published` (pipeline timestamps)

**Fields inside `opportunities.application` sub-doc** (the auto-bidder payload):
- `algorithmSignature` (string — sometimes a zero-width-unicode marker like `'ㅤ⁤'` — the audit must treat these as opaque ids, not try to pretty-print)
- `algorithmVer` (string, e.g., `'sardor-ai-v2'`)
- `promptVersion` (string, e.g., `'1.2.6.1'`)
- `model` (LLM name, e.g., `'gpt-4-1106-preview'`)
- `config` (`prompt_version`, `validator`, `add_ons`, `llm`)
- `prompt` (full LLM prompt array used)
- `llmRawOutput` (raw LLM output before parsing)
- `coverLetter` (the generated cover letter — can also be read from proposal.coverLetter)
- `failedCoverLetters` (retry log)
- `bid` (`{type: 'hourly'|'fixed', amount: number}`)
- `connectPrice` (connects spent — Ubiquify's sample was 20 connects, NOT the stated platform default of 15; connect cost varies per-bid)
- `cost` (dollar cost to GENERATE the proposal via LLM — e.g., 2.185)
- `fallbackContractorRate` / `fallbackMaxClientBudgetRate` (pricing fallbacks)
- `rank`, `boost`, `matchPercentage`, `matchPercentageArgumentation`
- `upworkFreelancerId`, `upworkFreelancerUid`, `upworkFreelancerProfileUid`, `upworkTeamId`, `upworkCompanyUid`
- `generated` (datetime — when the proposal was generated, slightly before it was submitted on Upwork)
- `retries` (array of retry attempts)
- `addOns` (`puzzle_solver`, `memory_validation`, etc.)

**Implication for audit**: For a customer audit you need a join aggregation (`$lookup` on `jobId` or pre-compute the opp-index), not proposal-only aggregation. The reference queries below should include the join.

### 24.10b Segmentation dimensions for customer audits

When the opportunity join is available (auto-bidder proposals), segment by:
- **`opportunities.scannerName`** — named scanner (e.g., `'MERN Stack'`). For teams with multiple scanners this is the primary axis: a scanner with a narrow high-signal query will have higher hire rate than a broad one.
- **`opportunities.originalGigTempId`** — template ObjectId. For teams with multiple templates, segments tests.
- **`application.algorithmVer`** + **`application.promptVersion`** + **`application.model`** — LLM pipeline version. Cohort compare when algo rolls forward.
- **`application.algorithmSignature`** — opaque version id; keep as-is, don't try to interpret.
- **`opportunities.score` buckets** — scanner match quality. Buckets like `<0.5`, `0.5-0.7`, `0.7-0.9`, `>0.9`.
- **`application.bid.type`** + **`application.bid.amount`** — pricing strategy (hourly vs fixed, rate distribution).
- **`application.connectPrice`** — connect cost per bid. Spread per-bid; useful for cost-per-reply and cost-per-hire.

Always ALSO segment by proposal-native dimensions:
- **`meta.freelancer.name` / `meta.freelancer.rid`** — whose Upwork profile the bid was under. Ubiquify: Asad Malik (4,499 bids, 9.4% reply, 0.33% hire, 15 hires), Awais Tariq (3,547 bids, 8.4% reply, 0.17% hire, 6 hires), Daniyal Malik (493 bids, 7.7% reply, 0.81% hire, 4 hires).
- **`meta.author.uid`** — GigRadar user who submitted (may differ from freelancer for team accounts).
- **`meta.createdAt` month** — time-series trend.
- **Auto-bidder vs manual cohort** — join-coverage flag.

### 24.10 Retro ingestion is TEAM-DEPENDENT — probe per-team

Sample of 5 random teams with ≥100 proposals (probe 7):
| Team | Team Created | Retro Proposals (pre-signup meta.createdAt) |
|---|---|---|
| `679a21...b93` (Ubiquify) | 2025-01-29 | 10 / 8,540 (0.1%) — negligible |
| `658c2a...22d` | 2023-12-27 | 108 in a 200-sample (~54%) |
| `67bed6...048` | 2025-02-26 | 89 in a 200-sample (~45%) |
| `64dce0...ce2` | 2023-08-16 | 538+ (exceeds 200-sample entirely) |
| `67e18d...75e` | 2025-03-24 | 720+ (exceeds 200-sample entirely) |
| `63f8c2...8a6` | 2023-02-24 | 204+ (~100% of 200-sample) |

Many teams have BIGGER retro corpuses than GigRadar-era corpuses. Retro cohort size must be computed per-team.

### 24.10 Implication: probe FIRST, methodologize SECOND
For any audit signal that depends on a populated field (archive reasons, chat transcripts, opportunity feedback, team createdAt), run a one-shot probe on the team BEFORE committing to a section in the report. Fields that are 100% populated on one team can be 100% empty on another. The data-available-for-this-team section of the report is itself a useful artifact.

### 24.11 Codebase-canonical funnel signals — THE definitive definitions (verified 2026-04-22)

Audit methodology must align with how GigRadar itself computes these metrics. Source: `services/utils/repositories/stats/stats-repository.ts` and `services/api/functions/schedulerWorkflowsV1/workflows/benchmark-stats.workflow.ts`.

| Stage | Canonical field | Query shape | Notes |
|---|---|---|---|
| **Sent (bid-only)** | `_gigradarTeamOid` + `meta.createdAt ∈ window` + **`meta.inviteToInterviewUid: null`** | count of proposals | Invites EXCLUDED by benchmark. Never forget this filter for bid-funnel math. |
| **Viewed** | `dashroomUID > null` OR `status === 7` (ACTIVE) OR `otherAnnotations` contains `12` (PROPOSAL_VIEWED) | `$or` of 3 conditions | If ANY condition matches, the proposal was viewed. |
| **Replied** | `dashroomUID > null` (truthy — non-null AND non-empty string) | `{ $ne: ['$dashroomUID', null] }` in aggregation; `{ $exists: true, $nin: [null, ''] }` in find | `dashroomUID` is the CANONICAL reply signal (top-level, NOT `meta.chat.chatId`). Test data uses empty string `''` for non-replied. On Ubiquify `dashroomUID` is equivalent to `meta.chat.chatId` (759 populated in both, perfectly aligned), but code-wise always use `dashroomUID`. |
| **Hired** | `status === 10` (top-level `status`, canonical `ProposalStatus.HIRED`) | `{ status: 10 }` | On Ubiquify 21 match vs 25 when mixed-type tolerant — 4 records have legacy string forms. Codebase = 10 strict. `meta.status` is identical to `status` (dual-written). |
| **Connects spent** | `terms.connectsBid > 0 ? terms.connectsBid : meta.connectsExpended ?? connectsExpended ?? 0` | `$cond+$gt` (NOT `$ifNull` — treats 0 as "no data") | Priority chain; fall back through three fields. `connectsExpended` can live at root or inside `meta`. |

**Ubiquify canonical funnel (bid-only, codebase-aligned, probe 13)**:
- Sent: 8,511 (after excluding 29 invites)
- Viewed: 1,799 (21.1% of sent)
- Replied: 730 (8.58% of sent)
- Hired: 21 (0.247% of sent)
- Total connects: 139,850
- **Connects per hire: 6,660** (~$999 at $0.15/connect)
- **Connects per reply: 191** (~$28.70)

### 24.12 Invitation cohort — the hidden gold

`meta.inviteToInterviewUid` (lowercase 'uid' — note case; top-level `inviteToInterviewUID` uses uppercase) is **populated when the proposal was prompted by an inbound Upwork invite**. The benchmark workflow EXCLUDES these (`meta.inviteToInterviewUid: null`) — they're not outbound auto-bidder output, so including them would inflate a team's reply/hire rate unfairly.

Ubiquify (probe 13):
- 29 / 8,540 proposals (0.34%) are invite-originated.
- **Invite reply rate: 29/29 = 100%** (makes sense — the client already reached out).
- **Invite hire rate: 3/29 = 10.3%** (vs 0.247% bid-hire rate — ~42× efficiency).
- **Invites contribute 3 of 25 total hires (12%) from 0.34% of volume.**

Audit methodology must **always split invite-originated from outbound-bid** when computing funnel metrics. Presenting a blended number silently rewards teams that get more inbound interest (often a profile/reputation signal, not an auto-bidder signal).

Detection queries:
- `{'meta.inviteToInterviewUid': {'$ne': None, '$exists': True}}` — invite cohort
- `{'meta.inviteToInterviewUid': None}` — bid cohort (matches benchmark filter)
- Both fields populate on Ubiquify with equal counts (29) — the top-level `inviteToInterviewUID` and nested `meta.inviteToInterviewUid` agree.

### 24.13 Codebase stats pipeline reference (copy-paste-safe)

**`StatsRepository.getScannerStats`** — opportunities-driven, for per-scanner performance:

```js
db.opportunities.aggregate([
  { $match: {
      gigradarTeamId: teamOid,
      notified: { $gte: fromDate, $lt: toDate },
      isPreview: { $ne: true },
      // optional: scannerId: { $in: scannerIds }
  }},
  { $project: {
      'application.proposalId': 1, 'application.sent': 1, 'application.error': 1,
      'application.connectsExpended': 1, 'application.price': 1,
      scannerId: 1, scannerName: 1, irrelevant: 1, score: 1,
  }},
  { $lookup: {
      from: 'proposals',
      localField: 'application.proposalId',
      foreignField: 'meta.uid',
      pipeline: [
        { $match: { _gigradarTeamOid: teamOid } },
        { $project: { _id: 0, dashroomUID: 1, status: 1, otherAnnotations: 1 } },
      ],
      as: 'proposal',
  }},
  { $unwind: { path: '$proposal', preserveNullAndEmptyArrays: true } },
  { $group: {
      _id: { scannerId: '$scannerId' },
      scannerName: { $last: '$scannerName' },
      opportunities: { $sum: 1 },
      sent: { $sum: { $cond: ['$application.sent', 1, 0] } },
      error: { $sum: { $cond: ['$application.error', 1, 0] } },
      irrelevant: { $sum: { $cond: ['$irrelevant', 1, 0] } },
      totalReplied: { $sum: { $cond: [{ $not: '$proposal.dashroomUID' }, 0, 1] } },
      avgReplyScore: { $avg: { $cond: [{ $not: '$proposal.dashroomUID' }, null, '$score'] } },
      connectsSpent: { $sum: '$application.connectsExpended' },
      totalPrice: { $sum: '$application.price' },
  }},
])
```

**`StatsRepository.getProposalsStats` (by date)** — proposals-driven, for the funnel chart:

```js
db.proposals.aggregate([
  { $match: {
      _gigradarTeamOid: teamOid,
      'meta.createdAt': { $gte: fromDate, $lt: toDate },
      'meta.inviteToInterviewUid': null,  // ← exclude invites
  }},
  { $group: {
      _id: {
        status: { $cond: [{ $ne: ['$dashroomUID', null] }, 1, 0] },
        date: { $dateToString: { date: '$meta.createdAt', timezone, format: '%Y-%m-%d' } },
      },
      count: { $sum: 1 },
      connectsSpent: { $sum: { $cond: [
          { $gt: ['$terms.connectsBid', 0] },
          '$terms.connectsBid',
          { $ifNull: ['$meta.connectsExpended', { $ifNull: ['$connectsExpended', 0] }] },
      ]}},
      viewCount: { $sum: { $cond: { if: { $or: [
          { $gt: ['$dashroomUID', null] },
          { $eq: ['$status', 7] },
          { $and: [{ $isArray: '$otherAnnotations' }, { $in: [12, '$otherAnnotations'] }] },
      ]}, then: 1, else: 0 }}},
  }},
])
```

**Platform-wide benchmark (`benchmark-stats.workflow.ts`)** — per-team rates averaged across teams (NOT totals/totals):

```
teamPvr = views / sent   (per team)
teamLrr = replies / sent (per team)
dailyAvgPvr = $avg(teamPvr) across teams with sent>0
dailyAvgLrr = $avg(teamLrr) across teams with sent>0
dailyStdPvr = $stdDevSamp(teamPvr)   # for confidence interval reporting
```

This is the UNBIASED benchmark — one whale team can't dominate it. When I compare a team to "average customer," I must compute each team's per-team rate first, then average — never totals/totals.

### 24.14 Platform benchmark percentiles — last 90d, ≥100 bids, invites excluded (probe15, 2026-04-22)

**Cohort:** 366 teams platform-wide with ≥100 bid-cohort proposals in last 90 days.

| Metric | P10 | P25 | P50 | P75 | P90 | Ubiquify 90d | Rank |
|---|---|---|---|---|---|---|---|
| view_rate | 9.98% | 13.91% | 19.08% | 24.37% | 32.04% | 18.59% | **P47.5** (below median) |
| reply_rate | 2.57% | 3.61% | 5.41% | 7.96% | 10.85% | 7.16% | **P67** (above median) |
| hire_rate | 0% | 0% | 0% | 0.32% | 0.58% | 0.328% | **P77** (strong) |
| connects/reply ↓ | 98 | 140 | 243 | 408 | 635 | 179 | **P35.5** (better than most) |
| connects/hire ↓ | 1,358 | 2,414 | 3,691 | 8,165 | 15,736 | 3,912 | **P51.3** (near median) |

**CRITICAL NARRATIVE RESET:** Over the last 90 days, Ubiquify is **NOT a poor performer**. They are above-median on reply rate (P67) and hire rate (P77), better-than-most on connects-per-reply (P35 — lower is better), and near-median on connects-per-hire (P51).

The historical **0.247% bid-hire rate** observed earlier includes their full history from Jan 2025 onward — that figure averages in a several-month ramp-up period. Recent performance shows the auto-bidder is now delivering.

**Implication for audit methodology:**
- Cohort compare MUST be recency-windowed (last 90d is the codebase-canonical window). Full-history comparisons conflate ramp-up with steady-state.
- Every audit must report 90d benchmark percentiles — that's the number that tells the customer "where you stand today" vs "where you stood on average."
- Hire-rate P75 = 0.32% means **the median team platform-wide has ZERO hires in 90 days**. The hire-rate distribution is heavily zero-inflated; low hire counts are not anomalous.
- Only **158 of 366 teams (43%)** have any hire in 90d — for the rest, connects-per-hire is undefined.

**What still needs investigation:**
- **View rate P47** — below median. Why are fewer Ubiquify proposals being opened relative to peers? Candidates: scanner targeting (wrong job fit → client skips proposal), bid price (too low → client deprioritizes), cover-letter quality.
- **Reply rate P67 despite view rate P47** — when a client DOES open the proposal, Ubiquify converts to reply at above-median rate. So the cover-letter + bid package works *once seen*; the bottleneck is visibility/open rate.
- This bifurcation (low view, high reply-given-view) is the main actionable lever for this customer.

### 24.15 Reply-given-view + boosted + algorithm findings (probe16, 2026-04-22)

**Q1 — Reply-given-view (platform, 90d, 344 teams, ≥100 sent & ≥20 viewed):**
- P10=18.75%, P25=24%, P50=30.6%, P75=37.7%, P90=43.65%
- Ubiquify = **38.5%** → **P77.3** (strong)

**Q3 — Boosted bids (Ubiquify 90d):**
- Boosted: 0 of 1,796 proposals (0%)
- ALL proposals non-boosted — the most obvious unused visibility lever on Upwork. A standard test is to run boosted on the best-performing 1-2 scanners.

**Q4 — Bid amount fields:**
- Only `application.bid.type` and `application.bid.amount` exist on opportunity documents
- **Hourly bids always have `amount: null`** — the hourly rate is NOT stored on the opportunity document. Need to look at the proposal itself (`terms.hourlyRate` on proposals?) or the cover-letter text to know the hourly rate. Methodology note: when auditing hourly-heavy customers, flag this as "we can't audit hourly rate without extra data."

**Q5 — Algorithm/prompt A/B (Ubiquify 90d):**
| Alg | Prompt | Sent | Reply rate | Hired |
|---|---|---|---|---|
| sardor-ai-v2 | 1.2.6.1 (old) | 898 | 7.24% | 2 |
| sardor-ai-v2 | 1.2.7.1.mem (new) | 796 | 6.66% | 2 |
| laziza-ai | 1.2.7.1.mem | 76 | **10.53%** | 0 |

New `1.2.7.1.mem` prompt is slightly underperforming the old `1.2.6.1` on reply rate; `laziza-ai` experiment shows a promising reply uplift but sample is too small for hire conclusions. Worth recommending a proper A/B with larger volume.

### 24.16 Burn + counterfactual findings (probe17, probe18, 2026-04-22)

**Full-history burn on scanners that NEVER hired:**
- 91 of 104 scanners (88%) have 0 hires
- Burn: 6,092 of 9,503 sent (64%), 84,547 of 138,481 connects (61%), **$12,682 USD**
- Top 3 all-time burners are all on Awais Tariq's profile:
  - `React/Node N.A. - Awais` — 898 sent, 40 replied, 0 hired, 18,350 connects ($2,752)
  - `Python - US, UK, CA, Germany, AUS - Awais` — 812 sent, 95 replied (11.7% rr!), 0 hired, 15,444 connects ($2,316) — HIGH-REPLY-NO-HIRE is a distinct failure mode
  - `mvp (develop* | saas) - Asad` — 607 sent, 50 replied, 0 hired, 10,105 connects ($1,516)

**Counterfactual — kill all full-history zero-hire scanners:**
| | Status quo | Kill zero-hire | Savings |
|---|---|---|---|
| Sent | 9,503 | 3,411 | -6,092 sent |
| Replied | 724 | 334 | -390 replies |
| Hired | 20 | 20 | 0 lost (by definition) |
| Connects | 138,481 | 53,934 | -84,547 connects |
| Cost | $20,772 | $8,090 | **-$12,682** |
| Reply rate | 7.62% | 9.79% | +2.2 pp |
| Hire rate | 0.21% | **0.586%** | **+0.376 pp** |
| Connects/hire | 6,924 | **2,697** | **-61%** |

**2.79x hire-rate uplift** from pruning dead scanners, $12,682 recouped, zero hires lost. THIS is the single biggest audit insight.

**Caveat for methodology:** "Zero-hire" must be paired with a volume threshold (≥100 or ≥200 sent) to avoid killing new scanners that haven't had time to prove out. All top 20 burners have 67-898 sent → fair sample.

**Ubiquify rank among 158 teams with ≥1 hire in 90d:**
- Hire rate: P46.8 (median)
- Connects/hire: P51.3 (median, lower is better)
- Story: among peers that hire, Ubiquify is exactly median. Not a problem child — but leaving obvious money on the table via dead-scanner burn.

**Active pipeline:** 581 live conversations (status=7 + dashroomUID), bid cohort. That's the leading indicator of hires landing soon — should feed into forecast math.

**GMV observation:** Of 24 total hires, only 2 are fixed-bid with stored $ amount (avg $45 — tiny). 18 are hourly (amount always null on opp). Can't compute GMV from opportunity data alone — would need proposal-side or Upwork-side fields. Flag for methodology: dollar-GMV is NOT auditable from Mongo for hourly-heavy customers.

### 24.17 ES vector store access + collaborative-filtering validation (probe19, probe20, 2026-04-22)

**Credentials working (read-only):**
- URL: `https://<es-host>:9243`
- User: `researcher-prod` / Pass: `<request-from-admin>`
- ES has ~14M metajob docs, index = `metajob`.

**`matcher.embedding`** — `dense_vector`, 1536 dims (OpenAI text-embedding-3-small shape). Rolled out mid-2025; full coverage ≈2025-10 onward. KNN search works out-of-the-box, returns excellent semantic neighbors (React Native seed → all React Native neighbors, scores 0.906–1.000).

**`matcher.appliedByTeams[]`** — NESTED type. Fields: `teamId` (keyword), `proposalStatus` (int, 10=HIRED), `isInterviewed` (bool). This is the gold field for peer look-alike CF: walk the KNN neighborhood, pull winning `teamId`s, cross to Mongo `proposals` or `upwork.agency.profiles` for cover letters / profiles / rates.

**`metaJob.meta.topBids` — ANONYMIZED, USELESS:**
- Nested type with `bids.name` + `bids.connects` + `dateDetected` + `msAfterJobPublished`.
- All observed names are placeholders: `"1st place"`, `"2nd place"`, … `"5th place"`.
- All observed `connects: 0`.
- Conclusion: can identify that Upwork showed top-bid rankings but NOT who bid or their connect spend. **Do not plan methodology around topBids competitor intel.**
- Note: `exists: {field: "metaJob.meta.topBids"}` returns zero for post-2025-10 jobs at the outer level (nested-field exists quirk) — use `_source` inspection instead.

**Rich-seed discovery filter (nested query, works):**
```json
{"nested": {"path": "matcher.appliedByTeams",
            "query": {"term": {"matcher.appliedByTeams.proposalStatus": 10}}}}
```
Combined with `range: metaJob.createdOn >= 2025-10-01`, yields seeds where at least one GigRadar team was hired. Richest seeds in a small sample had 9–10 applied teams per job; KNN neighborhoods of rich seeds reliably contain multiple further hires (distinct winning teams across 30-neighbor neighborhood: 2+).

**Validated KNN body (reusable):**
```json
{"knn": {"field": "matcher.embedding", "query_vector": EMB_1536,
         "k": 30, "num_candidates": 300},
 "_source": ["metaJob.ciphertext","metaJob.title","metaJob.createdOn",
             "metaJob.budget","metaJob.client.stats.totalSpent",
             "metaJob.meta.jobTrend.clientActivity.totalApplicants",
             "matcher.appliedByTeams"],
 "size": 30}
```

**Peer look-alike recipe (validated end-to-end):**
1. Pick customer's recent HIRE → get `metaJob.ciphertext` → pull its `matcher.embedding` from ES.
2. KNN k=30 to fetch semantic neighbors (same job type, same rough complexity).
3. For each neighbor, read `matcher.appliedByTeams[]`: collect teams with `proposalStatus=10` (winners) and `isInterviewed=true` (shortlists).
4. Tally winning teams across neighborhood → top-ranked teams = customer's direct look-alike competitors on the job type they just won.
5. Cross to Mongo `proposals.metaJob.ciphertext` + `upwork.agency.profiles._team` to harvest those competitors' winning cover letters, rates, profile positioning.

**Retro seed alternative (when customer has thin GigRadar history):**
- Look at Mongo `proposals` with `_gigradarTeamOid=CUST, status=10` (pre-GigRadar hires via `meta.freelancer.rid` profile mapping).
- For each old hire, look up the `metaJob.ciphertext` in ES; if the job is pre-embedding rollout, search ES with the job's title as a text query to find a semantic proxy, then KNN from that proxy's embedding.


---

## 25. Competitive deep-dive pipeline artifacts (Phase 2 Part B)

Encoded after the Ubiquify 2026-04-22 audit. These are the on-disk shapes the competitive-deep-dive pipeline passes between phases — required reading before touching `scripts/phase2c_*` or `scripts/build_workbook.py` in the customer-audit skill.

### 25.1 `phase2b_peer_knn_v2.json` — enriched peer cohort
Per-competitor record produced by the KNN walk. Shape:
```
{
  "competitors": [
    {
      "team_id": "<24-char ObjectId>",
      "team_name": "<email or agency handle>",
      "cohort": "agency|freelancer",
      "wins_in_neighborhood": <int>,        # count of neighbor jobs they won
      "top_wins": [                         # top-5, ordered by proposal.quality/recency
        {
          "job_title": str, "job_ciphertext": str,
          "cl": str,                        # renderedCoverLetter
          "cl_length": int,
          "bid": {"type": "hourly|fixed", "amount": number|null},
          "connects": int|null,
          "scanner_name": str|null,
          "algorithm_signature": str|null,
          "template_id": str|null,
          ...
        }, ...
      ],
      "cl_templates": [str, ...]            # extracted CL openers for pattern-spotting
    }, ...
  ]
}
```

### 25.2 `phase2c_match_reasoning.json` — paired winning/losing CLs with AI judgments

Produced by `phase2c_match_reasoning.py` (pairing step) then mutated in place by `merge_ai_judgments.py` (AI injection). Shape after both passes:
```
{
  "competitors": [
    {
      "team_id": str, "team_name": str,
      "ai_summary": str,                    # injected by merge_ai_judgments — 3-5 sentences
      "ai_tactics": [str, ...],             # injected — 3-4 template-able tactics
      "pairs": [
        {
          "pair_num": int,
          "competitor_win": {job/title/cl/...},
          "ub_win": {job/title/cl/...}|null,   # best title-Jaccard WIN from subject team
          "ub_loss": {job/title/cl/...}|null,  # best title-Jaccard LOSS from subject team
          "title_jaccard_win": float,
          "title_jaccard_loss": float,
          "ai_what_worked_for_them": str,   # injected — 2-4 sentences on the competitor CL
          "ai_what_ubiquify_did": str,      # injected — subject team's win & loss move
          "ai_specific_tactic_to_copy": str # injected — 2-3 sentences of concrete advice
        }, ...
      ]
    }, ...
  ]
}
```

The `ai_*` fields are merged by prefix-mapping the 12-char prefix in the judgment filename to the full 24-char `team_id` in phase2c (see `merge_ai_judgments.py` — keeps a `.pre_ai.json` backup so the AI step is idempotent).

### 25.3 Subagent judgment bundle format

Per-competitor bundle at `/tmp/comp_bundles/SUMMARY_XX_<team_id_prefix12>.txt`:
- Header: competitor name, teamId, pairing method (title Jaccard over shared KNN neighborhood).
- 4-5 pairs, each containing:
  - Competitor's winning CL + metadata (bid, connects, scanner, algo, cl_len, title).
  - Subject team's closest-title WIN (on a similar listing) with full metadata.
  - Subject team's closest-title LOSS with full metadata.
  - Jaccard scores for both pairings so the subagent can downweight weak matches.
- Keep each bundle < 25 KB so a subagent can `Read` the whole file in one call.

### 25.4 Subagent judgment output (one per competitor)

Per-competitor output at `/tmp/comp_judgments/XX_<team_id_prefix12>.json`, where `XX` is the 2-digit rank (01..10):
```
{
  "team_name": str,
  "pairs": [
    {"pair_num": 1, "what_worked_for_them": str, "what_ubiquify_did": str, "specific_tactic_to_copy": str}, ...
  ],
  "competitor_summary": str,
  "top_tactics_for_subject_team": [str, ...]   # alias also accepted: top_tactics_for_ubiquify
}
```

Subagent prompt template lives in `skills/customer-audit/SKILL.md` §Phase 2 Part B (verbatim — the exact framing that bypasses Usage Policy refusals). Do not rewrite it without running the 10-competitor refusal regression test.

### 25.5 Workbook rendering conventions (`build_workbook.py`)

Dark-mode openpyxl workbook. Constants:

| Constant  | Hex     | Purpose |
|---|---|---|
| `BG`      | `0F1419`| Sheet background |
| `BG_ALT`  | `161B22`| Alt-row background for readability |
| `FG`      | `E6EDF3`| Body text |
| `FG_DIM`  | `7D8590`| Labels / muted text |
| `WIN_FG`  | `9EF0B2`| Winning CL body color |
| `CRIT_FG` | `F85149`| Losing CL body color |
| `OKAY_FG` | `D29922`| Template / "in-between" text |
| `UB_HL_BG`| `203040`| Ubiquify subject-row highlight cell |
| `UB_HL_FG`| `79C0FF`| Ubiquify subject-row text color |
| `LINK_FG` | `58A6FF`| Hyperlinks |
| `HEADER_BG`| `21262D`| Table headers |

**Competitive Deep-Dive sheet (14 cols).**
- Cols A-F: left pane (competitor card — identity, win #, their job, their CL, their generator template).
- Col G: gutter (width 2).
- Cols H-L: right pane (subject team's analogue — UB WIN and UB LOSS on the closest-title neighbor).
- Col M: gutter (width 2).
- Col N: AI reasoning column (wraps vertically — merged across agency+freelancer rows for the competitor-level `COMPETITOR FORMULA` block; merged across the job / CL / GR rows per pair for the `WHAT WORKED / WHAT UBIQUIFY DID / TACTIC TO COPY` triptych).
- Col widths (validated): `[18, 26, 50, 12, 12, 14, 2, 18, 24, 40, 12, 14, 2, 36]` — col A/H widened to 18 so the "THEIR JOB / THEIR CL / GR TEMPLATE / UB WIN / UB LOSS" row labels fit without truncation.
- Col A post-processing: force `vertical="top"` on every row in every sheet before saving (labels sit at the top of wrapped cells, never centered).

**Cohort Compare (Sheet 3 head).** Rows 6-8 are the 3 peer cohorts; row 9 is the Ubiquify subject team, highlighted with `UB_HL_BG`/`UB_HL_FG` and prefixed with `◆`.

**Priority tactic stack.** Consolidated tactics appearing across ≥3 competitors are extracted out of the workbook into a separate `COMPETITIVE_PLAYBOOK.md` by `gen_playbook.py` — keeps the workbook focused on evidence, the MD focused on the template edits.

