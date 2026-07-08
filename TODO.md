# Hotfix TODO

- [ ] Update `api/index.py` `/proxy` endpoint to be Range-capable:
  - [ ] Handle `Range` requests properly (return 206)
  - [ ] Ensure `Content-Range`, `Content-Length`, `Accept-Ranges: bytes`, stable `Content-Type: audio/mpeg`
  - [ ] Explicit header mapping (stop blindly forwarding headers)
- [ ] Update `src/components/AudioPlayer.astro` lyrics engine:
  - [ ] Replace `parseLRC()` with tolerant regex supporting multiple separators and multiple timestamps per line
  - [ ] Normalize fractional timestamp digits into seconds
  - [ ] Add guards in `updateActiveLyric()` (non-finite times / missing container)
- [ ] Update `src/components/AudioPlayer.astro` autoplay/queue:
  - [ ] Prefetch stream URLs for 2-step lookahead (next + one after) while playing
  - [ ] Ensure `onended` transition is cache-first and avoids async network work
- [ ] Build validation:
  - [ ] Run `npm run build`
- [ ] Git:
  - [ ] Commit changes with a clear message
  - [ ] Push to GitHub main
