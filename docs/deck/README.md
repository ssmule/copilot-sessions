# Deck

A two-slide HTML deck introducing `cs`.

| Slide | Content |
|---|---|
| 1 | **The problem** — context does not outlive the session: the questions we ask constantly, and what it costs (tokens, redone work, security gap) |
| 2 | **The solution** — `cs`: find, measure, secure, improve, with the landing screen and the security guarantees |

## Presenting

```bash
open docs/deck/index.html
```

`→` / `space` / click-right advances · `←` / click-left goes back · `f` fullscreen.
Both slides animate in on arrival, and replay if you navigate back to them.

Self-contained: `index.html` plus one local SVG, with no fonts and no scripts to
fetch, so it works offline. Copy the two files together — the poster is
referenced relatively, so the deck loses its artwork if you move the HTML alone.

Slide one's poster (`poster.svg`) is **original artwork drawn for this deck**,
not the film's own — naming a film and describing it is commentary, shipping its
marketing in an MIT repository is not. It is vector, so it stays sharp on a
projector at a few KB. The deck honours `prefers-reduced-motion`.

### Presenting with a different poster

Presenting is not publishing. If you have an image you are entitled to use, drop
it next to `index.html` as `poster.local.jpg` (`.jpeg`, `.png` and `.webp` also
work) and the deck will use it instead. Any aspect ratio is fine; the frame
follows the image.

```bash
cp -f ~/my-poster.jpg docs/deck/poster.local.jpg
```

Those names are gitignored, so the image cannot be committed by accident, and
`git status` stays clean while it is there. That guard is deliberate: a blob
that is pushed once remains fetchable at its old SHA even after a later commit
deletes the file, so "commit it now, tidy it later" is not something git
actually offers. Delete the file and the drawn poster returns.

All figures shown are **synthetic**, matching the examples in the root README.
