# Invoice / Receipt OCR Service API Spec

## 1. Overview

- Auth source: WordPress JWT
- App style: separate web app
- OCR path: web app -> middleware -> LLM server
- Tenant rule: every request must be scoped by `company_id`
- Base path: `/api`
- WordPress REST in the current environment is confirmed working through `?rest_route=...`

## 2. Auth

### POST `/api/auth/jwt-login`

Uses a WordPress JWT token and creates an app session or app access token.
The WordPress JWT should be obtained from the live endpoint:

`POST https://office.nusome.co.kr/?rest_route=/nusome-jwt/v1/login`

Request:

```json
{
  "jwt_token": "wordpress-jwt-token"
}
```

Response:

```json
{
  "access_token": "app-access-token",
  "token_type": "Bearer",
  "expires_in": 3600,
  "user": {
    "id": "uuid",
    "company_id": "uuid",
    "name": "홍길동",
    "email": "user@example.com"
  }
}
```

WordPress JWT validate endpoint:

`GET https://office.nusome.co.kr/?rest_route=/nusome-jwt/v1/validate`

## 3. Documents

### POST `/api/documents`

Uploads one or more files and registers OCR jobs.

Request:
- Content-Type: `multipart/form-data`
- Fields:
  - `type`: `invoice` or `receipt`
  - `files[]`: one or more files

Response:

```json
{
  "documents": [
    {
      "id": "uuid",
      "original_filename": "invoice-001.pdf",
      "status": "uploaded"
    }
  ]
}
```

### GET `/api/documents`

Searches documents.

Query params:
- `status`
- `type`
- `date_from`
- `date_to`
- `vendor_name`
- `amount_min`
- `amount_max`
- `item_name`
- `page`
- `page_size`

Response:

```json
{
  "items": [
    {
      "id": "uuid",
      "type": "invoice",
      "status": "review",
      "vendor_name": "ABC Supplier",
      "issue_date": "2026-04-01",
      "total_amount": 120000,
      "currency": "KRW"
    }
  ],
  "pagination": {
    "page": 1,
    "page_size": 20,
    "total": 135
  }
}
```

### GET `/api/documents/{id}`

Gets one document with OCR result and items.

Response:

```json
{
  "id": "uuid",
  "type": "invoice",
  "status": "review",
  "original_filename": "invoice-001.pdf",
  "file_url": "https://...",
  "vendor_name": "ABC Supplier",
  "issue_date": "2026-04-01",
  "supply_amount": 100000,
  "tax_amount": 10000,
  "total_amount": 110000,
  "currency": "KRW",
  "payment_method": null,
  "invoice_number": "INV-2026-001",
  "receipt_number": null,
  "raw_text": "full ocr text",
  "items": [
    {
      "line_no": 1,
      "item_name": "Paper",
      "quantity": 10,
      "unit_price": 5000,
      "line_amount": 50000
    }
  ]
}
```

### PUT `/api/documents/{id}`

Updates a document during review.

Request:

```json
{
  "vendor_name": "ABC Supplier",
  "issue_date": "2026-04-01",
  "supply_amount": 100000,
  "tax_amount": 10000,
  "total_amount": 110000,
  "currency": "KRW",
  "payment_method": "card",
  "invoice_number": "INV-2026-001",
  "receipt_number": null,
  "items": [
    {
      "line_no": 1,
      "item_name": "Paper",
      "quantity": 10,
      "unit_price": 5000,
      "line_amount": 50000
    }
  ]
}
```

### POST `/api/documents/{id}/complete`

Marks a reviewed document as completed.

Request:

```json
{
  "confirm": true
}
```

### POST `/api/documents/{id}/retry`

Creates a new OCR job for manual retry.

Response:

```json
{
  "job_id": "uuid",
  "status": "queued"
}
```

### DELETE `/api/documents/{id}`

Moves a document to trash.

Response:

```json
{
  "id": "uuid",
  "status": "deleted",
  "purge_at": "2026-04-15T00:00:00Z"
}
```

### POST `/api/documents/{id}/restore`

Restores a trashed document within 7 days.

Response:

```json
{
  "id": "uuid",
  "status": "review"
}
```

## 4. Reports

### GET `/api/reports/summary`

Query params:
- `period_type`: `monthly` or `quarterly`
- `date_from`
- `date_to`

Response:

```json
{
  "summary": {
    "document_count": 120,
    "supply_amount_sum": 10000000,
    "tax_amount_sum": 1000000,
    "total_amount_sum": 11000000
  },
  "vendors": [
    {
      "vendor_name": "ABC Supplier",
      "document_count": 33,
      "total_amount_sum": 2500000
    }
  ],
  "items": [
    {
      "item_name": "Paper",
      "line_count": 84,
      "line_amount_sum": 800000
    }
  ]
}
```

### GET `/api/reports/export.xlsx`

Exports the same filtered dataset in Excel format.

### GET `/api/reports/export.pdf`

Exports the same filtered dataset in PDF format.

## 5. Middleware Internal APIs

### POST `/internal/ocr/jobs`

Registers OCR work from the web app or app backend.

Request:

```json
{
  "document_id": "uuid",
  "file_path": "/storage/documents/uuid.pdf",
  "type": "invoice"
}
```

### POST `/internal/ocr/callback`

Receives parsed OCR result from the LLM processing worker.

Request:

```json
{
  "document_id": "uuid",
  "status": "completed",
  "raw_text": "full text",
  "fields": {
    "vendor_name": "ABC Supplier",
    "issue_date": "2026-04-01",
    "supply_amount": 100000,
    "tax_amount": 10000,
    "total_amount": 110000,
    "currency": "KRW"
  },
  "items": [
    {
      "line_no": 1,
      "item_name": "Paper",
      "quantity": 10,
      "unit_price": 5000,
      "line_amount": 50000
    }
  ]
}
```

## 6. Audit Events

Recommended `document_audit_logs.action` values:

- `login`
- `upload`
- `ocr_queued`
- `ocr_started`
- `ocr_completed`
- `ocr_failed`
- `ocr_retried`
- `document_updated`
- `document_completed`
- `document_deleted`
- `document_restored`
- `report_exported`

## 7. Validation Notes

- `currency` is fixed to `KRW` in MVP.
- `company_id` must be derived from authenticated user context, not from client input.
- Only `review` or `failed` documents can be manually retried.
- Only trashed documents within 7 days can be restored.
- Only `completed` documents are included in reports.
