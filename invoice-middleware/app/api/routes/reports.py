from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.core.security import verify_internal_token
from app.services.report_service import build_report_pdf, build_report_xlsx, get_report_summary

router = APIRouter()


@router.get("/summary")
def report_summary(
    company_id: str,
    period_type: str = "monthly",
    date_from: str | None = None,
    date_to: str | None = None,
    include_tax: bool = True,
    _: None = Depends(verify_internal_token),
) -> dict:
    try:
        return get_report_summary(
            company_id=company_id,
            period_type=period_type,
            date_from=date_from,
            date_to=date_to,
            include_tax=include_tax,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/export.xlsx")
def export_xlsx(
    company_id: str,
    period_type: str = "monthly",
    date_from: str | None = None,
    date_to: str | None = None,
    include_tax: bool = True,
    _: None = Depends(verify_internal_token),
):
    try:
        buffer = build_report_xlsx(
            company_id=company_id,
            period_type=period_type,
            date_from=date_from,
            date_to=date_to,
            include_tax=include_tax,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="invoice-report-{period_type}.xlsx"'},
    )


@router.get("/export.pdf")
def export_pdf(
    company_id: str,
    period_type: str = "monthly",
    date_from: str | None = None,
    date_to: str | None = None,
    include_tax: bool = True,
    _: None = Depends(verify_internal_token),
):
    try:
        buffer = build_report_pdf(
            company_id=company_id,
            period_type=period_type,
            date_from=date_from,
            date_to=date_to,
            include_tax=include_tax,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="invoice-report-{period_type}.pdf"'},
    )
