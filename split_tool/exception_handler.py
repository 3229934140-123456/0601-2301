"""异常处理模块"""

from typing import List, Dict, Any, Optional
from datetime import datetime

from .models import SplitException, ExceptionStatus, Order, ExceptionType
from .storage import DataStore


class ExceptionHandler:
    """异常处理器"""

    def __init__(self, store: DataStore):
        self.store = store

    def _validate_fix(self, exc: SplitException, period: str) -> Dict[str, Any]:
        """验证异常是否真的可以标记为已修复"""
        if exc.exception_type == ExceptionType.MISSING_INFO:
            orders = self.store.load_orders(period)
            order = next((o for o in orders if o.order_id == exc.order_id), None)
            if not order:
                return {"ok": False, "reason": f"未找到订单 {exc.order_id}"}
            missing = []
            if not order.provider_id:
                missing.append("提供方ID")
            if not order.channel_id:
                missing.append("渠道方ID")
            if not order.service_id:
                missing.append("服务方ID")
            if not order.product_code:
                missing.append("产品编码")
            if missing:
                return {
                    "ok": False,
                    "reason": f"关键字段仍未补齐: {', '.join(missing)}。请先用 --fix-order 修正订单字段。"
                }
        elif exc.exception_type == ExceptionType.INVALID_AMOUNT:
            orders = self.store.load_orders(period)
            order = next((o for o in orders if o.order_id == exc.order_id), None)
            if not order:
                return {"ok": False, "reason": f"未找到订单 {exc.order_id}"}
            if order.order_amount <= 0:
                return {
                    "ok": False,
                    "reason": f"订单金额仍为 {order.order_amount}，请先用 --fix-order 修正 order_amount。"
                }
            if order.net_amount <= 0:
                return {
                    "ok": False,
                    "reason": f"订单净金额仍为 {order.net_amount}，请检查退款/折让设置。"
                }
        elif exc.exception_type == ExceptionType.MISSING_RULE:
            from .rule_manager import RuleManager
            orders = self.store.load_orders(period)
            order = next((o for o in orders if o.order_id == exc.order_id), None)
            if not order:
                return {"ok": False, "reason": f"未找到订单 {exc.order_id}"}
            rule_mgr = RuleManager(self.store)
            if not rule_mgr.get_rule(order.product_code):
                return {
                    "ok": False,
                    "reason": f"产品 {order.product_code} 仍未配置分成规则，请先用 config-rule 添加规则。"
                }
        elif exc.exception_type == ExceptionType.REFUND_EXCEED:
            orders = self.store.load_orders(period)
            order = next((o for o in orders if o.order_id == exc.order_id), None)
            if not order:
                return {"ok": False, "reason": f"未找到订单 {exc.order_id}"}
            if order.refund_amount > order.order_amount:
                return {
                    "ok": False,
                    "reason": f"退款金额 {order.refund_amount} 仍大于订单金额 {order.order_amount}，请修正。"
                }
        return {"ok": True}

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
                check = self._validate_fix(e, period)
                if not check["ok"]:
                    return {"success": False, "message": check["reason"]}
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
                    if field in ("order_amount", "refund_amount", "discount_amount"):
                        try:
                            fval = float(value)
                            setattr(o, field, fval)
                        except ValueError:
                            return {"success": False, "message": f"字段 {field} 需要数值，'{value}' 无法转换"}
                    else:
                        setattr(o, field, value)
                    self.store.save_orders(period, orders)
                    return {
                        "success": True,
                        "order_id": order_id,
                        "field": field,
                        "value": value
                    }
                return {"success": False, "message": f"订单不存在字段 {field}。可用字段: order_id, product_code, product_name, order_amount, refund_amount, discount_amount, provider_id, provider_name, channel_id, channel_name, service_id, service_name"}
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
