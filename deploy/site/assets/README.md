# Figma-exported assets

Static assets (SVG icons, brand mark, plot graphics) exported from the Figma file
"Profile Coherence — Datathon Polyfinance" for use by app.html.
Referenced by deploy/site/app.html; copied to the built site by deploy/build_site.py.

All files below were downloaded from the Figma MCP asset API for file
`szuK4mYLcfWsO7s0TDxt3x` (screen node `7:222`, "Accounts / Summary", plus the
icon and chart nodes the shell and the screen fragments need). Colours are baked
into the SVG strokes exactly as the design exports them, so icons keep their
Figma colour regardless of CSS.

## Manifest

| File | Figma node / description | Used by |
| --- | --- | --- |
| `brand.png` | `Brand` image (`2:31`, 2048×751 PNG) | `.brand__img` — global header, left |
| `icon-help.svg` | `icon/help` (`2:35`) | `.iconbtn` — header utility |
| `icon-mail.svg` | `icon/mail` (`2:38`) | `.iconbtn` — header utility |
| `icon-user.svg` | `icon/user` (`2:42`) | `.user-chip` — header account pill |
| `icon-out.svg` | `icon/out` (`2:47`) | `.signout` — header "Sign out" |
| `icon-ask.svg` | `icon/ask` (`7:490`) | `.fab` — ask button, bottom right, links to `#/search` |
| `icon-chev.svg` | `icon/chev`, default ink `#172b3f` (`2:56`) | `.tab` inactive tabs, `.select` chevron (shell and `whatif`) |
| `icon-chev-active.svg` | `icon/chev`, active blue `#0b6bcb` (`2:52`) | `.tab--active` |
| `icon-refresh.svg` | `icon/refresh` (`4:58`) | `summary` — "last run" stamp |
| `icon-search.svg` | `icon/search` (`5:319`) | `search` — query field and filter chips |
| `icon-back.svg` | `icon/back` (`4:423`) | back links: `objectives`, `risk`, `constraints`, `preferences`, `coherence`, `search`, `whatif` |
| `icon-check.svg` | `icon/check` (`2:65`) | `App.chip("pass")` — `summary`, `objectives`, `risk`, `constraints`, `preferences`, `coherence` |
| `icon-warn.svg` | `icon/warn` (`2:70`) | `App.chip("warning")` — `summary`, `objectives`, `risk`, `constraints`, `preferences`, `coherence` |
| `icon-x.svg` | `icon/x` (`2:75`) | `App.chip("violation")` — `summary`, `objectives`, `risk`, `constraints`, `preferences`, `whatif` |
| `icon-info.svg` | `icon/info` (`5:326`) | `App.chip("info")` / `App.chip("unknown")`, `search` scope line |
| `icon-ok.svg` | `icon/ok`, 26×26 filled green circle with check (`#1a7f47`) | round status badge: `objectives`, `constraints`, `preferences`, `coherence` |
| `icon-bad.svg` | `icon/bad`, 28×28 filled red circle with cross (`#e41c23`) | round status badge: `objectives`, `constraints` |
| `icon-warn-circle.svg` | `icon/warn`, 26×26 filled grey circle with exclamation (`#4d6a87`) | round status badge: `constraints`, `preferences`, `coherence` |
| `icon-check-white.svg` | `icon/check`, white 12×12 check on transparent | `search` — checked row checkbox (CSS background) |
| `icon-x-blue.svg` | `icon/x`, blue 12×12 cross | `search` — remove-filter chips |
| `swatch-band.svg` | `Swatch`, 20×10 ink at 8% (P5 to P95 band) | `summary` — chart legend |
| `swatch-iqr.svg` | `Swatch`, 20×10 ink at 22% (P25 to P75 band) | `summary` — chart legend |
| `swatch-line-ink.svg` | `Swatch`, 20×4 ink line (`#172b3f`, median) | `summary` — chart legend |
| `swatch-line-red.svg` | `Swatch`, 20×4 red line (`#e41c23`, pessimistic P5) | `summary` — chart legend |
| `swatch-line-floor.svg` | `Swatch`, 20×4 dashed red line (`#e41c23`, floor) | `summary` — chart legend |

## Notes

- `icon-x.svg`, `icon-bad.svg`, `swatch-line-red.svg` and `swatch-line-floor.svg` are
  exported with `#e41c23`, not the `--red` token (`#de171e`). They are used verbatim,
  as the design draws them.
- `icon-info.svg` has a 14×14 viewBox; the `.st__icon` class renders it at 16px so it
  aligns with the 16px check/warn/x icons in a status chip. `img` keeps the 1:1 ratio,
  so it scales uniformly.
- `icon-ok.svg` and `icon-warn-circle.svg` are 26×26 and `icon-bad.svg` is 28×28. The
  screens that show them size them with `.i28` / `.i16` classes and `width`/`height`
  attributes; as SVG they scale without distortion.
- Screen-specific graphics stay with the screen that draws them: the two plot images
  and the five legend swatches above are here because the screens reference them by
  name, and nothing else is referenced by the app.
- Do not edit these files; change the design in Figma and re-export.
