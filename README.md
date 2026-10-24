# CareCloud Voice AI Patient Registration — Technical Assessment Demo

**Status:** Code and local automated tests implemented. You must configure your own hosting, Vapi account, inbound US phone number and test actual calls before submission. Do **not** claim these integrations are live until verified.

A voice-based **synthetic-patient** registration demo, implementing the CareCloud PDF challenge. A caller speaks to a Vapi voice assistant, confirms collected information, and Vapi invokes a secured FastAPI webhook that writes the patient to PostgreSQL. An authenticated REST API and password-gated lightweight dashboard can view the records.

**Do not collect or store real patients' data. This is not HIPAA-compliant software.**

## Stack and rationale

- **Vapi** handles dialable US phone number, STT, TTS, interruption and voice orchestration; avoids reinventing speech infrastructure during a time-boxed evaluation.
- **FastAPI + Pydantic v2** for typed patient validation and automatic API docs.
- **SQLAlchemy + PostgreSQL** for restart-safe persistent deployment. SQLite is offered for local testing only.
- **Minimal vanilla HTML/CSS/JS dashboard** for a small, dependency-free demo.
- **Pytest** for authentication, CRUD, validation and voice-tool response tests.

## Architecture

```text
Caller -> US Vapi number -> Vapi assistant (LLM/STT/TTS)
                              | function calls (HTTPS, X-Webhook-Secret)
                              v
                     POST /vapi/webhook
                              | shared domain validation + service functions
                              v
                      PostgreSQL database
                              ^
                              |
                 GET/POST/PUT/DELETE /patients
                              ^
                              |
                Dashboard (cookie session, login)
```

## Included files

```text
app.py                    FastAPI server, Pydantic validation, ORM, webhook, dashboard login
requirements.txt          Python requirements
static/index.html         Responsive dashboard
vapi/SYSTEM_PROMPT.md     Fully written conversational system prompt
vapi/tool_definitions.json   Schemas for the 3 Vapi Function tools
.env.example              Required environment variables
railway.json               Railway deployment settings
Procfile                   Alternative start command
tests/test_api.py         Initial automated tests
```

## STEP 1 — Run the project locally

Install **Python 3.11+** and **Git**. Extract this repository and open a terminal in its folder.

**Windows (PowerShell):**

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

**macOS/Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`. Generate **three different** strong random passwords/secrets for `API_KEY`, `DASHBOARD_PASSWORD`, `WEBHOOK_SECRET`. Do not use the sample placeholder strings. Optional separate `SESSION_SECRET` is recommended (another long random secret). For local testing keep `DATABASE_URL=sqlite:///./patients.db` and `COOKIE_SECURE=false`.

Run:

```bash
uvicorn app:app --reload
```

Open <http://127.0.0.1:8000/> and log in with your `DASHBOARD_PASSWORD`. API docs: <http://127.0.0.1:8000/docs>. Health: <http://127.0.0.1:8000/health>.

### Run automated tests

```bash
python -m pytest -q
```

Tests are local mocks/HTTP calls. They do **not** prove real telephony, production database or deployment.

## STEP 2 — Create a persistent cloud deployment on Railway

1. Create a **private** GitHub repository. Don't commit `.env`, SQLite database, or real secrets (these are `.gitignore`d).
2. From your project folder, run `git init`, `git add .`, `git commit -m "CareCloud voice intake demo"`, add a remote and `git push` using your own repository URL.
3. Open [Railway](https://railway.com/). Create a new project and **add PostgreSQL**.
4. Add a **GitHub repository service** in the same Railway project, linking your pushed repository.
5. Under the application service's **Variables**, set:
   - `DATABASE_URL` = the PostgreSQL service's `DATABASE_URL` using Railway's variable reference feature. Ensure it's a standard `postgresql://...` or `postgres://...` URI (the app converts it for psycopg).
   - `API_KEY` = a long unique random string.
   - `DASHBOARD_PASSWORD` = a different long random string.
   - `WEBHOOK_SECRET` = a third long random string.
   - `SESSION_SECRET` = a fourth long random string.
   - `COOKIE_SECURE` = `true` (public HTTPS only).
6. Deploy and watch logs. `railway.json` configures uvicorn at Railway's `$PORT` and `/health` monitoring.
7. In the service **Networking**, create a public domain. You will receive a base URL like `https://your-deployment.up.railway.app`.
8. Verify `https://your-deployment.up.railway.app/health` returns `{"data":{"status":"ok"},"error":null}`. Visit `/` and sign in. Visit `/docs` for generated API reference.
9. To verify persistence, create a **fictional** patient via the REST API, restart the app, and query it again. The PostgreSQL database must survive the restart.

**Warning:** Railway-hosted SQLite without a persistent volume loses data after a redeploy. Production deployments should use PostgreSQL. SQLite is fine for local tests.

## STEP 3 — Configure Vapi assistant and US number

Current Vapi instructions: [Vapi assistant quickstart](https://docs.vapi.ai/assistants/quickstart) and [Function tools](https://docs.vapi.ai/tools/custom-tools).

1. Sign up/login at [Vapi Dashboard](https://dashboard.vapi.ai).
2. Create a **new assistant**, name it `CareCloud — Patient Registration Demo`.
3. Paste the entire body of `vapi/SYSTEM_PROMPT.md` into the assistant's **System Prompt** field. Set first message to the one in the prompt; choose a natural English voice. Choose a compatible LLM in the Vapi model settings, with tool/function calling enabled.
4. Go to **Tools -> Create Tool -> Function Tool**; create each of the **three** functions from `vapi/tool_definitions.json`. For each, enter the exact function name, description and parameter JSON schema shown in the file. (Depending on current UI, you may paste the entire parameters schema or add fields individually.)
5. For **every function tool**, set **Server URL** to `https://YOUR-RAILWAY-DOMAIN/vapi/webhook` (the same URL for all 3 functions).
6. Add a **secret header** to each tool's server request: name `X-Webhook-Secret`, value matching Railway's `VAPI_WEBHOOK_SECRET` (or `WEBHOOK_SECRET`). Use Vapi's secure credential/secret option where offered. Do not paste that secret into the system prompt or code. Use synchronous tool execution; the result is needed before the agent can reply.
7. Attach all three function tools to the assistant model. Ensure tool use isn't disabled. Publish or save your assistant.
8. Go to **Phone Numbers -> Create Phone Number -> Free Vapi Number** (where available to your account). Choose an available US area code. Vapi's free numbers are inbound-only. If unavailable, connect an eligible Twilio/Telnyx number instead.
9. Open the number, set its **Inbound Assistant** to this assistant, save, and wait for activation.
10. Make a **real inbound test call** from a phone capable of calling US numbers. Confirm the voice greets you, walks through registration, recognizes corrections, and doesn't save until explicit consent.
11. After confirmation, check the dashboard and `/patients` endpoint; place a second call to test duplicate detection and persistence.

Important: Vapi tool webhooks require a response with `results: [{toolCallId, result: "..."}]`, and **results are strings**; this API implements that format. Vapi reports a tool failure using an `error` string in that result, not an HTTP 500 response.

### Example webhook test using curl

```bash
curl -X POST https://YOUR-RAILWAY-DOMAIN/vapi/webhook \
  -H 'Content-Type: application/json' \
  -H 'X-Webhook-Secret: YOUR_WEBHOOK_SECRET' \
  -d '{"message":{"type":"tool-calls","toolCallList":[{"id":"example1","function":{"name":"find_patient_by_phone","arguments":{"phone_number":"2125550199"}}}]}}'
```

### API endpoints

Every patient API endpoint requires `X-API-Key: <API_KEY>` unless accessed by an authenticated dashboard session.

| Method | Endpoint | Details |
| --- | --- | --- |
| GET | `/patients` | Lists active records; optional `last_name`, `date_of_birth` (ISO `YYYY-MM-DD`), `phone_number` |
| GET | `/patients/{patient_id}` | Retrieve by UUID |
| POST | `/patients` | Create patient; 201 |
| PUT | `/patients/{patient_id}` | Partial update allowed |
| DELETE | `/patients/{patient_id}` | Soft delete (`deleted_at`) |
| GET | `/health` | Deployment health |
| POST | `/vapi/webhook` | Function calls from Vapi, with webhook header authentication |
| GET | `/` | Dashboard UI |

Successful API responses use `{"data": ..., "error": null}`. Errors use `{"data":null,"error":{"code":"...","message":"..."}}`; validation errors additionally include a `fields` array of field/message pairs. Patient API dates are serialized and accepted as ISO `YYYY-MM-DD`. During a voice call, the agent asks for and speaks the date as `MM/DD/YYYY`, then sends the corresponding ISO date to the API. The Vapi webhook retains its required `results` response format. FastAPI automatically documents routes at `/docs`.

Example **synthetic** registration:

```bash
curl -X POST https://YOUR-RAILWAY-DOMAIN/patients \
  -H 'X-API-Key: YOUR_API_KEY' \
  -H 'Content-Type: application/json' \
  -d '{"first_name":"Jane","last_name":"Doe","date_of_birth":"1990-05-12","sex":"Female","phone_number":"2125550199","address_line_1":"123 Main Street","city":"New York","state":"NY","zip_code":"10001"}'
```

## STEP 4 — Manual final QA checklist

- [ ] A real US phone number rings the assistant and the assistant replies without long unexplained pauses.
- [ ] Names, birthdate, sex, phone and required postal address captured correctly, including out-of-order answers.
- [ ] Optional details are optional, and caller is offered insurance, preferred language and emergency contact.
- [ ] Invalid future DOB / 3-digit phone / ZIP are rejected and agent reprompts the **specific field**.
- [ ] Caller correction, interruption, start-over and declining registration do not accidentally create a record.
- [ ] All collected information is read back and explicit permission is obtained before any save.
- [ ] Only successful database write results in a success announcement.
- [ ] Calling again with the same phone results in an existing-record option, not a silent duplicate.
- [ ] REST CRUD and filters work with expected JSON statuses; deleted records do not appear in listings.
- [ ] App restart retains data and password authentication still works.
- [ ] Dashboard works on desktop and mobile.
- [ ] Repo is accessible to reviewer, API base URL resolves, Vapi number works when reviewer calls.
- [ ] No real patient information, credentials or secrets are included in source, screenshots or console logs.

## Known limitations and deliberately scoped trade-offs

1. The demo is **not** HIPAA compliant. Do not use it with real patient details.
2. Voice-agent read-back and verbal consent rely on the LLM instructions. The backend checks the `confirmed=true` tool argument, not audio evidence or cryptographic proof that the caller consented.
3. Duplicate detection returns a matching name and ID to the voice assistant. Date-of-birth verification is LLM-directed, not a separate server-side identity assurance workflow. This is **not sufficient** for real clinical account changes.
4. The phone number may cost money or require account validation, and live Vapi operation must be configured and tested manually.
5. `/docs` is publicly browsable (operations still require a secret); protect or disable it for a real deployment.
6. The dashboard exposes test records to a single administrator password rather than implementing user roles. It provides view/search and soft-delete; edits are available through PUT API, not a visual edit form.
7. This initial version does not store call recordings or transcripts. It logs the final collected payload after a successful Vapi registration to satisfy the assessment. **Use synthetic data only** and restrict access to application logs; in a real clinical setting this must be replaced with privacy-preserving, encrypted audit logging.
8. SQLAlchemy `create_all()` is used instead of production migrations. Add Alembic before extending the deployed schema.
9. Registration saving is **not idempotent** if Vapi retries a successful save after a dropped response; duplicate detection limits accidental repeats, but explicit server-side idempotency keys would improve reliability.
10. If a browser dashboard session exists, it's accepted by `/patients`; for production add stronger cross-site request forgery protection.

## Submission checklist / email template

**Repository URL:** (fill in verified GitHub repository URL)

**US phone number:** (fill in verified live number in +1 E.164 format)

**API base URL:** (fill in verified Railway HTTPS domain)

**Dashboard:** (same HTTPS domain `/`)

**Dashboard test login:** Share via a secure channel and keep credentials separate from the public repository.

**Notes:** "This is a synthetic-patient technical assessment demo. Phone registration persists to PostgreSQL; dashboard and REST API display the same records. Full setup and design limitations documented in README."

The original PDF specifies a **3-hour maximum** and requests submission within 3 hours of receipt, while the cover email requests the earliest convenient submission. Confirm late submission acceptance with the recruiter if applicable.
