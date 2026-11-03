# Pre-registration

The method for measuring which search engine finds US election results first was frozen **before the first polls closed** (Tue 3 Nov 2026, 6:00pm ET).

- **SHA-256 of the method archive:** `ee5f5a94d62b20b9c314c147b90e5b0f70b4626e9693543232e7e938b0873c69`
- committed_at: 2026-11-03T08:29:07-05:00
- **Parent commit:** `d6052075307c86a22a951770a21a1bf608c04a0f`
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
| `docs/SCORING.md` | `8384cc697899417b3dbbc9938bb12c77a5ab74ce976725949573a988d34fa397` |
| `poller/config.json` | `4466c7adfc0d90ece5b67d142bf603e63317007127ef650f5e3dbbd082229d26` |
| `poller/poller.py` | `bd7af6838e20eafc058824fdf3f0ad18a797db5d71de85d655f0bb3d3ccd1759` |
| `state/build_state.py` | `1cb2fdedb7b8c3821e3b85212ff4421a193fb4f920e5c52eb3e5a2ebcac23226` |

Any change after this point is logged in `CHANGES.md` with a timestamp, and published.
