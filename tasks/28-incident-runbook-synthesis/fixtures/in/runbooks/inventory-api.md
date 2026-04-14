# Runbook: inventory-api APAC reservation failures

Symptoms:
- `reserveStock` 409 rate above 5%.
- `PolicyVersionMismatch` in inventory-api logs.
- Cart and payment services may alert as downstream symptoms.

Likely cause:
- Reservation policy rollout incompatible with legacy cart tokens.

Safe mitigation:
1. Confirm APAC impact and correlate with a recent inventory-api change.
2. Ask incident commander for approval before changing production flags.
3. If approved, set `inventory.reservation_policy=v2.4.2` for APAC.
4. Restart only the policy cache worker; do not restart all inventory-api pods unless latency remains high.
5. Verify `reserveStock` error rate below 2%, checkout success above 98.5%, and no increase in oversell events.

Stop conditions:
- Error rate continues rising after rollback.
- Database lag exceeds 30 seconds.
- Oversell protection alarms trigger.
