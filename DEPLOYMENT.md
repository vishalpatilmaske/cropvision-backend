# Deploying CropVision AI on Vercel (frontend and backend separately)

Two GitHub repos, each deployed as its own Vercel project, plus a hosted database:

- Frontend: https://github.com/vishalpatilmaske/cropvision-frontend
- Backend: https://github.com/vishalpatilmaske/cropvision-backend

```
Browser ──▶ cropvision-web.vercel.app  (frontend: React static site)
   │
   └── API calls ──▶ cropvision-api.vercel.app  (backend: Flask as a serverless function)
                          └──▶ MongoDB Atlas (hosted database, free tier)
```

Vercel can't run a database, so MongoDB lives on **MongoDB Atlas**.

## 1. Create the database (MongoDB Atlas)

1. Sign up at https://www.mongodb.com/cloud/atlas and create a free **M0** cluster.
2. **Database Access** → Add a database user with a password.
3. **Network Access** → Add IP Address → **Allow access from anywhere** (`0.0.0.0/0`).
   Vercel functions don't have fixed IP addresses, so this is required.
4. **Connect** → Drivers → copy the connection string and add the database name
   `cropvision` before the `?`:

   ```
   mongodb+srv://USER:PASSWORD@cluster0.xxxxx.mongodb.net/cropvision?retryWrites=true&w=majority
   ```

## 2. Code on GitHub

Both repos are already on GitHub (links above). `.env` files are in each repo's `.gitignore`,
so secrets are never uploaded — they go into Vercel's Environment Variables instead.

## 3. Deploy the backend

On https://vercel.com → **Add New… → Project** → import **cropvision-backend**, then:

- **Project name:** e.g. `cropvision-api`
- **Framework Preset:** `Other`

**Environment Variables** (generate each secret with
`python3 -c "import secrets; print(secrets.token_hex(32))"`):

| Name | Value |
|---|---|
| `FLASK_ENV` | `production` |
| `SECRET_KEY` | new 64-char random value |
| `JWT_SECRET` | a different 64-char random value |
| `MONGO_URI` | your Atlas connection string from step 1 |
| `RATELIMIT_STORAGE_URI` | the same Atlas connection string |
| `TRUST_PROXY` | `true` |
| `OPENAI_API_KEY` | your key |
| `AI_REQUEST_TIMEOUT_SECONDS` | `25` |
| `AI_MAX_RETRIES` | `1` |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `MAIL_FROM_NAME` | same as your local `.env` |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | your admin login (use a strong password) |

Click **Deploy**. When it finishes, open `https://cropvision-api.vercel.app/health` — you should see
`{"ai_configured": true, "status": "ok"}`.

## 4. Deploy the frontend

**Add New… → Project** → import **cropvision-frontend**:

- **Project name:** e.g. `cropvision-web`
- **Framework Preset:** `Vite` (detected automatically)
- **Environment Variable:** `VITE_API_BASE_URL` = `https://cropvision-api.vercel.app`
  (your backend URL from step 3, **no** trailing slash)

Click **Deploy**.

## 5. Connect the two

Nothing to do: the backend always allows `https://cropvision-frontend.vercel.app` and
`http://localhost:5173` (CORS), and the frontend's `.env.production` points at
`https://cropvision-backend.vercel.app`. Open the frontend URL and sign up — the site is live.

Only for an **extra** site (e.g. a custom domain): set `ALLOWED_ORIGINS` on the backend
(comma-separated) and redeploy. It adds to the built-in list; it never removes it.

## Updating

Push to either repo — its Vercel project redeploys automatically:

```bash
git add . && git commit -m "Describe the change" && git push
```

## Good to know

- **`VITE_API_BASE_URL` is baked in at build time.** If you change it, redeploy the frontend.
- **Uploads:** Vercel rejects request bodies over 4.5 MB. The app shrinks photos in the browser to
  ~1600 px (usually well under 2 MB) before uploading, so normal phone photos are fine.
- **Time limit:** each API request can run for up to 60 s (`vercel.json` in the backend repo). That's why
  `AI_MAX_RETRIES=1` and `AI_REQUEST_TIMEOUT_SECONDS=25` — one AI retry still fits.
- **Cold starts:** after a quiet period the first request takes a few extra seconds while the
  function starts; later requests are fast.
- **Logs:** Vercel dashboard → backend project → **Logs**.
- **Custom domain:** project → **Settings → Domains**. Add it to the backend's `ALLOWED_ORIGINS` too.
