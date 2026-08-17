# LinkPlease Automation Backend

A robust, backend-only automation system that receives comment webhooks from PseudoGram (a mock Instagram API), matches comments against user-defined keyword rules, and sends the correct Direct Messages (DMs) to commenters.

Designed for high reliability under hostile network conditions (such as duplicate webhook events, 500 errors, 429 rate limits, and application restarts).

## Architecture & Design Decisions

### Background Processing & Task Queue
* The `/webhook` endpoint validates, deduplicates, and enqueues work into MongoDB in **under 5 seconds** (no slow synchronous operations occur in the request lifecycle).
* An asynchronous background worker (`dm_worker.py`) polls MongoDB for pending tasks, claims them atomically, and processes the sends.
* An asynchronous reconciliation worker (`reconciliation_worker.py`) handles Part C, verifying delivery status via polling and handling failures.

### Atomic Operations & Deduplication
* **Event Deduplication:** Database-level uniqueness constraint on `event_id` prevents duplicate webhook payloads from processing twice.
* **Duplicate DM Protection:** Compound unique index on `(user_id, rule_id)` prevents the same user from receiving the same DM rule twice.
* **Atomic Job Claiming:** Workers claim jobs using MongoDB's atomic `findOneAndUpdate` to transition deliveries from `pending` to `claimed`. No race conditions between workers.

### Idempotency Key Semantics
* An attempt-specific idempotency key is generated (`attempt:<uuid>`) for each sending attempt.
* The same key is reused during 500 retries since the remote end might have received the message.
* If PseudoGram confirms a terminal failure, the reconciliation loop increments the reconciliation attempt count and generates a **new attempt-specific key** to start a fresh sending lifecycle.

### Rate Limiting (10 requests per 60s)
* Enforced globally across all instances via a MongoDB-backed sliding window array in `rate_limit`.
* Uses `findOneAndUpdate` with an aggregation pipeline to atomically check/append timestamps.
* Honors the `Retry-After` header dynamically if a 429 response is received from PseudoGram.

---

## Getting Started

### Prerequisites
* Python 3.12+
* MongoDB running locally (default: `mongodb://localhost:27017`) or a MongoDB Atlas URI

### Configuration
1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
2. Configure the variables:
   * `PSEUDOGRAM_API_KEY`: Your simulator/production API key.
   * `MONGODB_URL`: Connection string to your MongoDB server.
   * `DATABASE_NAME`: Database name (e.g., `linkplease`).

### Installation & Run
1. Create a virtual environment and activate it:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Start the application:
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```

---

## API Documentation

### 1. Create a Rule
* **Endpoint:** `POST /rules`
* **Request Body:**
  ```json
  {
    "keyword": "PRICE",
    "dm_message": "Here is the price list: ..."
  }
  ```
* **Response (HTTP 201):**
  ```json
  {
    "rule_id": "8a09f8d1-d25b-4b2a-9a00-1cfa976df080",
    "keyword": "PRICE",
    "dm_message": "Here is the price list: ..."
  }
  ```

### 2. Receive Webhook
* **Endpoint:** `POST /webhook`
* **Headers Required:**
  * `X-PseudoGram-Signature`: `sha256=<hex_signature>` (HMAC-SHA256 of the raw body using the API key).
* **Response:** HTTP 200 (processed/enqueued immediately).

### 3. Retrieve Statistics
* **Endpoint:** `GET /stats`
* **Response (HTTP 200):**
  ```json
  {
    "sent": 0,
    "failed": 0,
    "queued": 0,
    "duplicates_blocked": 0
  }
  ```

---

## Testing

### Automated Local Tests
To run unit and integration tests locally, run:
```bash
pytest
```
Tests cover signature verification, rules CRUD, event and user-level duplicate protection, backoff retry logic, rate limit safety, and statistics updates.

### Real PseudoGram Simulator Test Procedure
1. Deploy the backend to a public URL (e.g., Fly.io, Render, Railway). Ensure your `PSEUDOGRAM_API_KEY` is configured in the environment.
2. Register a rule on your deployed service:
   ```bash
   curl -X POST https://YOUR_DEPLOYED_URL/rules \
     -H "Content-Type: application/json" \
     -d '{"keyword":"PRICE","dm_message":"Price is $10"}'
   ```
3. Trigger the PseudoGram simulator:
   ```bash
   curl -X POST https://pseudogram-api.onrender.com/v1/simulate/start \
     -H "Content-Type: application/json" \
     -H "X-API-Key: YOUR_API_KEY" \
     -d '{"webhook_url": "https://YOUR_DEPLOYED_URL/webhook", "count": 500, "duration_seconds": 10}'
   ```
4. Note the returned `run_id`.
5. Wait for the simulation to finish, then fetch your `/stats` endpoint:
   ```bash
   curl https://YOUR_DEPLOYED_URL/stats
   ```
6. Compare your stats with the PseudoGram ground truth:
   ```bash
   curl -H "X-API-Key: YOUR_API_KEY" https://pseudogram-api.onrender.com/v1/simulate/{run_id}/truth
   ```
7. Verify that `sent`, `failed`, `queued`, and `duplicates_blocked` match perfectly.
