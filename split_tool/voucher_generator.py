"""凭证生成模块"""

from typing import List, Dict, Any
from datetime import datetime
from collections import defaultdict

from .models import (
    PaymentVoucher, SplitDetail, SplitRole,
    ConfirmStatus, VoucherStatus, generate_id
)
from .storage import DataStore
from .trial_calculator import recalc_voucher_from_details


class VoucherGenerator:
    """付款凭证生成器"""

    def __init__(self, store: DataStore):
        self.store = store

    def _check_period_confirmed(self, period: str) -> Dict[str, Any]:
        period_info = self.store.load_period(period)
        if not period_info:
            return {"ok": False, "reason": f"结算周期 {period} 不存在"}
        if period_info.status not in (ConfirmStatus.CONFIRMED, ConfirmStatus.LOCKED):
            return {"ok": False, "reason": f"分账未确认（当前状态: {period_info.status.value}）"}
        return {"ok": True}

    def generate_vouchers(self, period: str, operator: str = "system") -> Dict[str, Any]:
        check = self._check_period_confirmed(period)
        if not check["ok"]:
            return {"success": False, "message": check["reason"]}

        details = self.store.load_confirmed_details(period)
        if not details:
            return {"success": False, "message": "未找到已确认的分账明细"}

        invalid_details = [d for d in details if not d.org_id]
        if invalid_details:
            invalid_orders = set(d.order_id for d in invalid_details)
            return {
                "success": False,
                "message": f"发现 {len(invalid_details)} 条分账明细的机构ID为空，涉及订单: {', '.join(sorted(invalid_orders))}。请先修正订单的机构信息，重新确认分账。"
            }

        voucher_calc = recalc_voucher_from_details(details)

        vouchers: List[PaymentVoucher] = []
        for v in voucher_calc["vouchers"]:
            voucher = PaymentVoucher(
                voucher_id=generate_id("PV"),
                period=period,
                role=v["role"],
                org_id=v["org_id"],
                org_name=v["org_name"],
                total_amount=v["total_amount"],
                order_count=v["order_count"],
                status=VoucherStatus.CREATED,
                created_at=datetime.now().isoformat(timespec="seconds"),
                remark=f"由分账自动生成，操作人: {operator}",
            )
            vouchers.append(voucher)

        self.store.save_vouchers(period, vouchers)

        return {
            "success": True,
            "period": period,
            "voucher_count": len(vouchers),
            "total_amount": voucher_calc["total_amount"],
            "detail_total_amount": voucher_calc["total_amount"],
            "vouchers": [v.to_dict() for v in vouchers],
        }

    def list_vouchers(self, period: str) -> List[Dict[str, Any]]:
        vouchers = self.store.load_vouchers(period)
        return [v.to_dict() for v in vouchers]

    def approve_voucher(
        self, period: str, voucher_id: str, operator: str = "system"
    ) -> Dict[str, Any]:
        vouchers = self.store.load_vouchers(period)
        for v in vouchers:
            if v.voucher_id == voucher_id:
                if v.status != VoucherStatus.CREATED:
                    return {"success": False, "message": f"凭证状态为 {v.status.value}，无法审批"}
                v.status = VoucherStatus.APPROVED
                v.approved_by = operator
                v.approved_at = datetime.now().isoformat(timespec="seconds")
                self.store.save_vouchers(period, vouchers)
                return {"success": True, "voucher": v.to_dict()}
        return {"success": False, "message": f"未找到凭证 {voucher_id}"}

    def mark_paid(
        self, period: str, voucher_id: str, operator: str = "system"
    ) -> Dict[str, Any]:
        vouchers = self.store.load_vouchers(period)
        for v in vouchers:
            if v.voucher_id == voucher_id:
                if v.status != VoucherStatus.APPROVED:
                    return {"success": False, "message": f"凭证状态为 {v.status.value}，需先审批"}
                v.status = VoucherStatus.PAID
                v.paid_at = datetime.now().isoformat(timespec="seconds")
                self.store.save_vouchers(period, vouchers)
                return {"success": True, "voucher": v.to_dict()}
        return {"success": False, "message": f"未找到凭证 {voucher_id}"}

    def export_vouchers_csv(self, period: str, output_path: str) -> Dict[str, Any]:
        vouchers = self.store.load_vouchers(period)
        if not vouchers:
            return {"success": False, "message": "未找到凭证数据"}
        total_cents = 0
        for v in vouchers:
            total_cents += int(round(v.total_amount * 100))
        total_amount = round(total_cents / 100, 2)
        self.store.save_vouchers_csv(period, vouchers, output_path)
        return {"success": True, "path": output_path, "count": len(vouchers), "total_amount": total_amount}
