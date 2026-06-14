"""分账确认模块"""

from typing import List, Dict, Any, Optional
from datetime import datetime

from .models import (
    SettlementPeriod, SplitDetail, ConfirmStatus,
    ExceptionStatus
)
from .storage import DataStore
from .exception_handler import ExceptionHandler
from .trial_calculator import recalc_from_details
from .audit import write_audit
import hashlib


class ConfirmManager:
    """分账确认管理器"""

    def __init__(self, store: DataStore):
        self.store = store
        self.exception_handler = ExceptionHandler(store)

    def get_period_status(self, period: str) -> Optional[SettlementPeriod]:
        return self.store.load_period(period)

    def can_confirm(self, period: str) -> Dict[str, Any]:
        period_info = self.store.load_period(period)
        if not period_info:
            return {"can_confirm": False, "reason": f"结算周期 {period} 不存在，请先执行试算"}

        if period_info.status == ConfirmStatus.LOCKED:
            return {"can_confirm": False, "reason": "结算周期已锁定"}

        if period_info.status == ConfirmStatus.CONFIRMED:
            return {"can_confirm": False, "reason": "分账已确认"}

        open_count = self.exception_handler.get_open_exception_count(period)
        if open_count > 0:
            return {
                "can_confirm": False,
                "reason": f"存在 {open_count} 条未处理异常，请先处理",
                "open_exception_count": open_count
            }

        trial = self.store.get_latest_trial(period)
        if not trial:
            return {"can_confirm": False, "reason": "未找到试算记录，请先执行试算"}

        return {"can_confirm": True, "trial": trial.to_dict()}

    def confirm_split(self, period: str, operator: str = "system") -> Dict[str, Any]:
        check = self.can_confirm(period)
        if not check["can_confirm"]:
            return {"success": False, "message": check["reason"]}

        trial = self.store.get_latest_trial(period)
        if not trial:
            return {"success": False, "message": "未找到试算记录"}

        self.store.save_confirmed_details(period, trial.details)

        calc = recalc_from_details(trial.details)

        fingerprint = self._compute_amount_fingerprint(trial.details)

        period_info = self.store.load_period(period)
        if not period_info:
            period_info = SettlementPeriod(period=period)
        period_info.status = ConfirmStatus.CONFIRMED
        period_info.confirmed_by = operator
        period_info.confirmed_at = datetime.now().isoformat(timespec="seconds")
        period_info.confirmed_trial_id = trial.trial_id
        period_info.confirmed_amount_fingerprint = fingerprint
        self.store.save_period(period_info)

        write_audit(
            self.store, period, "confirm_split", operator=operator,
            detail=f"确认分账，试算ID={trial.trial_id}，金额指纹={fingerprint[:8]}...，{calc['order_count']}笔订单，三方合计{calc['three_total']}",
            order_count=calc["order_count"],
            amount=calc["three_total"],
            extra={
                "trial_id": trial.trial_id,
                "amount_fingerprint": fingerprint,
                "provider_total": calc["provider_total"],
                "channel_total": calc["channel_total"],
                "service_total": calc["service_total"],
            },
        )

        return {
            "success": True,
            "period": period,
            "detail_count": calc["detail_count"],
            "order_count": calc["order_count"],
            "total_amount": calc["total_amount"],
            "provider_total": calc["provider_total"],
            "channel_total": calc["channel_total"],
            "service_total": calc["service_total"],
            "three_total": calc["three_total"],
            "diff": calc["diff"],
            "confirmed_trial_id": trial.trial_id,
            "amount_fingerprint": fingerprint,
        }

    @staticmethod
    def _compute_amount_fingerprint(details) -> str:
        amounts = []
        for d in sorted(details, key=lambda x: (x.order_id, x.role.value)):
            amounts.append(f"{d.order_id}:{d.role.value}:{int(round(d.final_amount * 100))}")
        raw = "|".join(amounts)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def can_lock(self, period: str) -> Dict[str, Any]:
        """锁定前的一致性检查

        锁定条件（全部满足）:
        1. 状态为已确认 (CONFIRMED)
        2. 没有待处理异常 (open_exception_count = 0)
        3. 确认时的试算批次ID = 最新试算批次ID（新增规则/订单后会变）
        4. 确认时的金额指纹 = 最新试算的金额指纹（兜底检查）
        5. 最新试算的订单集合必须 = 已确认的订单集合
        """
        period_info = self.store.load_period(period)
        if not period_info:
            return {"can_lock": False, "reason": f"结算周期 {period} 不存在"}

        if period_info.status != ConfirmStatus.CONFIRMED:
            if period_info.status == ConfirmStatus.LOCKED:
                return {"can_lock": False, "reason": "结算周期已锁定"}
            if period_info.status == ConfirmStatus.DRAFT:
                return {"can_lock": False, "reason": "分账尚未确认，请先执行 confirm-split --confirm"}
            return {"can_lock": False, "reason": f"只有已确认的分账才能锁定（当前状态: {period_info.status.value}）"}

        open_count = self.exception_handler.get_open_exception_count(period)
        if open_count > 0:
            return {
                "can_lock": False,
                "reason": f"发现 {open_count} 条未处理异常（可能是确认后又导入了新订单或补了规则），请先 handle-exception 处理并重新 trial-calc → confirm-split 后再锁定",
                "open_exception_count": open_count,
            }

        latest_trial = self.store.get_latest_trial(period)
        if not latest_trial:
            return {"can_lock": False, "reason": "未找到试算记录，请先重新执行 trial-calc 和 confirm-split"}

        if period_info.confirmed_trial_id and latest_trial.trial_id != period_info.confirmed_trial_id:
            confirmed_details = self.store.load_confirmed_details(period)
            confirmed_order_ids = {d.order_id for d in confirmed_details} if confirmed_details else set()
            trial_order_ids = {d.order_id for d in latest_trial.details} if latest_trial.details else set()
            new_in_trial = trial_order_ids - confirmed_order_ids
            removed_from_trial = confirmed_order_ids - trial_order_ids
            extra = []
            if new_in_trial:
                extra.append(f"新增订单 {len(new_in_trial)} 笔: {', '.join(sorted(new_in_trial)[:3])}{'...' if len(new_in_trial) > 3 else ''}")
            if removed_from_trial:
                extra.append(f"订单消失 {len(removed_from_trial)} 笔: {', '.join(sorted(removed_from_trial)[:3])}{'...' if len(removed_from_trial) > 3 else ''}")
            current_fingerprint = self._compute_amount_fingerprint(latest_trial.details)
            if current_fingerprint != period_info.confirmed_amount_fingerprint:
                extra.append("金额指纹不一致（规则或订单金额已变动）")
            return {
                "can_lock": False,
                "reason": f"确认时的试算批次({period_info.confirmed_trial_id})与最新试算批次({latest_trial.trial_id})不一致，可能是补了规则或修改了订单。{'，'.join(extra)}。请重新执行 confirm-split --confirm 后再锁定。",
            }

        current_fingerprint = self._compute_amount_fingerprint(latest_trial.details)
        if period_info.confirmed_amount_fingerprint and current_fingerprint != period_info.confirmed_amount_fingerprint:
            return {
                "can_lock": False,
                "reason": "金额指纹不一致（确认后的分账金额已变动），请重新执行 confirm-split --confirm 后再锁定",
            }

        confirmed_details = self.store.load_confirmed_details(period)
        confirmed_order_ids = {d.order_id for d in confirmed_details} if confirmed_details else set()
        trial_order_ids = {d.order_id for d in latest_trial.details} if latest_trial.details else set()

        if confirmed_order_ids != trial_order_ids:
            return {
                "can_lock": False,
                "reason": f"订单集不一致（已确认 {len(confirmed_order_ids)} 笔 vs 最新试算 {len(trial_order_ids)} 笔），请重新执行 trial-calc 和 confirm-split 后再锁定",
            }

        return {"can_lock": True}

    def lock_period(self, period: str, operator: str = "system") -> Dict[str, Any]:
        check = self.can_lock(period)
        if not check["can_lock"]:
            return {"success": False, "message": check["reason"]}

        period_info = self.store.load_period(period)
        if not period_info:
            return {"success": False, "message": f"结算周期 {period} 不存在"}

        details = self.store.load_confirmed_details(period)
        calc = recalc_from_details(details) if details else {}

        period_info.status = ConfirmStatus.LOCKED
        period_info.locked_at = datetime.now().isoformat(timespec="seconds")
        period_info.locked_by = operator
        self.store.save_period(period_info)

        write_audit(
            self.store, period, "lock_period", operator=operator,
            detail=f"锁定结算周期，{calc.get('order_count', 0)}笔订单，三方合计{calc.get('three_total', 0)}",
            order_count=calc.get("order_count", 0),
            amount=calc.get("three_total", 0),
            extra={
                "trial_id": period_info.confirmed_trial_id,
                "amount_fingerprint": period_info.confirmed_amount_fingerprint[:8] + "...",
            },
        )

        return {
            "success": True,
            "period": period,
            "status": "locked",
            "locked_at": period_info.locked_at,
            "reconcile": {
                "detail_count": calc.get("detail_count", 0),
                "order_count": calc.get("order_count", 0),
                "total_amount": calc.get("total_amount", 0),
                "three_total": calc.get("three_total", 0),
                "diff": calc.get("diff", 0),
            },
        }

    def reset_to_draft(self, period: str, operator: str = "system") -> Dict[str, Any]:
        period_info = self.store.load_period(period)
        if not period_info:
            return {"success": False, "message": f"结算周期 {period} 不存在"}

        if period_info.status == ConfirmStatus.LOCKED:
            return {"success": False, "message": "已锁定的周期无法重置"}

        old_status = period_info.status.value
        period_info.status = ConfirmStatus.DRAFT
        period_info.confirmed_by = ""
        period_info.confirmed_at = ""
        period_info.confirmed_trial_id = ""
        period_info.confirmed_amount_fingerprint = ""
        self.store.save_period(period_info)

        write_audit(
            self.store, period, "reset_to_draft", operator=operator,
            detail=f"重置周期状态从 {old_status} 到 draft",
            extra={"old_status": old_status},
        )

        return {"success": True, "period": period, "status": "draft"}

    def list_all_periods(self) -> List[Dict[str, Any]]:
        periods = self.store.load_all_periods()
        result = []
        for p in periods:
            d = p.to_dict()
            trial = self.store.get_latest_trial(p.period)
            if trial:
                d["last_trial_id"] = trial.trial_id
                d["last_trial_amount"] = trial.total_amount
            d["open_exception_count"] = self.exception_handler.get_open_exception_count(p.period)
            result.append(d)
        return result
