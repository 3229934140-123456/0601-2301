"""试算校验模块 - 核心分账计算引擎"""

from typing import List, Dict, Any, Tuple
from datetime import datetime
from collections import defaultdict

from .models import (
    Order, SplitRule, SplitDetail, SplitException,
    TrialRecord, SettlementPeriod,
    SplitRole, ExceptionType, ExceptionStatus,
    ConfirmStatus, generate_id
)
from .storage import DataStore
from .rule_manager import RuleManager


class SplitCalculator:
    """分账计算器"""

    def __init__(self, store: DataStore):
        self.store = store
        self.rule_manager = RuleManager(store)

    def calculate_order_split(
        self, order: Order, rule: SplitRule
    ) -> Tuple[List[SplitDetail], List[str]]:
        """对单个订单进行分账计算"""
        details: List[SplitDetail] = []
        remarks: List[str] = []

        net_amount = order.net_amount
        if net_amount <= 0:
            return details, ["订单净金额为0，跳过分账"]

        roles_config = [
            (SplitRole.PROVIDER, rule.provider_rate, order.provider_id, order.provider_name),
            (SplitRole.CHANNEL, rule.channel_rate, order.channel_id, order.channel_name),
            (SplitRole.SERVICE, rule.service_rate, order.service_id, order.service_name),
        ]

        raw_amounts = {}
        for role, rate, org_id, org_name in roles_config:
            raw_amounts[role] = net_amount * rate

        total_raw = sum(raw_amounts.values())
        if rule.cap_amount is not None and total_raw > rule.cap_amount:
            scale = rule.cap_amount / total_raw
            remarks.append(f"触发封顶: 原始合计{total_raw:.2f} > 封顶{rule.cap_amount:.2f}，按比例缩放")
            for role in raw_amounts:
                raw_amounts[role] *= scale
        elif rule.floor_amount is not None and total_raw < rule.floor_amount:
            scale = rule.floor_amount / total_raw
            remarks.append(f"触发保底: 原始合计{total_raw:.2f} < 保底{rule.floor_amount:.2f}，按比例缩放")
            for role in raw_amounts:
                raw_amounts[role] *= scale

        if order.refund_amount > 0 or order.discount_amount > 0:
            refund_note = []
            if order.refund_amount > 0:
                refund_note.append(f"退款{order.refund_amount:.2f}")
            if order.discount_amount > 0:
                refund_note.append(f"折让{order.discount_amount:.2f}")
            remarks.append("已扣除: " + ", ".join(refund_note))

        for role, rate, org_id, org_name in roles_config:
            detail = SplitDetail(
                detail_id=generate_id("SD"),
                order_id=order.order_id,
                product_code=order.product_code,
                role=role,
                org_id=org_id,
                org_name=org_name,
                order_amount=order.order_amount,
                net_amount=net_amount,
                rate=rate,
                raw_amount=round(net_amount * rate, 2),
                final_amount=round(raw_amounts[role], 2),
                period=order.period,
                remark="; ".join(remarks) if remarks else "",
            )
            details.append(detail)

        return details, remarks

    def run_trial(self, period: str) -> TrialRecord:
        """执行试算"""
        orders = self.store.load_orders(period)
        if not orders:
            raise ValueError(f"结算周期 {period} 没有订单数据")

        details: List[SplitDetail] = []
        exceptions: List[SplitException] = []
        existing_exceptions = self.store.load_exceptions(period)
        open_exception_orders = set()
        open_exceptions_list = []
        for e in existing_exceptions:
            if e.status == ExceptionStatus.OPEN:
                open_exception_orders.add(e.order_id)
                open_exceptions_list.append(e)

        total_amount = 0.0
        provider_total = 0.0
        channel_total = 0.0
        service_total = 0.0

        missing_products = set()

        for order in orders:
            if order.order_id in open_exception_orders:
                continue

            if order.status in ("refunded", "cancelled") and order.net_amount <= 0:
                continue

            rule = self.rule_manager.get_rule(order.product_code)
            if not rule:
                missing_products.add(order.product_code)
                exceptions.append(SplitException(
                    exception_id=generate_id("EXC"),
                    order_id=order.order_id,
                    exception_type=ExceptionType.MISSING_RULE,
                    description=f"产品 {order.product_code} 未配置分成规则",
                    created_at=datetime.now().isoformat(timespec="seconds"),
                    period=period,
                ))
                continue

            order_details, _ = self.calculate_order_split(order, rule)
            details.extend(order_details)

            total_amount += order.net_amount
            for d in order_details:
                if d.role == SplitRole.PROVIDER:
                    provider_total += d.final_amount
                elif d.role == SplitRole.CHANNEL:
                    channel_total += d.final_amount
                elif d.role == SplitRole.SERVICE:
                    service_total += d.final_amount

        all_exceptions = open_exceptions_list + exceptions

        trial = TrialRecord(
            trial_id=generate_id("TR"),
            period=period,
            created_at=datetime.now().isoformat(timespec="seconds"),
            order_count=len([o for o in orders if o.order_id not in open_exception_orders and o.product_code not in missing_products]),
            detail_count=len(details),
            exception_count=len(all_exceptions),
            total_amount=round(total_amount, 2),
            provider_total=round(provider_total, 2),
            channel_total=round(channel_total, 2),
            service_total=round(service_total, 2),
            details=details,
            exceptions=all_exceptions,
        )

        self.store.save_trial(trial)

        period_info = self.store.load_period(period)
        if not period_info:
            period_info = SettlementPeriod(period=period)
        period_info.trial_count += 1
        period_info.last_trial_at = trial.created_at
        self.store.save_period(period_info)

        return trial

    def generate_trial_summary(self, trial: TrialRecord) -> Dict[str, Any]:
        """生成试算摘要"""
        org_summary = defaultdict(lambda: {
            "order_count": 0, "order_amount": 0.0,
            "net_amount": 0.0, "split_amount": 0.0,
            "rates": []
        })

        for d in trial.details:
            key = (d.role.value, d.org_id)
            s = org_summary[key]
            s["order_count"] += 1
            s["order_amount"] += d.order_amount
            s["net_amount"] += d.net_amount
            s["split_amount"] += d.final_amount
            s["rates"].append(d.rate)
            s["org_name"] = d.org_name
            s["role"] = d.role.value

        summary_list = []
        for (role, org_id), s in org_summary.items():
            avg_rate = sum(s["rates"]) / len(s["rates"]) if s["rates"] else 0
            summary_list.append({
                "role": role,
                "org_id": org_id,
                "org_name": s.get("org_name", ""),
                "order_count": s["order_count"],
                "order_amount": round(s["order_amount"], 2),
                "net_amount": round(s["net_amount"], 2),
                "split_amount": round(s["split_amount"], 2),
                "avg_rate": round(avg_rate, 4),
            })

        exc_by_type = defaultdict(int)
        for e in trial.exceptions:
            exc_by_type[e.exception_type.value] += 1

        return {
            "trial_id": trial.trial_id,
            "created_at": trial.created_at,
            "order_count": trial.order_count,
            "detail_count": trial.detail_count,
            "exception_count": trial.exception_count,
            "total_amount": trial.total_amount,
            "provider_total": trial.provider_total,
            "channel_total": trial.channel_total,
            "service_total": trial.service_total,
            "diff": round(trial.total_amount - (trial.provider_total + trial.channel_total + trial.service_total), 2),
            "org_summary": sorted(summary_list, key=lambda x: (x["role"], x["org_id"])),
            "exceptions_by_type": dict(exc_by_type),
        }
