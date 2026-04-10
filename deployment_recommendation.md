# invoice.nusome.co.kr Deployment Recommendation

## 1. Recommended Topology

- Existing WordPress site stays at `https://office.nusome.co.kr`
- New OCR web app runs at `https://invoice.nusome.co.kr`
- Load balancer keeps using the existing `*.nusome.co.kr` wildcard certificate
- Internal flow:
  - browser -> load balancer -> invoice web app
  - invoice web app -> invoice middleware on LLM server
  - invoice middleware -> local Ollama / OCR service on the same LLM server

## 2. Why This Is the Right Fit

- No URL path collision with the current WordPress site
- Existing `office.nusome.co.kr` behavior remains unchanged
- Load balancer already terminates TLS for wildcard subdomains
- Future migration to another server or container is easy
- OCR and upload load stay outside the WordPress runtime
- Middleware and OCR stay physically close, reducing latency and simplifying internal firewall rules

## 3. Confirmed Current Routing

- `office.nusome.co.kr` is currently proxied by the load balancer to the `intranet` container
- Load balancer Apache config is mounted from:
  - `/home/suesoo/lb-apache/000-default.conf`
  - `/home/suesoo/lb-apache/001-nusome-ssl.conf`

## 4. Recommended Next Change

Add a new virtual host for `invoice.nusome.co.kr` that proxies to a new backend service:

- HTTP redirect: `invoice.nusome.co.kr:80` -> HTTPS
- HTTPS proxy target: `http://invoice-app:3000/`

Reference template:
- `invoice_subdomain_apache.conf`

## 5. Recommended App Deployment Shape

- Web server `192.168.20.16`
  - app container name: `invoice-app`
  - app code path: `/home/suesoo/invoice-app`
- LLM server `192.168.20.14`
  - middleware container name: `invoice-middleware`
  - middleware code path: `/home/suesoo/invoice-middleware`
  - storage path: `/home/suesoo/invoice-storage`

Reference template:
- `invoice_docker_compose_example.yaml`
- `llm_middleware_compose_example.yaml`

## 6. MySQL Recommendation

- Keep WordPress DB as is:
  - host: `192.168.20.10`
  - db: `mall`
- Create a separate MySQL database for the new service:
  - db: `invoice_ocr`
- Create a dedicated MySQL user for the new app rather than reusing `root`

## 7. WordPress Auth Recommendation

Use these live endpoints:

- login: `https://office.nusome.co.kr/?rest_route=/nusome-jwt/v1/login`
- validate: `https://office.nusome.co.kr/?rest_route=/nusome-jwt/v1/validate`

Recommended flow:

1. User logs in through the new web app
2. New web app exchanges credentials for WordPress JWT
3. New web app validates the JWT and maps `wp_user_id` to internal user/company
4. New web app issues its own app session token

## 8. Safe Rollout Order

1. Create `invoice_ocr` MySQL database and app user
2. Prepare `invoice-app` on `192.168.20.16`
3. Prepare `invoice-middleware` on `192.168.20.14`
4. Start middleware on the LLM server first
5. Start the web app on the web server
6. Verify web app -> middleware -> LLM connectivity
7. Add the `invoice.nusome.co.kr` Apache virtual host
8. Reload or recreate the load balancer container
9. Add DNS record for `invoice.nusome.co.kr` if not already present
10. Test end-to-end login, upload, OCR, and search

## 9. Important Note

Do not add the new `invoice.nusome.co.kr` proxy rule before the `invoice-app` backend is actually running, otherwise users will see gateway errors.

## 10. Network Recommendation

- Expose middleware only to the web app server if possible
- Prefer firewall allow-list:
  - source: `192.168.20.16`
  - target: `192.168.20.14:8080`
- Keep Ollama bound to local or internal-only access on the LLM server
