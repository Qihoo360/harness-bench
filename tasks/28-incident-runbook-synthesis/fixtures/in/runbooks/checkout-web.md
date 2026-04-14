# Runbook: checkout-web success-rate drop

Classify severity:
- SEV2 if regional checkout success drops below 98.5% for more than 10 minutes or SLO burn rate exceeds 10.
- SEV1 only if global checkout success drops below 95% or payment capture integrity is at risk.

Customer update template:
- A subset of users in {region} may see checkout failures or delays.
- Engineering is mitigating the dependency issue and will provide the next update by {time}.
