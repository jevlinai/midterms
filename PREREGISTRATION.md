# Pre-registration

The method for measuring which search engine finds US election results first was frozen **before the first polls closed** (Tue 3 Nov 2026, 6:00pm ET).

- **SHA-256 of the method archive:** `0d5581c776215f968e559f89995e686627d9d926d2851d70e8cd06ed1037ec03`
- committed_at: 2026-11-03T13:03:28-05:00
- **Parent commit:** `6741663904c59e5a7c36e68594cf6b2919048950`
- **Detector model:** TypeSafe `jev-1.14.0` (pinned in `detector/questions.json`)

The archive is a deterministic tar of the files below (sorted names, zeroed timestamps and owners), built by `scripts/preregister.py`. To check it, run `uv run python scripts/preregister.py verify` on this commit.

| File | SHA-256 |
|---|---|
| `data/house-competitive.json` | `2a9be46c0792a58515f15d7a350fa80f92c0178287b1e3af183b06c582c35e5a` |
| `data/races.json` | `99a9329195d26bb95a0503dfb5a31cf9cb51bb1a8d76b0ea9d37927de3ea9b94` |
| `detector/detect.py` | `f2bdd7058dfc5b209c4a93a0435c06cbf2158adfeb88d07234fdf43cef6203a4` |
| `detector/jev.py` | `8b13fa9f21a6466144b4ecbcf80b822e5ef05af675e7ec08ae0ca8460673e533` |
| `detector/questions.json` | `55ebc6a69f2efab0771039226dcebd1f28c07dd31fab58b683b344ace1e66a08` |
| `detector/textmatch.py` | `0360b16613ec83510a7628ce856ba5029a42c0ac868a059970233c4bc26bc397` |
| `docs/SCORING.md` | `f06a068481cb55d20f0d1a7f202eba2663cdd84dd5c4b9bf600929eddf7647fc` |
| `poller/config.json` | `2682e808cd26ba22982a7bb4236f335b192009a4cb5e03f62281860537f6f54b` |
| `poller/poller.py` | `80d9eb3d7734a30cd8bcb26728a424912f0c6c47fa3b43aba68c2c49a9fc7446` |
| `state/build_state.py` | `9f17ed1801705cab8e8d1a62be28bc209ce9fd938abf4223e6bd7f6cbde82eaf` |

Any change after this point is logged in `CHANGES.md` with a timestamp, and published.

## Earlier freezes, replaced

Two earlier freezes on 3 Nov were replaced before any hash was posted:

- **8:29am ET**, SHA-256 `ee5f5a94d62b20b9c314c147b90e5b0f70b4626e9693543232e7e938b0873c69`: included a seventh engine, Telem. Telem combines other providers rather than being one, so it was removed.
- **9:25am ET**, SHA-256 `1e9bfa222a9acdfd13ee1cbe149df7120f97475693cd486bb43beb4b6fb76efb`: the six engines, without the trace label. The label (each race is one session in the Jevlin console) was added; it changes nothing about how a search runs.

Neither hash was posted or used. Each showed on jevlin.ai/midterms for a few hours that morning.
