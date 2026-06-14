"""凭证生成模块"""

from typing import List, Dict, Any
from datetime import datetime
from collections import defaultdict

from .models import (
    PaymentVoucher, SplitDetail, SplitRole,
    ConfirmStatus, VoucherStatus, VoucherAdjustment,
    AdjustmentType, generate_id
)
from .storage import DataStore
from .trial_calculator import recalc_voucher_from_details
from .audit import write_audit


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

        write_audit(
            self.store, period, "gen_voucher", operator=operator,
            detail=f"生成{len(vouchers)}张凭证，合计{voucher_calc['total_amount']}",
            order_count=len(details),
            amount=voucher_calc["total_amount"],
            extra={"voucher_count": len(vouchers)},
        )

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

        write_audit(
            self.store, period, "export_vouchers", operator="system",
            detail=f"导出凭证CSV到 {output_path}，{len(vouchers)}条，合计{total_amount}",
            amount=total_amount,
            extra={"path": output_path, "count": len(vouchers)},
        )

        return {"success": True, "path": output_path, "count": len(vouchers), "total_amount": total_amount}

    def reverse_and_reissue(
        self, period: str, reason: str = "", operator: str = "system"
    ) -> Dict[str, Any]:
        """冲销原凭证并按最新确认结果重开

        流程:
        1. 将所有未付款(非paid)凭证标记为reversed
        2. 为每张原凭证生成一条反向调整记录
        3. 按最新确认明细重新生成凭证
        4. 为每张新凭证生成一条重开调整记录
        5. 对账时原凭证+冲销+重开金额一起展示
        """
        vouchers = self.store.load_vouchers(period)
        if not vouchers:
            return {"success": False, "message": "未找到凭证数据"}

        paid_vouchers = [v for v in vouchers if v.status == VoucherStatus.PAID]
        if paid_vouchers:
            return {
                "success": False,
                "message": f"存在 {len(paid_vouchers)} 张已付款凭证（{', '.join(v.voucher_id for v in paid_vouchers[:3])}），已付款凭证不能冲销，请先处理付款回退",
            }

        details = self.store.load_confirmed_details(period)
        if not details:
            return {"success": False, "message": "未找到已确认的分账明细，请先确认分账"}

        adjustments: List[VoucherAdjustment] = []
        reversal_total = 0.0
        now = datetime.now().isoformat(timespec="seconds")

        for v in vouchers:
            v.status = VoucherStatus.REVERSED
            v.remark = f"冲销，原因: {reason or '无'}，操作人: {operator}"
            reversal_total += v.total_amount

            adj = VoucherAdjustment(
                adjustment_id=generate_id("ADJ"),
                period=period,
                adjustment_type=AdjustmentType.REVERSAL,
                original_voucher_id=v.voucher_id,
                org_id=v.org_id,
                org_name=v.org_name,
                role=v.role,
                amount=-v.total_amount,
                operator=operator,
                created_at=now,
                reason=reason or "冲销原凭证",
            )
            adjustments.append(adj)

        self.store.save_vouchers(period, vouchers)

        voucher_calc = recalc_voucher_from_details(details)
        new_vouchers: List[PaymentVoucher] = []
        reissue_total = 0.0
        for v in voucher_calc["vouchers"]:
            nv = PaymentVoucher(
                voucher_id=generate_id("PV"),
                period=period,
                role=v["role"],
                org_id=v["org_id"],
                org_name=v["org_name"],
                total_amount=v["total_amount"],
                order_count=v["order_count"],
                status=VoucherStatus.CREATED,
                created_at=now,
                remark=f"冲销重开，操作人: {operator}，原因: {reason or '无'}",
            )
            new_vouchers.append(nv)
            reissue_total += v["total_amount"]

            adj = VoucherAdjustment(
                adjustment_id=generate_id("ADJ"),
                period=period,
                adjustment_type=AdjustmentType.REISSUE,
                original_voucher_id="",
                new_voucher_id=nv.voucher_id,
                org_id=v["org_id"],
                org_name=v["org_name"],
                role=v["role"],
                amount=v["total_amount"],
                operator=operator,
                created_at=now,
                reason=f"重开凭证，原因: {reason or '无'}",
            )
            adjustments.append(adj)

        all_vouchers = vouchers + new_vouchers
        self.store.save_vouchers(period, all_vouchers)
        self.store.append_adjustments(period, adjustments)

        write_audit(
            self.store, period, "reverse_and_reissue", operator=operator,
            detail=f"冲销{len(vouchers)}张凭证(合计{round(reversal_total, 2)})，重开{len(new_vouchers)}张凭证(合计{round(reissue_total, 2)})，原因: {reason or '无'}",
            amount=round(reissue_total, 2),
            extra={
                "reversed_count": len(vouchers),
                "reversal_total": round(reversal_total, 2),
                "reissued_count": len(new_vouchers),
                "reissue_total": round(reissue_total, 2),
            },
        )

        return {
            "success": True,
            "period": period,
            "reversed_count": len(vouchers),
            "reversal_total": round(reversal_total, 2),
            "reissued_count": len(new_vouchers),
            "reissue_total": round(reissue_total, 2),
            "adjustment_count": len(adjustments),
            "new_vouchers": [v.to_dict() for v in new_vouchers],
        }

    def list_adjustments(self, period: str) -> List[Dict[str, Any]]:
        adjustments = self.store.load_adjustments(period)
        return [a.to_dict() for a in adjustments]
