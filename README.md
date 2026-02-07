# Residency Rotation Calendar Subscription

A web application that generates personalized iCal calendar subscriptions from a residency rotation schedule.

## Features

- **Searchable resident lookup** - Find your name quickly with autocomplete
- **iCal subscription URLs** - Subscribe in iOS Calendar, Google Calendar, Outlook, or any calendar app
- **DB as source of truth** - Excel is used only for import; calendars are generated from database records
- **Hard rule validation** - Blocks schedule changes that violate Saturday week starts or duty-hour limits

## Quick Start

### Local Development

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Run the server:**
   ```bash
   uvicorn app.main:app --reload
   ```

3. **Open your browser:**
   Navigate to http://localhost:8000

### Docker

```bash
docker build -t rotation-calendar .
docker run -p 8000:8000 rotation-calendar
```

## Deployment

### Railway

1. Push this repo to GitHub
2. Connect to Railway
3. Deploy (auto-detected Dockerfile)
4. Set environment variable: `BASE_URL=https://your-app.railway.app`

### Render

1. Create a new Web Service
2. Connect your GitHub repo
3. Set build command: `docker build -t app .`
4. Set start command: `docker run -p 8000:8000 app`

### Fly.io

```bash
fly launch
fly deploy
```

## Rotation Schedule Rules

| Rotation | Days | Hours |
|----------|------|-------|
| Hospital Floors (Orange, Red, Purple, Green) | Sat-Fri | 6:00 AM - 7:30 PM |
| ICU | Sat-Fri | 6:00 AM - 7:00 PM |
| Night | Sat-Fri | 6:00 PM - 7:30 AM |
| Clinic (AMBULAT) | Mon-Fri | 6:00 AM - 7:30 PM |
| Other rotations | Sat-Fri | 6:00 AM - 7:30 PM |
| VAC / Research | Skipped | — |

## Updating the Schedule

1. Log in to admin portal
2. Upload XLSX through `POST /api/admin/schedule/import`
3. If validation fails, API returns HTTP 400 with:
   ```json
   {
     "status": "validation_failed",
     "context": "excel_import",
     "violations": []
   }
   ```

For one-time legacy migration (when the source spreadsheet violates new hard rules),
use:

```bash
python scripts/bootstrap_import_schedule.py --xlsx schedule.xlsx --allow-hard-violations
```

After this bootstrap step, keep using normal admin imports so hard rules stay enforced.

## API Endpoints

- `GET /` - Web UI
- `GET /api/residents` - List all resident names
- `GET /api/calendar/{identifier}.ics` - Get iCal file (calendar token, email, or DB resident name)
- `GET /api/health` - Health check
- `POST /api/admin/schedule/import` - Import schedule from XLSX (admin auth required)

## File Structure

```
├── app/
│   ├── main.py          # FastAPI application
│   ├── services/excel_import.py  # XLSX import into DB
│   ├── services/calendar.py      # DB-backed ICS generation
│   ├── services/validation.py    # Hard scheduling rules
│   └── config.py        # Rotation rules
├── static/
│   └── index.html       # Web UI
├── schedule.xlsx        # Import source data
├── render.yaml          # Render free-tier deployment template
├── Dockerfile
└── requirements.txt
```

## License

MIT
