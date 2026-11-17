# Clippzz

An automated clipping pipeline that runs on a Raspberry Pi 4. It turns long stream VODs into short vertical clips edited the way viral clip channels do it: the streamer's moment, plus memes, sound effects, zooms, text and the occasional AI voiceover, all chosen to fit that moment. The clips are for YouTube Shorts, Instagram Reels and Facebook Reels.

**Status:** planning approved on November 17, 2026. Nothing is built yet.

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

Secrets such as API keys and tokens live in a `.env` file on the Pi and are never committed.
