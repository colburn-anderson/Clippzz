# Clippzz plan (condensed)

This is the working summary of the approved plan from November 17, 2026. The full version, with tables and diagrams, is the [Clippzz plan doc](https://claude.ai/artifact/U9JveLdKb8qHtwBfq8DVcC). Where the two differ, the newer one wins.

## Goal

Prove that an automated system can make short clips that go viral, then turn that reach into income. Start with IShowSpeed and Stable Ronaldo stream VODs on YouTube. Expand to more creators and trends, GTA 6 first, once clip quality is proven.

**Quality bar (user, November 18, 2026):** every clip must be genuinely funny or entertaining, with an edit that is clearly better than a basic caption-and-crop. A boring moment with a good edit is a fail. A VOD's age does not matter, so older VODs (which have "most replayed" heatmaps) are fair game. When in doubt, the pipeline makes fewer clips, not weaker ones.

## Decisions

| Area | Decision |
| --- | --- |
| Clip engine | Built in this repo and run on the Raspberry Pi 4: yt-dlp, ffmpeg, whisper.cpp and a Claude model through the Anthropic API. Higgsfield's Personal Clipper has no public API, and its pages do not say how it picks moments or which edits it adds, so it stays a manual benchmark. Ready-made AI editors were checked on November 18, 2026 (see "Existing editors" below). None does the part that makes the target format work, so the editor was built here, and the first test clips were made the same day. The user's ratings decide whether it stays. |
| Clip format | The ClippingMania format (user, November 18, 2026): one bit that works with the sound off, 12 to 25 seconds, cut every 1.5 to 2.5 seconds with every pause removed. Stage-direction captions in meme slang tell the story (`*SHAVES HEAD*` → `*REGRET*` → `*CRIES*`). Speech captions run one or two words at a time, plus colour hits, punch-in zooms and short sound stings. Full detail in [STYLE.md](STYLE.md). The longer stitched-story format from the first two examples may come back later as a second format. |
| Style reference | Main target: the [ClippingMania](https://www.youtube.com/channel/UCQkh3PuF3CRHlV8mHBKreCA) channel, with https://youtube.com/shorts/eUFkorW-1kk as the user's example. Earlier examples: https://youtube.com/shorts/dz0OGGXHBVU and https://youtube.com/shorts/KKwER8qiAP4. All are analysed in [STYLE.md](STYLE.md). |
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
| Intake, analyzer, ranker, review dashboard | Built and running on the Pi (dashboard on port 8787). First test: a 12-hour Speed and Kai Cenat Minecraft VOD took 20 minutes and cost $0.47, and gave 8 "maybe" moments. The user says they all have potential but need a much better edit. |
| Edit planner, renderer, sound library | Built November 18, 2026. Claude Opus 5.5 plans each clip from frames twice a second, word timings from faster-whisper (small.en, on the Pi) and YouTube's captions. ffmpeg renders it at 1080×1920. First test, "Speed steals Kai's plate": two versions of 20 to 22 seconds, each about 5 minutes and $0.33 to $0.35 to make. After the owner's first review, the same day: captions cross-checked against YouTube's, 75 s of footage after each moment so clips show the ending, the spoken setup kept, the owner's meme sound library and music with ducking, emoji stickers, freeze-frame endings, and a stacked two-camera layout. The owner's notes feed every new plan. |
| Scheduler, publishers, stats, alerts | Month 1 |

How it differs from the outline below: the ranker's first pass ("scout", Claude Haiku 5.5) reads the entire transcript in ten-minute stretches, which costs about a cent per VOD, so there is no separate signal-based shortlist. Loudness and the heatmap are passed to it as hints. The second pass ("judge", Claude Opus 5.5) sees eight frames and the timed transcript for the top ten moments.

### Existing editors (checked November 18, 2026)

| Tool | What it does automatically | API | Price | Fit for the ClippingMania format |
| --- | --- | --- | --- | --- |
| [Submagic](https://docs.submagic.co/api-reference/create-project) | Word-by-word caption templates, auto-zoom, silence removal down to 0.1–0.2 s gaps, bad-take removal, stock or AI B-roll, music, hook title, your own media inserted at set times | Yes, on the Business + API plan (100 API minutes a month) | About $69 a month yearly, $120 monthly for the API plan | Captions and pause-cutting only. No sound effects, colour hits or stage directions through the API, and it can't pick beats from the picture. Over budget. |
| [OpusClip](https://help.opus.pro/api-reference/overview) | Finds clips in long videos, captions, brand templates, posting | Yes, on Pro (beta) and up | Pro plan | Built for 30 to 90 second talking clips. No stage directions or meme sounds found. |
| [Eklipse AI Edit](https://eklipse.gg/features/ai-edit) | Adds memes, sound effects, captions and zooms to gaming clips; uploads up to 5 minutes | No | About $12.50 to $25 a month | Library built for gaming clips. Mixed reviews. Manual uploads only. |
| [Vidova](https://vidova.ai/) | Agent editor: you say what's funny, it places sounds, captions and cuts | No public API (MCP connector only) | From $19 a month | Unproven for this format. Manual. |

What makes the target format work is choosing which beats to keep from the picture and writing a funny stage direction for each. No tool found does either; both are vision-and-humour judgement, the kind of task Claude is used for in this pipeline. The rest of the format (tight cuts, one-word captions, colour tints, zooms, a sound sting) is plain ffmpeg work.

### Money reference

ClippingMania made 126.4 million Shorts views in 15 months. At the $0.01 to $0.07 per 1,000 Shorts views most 2026 guides report, that is roughly $1,300 to $8,800 if it was monetized the whole time (unverified; guides vary). 79 percent of its views came from 18 Shorts and the median Short got 33,000 views, so income depends on a few hits. YouTube's reused-content rules can refuse monetization for clips of other creators without real creative input. The stage-direction edit is that input; a bare caption-and-crop would not be.

Storage care until the USB SSD arrives: scratch files (frames, section downloads) live in RAM, the audio scan is streamed and never saved, code is mounted read-only into the containers, container logs are capped at 4 MB, and the only regular writes are the SQLite database and a compressed transcript per VOD.

## Pipeline components

1. **Intake:** paste a VOD link in the dashboard. A watcher for new VODs comes in month 1.
2. **Analyzer:** finds candidate moments without downloading the whole video. Main signals: YouTube auto-captions (always present, with per-word timing and tags such as `[screaming]` and `[laughter]`) and loudness peaks from an audio-only download. Bonus signals when present: the "most replayed" heatmap, live chat replay, and which moments other clip channels already turned into popular Shorts (a sign the moment is funny; our edit must still be our own). See "Source signals" below for why the first two are often missing.
3. **Ranker:** funny first, in two passes. A cheaper Claude model reads every transcript window and keeps only those with a real joke, reaction, fail or story beat. A stronger Claude model then looks at frames plus transcript for the survivors, scores each from 0 to 100 on "would someone send this to a friend", explains the joke in one line, sets start and end, groups setups with payoffs, and flags unsafe content. Anything it cannot explain as funny or entertaining is dropped, even if the signals were loud. For the ClippingMania format it should favour one visual bit that works with the sound off, on IRL camera footage, over talk-heavy moments or small facecams. The current prompts don't do this yet.
4. **Edit planner:** a Claude model with vision looks at the moment at 2 frames a second, with word timings from whisper, and writes an edit plan following [STYLE.md](STYLE.md). The plan sets the keep-spans (the shortest run of beats that still tells the bit, 12 to 25 seconds in total), a stage-direction caption for each beat without speech, speech caption colours, colour hits, black-and-white "loss" beats, punch-ins, the crop centre for each span, and sound stings. A rare meme cutaway is allowed. Sounds come from a small library tagged by mood (shock, laughter, disbelief, hype, fail, loss).
5. **Renderer:** downloads only the chosen section, cuts it to the keep-spans, crops to fill 1080×1920 on the people (no blurred bars), and applies the plan with ffmpeg and ASS captions. Gaming streams with a small facecam are a poor fit for this format; skip them or add a facecam layout later.
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
- USB SSD: a 2 TB SanDisk Extreme, plugged in November 18, 2026. `sudo bash scripts/pi-ssd.sh /dev/sda` erases it and makes 250 GB for `/data` (Clippzz data plus Docker's storage, moved off the SD card) and shares the rest with the Mac as a network drive named SSD (Finder and Time Machine). The user's USB-C to USB-A cable runs at USB 2 speed, which is fine for Clippzz. The user may want to unplug the drive and use it directly on the Mac in a couple of years; that will need the shared partition reformatted.
- Docker memory limits are ignored on this Pi: the kernel's memory cgroup is off (`cgroup_enable=memory` is not in `cmdline.txt`). CPU limits work. Optional fix: add it to `/boot/firmware/cmdline.txt` and reboot.

Added only when its step is reached: ordinary YouTube, Instagram and Facebook accounts (for posting by hand, after the first good clip), Google Cloud project with the YouTube Data API, Instagram professional account plus Facebook Page plus Meta developer app, Telegram bot (optional), Twitch developer app, Whop account, AdSense, TikTok developer app.

Not needed: Cloudflare, GitHub Pages, any paid clipping service.

## Rules

- Credit the creator in every clip's title or caption and point to their channel.
- Remove a clip the same day on request. Pause a creator after any copyright claim.
- No commercial music in the meme and sound library.
- No rotating proxies, fake accounts or ban evasion. If something gets blocked, slow down and fix the cause.
- Flag for manual review: minors, nudity, slurs, real injuries, gambling, private information, drama about named people, leaked game footage, misleading titles.
- No false claims or invented quotes in hooks.
