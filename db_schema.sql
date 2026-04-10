-- Invoice / Receipt OCR Service
-- MySQL 8.0 schema draft for MVP
-- Recommended: use the same MySQL server as WordPress, but a separate database
-- Example database name: invoice_ocr

create database if not exists invoice_ocr
  character set utf8mb4
  collate utf8mb4_unicode_ci;

use invoice_ocr;

create table companies (
    id char(36) not null primary key,
    name varchar(200) not null,
    code varchar(100) not null,
    status enum('active', 'inactive') not null default 'active',
    created_at datetime not null default current_timestamp,
    updated_at datetime not null default current_timestamp on update current_timestamp,
    unique key uk_companies_code (code)
) engine=InnoDB default charset=utf8mb4 collate=utf8mb4_unicode_ci;

create table users (
    id char(36) not null primary key,
    company_id char(36) not null,
    wp_user_id bigint not null,
    email varchar(255) not null,
    name varchar(150) not null,
    status enum('active', 'inactive') not null default 'active',
    last_login_at datetime null,
    created_at datetime not null default current_timestamp,
    updated_at datetime not null default current_timestamp on update current_timestamp,
    unique key uk_users_company_wp_user (company_id, wp_user_id),
    unique key uk_users_company_email (company_id, email),
    key idx_users_company_id (company_id),
    constraint fk_users_company foreign key (company_id) references companies(id)
) engine=InnoDB default charset=utf8mb4 collate=utf8mb4_unicode_ci;

create table documents (
    id char(36) not null primary key,
    company_id char(36) not null,
    created_by char(36) not null,
    type enum('invoice', 'receipt') not null,
    status enum('uploaded', 'processing', 'review', 'completed', 'failed', 'deleted') not null,
    original_filename varchar(255) not null,
    file_path text not null,
    file_size bigint not null,
    mime_type varchar(100) not null,
    page_count int null,
    vendor_name varchar(255) null,
    issue_date date null,
    supply_amount decimal(15, 2) null,
    tax_amount decimal(15, 2) null,
    total_amount decimal(15, 2) null,
    currency varchar(10) not null default 'KRW',
    payment_method varchar(100) null,
    invoice_number varchar(100) null,
    receipt_number varchar(100) null,
    ocr_confidence decimal(5, 2) null,
    reviewed_by char(36) null,
    reviewed_at datetime null,
    deleted_at datetime null,
    purge_at datetime null,
    created_at datetime not null default current_timestamp,
    updated_at datetime not null default current_timestamp on update current_timestamp,
    key idx_documents_company_status (company_id, status),
    key idx_documents_company_issue_date (company_id, issue_date),
    key idx_documents_company_vendor (company_id, vendor_name),
    key idx_documents_company_total_amount (company_id, total_amount),
    key idx_documents_company_deleted_at (company_id, deleted_at),
    key idx_documents_created_by (created_by),
    key idx_documents_reviewed_by (reviewed_by),
    constraint fk_documents_company foreign key (company_id) references companies(id),
    constraint fk_documents_created_by foreign key (created_by) references users(id),
    constraint fk_documents_reviewed_by foreign key (reviewed_by) references users(id),
    constraint chk_documents_currency check (currency = 'KRW')
) engine=InnoDB default charset=utf8mb4 collate=utf8mb4_unicode_ci;

create table document_items (
    id char(36) not null primary key,
    document_id char(36) not null,
    line_no int not null,
    item_name varchar(255) not null,
    quantity decimal(15, 3) null,
    unit_price decimal(15, 2) null,
    line_amount decimal(15, 2) null,
    created_at datetime not null default current_timestamp,
    updated_at datetime not null default current_timestamp on update current_timestamp,
    unique key uk_document_items_document_line (document_id, line_no),
    key idx_document_items_document_id (document_id),
    key idx_document_items_item_name (item_name),
    constraint fk_document_items_document foreign key (document_id) references documents(id) on delete cascade
) engine=InnoDB default charset=utf8mb4 collate=utf8mb4_unicode_ci;

create table document_ocr_raw (
    id char(36) not null primary key,
    document_id char(36) not null,
    raw_text longtext not null,
    llm_response_json json not null,
    parser_version varchar(50) null,
    created_at datetime not null default current_timestamp,
    updated_at datetime not null default current_timestamp on update current_timestamp,
    unique key uk_document_ocr_raw_document_id (document_id),
    constraint fk_document_ocr_raw_document foreign key (document_id) references documents(id) on delete cascade
) engine=InnoDB default charset=utf8mb4 collate=utf8mb4_unicode_ci;

create table document_jobs (
    id char(36) not null primary key,
    document_id char(36) not null,
    job_type enum('ocr') not null default 'ocr',
    status enum('queued', 'processing', 'completed', 'failed', 'cancelled') not null,
    retry_count int not null default 0,
    max_retries int not null default 2,
    error_message text null,
    requested_by char(36) null,
    model_name varchar(100) null,
    requested_at datetime not null default current_timestamp,
    started_at datetime null,
    completed_at datetime null,
    created_at datetime not null default current_timestamp,
    updated_at datetime not null default current_timestamp on update current_timestamp,
    key idx_document_jobs_status_requested_at (status, requested_at),
    key idx_document_jobs_document_id (document_id),
    key idx_document_jobs_requested_by (requested_by),
    constraint fk_document_jobs_document foreign key (document_id) references documents(id) on delete cascade,
    constraint fk_document_jobs_requested_by foreign key (requested_by) references users(id)
) engine=InnoDB default charset=utf8mb4 collate=utf8mb4_unicode_ci;

create table document_audit_logs (
    id char(36) not null primary key,
    company_id char(36) not null,
    document_id char(36) null,
    user_id char(36) null,
    action varchar(50) not null,
    payload_json json null,
    ip_address varchar(45) null,
    user_agent text null,
    created_at datetime not null default current_timestamp,
    key idx_document_audit_logs_company_created_at (company_id, created_at),
    key idx_document_audit_logs_document_id (document_id),
    key idx_document_audit_logs_user_id (user_id),
    constraint fk_document_audit_logs_company foreign key (company_id) references companies(id),
    constraint fk_document_audit_logs_document foreign key (document_id) references documents(id) on delete set null,
    constraint fk_document_audit_logs_user foreign key (user_id) references users(id) on delete set null
) engine=InnoDB default charset=utf8mb4 collate=utf8mb4_unicode_ci;

create table deleted_documents (
    id char(36) not null primary key,
    document_id char(36) not null,
    company_id char(36) not null,
    deleted_by char(36) null,
    deleted_at datetime not null default current_timestamp,
    purge_at datetime not null,
    unique key uk_deleted_documents_document_id (document_id),
    key idx_deleted_documents_purge_at (purge_at),
    key idx_deleted_documents_company_id (company_id),
    constraint fk_deleted_documents_document foreign key (document_id) references documents(id) on delete cascade,
    constraint fk_deleted_documents_company foreign key (company_id) references companies(id),
    constraint fk_deleted_documents_user foreign key (deleted_by) references users(id)
) engine=InnoDB default charset=utf8mb4 collate=utf8mb4_unicode_ci;

create or replace view report_monthly_summary as
select
    company_id,
    date_format(issue_date, '%Y-%m-01') as period_start,
    count(*) as document_count,
    ifnull(sum(supply_amount), 0) as supply_amount_sum,
    ifnull(sum(tax_amount), 0) as tax_amount_sum,
    ifnull(sum(total_amount), 0) as total_amount_sum
from documents
where status = 'completed'
  and deleted_at is null
  and issue_date is not null
group by company_id, date_format(issue_date, '%Y-%m-01');

create or replace view report_vendor_summary as
select
    company_id,
    vendor_name,
    count(*) as document_count,
    ifnull(sum(total_amount), 0) as total_amount_sum
from documents
where status = 'completed'
  and deleted_at is null
  and vendor_name is not null
group by company_id, vendor_name;

create or replace view report_item_summary as
select
    d.company_id,
    i.item_name,
    count(*) as line_count,
    ifnull(sum(i.line_amount), 0) as line_amount_sum
from document_items i
join documents d on d.id = i.document_id
where d.status = 'completed'
  and d.deleted_at is null
group by d.company_id, i.item_name;
