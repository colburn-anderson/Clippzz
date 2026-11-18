# Clippzz plan (condensed)

This is the working summary of the approved plan from November 17, 2026. The full version, with tables and diagrams, is the [Clippzz plan doc](https://claude.ai/artifact/U9JveLdKb8qHtwBfq8DVcC). Where the two differ, the newer one wins.

## Goal

Prove that an automated system can make short clips that go viral, then turn that reach into income. Start with IShowSpeed and Stable Ronaldo stream VODs on YouTube. Expand to more creators and trends, GTA 6 first, once clip quality is proven.

**Quality bar (user, November 18, 2026):** every clip must be genuinely funny or entertaining, with an edit that is clearly better than a basic caption-and-crop. A boring moment with a good edit is a fail. A VOD's age does not matter, so older VODs (which have "most replayed" heatmaps) are fair game. When in doubt, the pipeline makes fewer clips, not weaker ones.

## Decisions

| Area | Decision |
| --- | --- |
| Clip engine | Built in this repo and run on the Raspberry Pi 4: yt-dlp, ffmpeg, whisper.cpp, Piper and a Claude model through the Anthropic API. No paid clipping service. Higgsfield's Personal Clipper has no public API, and its pages do not say how it picks moments or which edits it adds, so it stays a manual benchmark: run the same VOD through it by hand and compare. |
| Clip format | Meme-style edit, 30 to 50 seconds: a short story (setup and payoff, two or three segments) with word-by-word captions throughout, plus colour flashes with sound effects, punch-in zooms, whip transitions and small props (emoji, arrows, circles, effort meter) chosen to fit it. A visible beat every 2 to 4 seconds. Some clips get a one-sentence AI voiceover as a bridge between segments. No fixed template. Full detail in [STYLE.md](STYLE.md). |
| Style reference | The user's example Shorts: https://youtube.com/shorts/dz0OGGXHBVU and https://youtube.com/shorts/KKwER8qiAP4 (more to come), analysed in [STYLE.md](STYLE.md) |
| Sources | IShowSpeed VODs from the [@IShowSpeed streams tab](https://www.youtube.com/@IShowSpeed/streams). Stable Ronaldo streams on Twitch; his full streams reach YouTube as uploads on [Stable Ronaldo Live](https://www.youtube.com/channel/UCLldJScHWCtsFlfanYXS8nQ). Links pasted in by hand at first. |
| Accounts | One main account each on YouTube, Instagram and Facebook. Niche accounts later if data supports them. |
| Income order | 1. Reach. 2. Ad revenue where eligible. 3. Paid clipping campaigns (Whop Content Rewards and similar) only after the account has traction. |
| Budget | Under $30 a month; about $5 expected at the start |
| Where things run | The pipeline and dashboard run on the Pi in Docker, so they work when the MacBook is off. The dashboard is reached over Tailscale. Development happens from a local Claude Code session on the MacBook, which reaches the Pi over SSH. |
| Git | Push straight to `main`. The Pi pulls `main` and restarts itself. |
| Secrets | In `/home/tallergiraffe/.clippzz.env` on the Pi (owner-only permissions), outside the repo checkout so a `git pull` never touches it. Never committed. |
| The Pi | Raspberry Pi 4 Model B, 4 GB RAM, 64-bit Debian 13 (trixie), 32 GB SD card. Shared with the user's robot projects, so the containers get CPU and memory limits. |

## Build status (November 18, 2026)

| Piece | Status |
| --- | --- |
| Intake, analyzer, ranker, review dashboard | Built and running on the Pi (dashboard on port 8787) |
| Edit planner, renderer, prop and meme library | Next |
| Scheduler, publishers, stats, alerts | Month 1 |

How it differs from the outline below: the ranker's first pass ("scout", Claude Haiku 5.5) reads the entire transcript in ten-minute stretches, which costs about a cent per VOD, so there is no separate signal-based shortlist. Loudness and the heatmap are passed to it as hints. The second pass ("judge", Claude Opus 5.5) sees eight frames and the timed transcript for the top ten moments.

Storage care until the USB SSD arrives: scratch files (frames, section downloads) live in RAM, the audio scan is streamed and never saved, code is mounted read-only into the containers, container logs are capped at 4 MB, and the only regular writes are the SQLite database and a compressed transcript per VOD.

## Pipeline components

1. **Intake:** paste a VOD link in the dashboard. A watcher for new VODs comes in month 1.
2. **Analyzer:** finds candidate moments without downloading the whole video. Main signals: YouTube auto-captions (always present, with per-word timing and tags such as `[screaming]` and `[laughter]`) and loudness peaks from an audio-only download. Bonus signals when present: the "most replayed" heatmap, live chat replay, and which moments other clip channels already turned into popular Shorts (a sign the moment is funny; our edit must still be our own). See "Source signals" below for why the first two are often missing.
3. **Ranker:** funny first, in two passes. A cheaper Claude model reads every transcript window and keeps only those with a real joke, reaction, fail or story beat. A stronger Claude model then looks at frames plus transcript for the survivors, scores each from 0 to 100 on "would someone send this to a friend", explains the joke in one line, sets start and end, groups setups with payoffs, and flags unsafe content. Anything it cannot explain as funny or entertaining is dropped, even if the signals were loud.
4. **Edit planner:** a Claude model with vision looks at frames and the transcript of the chosen segments and writes an edit plan following [STYLE.md](STYLE.md): caption styling and keyword colours, stage-direction captions, flashes, sound effects, zooms, props, transitions, up to two brief meme cutaways (0.5 to 2 seconds, then back to the clip), and whether a bridge voiceover fits. Props and sounds come from a library tagged by mood (shock, laughter, disbelief, hype, fail, wholesome).
5. **Renderer:** downloads only the chosen segments, crops to fill 1080×1920 on the people (face-tracked; no blurred bars), and applies the edit plan with ffmpeg. A facecam-plus-gameplay layout comes later for gaming streams.
6. **Review:** the user watches each clip in the dashboard, rates it and leaves a note.
7. Month 1 and later: scheduler, publishers (YouTube Data API, Instagram Graph API, Facebook Pages API), stats collector, Telegram alerts.

### Source signals (checked November 17, 2026)

| Signal | IShowSpeed VODs | Stable Ronaldo uploads | Notes |
| --- | --- | --- | --- |
| Auto-captions | Yes, with per-word timing | Yes | Present on every VOD checked |
| "Most replayed" heatmap | Only on older VODs: none on 4 VODs up to 3 weeks old (2 to 4.5 million views each); present on one from August | Yes, on uploads 3 to 4 months old | Always 100 buckets, so a 3-hour VOD gets one value per 1.8 minutes and a 6-hour VOD one per 4 minutes. Good for picking a region, not an exact moment. |
| YouTube chat replay | No, on all 5 checked | No (uploads, not live streams) | yt-dlp does read chat replay from another streamer's VOD, so this is the channels, not the tool. Stable Ronaldo's Twitch chat is a possible later source. |

Data: one SQLite database plus files on the Pi under `/data` (sources, work, clips, logs, backups). Every job is a database row with a state, so a power cut loses nothing. Each step retries 3 times before alerting.

## Phases and gates

| Phase | Deliverable | Gate to move on |
| --- | --- | --- |
| Week 1 (by Nov 24, 2026) | VOD link in, up to 3 finished meme-style clips out. The user rates every clip and posts the good ones by hand. | The user rates 7 of 10 clips in a row good enough to post |
| Month 1 (by Dec 17, 2026) | Automatic uploads to all three platforms, VOD watcher, score threshold, stats and weekly report | 7 days unattended; median 500 views per clip at 72 hours |
| Year 1 (by Nov 2027) | Self-tuning edits, trend radar, niche accounts, TikTok, paid campaigns | 5 million views a month; several clips over 1 million |

## Setup

Needed now:
- A local Claude Code session on the MacBook with key-based SSH to the Pi at 100.95.15.84 (Tailscale), user `tallergiraffe`. Done and working.
- The user's existing Anthropic API key, with a $5 monthly spend limit, stored as `ANTHROPIC_API_KEY=...` in `/home/tallergiraffe/.clippzz.env` on the Pi
- Docker Engine and the Compose plugin on the Pi, plus the pending OS updates (444 on November 17): run `bash ~/clippzz/scripts/pi-setup.sh` on the Pi, which needs the user's sudo password
- Fan: `bash ~/clippzz/scripts/pi-fan.sh <GPIO> [temperature]`. Physical pin 8 is GPIO 14, which the robot arm server already drives (stepper direction pins are GPIO 14, 15 and 27), so the fan's control wire needs a free pin such as GPIO 18 (physical pin 12).
- If Tailscale on the Mac stops passing traffic ("MagicSock function not running"), quit and reopen the Tailscale app; reboot the Mac if that does not fix it. SSH over the home network (`ssh tallergiraffe@TallerGiraffe.local`) works as a fallback.
- A USB SSD (256 GB or more) on a blue USB 3 port, mounted at `/data`. Moved up from "later": the SD card has 15 GB free and constant video writes wear SD cards out.

Added only when its step is reached: ordinary YouTube, Instagram and Facebook accounts (for posting by hand, after the first good clip), Google Cloud project with the YouTube Data API, Instagram professional account plus Facebook Page plus Meta developer app, Telegram bot (optional), Twitch developer app, Whop account, AdSense, TikTok developer app.

Not needed: Cloudflare, GitHub Pages, any paid clipping service.

## Rules

- Credit the creator in every clip's title or caption and point to their channel.
- Remove a clip the same day on request. Pause a creator after any copyright claim.
- No commercial music in the meme and sound library.
- No rotating proxies, fake accounts or ban evasion. If something gets blocked, slow down and fix the cause.
- Flag for manual review: minors, nudity, slurs, real injuries, gambling, private information, drama about named people, leaked game footage, misleading titles.
- No false claims or invented quotes in hooks.
