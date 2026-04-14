# Change Log

## CHG-8842
- Time: 2026-04-07T13:58:00+08:00
- Service: inventory-api
- Region: APAC
- Change: Roll out reservation policy v2.4.3 to 100% of APAC traffic.
- Risk: New policy rejects legacy cart token unless compatibility flag `allow_legacy_cart_token` remains enabled.
- Owner: supply-platform-oncall
- Rollback: Set feature flag `inventory.reservation_policy=v2.4.2` for APAC, then restart only the policy cache worker after approval.

## CHG-8843
- Time: 2026-04-07T14:01:00+08:00
- Service: payment-api
- Region: Global
- Change: Increase fraud API timeout from 700ms to 900ms.
- Risk: Could add small latency but should not create inventory `PolicyVersionMismatch`.

## CHG-8844
- Time: 2026-04-07T14:06:00+08:00
- Service: customer-support
- Region: APAC
- Change: Switch support macro copy for checkout outage.
- Risk: Communication only, no runtime effect.
