# Pre-registration

The method for measuring which search engine finds US election results first was frozen **before the first polls closed** (Tue 3 Nov 2026, 6:00pm ET).

- **SHA-256 of the method archive:** `1e9bfa222a9acdfd13ee1cbe149df7120f97475693cd486bb43beb4b6fb76efb`
- committed_at: 2026-11-03T09:25:34-05:00
- **Parent commit:** `67a414163a182ed1f5315e8eedc7ff2676934a1a`
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
| `docs/SCORING.md` | `f57e39999f9c3bb9f3abcf382819c18f6d64bd7d670f98153da90d914ba158cd` |
| `poller/config.json` | `3df1ec5ca9a73f206e959fb74430f160624fc8dcea9646a9af514a2fb94abaf4` |
| `poller/poller.py` | `bd7af6838e20eafc058824fdf3f0ad18a797db5d71de85d655f0bb3d3ccd1759` |
| `state/build_state.py` | `9f17ed1801705cab8e8d1a62be28bc209ce9fd938abf4223e6bd7f6cbde82eaf` |

Any change after this point is logged in `CHANGES.md` with a timestamp, and published.

## Earlier freeze, replaced

A first freeze at 8:29am ET on 3 Nov (SHA-256 `ee5f5a94d62b20b9c314c147b90e5b0f70b4626e9693543232e7e938b0873c69`, commit `27ae60d`) included a seventh engine, Telem. Telem combines other providers rather than being one, so it was removed and the method was frozen again, as above. The first hash showed on jevlin.ai/midterms for about two hours that morning and was never posted or used. Every other method file is unchanged between the two freezes.
