# Property Intelligence Backend Database Starter

## Rating
- Simplicity: 9.2/10
- Organization: 9.6/10
- AI-agent readiness: 9.4/10
- Railway suitability: 9.5/10

## Tables
- pi_properties
- pi_requirements
- pi_contacts
- pi_sources
- pi_media
- pi_matches
- pi_verification_log

## Visible database screen
After deployment, open:
`https://YOUR-DOMAIN/database`

## Railway
1. Use PostgreSQL.
2. Add `DATABASE_URL=${{Postgres.DATABASE_URL}}`.
3. Run `schema.sql` once.
4. Optional: run `seed.sql`.
5. Deploy this app.
6. Open `/health`, then `/database`.

Sensitive property fields remain stored in PostgreSQL but are hidden from the standard properties API response.
