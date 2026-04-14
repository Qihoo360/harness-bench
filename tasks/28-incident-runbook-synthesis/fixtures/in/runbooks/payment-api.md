# Runbook: payment-api latency

Payment latency often pages when checkout or cart dependencies stall.

Before changing payment settings:
1. Check whether authorization is blocked on a cart reservation token.
2. Inspect cart-api dependency errors.
3. Do not roll back fraud timeout unless payment errors continue after cart and inventory recover.

Escalate to payments-oncall only if payment authorization failures remain above 3% after dependency recovery.
