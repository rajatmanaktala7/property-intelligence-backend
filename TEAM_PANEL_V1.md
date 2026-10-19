# Alliance Team Panel V1

URL: `/team-panel` (same Alliance login). Owner view: `/team-panel/admin` (Admin login).

## What the team sees every working morning
1. **Follow-ups**: every lead due now or overdue, on top, in red. Quick buttons: Later today, Tomorrow, In 3 days, Next week, pick a date, Close.
2. **WhatsApp outreach**: 10 batches x 25 outlet contacts (restaurants, cafes, bars, cloud kitchens, banquets, fashion, jewellery, salons, footwear, grocery). Write message, tap "Make it better with AI", tap "Use this message", tap Send on each contact. WhatsApp opens with the message ready and the outlet name filled in.
3. **Hot matches**: 10 broker requirements with a matching Master property and a ready message with no contact numbers. A yellow "Team only" box shows where the property came from and whom to call.

Yogesh gets Delhi NCR only. Zoya and Priya get Delhi NCR and Goa.

## Automatic jobs (IST)
| Time | Job |
|---|---|
| 07:00 Mon-Sat | Prepare outreach + matches for all members (idempotent, tops up partial match sets) |
| 20:30 Mon-Sat | Save daily report per member |
| Last working day 20:30 | Monthly AI review |

## Rules
- Reads Master authorities only. Writes only `tp_*` tables.
- No contact is given twice on the same day. SENT contacts rest for 30 days. NO_WHATSAPP and STOP are never contacted again. Unsent contacts return the next day.
- "Replied" on any contact or match creates a lead that is due immediately.

## Settings (Railway variables, all optional)
| Variable | Default |
|---|---|
| `TEAM_PANEL_PLAN_TIME` / `TEAM_PANEL_REPORT_TIME` | `07:00` / `20:30` |
| `TEAM_PANEL_WORKDAYS` | `0,1,2,3,4,5` (Mon-Sat) |
| `TEAM_PANEL_BATCHES` / `TEAM_PANEL_BATCH_SIZE` / `TEAM_PANEL_MATCHES` | `10` / `25` / `10` |
| `TEAM_PANEL_COOLDOWN_DAYS` | `30` |
| `TEAM_PANEL_MIN_SCORE` / `TEAM_PANEL_MATCH_DAYS` | `75` / `21` |
| `TEAM_PANEL_MEMBERS` | JSON, e.g. `{"yogesh":{"name":"Yogesh Mehra","short":"Yogesh","regions":["NCR"]}}` |
| `TEAM_PANEL_SCHEDULER` | `on` |
| AI key | `GEMINI_API_KEY` (already used by Alliance). Without it, messages fall back to clean templates. |

## Phase 2: WhatsApp Business API
Sending today uses wa.me links from each member's own WhatsApp. After Meta approval, cold messages must use an approved template. The AI message becomes the template body variables, and the Cloud API webhook can call `set_outreach_status(..., "REPLIED")` by phone so replies turn into leads with no tap.
