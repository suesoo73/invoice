# WordPress User Sync

`invoice-app` now uses:
- WordPress JWT login
- middleware user mapping by `wp_user_id` and `email`
- app session cookie

To allow a WordPress user to log in, that user must exist in `invoice_ocr.users`.

## Sync Script

Script path:

- `invoice-middleware/app/scripts/sync_wp_users.py`

What it does:
- creates or updates a target company
- reads users from the WordPress `wp_users` table
- inserts or updates matching users in `invoice_ocr.users`

## Example

Sync all `@nusome.co.kr` WordPress users into one company:

```bash
cd ~/invoice-middleware
. .venv/bin/activate
python -m app.scripts.sync_wp_users \
  --company-id 11111111-1111-1111-1111-111111111111 \
  --company-name "Nusome Internal" \
  --company-code nusome-internal \
  --wp-host 192.168.20.10 \
  --wp-db mall \
  --wp-user root \
  --wp-password goose \
  --email-domain nusome.co.kr
```

## Notes

- This is an operational sync tool, not an automatic scheduler yet.
- External customer users should usually be synced into separate companies.
- If a user is not synced, app login succeeds at WordPress but fails at app mapping.
