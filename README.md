# Clippzz

An automated clipping pipeline that runs on a Raspberry Pi 4. It turns long stream VODs into short vertical clips edited the way viral clip channels do it: the streamer's moment, plus memes, sound effects, zooms, text and the occasional AI voiceover, all chosen to fit that moment. The clips are for YouTube Shorts, Instagram Reels and Facebook Reels.

**Status (November 18, 2026):** the moment finder and the rating dashboard are built and running on the Pi. The editor and renderer (the actual clips) come next.

**Full plan:** [Clippzz — Automated Clipping Pipeline Plan](https://claude.ai/artifact/U9JveLdKb8qHtwBfq8DVcC), condensed in [docs/PLAN.md](docs/PLAN.md)

## Decisions so far

| Area | Decision |
| --- | --- |
| Clip engine | Built in this repo and run on the Pi: yt-dlp, ffmpeg and a Claude model. No paid clipping service. |
| Clip format | Meme-style edit fitted to each moment. No fixed template. |
| First sources | IShowSpeed and Stable Ronaldo stream VODs on YouTube, pasted in by hand |
| Accounts | One main account on each platform. Niche accounts later if needed. |
| Order of work | Prove clip quality first, with every clip rated and the good ones posted by hand. Automatic uploads, automatic video discovery and paid clipping campaigns come later. |
| Budget | Under $30 a month; about $5 expected at the start |
| Hosting | Everything runs on the Pi in Docker. The dashboard is reached over Tailscale. |

## Phases

1. **Week 1 (by November 24, 2026):** VOD link in, finished meme-style clip out. You rate each clip. Gate: 7 of 10 clips in a row good enough to post.
2. **Month 1 (by December 17, 2026):** automatic uploads, a watcher for new VODs, stats and a weekly report.
3. **Year 1:** self-tuning edits, a trend radar starting with GTA 6, more channels, TikTok and paid campaigns.

Secrets such as API keys and tokens live in `/home/tallergiraffe/.clippzz.env` on the Pi and are never committed. The edit style is described in [docs/STYLE.md](docs/STYLE.md).

## Using it

Open the dashboard at http://100.95.15.84:8787 (Tailscale) or http://tallergiraffe.local:8787 (home Wi-Fi). Paste a YouTube VOD link. The worker reads the captions, scans the audio for loud moments, has a cheap Claude model propose moments from the whole transcript, then has a stronger Claude model check frames for the best ten and explain each joke. Rate every moment "Funny" or "Not funny"; the ratings are what the picks get tuned against.

## Running it

| Task | Command (on the Pi, in `~/clippzz`) |
| --- | --- |
| Start or update | `docker compose up -d --build` |
| See worker logs | `docker compose logs -f worker` |
| Stop | `docker compose down` |
| First-time Pi setup | `bash scripts/pi-setup.sh` |
| Fan control | `bash scripts/pi-fan.sh <GPIO> [temperature]` |
| Run the tests | `python -m unittest discover tests` |

The Pi checks GitHub every 5 minutes (`scripts/pi-update.sh`, from cron) and restarts itself on a new commit to `main`.

Settings go in `~/.clippzz.env` on the Pi next to the API key: `CLIPPZZ_MONTHLY_BUDGET_USD` (default 5; the worker pauses when the month's spend reaches it) and `CLIPPZZ_MAX_JUDGED` (default 10 moments per VOD get the detailed check).
