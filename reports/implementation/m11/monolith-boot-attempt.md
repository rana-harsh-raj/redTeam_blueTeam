# M11 -- API monolith boot attempt (razorpay/api @ 2d665f918b60)

Generated 2026-09-09T16:19:59.021449+00:00 by scripts/m11/monolith_boot_attempt.py

| prerequisite | result |
|---|---|
| php on host | absent |
| composer on host | absent |
| composer require (total / razorpay private) | 98 / 18 |
| private VCS repositories readable | 5 / 23 |
| Dockerfile base images pullable | 0 / 3 |
| config/*.php env keys the runtime expects | 3814 |

## Verdict

bootable here: **False**

- no PHP interpreter / composer on this machine (and no credential-free base image carries them: see images)
- 18 of 23 private composer VCS repositories are not readable with the current identity (composer install cannot resolve spine, upi-clients, trace, oauth, ufh-sdk-php, dcs-php-sdk ...)
- 3 of 3 Dockerfile base images are not pullable without registry credentials (harbor.razorpay.com/razorpay/rzp-docker-image-inventory-multi-arch:rzp-legacy-image-api-curl-v4, harbor.razorpay.com/razorpay/rzp-docker-image-inventory-multi-arch:rzp-legacy-image-php-8.2-opencensus, harbor.razorpay.com/razorpay/rzp-docker-image-inventory-multi-arch:rzp-legacy-image-php-8.2-api-web-v4)

## Private VCS repositories

| repository | ssh | https |
|---|---|---|
| git@github.com:razorpay/spine.git | auth_denied | not_found_or_no_access |
| git@github.com:razorpay/upi-clients.git | auth_denied | not_found_or_no_access |
| git@github.com:razorpay/trace.git | auth_denied | not_found_or_no_access |
| git@github.com:razorpay/oauth.git | auth_denied | not_found_or_no_access |
| git@github.com:razorpay/ufh-sdk-php.git | auth_denied | not_found_or_no_access |
| git@github.com:razorpay/dcs-php-sdk.git | auth_denied | not_found_or_no_access |
| git@github.com:razorpay/config-proto.git | auth_denied | reachable |
| git@github.com:razorpay/hodor.git | auth_denied | not_found_or_no_access |
| git@github.com:razorpay/metrics-php.git | auth_denied | not_found_or_no_access |
| git@github.com:razorpay/edge-passport-php.git | auth_denied | not_found_or_no_access |
| git@github.com:razorpay/opencensus-php.git | auth_denied | reachable |
| git@github.com:razorpay/opencensus-php-exporter-jaeger.git | auth_denied | reachable |
| git@github.com:razorpay/thrift.git | auth_denied | not_found_or_no_access |
| git@github.com:razorpay/outbox-php.git | auth_denied | not_found_or_no_access |
| git@github.com:razorpay/slack-laravel.git | auth_denied | reachable |
| git@github.com:razorpay/TestDummy.git | auth_denied | reachable |
| git@github.com:razorpay/wda-php-sdk.git | auth_denied | not_found_or_no_access |
| git@github.com:razorpay/account-service-php-sdk.git | auth_denied | not_found_or_no_access |
| git@github.com:razorpay/redis-counting-semaphore.git | auth_denied | not_found_or_no_access |
| git@github.com:razorpay/TrustedProxy.git | auth_denied | not_found_or_no_access |
| git@github.com:razorpay/laravel-tagging.git | auth_denied | not_found_or_no_access |
| git@github.com:razorpay/no-leaks.git | auth_denied | not_found_or_no_access |
| git@github.com:razorpay/PasswordStrengthPackage.git | auth_denied | not_found_or_no_access |

## Base images

| image | Dockerfile | pullable | reason |
|---|---|---|---|
| harbor.razorpay.com/razorpay/rzp-docker-image-inventory-multi-arch:rzp-legacy-image-api-curl-v4 | Dockerfile.dev | False | unauthorized_or_private_registry |
| harbor.razorpay.com/razorpay/rzp-docker-image-inventory-multi-arch:rzp-legacy-image-php-8.2-opencensus | Dockerfile.dev | False | unauthorized_or_private_registry |
| harbor.razorpay.com/razorpay/rzp-docker-image-inventory-multi-arch:rzp-legacy-image-php-8.2-api-web-v4 | Dockerfile.dev | False | unauthorized_or_private_registry |
