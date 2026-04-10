from pydantic import BaseModel, ConfigDict


class OCRJobCreate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    document_id: str
    company_id: str
    file_path: str
    document_type: str
    requested_by: str | None = None
    model_name: str | None = None


class OCRJobResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    job_id: str
    status: str
    document_id: str
    company_id: str
    retry_count: int
    model_name: str


class OCRUploadResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    document_id: str
    job_id: str
    status: str
    stored_file_path: str
    model_name: str


class DocumentItemInput(BaseModel):
    line_no: int
    item_name: str
    quantity: float | None = None
    unit_price: float | None = None
    line_amount: float | None = None


class DocumentReviewUpdate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    requested_by: str
    vendor_name: str | None = None
    vendor_reg_no: str | None = None
    buyer_name: str | None = None
    buyer_reg_no: str | None = None
    issue_date: str | None = None
    supply_amount: float | None = None
    tax_amount: float | None = None
    total_amount: float | None = None
    payment_method: str | None = None
    invoice_number: str | None = None
    receipt_number: str | None = None
    items: list[DocumentItemInput] = []


class DocumentReprocessRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    requested_by: str
    model_name: str | None = None
