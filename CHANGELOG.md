# Changelog

## v0.1.0-alpha - 2025-12-10
### Added
- Default Command '!Bober' 
- Feast Command '!Bober FEAST'
- Art Command '!Bober ART'
- Time Command '!Bober TIME'

## v0.2.0-alpha - 2025-12-10
### Reworked
- Command prefix changed from '!' to '/'

## v0.2.0-beta - 2025-12-12
### Reworked
- Command prefix removed and replaced for / commands
- Bot now based on discord.ext commands and not client messages
- !Bober TIME command replaced with bober-time (using / commands)
### Removed
- Removed !Bober command
- Removed !Bober FEAST command
- Removed !Bober ART command
- Removed bober_art.txt file

## v1.0.0-beta - 2026-10-15
### Added
- bober-track: live-tracks one of today's Premier League / La Liga / Ligue 1 / Bundesliga / World Cup matches via API-Football (autocomplete, optional yellow card / substitution / role ping options)
- bober-untrack: stop tracking (confirmation button once the match has kicked off)
- bober-status: tracked match, score and remaining API requests
- Live posts for goals, missed penalties, red cards, VAR decisions, kickoff, half-time, second half and full time
- bober_lines.json: random Bober lines per event type
- SQLite persistence so tracking survives restarts without reposting events
- Replay mode (BOBER_REPLAY) and pytest tests
- Events the API corrects (minute, team, goal type) edit the original post instead of reposting
- Goals the API deletes without a VAR event are struck through once the scoreboard drops
- Own goals are credited to the right team, learned by checking the API's events against the score
- README.md 
### Reworked
- Bot split into config, cogs/tracking, tracker, events, storage, football_api and embeds modules, inside a bober/ package
- Data files (bober_lines.json, replays) moved to data/
### Removed
- bober-time command
- bober-play command, yt-dlp, voice support and the ffmpeg requirement

## v1.0.1-beta - 2026-11-04
### Added
- CI test workflow (pytest on Python 3.10 and 3.13, with a coverage report)
- Tests for the slash commands, the API client, storage, the poll loop and booting the bot, plus known bugs as expected-failure tests
### Reworked
- Unloading the tracking cog now cancels its pending resume task
- Tests fail on leaked resources and un-awaited coroutines
