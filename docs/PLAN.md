# Clippzz plan (condensed)

This is the working summary of the approved plan from November 17, 2026. The full version, with tables and diagrams, is the [Clippzz plan doc](https://claude.ai/artifact/U9JveLdKb8qHtwBfq8DVcC). Where the two differ, the newer one wins.

## Goal

Prove that an automated system can make short clips that go viral, then turn that reach into income. Start with IShowSpeed and Stable Ronaldo stream VODs on YouTube. Expand to more creators and trends, GTA 6 first, once clip quality is proven.

## Decisions

| Area | Decision |
| --- | --- |
| Clip engine | Built in this repo and run on the Raspberry Pi 4: yt-dlp, ffmpeg, whisper.cpp, Piper and a Claude model through the Anthropic API. No paid clipping service; Higgsfield has no API. |
| Clip format | Meme-style edit: the streamer's moment, plus memes, sound effects, zooms and short text chosen to fit it. Some clips get a one-line AI voiceover intro. No fixed template. Typically 2 to 5 edit beats per clip, not constant noise. |
| Style reference | The user's example Shorts: https://youtube.com/shorts/dz0OGGXHBVU and https://youtube.com/shorts/KKwER8qiAP4 (more to come) |
| Sources | IShowSpeed and Stable Ronaldo stream VODs on YouTube, links pasted in by hand at first |
| Accounts | One main account each on YouTube, Instagram and Facebook. Niche accounts later if data supports them. |
| Income order | 1. Reach. 2. Ad revenue where eligible. 3. Paid clipping campaigns (Whop Content Rewards and similar) only after the account has traction. |
| Budget | Under $30 a month; about $5 expected at the start |
| Where things run | The pipeline and dashboard run on the Pi in Docker, so they work when the MacBook is off. The dashboard is reached over Tailscale. Development happens from a local Claude Code session on the MacBook, which reaches the Pi over SSH. |
| Git | Push straight to `main`. The Pi pulls `main` and restarts itself. |
| Secrets | In a `.env` file on the Pi. Never committed. |

## Pipeline components

1. **Intake:** paste a VOD link in the dashboard. A watcher for new VODs comes in month 1.
2. **Analyzer:** finds candidate moments without downloading the whole video: YouTube captions, the "most replayed" heatmap, live chat replay and audio peaks.
3. **Ranker:** a Claude model scores each candidate from 0 to 100, sets start and end, and flags unsafe content.
4. **Edit planner:** a Claude model with vision looks at frames and the transcript of the chosen moment and writes an edit plan: which memes, sound effects, zooms and text go where, and whether a voiceover fits. Memes and sounds come from a library tagged by mood (shock, laughter, disbelief, hype, fail).
5. **Renderer:** downloads only the chosen segment, reframes to 9:16 (blurred background first; facecam and gameplay split later) and applies the edit plan with ffmpeg.
6. **Review:** the user watches each clip in the dashboard, rates it and leaves a note.
7. Month 1 and later: scheduler, publishers (YouTube Data API, Instagram Graph API, Facebook Pages API), stats collector, Telegram alerts.

Data: one SQLite database plus files on the Pi under `/data` (sources, work, clips, logs, backups). Every job is a database row with a state, so a power cut loses nothing. Each step retries 3 times before alerting.

## Phases and gates

| Phase | Deliverable | Gate to move on |
| --- | --- | --- |
| Week 1 (by Nov 24, 2026) | VOD link in, up to 3 finished meme-style clips out. The user rates every clip and posts the good ones by hand. | The user rates 7 of 10 clips in a row good enough to post |
| Month 1 (by Dec 17, 2026) | Automatic uploads to all three platforms, VOD watcher, score threshold, stats and weekly report | 7 days unattended; median 500 views per clip at 72 hours |
| Year 1 (by Nov 2027) | Self-tuning edits, trend radar, niche accounts, TikTok, paid campaigns | 5 million views a month; several clips over 1 million |

## Setup

Needed now:
- A local Claude Code session on the MacBook with key-based SSH to the Pi at 100.95.15.84 (Tailscale)
- The user's existing Anthropic API key, with a $5 monthly spend limit, stored in `.env` on the Pi

Added only when its step is reached: ordinary YouTube, Instagram and Facebook accounts (for posting by hand, after the first good clip), Google Cloud project with the YouTube Data API, Instagram professional account plus Facebook Page plus Meta developer app, Telegram bot (optional), USB SSD, Twitch developer app, Whop account, AdSense, TikTok developer app.

Not needed: Cloudflare, GitHub Pages, any paid clipping service.

## Rules

- Credit the creator in every clip's title or caption and point to their channel.
- Remove a clip the same day on request. Pause a creator after any copyright claim.
- No commercial music in the meme and sound library.
- No rotating proxies, fake accounts or ban evasion. If something gets blocked, slow down and fix the cause.
- Flag for manual review: minors, nudity, slurs, real injuries, gambling, private information, drama about named people, leaked game footage, misleading titles.
- No false claims or invented quotes in hooks.
