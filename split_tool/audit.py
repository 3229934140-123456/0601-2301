from datetime import datetime
from .models import AuditRecord, generate_id


def write_audit(
    store, period: str, action: str, operator: str = "system",
    detail: str = "", order_count: int = 0, amount: float = 0.0,
    extra: dict = None
):
    record = AuditRecord(
        audit_id=generate_id("AUD"),
        period=period,
        action=action,
        operator=operator,
        timestamp=datetime.now().isoformat(timespec="seconds"),
        detail=detail,
        order_count=order_count,
        amount=amount,
        extra=extra or {},
    )
    store.append_audit_log(record)
