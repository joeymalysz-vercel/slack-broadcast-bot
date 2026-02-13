# Slack Alert Bot

A Slack-native broadcast tool for sending important updates to all partner Slack channels the bot is added to — safely, reliably, and at scale.

Built for **Vercel**, designed for **Slack Connect**, and optimized to avoid Slack API rate limits.

---

## What this does

Slack Alert Bot allows authorized users to:

- Draft a message once
- Review exactly how it will appear to partners
- Send it to **every partner channel** the bot is a member of

The bot automatically tracks which channels it belongs to and **only sends messages to active channels**, ensuring accuracy and preventing delivery errors.

---

## Key features

### Slack-native workflow
- `/partner_broadcast` opens a **Draft modal**
- Submit → **Review modal** with real preview
- Click **Send** → broadcast starts immediately
- Sender receives a **DM summary** when delivery completes

No “CONFIRM:” commands, no brittle text flows.

---

### Automatic channel tracking
- Uses Slack **Event Subscriptions**
- When the bot is added to a channel → channel ID is stored
- When removed → channel is automatically removed
- No polling, no manual lists, no drift

---

### Safe by default
- Preview before send
- Optional allowlist of approved broadcasters
- Optional per-user cooldown
- Hard cap on number of channels per broadcast
- Rate-limit aware delivery (`Retry-After` respected)

---

### Optimized for scale (and cost)
- No `conversations.list`
- No cron jobs
- No background workers running idle
- No unnecessary Slack API calls
- Uses **Vercel KV (Upstash Redis)** for lightweight state

Broadcast work is triggered **on demand only**.

---

## Architecture overview

Slack
├─ Slash Command → /api/slack
├─ Modal Interactions → /api/interactions
├─ Channel Join/Leave Events → /api/events
└─ Message Delivery → /api/worker (on-demand)

Vercel
├─ Serverless Functions
├─ Vercel KV (Redis)
└─ No always-on processes

---

## Tech stack

- **Slack APIs**
  - Slash Commands
  - Block Kit (modals + messages)
  - Event Subscriptions
- **Vercel**
  - Serverless Functions
  - Vercel KV (Upstash Redis)
- **Python**
  - `slack_sdk`
  - `upstash-redis`

---

## Required environment variables

```env
SLACK_BOT_TOKEN=...
SLACK_SIGNING_SECRET=...
SLACK_BOT_USER_ID=U...

KV_REST_API_URL=...
KV_REST_API_TOKEN=...

PUBLIC_BASE_URL=https://<your-vercel-domain>
WORKER_SECRET=<random-string>

MAX_BROADCAST_CHANNELS=500
POST_THROTTLE_SECONDS=0.2
BROADCAST_COOLDOWN_SECONDS=0
```

## Managing Allowed Broadcasters

User authorization is now managed via Redis instead of environment variables. This allows for dynamic management of who can use the broadcast functionality.

### Adding allowed broadcasters

To add users who can use the broadcast functionality, use Redis SET commands:

```bash
# Add a single user
SADD partner_alert_bot:allowed_broadcasters U1234567890

# Add multiple users at once
SADD partner_alert_bot:allowed_broadcasters U1234567890 U0987654321 U1122334455
```

### Removing allowed broadcasters

```bash
# Remove a single user
SREM partner_alert_bot:allowed_broadcasters U1234567890
```

### Viewing current allowed broadcasters

```bash
# List all allowed broadcasters
SMEMBERS partner_alert_bot:allowed_broadcasters
```

### Default behavior

- If no users are configured in Redis (`partner_alert_bot:allowed_broadcasters` key is empty or doesn't exist), **all users** are allowed to broadcast (for backward compatibility)
- If Redis is unavailable, the system fails open and allows all users to broadcast (for availability)
- User IDs should be Slack user IDs (format: `U1234567890`)
