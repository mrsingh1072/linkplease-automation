# Failures and Limitations Audit

This document describes the actual known limitations, failure modes, and recovery behaviors of the `linkplease-automation` backend based on its design and implementation constraints.

## 1. Local Process Restarts & Task Lifecycles
* **Scenario:** The backend process is killed or restarts (e.g., during deployment or crash recovery) while the worker is in the middle of processing a claimed delivery.
* **Failure Mode:** A delivery task might remain in the `claimed` state indefinitely, preventing other workers or the restarted process from claiming it.
* **Recovery Mechanism:** Yes. The `dm_worker` periodically sweeps for deliveries stuck in the `claimed` state for more than 120 seconds and resets their status to `pending`, allowing them to be processed again.
* **Potential Side Effect:** If the process was killed *after* successfully making the network request to PseudoGram but *before* writing the status update to MongoDB, resetting the status to `pending` will trigger a retry. This retry is protected by the **attempt-specific idempotency key**, which guarantees that PseudoGram returns the original `dm_id` instead of sending a new DM.

## 2. PseudoGram Reconciliation Inconsistencies
* **Scenario:** PseudoGram accepts a DM (HTTP 202) but subsequently fails to deliver it, transitioning the status to `failed` on the `/v1/dm/{dm_id}` endpoint.
* **Failure Mode:** Under the idempotency rules, we must not reuse the same idempotency key if we are creating a new attempt after a confirmed terminal failure.
* **Recovery Mechanism:** The `reconciliation_worker` handles this. If a terminal `failed` status is confirmed from PseudoGram:
  1. If the reconciliation attempt count is below the threshold (3), the worker clears the `dm_id` and resets the delivery status to `pending`.
  2. It generates a **new attempt-specific idempotency key** (`attempt:<uuid>`), ensuring that PseudoGram treats it as a fresh logical request.
  3. If the retry threshold is reached, the status becomes `failed` permanently.

## 3. Simultaneous Rate Limit Window Sweeping (Race Conditions)
* **Scenario:** Multiple backend workers run concurrently on separate instances and try to acquire rate limit slots at the exact same millisecond.
* **Failure Mode:** If rate limiting were done in-memory, process boundaries would cause rate limits to be breached.
* **Recovery Mechanism:** Rate-limit slot tracking is stored in the database in a single document `dm_rate_limit`. The workers use an atomic `find_one_and_update` operation with an aggregation pipeline to evict expired timestamps and append the new timestamp only if the size of the array is less than 10. This ensures atomic, concurrent-safe rate limiting at the database level.
* **Unresolved Edge Case:** If the database operation succeeds but the worker immediately crashes before calling the PseudoGram API, the rate-limiting slot is "leaked" for 60 seconds (since the timestamp was inserted). This is a safe failure mode as it fails closed (rate-limiting is conservative) rather than open.

## 4. Webhook Signature Mismatch
* **Scenario:** The environment variable `PSEUDOGRAM_API_KEY` on our deployed server does not match the key used by the PseudoGram simulator.
* **Failure Mode:** All incoming webhooks will fail signature verification (HTTP 401) and will not be processed.
* **Recovery Mechanism:** None. The API key must be configured correctly on both ends.

## 5. Clock Drift
* **Scenario:** The local system clock of the backend server differs significantly from the database server or the PseudoGram API server.
* **Failure Mode:** Rate limiting checks relying on `datetime.now(timezone.utc)` and MongoDB aggregation comparisons might either block requests too early or allow them to bleed past the window.
* **Recovery Mechanism:** Bounded retries and honoring the HTTP 429 `Retry-After` header directly from the response are used as a fallback to ensure we adapt to the remote clock if it rate-limits us.
