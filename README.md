# Residency Rotation Calendar Subscription

A web application that generates personalized iCal calendar subscriptions from a residency rotation schedule.

## Features

- **Searchable resident lookup** - Find your name quickly with autocomplete
- **iCal subscription URLs** - Subscribe in iOS Calendar, Google Calendar, Outlook, or any calendar app
- **Automatic rotation rules** - Different schedules for ICU, Night, Clinic, and other rotations
- **Live calendar updates** - When the schedule XLSX is updated, calendars refresh automatically

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

1. Replace `schedule.xlsx` with the new file
2. Restart the server, OR
3. Call `POST /api/reload` to refresh without restart

## API Endpoints

- `GET /` - Web UI
- `GET /api/residents` - List all resident names
- `GET /api/calendar/{name}.ics` - Get iCal file for a resident
- `GET /api/health` - Health check
- `POST /api/reload` - Reload schedule from XLSX

## File Structure

```
├── app/
│   ├── main.py          # FastAPI application
│   ├── parser.py        # XLSX parsing logic
│   ├── calendar_gen.py  # ICS generation
│   └── config.py        # Rotation rules
├── static/
│   └── index.html       # Web UI
├── schedule.xlsx        # Source data
├── Dockerfile
└── requirements.txt
```

## License

MIT

