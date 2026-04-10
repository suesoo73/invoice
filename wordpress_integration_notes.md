# WordPress Integration Notes

## 1. Confirmed Environment

- Web server SSH: `ssh 192.168.20.16`
- Hostname: `shinsu`
- WordPress container: `5d22042b6319`
- WordPress path in container: `/var/www/html`
- WordPress site: `https://office.nusome.co.kr`

## 2. Confirmed WordPress DB Settings

Read from `wp-config.php` inside the container:

- DB host: `192.168.20.10`
- DB name: `mall`
- DB user: `root`
- Table prefix: `wp_`

Recommendation:
- Keep WordPress tables in `mall`
- Create a separate MySQL database such as `invoice_ocr` on the same MySQL server
- Do not mix new OCR service tables directly into the main WordPress schema unless necessary

## 3. Confirmed JWT Plugin

Installed plugin:
- `jwt-auth-nusome`

Confirmed namespace from live REST index:
- `nusome-jwt/v1`

Confirmed endpoints from plugin code:
- `POST /?rest_route=/nusome-jwt/v1/login`
- `GET /?rest_route=/nusome-jwt/v1/validate`
- `GET /?rest_route=/nusome-jwt/v1/me`

## 4. Live Endpoint Test Results

### REST root

Works with query-string routing:

- `https://office.nusome.co.kr/?rest_route=/`

Note:
- `https://office.nusome.co.kr/wp-json/` returned HTML instead of JSON during testing
- For the separate app, prefer `?rest_route=` based URLs unless server rewrite rules are fixed later

### JWT login

Confirmed working request pattern:

```http
POST https://office.nusome.co.kr/?rest_route=/nusome-jwt/v1/login
Content-Type: application/x-www-form-urlencoded

username=suesoo&password=8088
```

Confirmed response shape:

```json
{
  "success": true,
  "site": "https://office.nusome.co.kr",
  "token": "jwt-token",
  "token_type": "Bearer",
  "expires_in": 3600,
  "user": {
    "id": 1,
    "login": "suesoo",
    "email": "suesoo@nusome.co.kr",
    "display_name": "suesoo"
  }
}
```

### JWT validate

Without bearer token, the endpoint returns:

```json
{
  "code": "nusome_jwt_missing_token",
  "message": "Authorization bearer token is missing.",
  "data": {
    "status": 401
  }
}
```

## 5. Integration Design Recommendation

Recommended auth flow for the new app:

1. User enters WordPress ID and password in the separate app login screen
2. Separate app sends credentials to WordPress JWT login endpoint
3. Separate app receives WordPress JWT
4. Separate app validates token and resolves WordPress user ID
5. Separate app maps `wp_user_id` to internal `users.id` and `company_id`
6. Separate app issues its own session or access token for app API calls

Why this is better than trusting the WordPress JWT alone:
- Internal app sessions can include `company_id`
- Internal sessions can be revoked separately
- Audit logging and tenant enforcement are simpler

## 6. User Mapping Requirement

The current JWT payload includes:
- WordPress user ID
- login
- email

The payload does not include:
- company ID
- tenant code

Therefore, the separate app must maintain its own mapping table:

- `users.wp_user_id`
- `users.company_id`

## 7. Suggested MySQL Layout

Preferred:
- MySQL server: `192.168.20.10`
- WordPress DB: `mall`
- OCR app DB: `invoice_ocr`

This gives:
- Easier backup/restore separation
- Lower risk to WordPress tables
- Cleaner migration management

## 8. Immediate Next Build Steps

- Create `invoice_ocr` database on MySQL server
- Build app auth endpoint that exchanges WordPress JWT for app session
- Add company-user mapping seed data
- Implement first upload + OCR job flow
