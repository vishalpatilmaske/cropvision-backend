# CropVision AI — Backend

Smart farming platform for smallholder farmers: crop/pest/disease image analysis, field advisory,
Krishi Panchayat (multi-expert AI review), weather-aware irrigation advice, crop and fertilizer
recommendations, and yield estimates.

This repo is the **Flask REST API**. The React app lives in
[cropvision-frontend](https://github.com/vishalpatilmaske/cropvision-frontend).

Disease and pest analysis uses a **hosted vision LLM (OpenAI GPT vision)**, not a locally trained
model. All AI calls happen server-side in Flask — the browser never talks to the AI provider.

```
React app (browser)  ->  Flask REST API  ->  OpenAI (vision LLM)
                                                         ->  Open-Meteo (weather)
                                                         ->  MongoDB
```

## Project structure

```
cropvision-backend/
├── run.py                   Local entry point — serves on :8000
├── api/index.py             Vercel entry point (Flask as a serverless function)
├── vercel.json              Vercel function time limit
├── requirements.txt         Runtime dependencies (what Vercel installs)
├── requirements-dev.txt     + test tools
├── .env.example             Template for .env
├── DEPLOYMENT.md            Step-by-step Vercel + MongoDB Atlas guide
├── app/
│   ├── __init__.py          App factory, error handlers, /health
│   ├── config.py            Env-driven config (Config, TestConfig)
│   ├── extensions.py        mongo, jwt, cors, limiter
│   ├── models/              MongoDB document wrappers (User, Farm, Crop, predictions, recommendations)
│   ├── routes/              Flask blueprints — one file per API area
│   ├── services/
│   │   ├── ai/              LLM-backed features
│   │   │   ├── llm_client.py            OpenAI client: timeout, retry, error classification
│   │   │   ├── farm_agent.py            Krishi Mitra chat agent (OpenAI function calling)
│   │   │   ├── disease_pest_service.py  Image analysis prompt + response validation
│   │   │   ├── advisory_service.py      Field advisory
│   │   │   └── panchayat_service.py     Krishi Panchayat multi-expert review
│   │   ├── google_auth_service.py       "Continue with Google" token check
│   │   ├── recommendation_service.py    Rule-based crop + fertilizer engine
│   │   ├── irrigation_service.py        Rule-based irrigation advisory
│   │   ├── yield_service.py             Heuristic yield estimate
│   │   └── weather_service.py           Open-Meteo weather + soil snapshot
│   └── utils/               Responses, pagination, image validation, admin auth, Mongo helpers
└── tests/                   pytest suite (AI client mocked)
```

## Setup

**Prerequisites:** Python 3.10+ and MongoDB — local on `27017`, or a MongoDB Atlas
connection string in `MONGO_URI`.

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env        # then fill in SECRET_KEY, JWT_SECRET, OPENAI_API_KEY, ADMIN_PASSWORD
python run.py               # http://localhost:8000
```

Then run the [frontend](https://github.com/vishalpatilmaske/cropvision-frontend) on :5173.

### Tests

```bash
source venv/bin/activate
python -m pytest -q
```

Tests use a `cropvision_test` database on a **local** MongoDB (dropped after each test), so
MongoDB must be running locally even if the app itself uses Atlas. The OpenAI client is mocked — no real API calls are made.

## Deployment

Frontend and backend deploy to **Vercel** as two separate projects, with the database on
**MongoDB Atlas**. Step-by-step for both: [`DEPLOYMENT.md`](DEPLOYMENT.md).

## Environment variables

`.env` (see `.env.example`; on Vercel, the project's Environment Variables):

| Variable | Purpose |
|---|---|
| `FLASK_ENV` | `development`, or `production` (refuses weak secrets) |
| `SECRET_KEY`, `JWT_SECRET` | Flask + JWT signing secrets |
| `JWT_ACCESS_TOKEN_EXPIRES_HOURS` | Login session length (default 12) |
| `ALLOWED_ORIGINS` | CORS origins, comma-separated (`http://localhost:5173`) |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | Vision LLM (default model `gpt-4o`) |
| `AI_REQUEST_TIMEOUT_SECONDS`, `AI_MAX_RETRIES` | AI call limits |
| `MONGO_URI` (or `MONGODB_URI`) | Local MongoDB or Atlas connection string. Default `mongodb://localhost:27017/cropvision` |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `MAIL_FROM_NAME` | Email for login codes (Gmail: use an App Password) |
| `OTP_TTL_MINUTES`, `OTP_MAX_ATTEMPTS`, `OTP_RESEND_SECONDS`, `RATE_LIMIT_OTP` | Login code rules (10 min, 5 tries, 60 s, 5/min) |
| `GOOGLE_CLIENT_ID` | OAuth Client ID for "Continue with Google" (empty = button hidden) |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | Fixed admin-panel login (change in production) |
| `WEATHER_API_URL`, `GEOCODING_API_URL`, `CLIMATE_API_URL` | Open-Meteo forecast, place lookup, historical weather (no key needed) |
| `TRUST_PROXY`, `RATELIMIT_STORAGE_URI` | Production: real client IPs behind Vercel's proxy, rate limits shared across instances |
| `MAX_UPLOAD_SIZE_BYTES`, `MAX_IMAGE_DIMENSION_PX`, `RATE_LIMIT_ANALYZE`, `RATE_LIMIT_CHAT` | Upload / cost control |

The frontend only needs `VITE_API_BASE_URL` (this API's URL). Never put an AI key in a `VITE_*`
variable — Vite inlines those into the browser bundle.

## API

All responses use `{ "success": true, "data": ..., "message": ... }` or
`{ "success": false, "error": { "code": ..., "message": ... } }`.

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | — | Health check: database ping + which services are configured (200 ok / 503 database down) |
| POST | `/api/auth/otp/request` | — | Email a 6-digit code (`purpose`: `login` or `register` + `name`, `phone`) |
| POST | `/api/auth/otp/verify` | — | Check the code → `{user, access_token}` (creates the account for `register`) |
| GET | `/api/auth/providers` | — | `{google_client_id}` — whether to show the Google button |
| POST | `/api/auth/google` | — | Google sign-in token (`access_token`) → `{user, access_token, created}` |
| GET | `/api/auth/me` | JWT | Current user |
| POST | `/api/disease/analyze` | JWT | Upload a crop/leaf image for AI analysis |
| GET | `/api/disease/history` | JWT | Paginated analysis history |
| GET | `/api/disease/history/:id` | JWT | One analysis record |
| POST | `/api/assistant/chat` | JWT | Krishi Mitra farm assistant (chat agent) |
| POST | `/api/advisory/field` | JWT | AI field advisory — no UI yet |
| POST | `/api/advisory/panchayat` | JWT | Krishi Panchayat multi-expert review (image) — no UI yet |
| GET/POST | `/api/farms` | JWT | List / create farms |
| GET | `/api/farms/:id` | JWT | One farm |
| POST | `/api/farms/:id/crops` | JWT | Add a crop to a farm |
| POST | `/api/crop-recommendation` | JWT | Rule-based crop ranking (`"save": false` = live preview, not saved) |
| GET | `/api/crop-conditions?latitude=&longitude=&season=` | JWT | Last season's rain + temperature at a location |
| GET | `/api/crop-recommendation/history` | JWT | Past crop recommendations |
| POST | `/api/fertilizer-recommendation` | JWT | Urea / DAP / MOP bags + when-to-apply schedule (`"save": false` = preview) |
| POST | `/api/irrigation-recommendation` | JWT | Today's decision + 7-day FAO water-balance plan (`"save": false` = preview) |
| POST | `/api/yield-prediction` | JWT | Yield estimate with factor breakdown, optional latest health check (`"save": false` = preview) |
| GET | `/api/{crop,fertilizer,irrigation}-recommendation/history`, `/api/yield-prediction/history` | JWT | Saved plans (My Farm Records) |
| GET | `/api/weather/places?name=` | JWT | Village / town search (when location is off) |
| GET | `/api/yield-baseline?crop_name=` | JWT | Average quintals/acre for a crop (read-only) |
| GET | `/api/weather` | JWT | Weather + soil snapshot for lat/lon |
| POST | `/api/admin/login` | — | Admin login |
| GET | `/api/admin/stats` | Admin | Dashboard counts |
| GET/POST | `/api/admin/users` | Admin | List / create users |
| GET/PUT/DELETE | `/api/admin/users/:id` | Admin | Manage one user |

## Farmer sign in (email code, no password)

Sign up: name, email, optional phone → a 6-digit code is emailed → entering it creates the account.
Sign in: email → code → signed in. `app/services/otp_service.py` stores only an HMAC of the
code (collection `otp_codes`, auto-deleted by a TTL index), allows 5 wrong tries, expires codes after
10 minutes, and lets a new code be sent only after 60 seconds. Emails go out over SMTP
(`app/services/email_service.py`); in tests they're captured in `email_service.outbox` instead.
The admin panel keeps its separate email + password login.

**Continue with Google** (optional): the login and sign-up pages have a "Continue with Google"
button that opens Google's sign-in popup; it works once `GOOGLE_CLIENT_ID` is set in `.env`.
The backend checks the returned token with Google (`app/services/google_auth_service.py`): it must be
issued to our Client ID and belong to a verified email. It then signs in the account with that email
or creates one. To get a Client ID: Google Cloud Console → APIs & Services →
Credentials → Create OAuth client ID → *Web application*, with `http://localhost:5173` and
`http://localhost` as Authorized JavaScript origins.

## Krishi Mitra (farm assistant)

A floating chat button (bottom-right, every logged-in page) opens an AI agent built on the OpenAI
SDK's function calling (`app/services/ai/farm_agent.py`). On each message the model can
call tools, always scoped to the logged-in farmer, and then answers:

| Tool | What it does |
|---|---|
| `get_my_farms` | The farmer's farms and crops |
| `get_crop_health_checks` / `get_crop_health_report` | Their past photo health checks |
| `get_weather_forecast` | Open-Meteo current weather, soil and 7-day forecast (rain, wind) + heatwave / heavy-rain alerts |
| `get_irrigation_advice` | Irrigate-or-skip decision for today |
| `find_place` | Village / town / district name → coordinates (Open-Meteo geocoding) |
| `recommend_crops`, `recommend_fertilizer`, `estimate_yield` | The rule-based engines |

Weather tools find the location in this order: coordinates given by the model, a place name the
farmer mentioned, then the location the browser shared when the chat was opened. If none is known the
agent asks the farmer.

The chat history is kept in the browser (last 20 messages) and sent with each request; the server
accepts only `user`/`assistant` messages, so a client can't inject system or tool messages. "Ask
Krishi Mitra about this" on a health report opens the chat with that report attached as context.

## Crop health report

The Disease & Pest page is one step for the farmer: picking or taking a photo starts the check
immediately. The AI identifies the crop and growth stage itself; crop name and notes are optional
(collapsed under "Add details"). If the browser shares its location, the page sends `latitude` /
`longitude` and the backend adds local weather (humidity, rain in the next 3 days, heat / heavy-rain
alerts) to the analysis, so the report can advise on spray timing. Without location it still works.

`POST /api/disease/analyze` returns, besides the diagnosis and recommendations, a `report` block:
`health_score` (0–100), plain-language `summary`, `affected_area_pct`, `spread_risk`, `urgency`,
`organic_options`, `monitoring_plan` and `recovery_outlook`. The block is best-effort: bad or
missing fields are defaulted, never failing the analysis. Reports can be printed / saved as PDF.

## Crop recommendation

The farmer picks only their soil (picture cards) and water (rain only / some / full irrigation).
The season defaults to the next sowing season from today's date, and season rain + average
temperature come from last year's actual weather at their location (Open-Meteo archive) — they
can edit these or type them if location is off. Results re-rank live as they change a choice.

The engine (`app/services/recommendation_service.py`) scores 17 crops out of 100 across
season (25), soil (25), water (30) and temperature (20), and returns the breakdown, warnings,
duration, water need and usual yield per crop. It counts monsoon moisture stored in the soil for
rabi (black soil ~200 mm), demotes irrigation-only crops (vegetables, sugarcane, potato) on rain-fed
fields, flags waterlogging, and leaves out (rather than penalises) any input it doesn't have.

## Fertilizer, irrigation and yield tools

All three follow the Crop page pattern: tap-to-choose inputs (remembered across tools: crop, soil,
water, farm size), live results, and "Save to My Records".

- **Fertilizer** (`recommendation_service.recommend_fertilizer`): general recommended doses
  (kg/ha N:P₂O₅:K₂O) for all 17 crops, optionally adjusted ±25% per nutrient from Soil Health Card
  values (low / medium / high ratings), converted to Urea (46% N), DAP (18-46-0) and MOP (60% K₂O)
  in bags, split into a sowing + top-dressing schedule by crop type, with approximate subsidised
  MRP cost.
- **Irrigation** (`irrigation_service.build_irrigation_plan`): FAO-56 water balance on the live
  forecast — crop water use = reference evapotranspiration × crop coefficient (by crop and stage),
  minus useful rain; water when the root zone has dried by the method's trigger (flood 40 mm,
  sprinkler 25 mm, drip 8 mm), skip if good rain is due tomorrow. Gives mm and litres per acre.
- **Yield** (`yield_service.estimate_yield_detailed`): well-managed baseline per acre × soil ×
  water (same water model as the crop engine) × care level × latest health check score (last 60
  days), shown step by step, plus income from the farmer's mandi price.

**My Farm Records** (`/history`) has tabs for health checks (with summary stats and filters),
crop plans, fertilizer, irrigation and yield.

## 3D Digital Twin

After a health check that finds a disease, pest or nutrient problem, the Disease page shows a 3D leaf
and a 14-day "if not treated vs if treated now" projection built from that report
(frontend: `src/lib/cropSimulation.js`):

- Starts from the report's affected leaf area (or severity for older reports).
- Spreads at the report's spread-risk rate, 30% faster when local weather is wet / humid.
- Treatment stops new damage after ~2 days; nutrient deficiency recovers once corrected.
- Yield loss ≈ a share of affected leaf area; quantities use the crop's average yield
  (`/api/yield-baseline`, editable) × the farmer's acres, and rupees only if the farmer enters
  their mandi price (remembered per crop in the browser).

All assumptions are listed under the simulation — it is a planning aid, not a prediction.

## Images

- JPG, JPEG, PNG, WEBP; max 8 MB; integrity-checked with Pillow.
- Resized to 1600px max and re-encoded as JPEG before being sent to the AI.
- Kept in memory only for the request — never written to disk. Only the structured result
  is stored in MongoDB.

## AI limitations

- Disease/pest identification is a vision LLM's visual assessment, not a lab diagnosis. It reports
  confirmed / likely / possible / unknown, and the UI asks users to confirm uncertain or severe
  results with an agricultural expert.
- Confidence is a subjective 0–100 score, not a statistical probability.
- Crop, fertilizer, irrigation and yield outputs are transparent rule-based heuristics over small
  reference tables (~10 common crops), not trained ML models.
