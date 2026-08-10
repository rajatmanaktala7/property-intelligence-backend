# Property Intelligence V15 - Exhaustive Scanner
Version 7.2.0

High-recall scanner for newspapers and magazines:
- full-page scan
- 3x3 overlapping tile scan
- 12% overlap
- PDF pages rendered at 220 DPI
- every tile audited in pi_scan_tiles
- failed tiles do not stop the rest of the source
- overlap duplicates are deduplicated by the existing database layer
- Database Edit links now use stable internal row IDs and self-heal legacy rows with missing property IDs

Railway variables to add:
SCAN_TILE_COLS=3
SCAN_TILE_ROWS=3
SCAN_TILE_OVERLAP=0.12
PDF_RENDER_DPI=220

Keep the same repo, Railway service, Postgres and domain. /health must show 7.2.0.
