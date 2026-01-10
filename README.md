# Slack Broadcast Bot

A Slack bot that broadcasts a message to every Slack channel it has been added to.

Built for Vercel serverless and Vercel KV (Upstash Redis).

## What it does

- Tracks channel membership automatically:
  - Stores a channel ID when the bot joins a channel
  - Removes the channel ID when the bot leaves a channel
- Broadcasts messages via `/partner_broadcast`
- Includes guardrails:
  - Preview → confirm flow
  - Optional allowlist of broadcasters
  - Rate limit handling and throttling
  - Safety cap on number of channels per broadcast

## Architecture

- `api/slack.py` — Slash command handler (`/partner_broadcast`)
- `api/events.py` — Slack Events API handler (join/leave tracking)
- `api/_redis.py` — Vercel KV helper (REST-based Upstash client)

## Environment Variables

Required:

- `SLACK_BOT_TOKEN`
- `SLACK_SIGNING_SECRET`
- `SLACK_BOT_USER_ID`
- `STORAGE_KV_REST_API_URL`
- `STORAGE_KV_REST_API_TOKEN`

Optional:

- `ALLOWED_BROADCASTERS` (comma-separated user IDs)
- `MAX_BROADCAST_CHANNELS` (default: 500)
- `POST_THROTTLE_SECONDS` (default: 0.2)
- `BROADCAST_COOLDOWN_SECONDS` (default: 0)

## Deploy

1. Push to GitHub
2. Create a Vercel project from the repo
3. Add env vars in Vercel Project Settings
4. Deploy

## Slack App Configuration

- Slash Command `/partner_broadcast` → `https://<vercel-domain>/api/slack`
- Event Subscriptions Request URL → `https://<vercel-domain>/api/events`
- Bot events subscribed:
  - `member_joined_channel`
  - `member_left_channel`