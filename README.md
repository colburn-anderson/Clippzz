# Clippzz

An automated clipping pipeline that runs on a Raspberry Pi 4. It turns long stream VODs into short vertical clips edited the way viral clip channels do it: the streamer's moment, plus memes, sound effects, zooms, text and the occasional AI voiceover, all chosen to fit that moment. The clips are for YouTube Shorts, Instagram Reels and Facebook Reels.

**Status (November 18, 2026):** the moment finder, the clip editor and the rating dashboard run on the Pi. Clips follow the ClippingMania format described in [docs/STYLE.md](docs/STYLE.md).

**Full plan:** [Clippzz — Automated Clipping Pipeline Plan](https://claude.ai/artifact/U9JveLdKb8qHtwBfq8DVcC), condensed in [docs/PLAN.md](docs/PLAN.md)

## Decisions so far

| Area | Decision |
| --- | --- |
| Clip engine | Built in this repo and run on the Pi: yt-dlp, ffmpeg and a Claude model. No paid clipping service. |
| Clip format | The ClippingMania format: one bit, 12 to 25 seconds, chopped hard, stage-direction captions in meme slang, one-word speech captions, colour hits and sound stings |
| First sources | The creators' highlight uploads (IShowSpeed, Kai Cenat), then stream VODs, pasted in by hand |
| Accounts | Chat Clip That, @ChatCl1pThat, on YouTube, Instagram and Facebook ([brand guide](https://claude.ai/artifact/XQr4RkWvcuuUWgrU4hptq1)) |
| Order of work | Prove clip quality first, with every clip rated and the good ones posted by hand. Automatic uploads, automatic video discovery and paid clipping campaigns come later. |
| Budget | Under $30 a month; about $5 expected at the start |
| Hosting | Everything runs on the Pi in Docker. The dashboard is reached over Tailscale. |

## Phases

1. **Week 1 (by November 24, 2026):** VOD link in, finished meme-style clip out. You rate each clip. Gate: 7 of 10 clips in a row good enough to post.
2. **Month 1 (by December 17, 2026):** automatic uploads, a watcher for new VODs, stats and a weekly report.
3. **Year 1:** self-tuning edits, a trend radar starting with GTA 6, more channels, TikTok and paid campaigns.

Secrets such as API keys and tokens live in `/home/tallergiraffe/.clippzz.env` on the Pi and are never committed.

Storage: Clippzz's data (`/data`) and Docker's storage live on a 250 GB partition of the USB SSD, so the SD card holds only the system. The rest of the SSD is mounted at `/srv/storage` and shared to the Mac as a network drive named SSD (Finder: Go > Connect to Server > `smb://tallergiraffe.local/SSD`, or `smb://100.95.15.84/SSD` over Tailscale), which also works as a Time Machine disk. The edit style is described in [docs/STYLE.md](docs/STYLE.md).

## Using it

Open the dashboard at http://100.95.15.84:8787 (Tailscale) or http://tallergiraffe.local:8787 (home Wi-Fi). Paste a YouTube VOD link. The worker reads the captions, scans the audio for loud moments, has a cheap Claude model propose moments from the whole transcript, then has a stronger Claude model check frames for the best ten and explain each joke. Rate every moment "Funny" or "Not funny"; the ratings are what the picks get tuned against.

Press **Make a clip** on a moment to turn it into a finished Short. The worker downloads just that part of the stream in HD (into RAM, not the SD card), writes captions from the audio with faster-whisper, and has Claude watch the moment twice a second and plan the edit: which beats to keep, the stage directions, colour hits, sound stings and framing. ffmpeg then renders a 1080×1920 MP4, which plays on the moment's card. Rate each clip "Post it" or "Not good enough" and say what to change. A clip takes about 5 to 10 minutes and costs roughly 50 cents.

## Running it

| Task | Command (on the Pi, in `~/clippzz`) |
| --- | --- |
| Start or update | `docker compose up -d --build` |
| See worker logs | `docker compose logs -f worker` |
| Stop | `docker compose down` |
| First-time Pi setup | `bash scripts/pi-setup.sh` |
| Set up the USB SSD (erases it) | `sudo bash scripts/pi-ssd.sh /dev/sda` |
| Free the SD card after the SSD move | `sudo bash scripts/pi-ssd.sh --cleanup` |
| Fan control | `bash scripts/pi-fan.sh <GPIO> [temperature]` |
| Queue a clip by hand | `docker compose exec worker python -m clippzz.clipper --moment <id>` |
| Render a clip again from its saved plan (free) | `docker compose exec worker python -m clippzz.clipper --clip <id> --rerender` |
| Run the tests | `docker compose exec worker python -m unittest discover tests` |

`scripts/pi-update.sh` pulls a new commit on `main` and restarts the containers. To make the Pi do that by itself every 5 minutes, add it to cron (not set up yet):
`(crontab -l 2>/dev/null; echo '*/5 * * * * $HOME/clippzz/scripts/pi-update.sh >> /tmp/clippzz-update.log 2>&1') | crontab -`

Settings go in `~/.clippzz.env` on the Pi next to the API key: `CLIPPZZ_MONTHLY_BUDGET_USD` (set to 30 on November 18, 2026; the worker pauses when the month's spend reaches it), `CLIPPZZ_MAX_JUDGED` (default 10 moments per VOD get the detailed check), `CLIPPZZ_PLANNER_EFFORT` (default `medium`, about 40 cents a clip; `high` made the clip the owner called perfect, for 58 cents) and `CLIPPZZ_WHISPER_MODEL` (default `small.en`). After editing that file, run `docker compose up -d --force-recreate worker`; a plain restart keeps the old values.
