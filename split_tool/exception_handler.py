"""异常处理模块"""

from typing import List, Dict, Any, Optional
from datetime import datetime

from .models import SplitException, ExceptionStatus, Order
from .storage import DataStore


class ExceptionHandler:
    """异常处理器"""

    def __init__(self, store: DataStore):
        self.store = store

    def list_exceptions(self, period: str, status: str = None) -> List[Dict[str, Any]]:
        exceptions = self.store.load_exceptions(period)
        if status:
            target = ExceptionStatus(status)
            exceptions = [e for e in exceptions if e.status == target]
        return [e.to_dict() for e in exceptions]

    def fix_exception(
        self, period: str, exception_id: str, fix_note: str,
        operator: str = "system"
    ) -> Dict[str, Any]:
        exceptions = self.store.load_exceptions(period)
        for e in exceptions:
            if e.exception_id == exception_id:
                e.status = ExceptionStatus.FIXED
                e.fix_note = fix_note
                e.fixed_by = operator
                e.fixed_at = datetime.now().isoformat(timespec="seconds")
                self.store.save_exceptions(period, exceptions)
                return {"success": True, "exception": e.to_dict()}
        return {"success": False, "message": f"未找到异常记录 {exception_id}"}

    def ignore_exception(
        self, period: str, exception_id: str, fix_note: str = "",
        operator: str = "system"
    ) -> Dict[str, Any]:
        exceptions = self.store.load_exceptions(period)
        for e in exceptions:
            if e.exception_id == exception_id:
                e.status = ExceptionStatus.IGNORED
                e.fix_note = fix_note
                e.fixed_by = operator
                e.fixed_at = datetime.now().isoformat(timespec="seconds")
                self.store.save_exceptions(period, exceptions)
                return {"success": True, "exception": e.to_dict()}
        return {"success": False, "message": f"未找到异常记录 {exception_id}"}

    def fix_order_field(
        self, period: str, order_id: str, field: str, value: str
    ) -> Dict[str, Any]:
        orders = self.store.load_orders(period)
        for o in orders:
            if o.order_id == order_id:
                if hasattr(o, field):
                    setattr(o, field, value)
                    self.store.save_orders(period, orders)
                    return {
                        "success": True,
                        "order_id": order_id,
                        "field": field,
                        "value": value
                    }
                return {"success": False, "message": f"订单不存在字段 {field}"}
        return {"success": False, "message": f"未找到订单 {order_id}"}

    def get_open_exception_count(self, period: str) -> int:
        exceptions = self.store.load_exceptions(period)
        return len([e for e in exceptions if e.status == ExceptionStatus.OPEN])

    def get_exception_stats(self, period: str) -> Dict[str, int]:
        exceptions = self.store.load_exceptions(period)
        stats = {}
        for e in exceptions:
            key = e.status.value
            stats[key] = stats.get(key, 0) + 1
        return stats
