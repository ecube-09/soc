---
incident_id: EXT-0001
title: Checkout errors after a cache cluster version upgrade
service: checkout-api
environment: production
severity: SEV-2
region: us-east-1
started_at: 2024-03-02T10:15
resolved_at: 2024-03-02T11:40
origin: fixture:tests/sample_postmortem.md
related_services: redis-cache
tech_context: Redis 7.0, Kubernetes 1.28
---

Test fixture written in the shape of a public postmortem. Not a real incident.

## Summary
Checkout requests failed for 85 minutes after the cache cluster was upgraded in place.

## Symptoms
- checkout-api 5xx rate 18%
- cache client connection refused errors
- no application deploy that morning

## Timeline
- 10:10 deploy redis-cache: In-place upgrade of the cache cluster started.
- 10:15 ERROR checkout connection refused talking to redis
- 10:21 alert CHECKOUT-5XX paged the on-call
- 11:02 mitigation redis-cache: Rolled the cache cluster back to the previous version.
- the dashboard was unhelpful

## Root cause
The upgrade restarted every cache node at once instead of one at a time, so clients had no node to fail over to.

## Resolution
Rolled the cache cluster back, then repeated the upgrade node by node in a maintenance window.

## Actions
- rollback redis_version on redis-cache 7.2 -> 7.0
- reconfigure upgrade_strategy on redis-cache all-at-once -> rolling
- fixed it somehow

## Impact
About 18% of checkout attempts failed for 85 minutes.

## Lessons learned
- Upgrade stateful clusters one node at a time.
- Alert on cache connection refusals, not only on checkout errors.
