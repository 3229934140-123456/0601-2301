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
        """对单个订单进行分账计算，确保三方合计精确等于净金额（尾差分摊到服务方）"""
        details: List[SplitDetail] = []
        remarks: List[str] = []

        net_amount = order.net_amount
        if net_amount <= 0:
            return details, ["订单净金额为0，跳过分账"]

        net_cents = int(round(net_amount * 100))

        roles_config = [
            (SplitRole.PROVIDER, rule.provider_rate, order.provider_id, order.provider_name),
            (SplitRole.CHANNEL, rule.channel_rate, order.channel_id, order.channel_name),
            (SplitRole.SERVICE, rule.service_rate, order.service_id, order.service_name),
        ]

        raw_amounts = {}
        for role, rate, org_id, org_name in roles_config:
            raw_amounts[role] = net_amount * rate

        total_raw = sum(raw_amounts.values())
        scaled = {}
        if rule.cap_amount is not None and total_raw > rule.cap_amount:
            scale = rule.cap_amount / total_raw
            remarks.append(f"触发封顶: 原始合计{total_raw:.2f} > 封顶{rule.cap_amount:.2f}，按比例缩放")
            for role in raw_amounts:
                scaled[role] = raw_amounts[role] * scale
        elif rule.floor_amount is not None and total_raw < rule.floor_amount:
            scale = rule.floor_amount / total_raw
            remarks.append(f"触发保底: 原始合计{total_raw:.2f} < 保底{rule.floor_amount:.2f}，按比例缩放")
            for role in raw_amounts:
                scaled[role] = raw_amounts[role] * scale
        else:
            scaled = dict(raw_amounts)

        if order.refund_amount > 0 or order.discount_amount > 0:
            refund_note = []
            if order.refund_amount > 0:
                refund_note.append(f"退款{order.refund_amount:.2f}")
            if order.discount_amount > 0:
                refund_note.append(f"折让{order.discount_amount:.2f}")
            remarks.append("已扣除: " + ", ".join(refund_note))

        cents = {}
        assigned_cents = 0
        service_role = SplitRole.SERVICE
        for idx, (role, rate, org_id, org_name) in enumerate(roles_config):
            if role == service_role:
                continue
            c = int(round(scaled[role] * 100))
            cents[role] = c
            assigned_cents += c

        cents[service_role] = net_cents - assigned_cents
        if abs(cents[service_role] - int(round(scaled[service_role] * 100))) > 1:
            pass

        total_cents_check = sum(cents.values())
        if total_cents_check != net_cents:
            diff = net_cents - total_cents_check
            cents[service_role] += diff
            remarks.append(f"尾差调整{diff / 100:.2f}元至服务方")

        for role, rate, org_id, org_name in roles_config:
            final_amt = round(cents[role] / 100, 2)
            raw_amt = round(net_amount * rate, 2)
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
                raw_amount=raw_amt,
                final_amount=final_amt,
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
        ignored_exception_orders = set()
        open_exceptions_list = []
        existing_ids = {e.exception_id for e in existing_exceptions}
        for e in existing_exceptions:
            if e.status == ExceptionStatus.OPEN:
                open_exception_orders.add(e.order_id)
                open_exceptions_list.append(e)
            elif e.status == ExceptionStatus.IGNORED:
                ignored_exception_orders.add(e.order_id)

        total_amount = 0.0
        provider_total = 0.0
        channel_total = 0.0
        service_total = 0.0

        excluded_orders = []

        for order in orders:
            if order.order_id in open_exception_orders:
                exc_for_order = [e for e in open_exceptions_list if e.order_id == order.order_id]
                reasons = "; ".join(e.description for e in exc_for_order)
                excluded_orders.append({
                    "order_id": order.order_id,
                    "reason": reasons or "存在未处理异常",
                })
                continue

            if order.order_id in ignored_exception_orders:
                excluded_orders.append({
                    "order_id": order.order_id,
                    "reason": "异常已忽略，不参与分账",
                })
                continue

            if order.status in ("refunded", "cancelled") and order.net_amount <= 0:
                excluded_orders.append({
                    "order_id": order.order_id,
                    "reason": f"订单已{order.status.value}且净金额为0",
                })
                continue

            if order.order_amount <= 0 or order.net_amount <= 0:
                excluded_orders.append({
                    "order_id": order.order_id,
                    "reason": f"订单金额无效: 订单金额={order.order_amount}, 净金额={order.net_amount}",
                })
                continue

            if not order.provider_id or not order.channel_id or not order.service_id:
                missing = []
                if not order.provider_id:
                    missing.append("提供方ID")
                if not order.channel_id:
                    missing.append("渠道方ID")
                if not order.service_id:
                    missing.append("服务方ID")
                excluded_orders.append({
                    "order_id": order.order_id,
                    "reason": f"机构信息缺失: {', '.join(missing)}",
                })
                continue

            rule = self.rule_manager.get_rule(order.product_code)
            if not rule:
                exc_id = generate_id("EXC")
                exc = SplitException(
                    exception_id=exc_id,
                    order_id=order.order_id,
                    exception_type=ExceptionType.MISSING_RULE,
                    description=f"产品 {order.product_code} 未配置分成规则",
                    created_at=datetime.now().isoformat(timespec="seconds"),
                    period=period,
                )
                exceptions.append(exc)
                excluded_orders.append({
                    "order_id": order.order_id,
                    "reason": exc.description,
                })
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

        if exceptions:
            merged = list(existing_exceptions)
            for e in exceptions:
                if e.exception_id not in existing_ids:
                    merged.append(e)
            self.store.save_exceptions(period, merged)

        trial = TrialRecord(
            trial_id=generate_id("TR"),
            period=period,
            created_at=datetime.now().isoformat(timespec="seconds"),
            order_count=len(orders) - len(excluded_orders),
            detail_count=len(details),
            exception_count=len([e for e in all_exceptions if e.status == ExceptionStatus.OPEN]),
            total_amount=round(total_amount, 2),
            provider_total=round(provider_total, 2),
            channel_total=round(channel_total, 2),
            service_total=round(service_total, 2),
            details=details,
            exceptions=all_exceptions,
        )
        trial.excluded_orders = excluded_orders

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
        open_exc_detail = []
        for e in trial.exceptions:
            if e.status == ExceptionStatus.OPEN:
                exc_by_type[e.exception_type.value] += 1
                open_exc_detail.append({
                    "exception_id": e.exception_id,
                    "order_id": e.order_id,
                    "exception_type": e.exception_type.value,
                    "description": e.description,
                })

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
            "open_exceptions": open_exc_detail,
            "excluded_orders": getattr(trial, "excluded_orders", []),
        }


def recalc_from_details(details: List[SplitDetail]) -> Dict[str, Any]:
    """从分账明细使用分精度重算合计，保证所有环节口径一致（无浮点误差）"""
    from collections import defaultdict
    from .models import SplitRole

    order_ids = set()
    total_cents = 0
    provider_cents = 0
    channel_cents = 0
    service_cents = 0
    order_net_cents = 0

    org_cents = defaultdict(int)
    org_order_count = defaultdict(set)
    org_meta = {}

    for d in details:
        fc = int(round(d.final_amount * 100))
        nc = int(round(d.net_amount * 100))
        oc = int(round(d.order_amount * 100))

        if d.role == SplitRole.PROVIDER:
            provider_cents += fc
            order_ids.add(d.order_id)
            order_net_cents += nc
            total_cents += fc
        elif d.role == SplitRole.CHANNEL:
            channel_cents += fc
            total_cents += fc
        elif d.role == SplitRole.SERVICE:
            service_cents += fc
            total_cents += fc

        key = (d.role.value, d.org_id)
        org_cents[key] += fc
        org_order_count[key].add(d.order_id)
        org_meta[key] = {
            "org_name": d.org_name,
            "role": d.role.value,
            "org_id": d.org_id,
        }

    org_summary = []
    for key, meta in org_meta.items():
        order_amount_cents = 0
        net_amount_cents = 0
        rates = []
        for d in details:
            if (d.role.value, d.org_id) == key:
                order_amount_cents += int(round(d.order_amount * 100))
                net_amount_cents += int(round(d.net_amount * 100))
                rates.append(d.rate)
        avg_rate = sum(rates) / len(rates) if rates else 0
        org_summary.append({
            "role": meta["role"],
            "org_id": meta["org_id"],
            "org_name": meta["org_name"],
            "order_count": len(org_order_count[key]),
            "order_amount": round(order_amount_cents / 100, 2),
            "net_amount": round(net_amount_cents / 100, 2),
            "split_amount": round(org_cents[key] / 100, 2),
            "avg_rate": round(avg_rate, 4),
        })

    three_total_cents = provider_cents + channel_cents + service_cents
    return {
        "detail_count": len(details),
        "order_count": len(order_ids),
        "total_amount": round(order_net_cents / 100, 2),
        "provider_total": round(provider_cents / 100, 2),
        "channel_total": round(channel_cents / 100, 2),
        "service_total": round(service_cents / 100, 2),
        "three_total": round(three_total_cents / 100, 2),
        "diff": round((order_net_cents - three_total_cents) / 100, 2),
        "org_summary": sorted(org_summary, key=lambda x: (x["role"], x["org_id"])),
    }


def recalc_voucher_from_details(details: List[SplitDetail]) -> Dict[str, Any]:
    """从已确认明细用分精度重算凭证汇总（和recalc_from_details同一口径）"""
    from collections import defaultdict
    from .models import SplitRole

    grouped_cents = defaultdict(int)
    grouped_order_ids = defaultdict(set)
    grouped_meta = {}
    total_cents = 0

    for d in details:
        if not d.org_id:
            continue
        key = (d.role, d.org_id)
        fc = int(round(d.final_amount * 100))
        grouped_cents[key] += fc
        grouped_order_ids[key].add(d.order_id)
        grouped_meta[key] = {
            "role": d.role,
            "org_id": d.org_id,
            "org_name": d.org_name,
        }
        total_cents += fc

    vouchers_data = []
    for key, meta in grouped_meta.items():
        vouchers_data.append({
            "role": meta["role"],
            "org_id": meta["org_id"],
            "org_name": meta["org_name"],
            "order_count": len(grouped_order_ids[key]),
            "total_amount": round(grouped_cents[key] / 100, 2),
        })

    return {
        "voucher_count": len(vouchers_data),
        "total_amount": round(total_cents / 100, 2),
        "vouchers": sorted(vouchers_data, key=lambda x: (x["role"].value, x["org_id"])),
    }
