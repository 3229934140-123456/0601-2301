"""试算校验模块 - 核心分账计算引擎"""

from typing import List, Dict, Any, Tuple, Optional
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
        """对单个订单进行分账计算

        关键口径：
        - 无封顶保底：三方合计 ≡ 订单净金额（尾差分摊到服务方）
        - 触发封顶：三方合计 ≡ 封顶金额 cap_amount（尾差分摊到服务方）
        - 触发保底：三方合计 ≡ 保底金额 floor_amount（尾差分摊到服务方），且每方金额≥0
        """
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
        cap_applied = False
        floor_applied = False
        target_cents = net_cents

        if rule.cap_amount is not None and total_raw > rule.cap_amount:
            cap_cents = int(round(rule.cap_amount * 100))
            scale = rule.cap_amount / total_raw
            cap_applied = True
            target_cents = cap_cents
            remarks.append(f"触发封顶: 原始合计{total_raw:.2f} > 封顶{rule.cap_amount:.2f}，按比例缩放，三方合计=封顶值")
            for role in raw_amounts:
                scaled[role] = raw_amounts[role] * scale
        elif rule.floor_amount is not None and total_raw < rule.floor_amount:
            floor_cents = int(round(rule.floor_amount * 100))
            scale = rule.floor_amount / total_raw
            floor_applied = True
            target_cents = floor_cents
            remarks.append(f"触发保底: 原始合计{total_raw:.2f} < 保底{rule.floor_amount:.2f}，按比例缩放，三方合计=保底值")
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
            if floor_applied and c < 0:
                c = 0
            cents[role] = c
            assigned_cents += c

        cents[service_role] = target_cents - assigned_cents

        if floor_applied and cents[service_role] < 0:
            cents[service_role] = 0
            diff = target_cents - assigned_cents
            if diff > 0:
                cents[service_role] = diff
            if diff < 0:
                pass

        total_cents_check = sum(cents.values())
        if total_cents_check != target_cents:
            diff = target_cents - total_cents_check
            cents[service_role] += diff
            remarks.append(f"尾差调整{diff / 100:.2f}元至服务方")

        if floor_applied:
            for role in cents:
                if cents[role] < 0:
                    negative_cents = cents[role]
                    cents[role] = 0
                    cents[service_role] += negative_cents
                    remarks.append(f"{role.value}负分成归零，差额{negative_cents / 100:.2f}元调整至服务方")

        cap_flag = 1 if cap_applied else 0
        floor_flag = 1 if floor_applied else 0

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
                cap_applied=cap_flag,
                floor_applied=floor_flag,
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

        cap_orders = set()
        cap_amount_cents = 0
        floor_orders = set()
        floor_amount_cents = 0
        for d in details:
            if d.cap_applied:
                cap_orders.add(d.order_id)
                cap_amount_cents += int(round(d.final_amount * 100))
            if d.floor_applied:
                floor_orders.add(d.order_id)
                floor_amount_cents += int(round(d.final_amount * 100))

        cap_stats = {
            "order_count": len(cap_orders),
            "detail_count": len([d for d in details if d.cap_applied]),
            "total_amount": round(cap_amount_cents / 100, 2),
            "order_ids": sorted(cap_orders),
        }
        floor_stats = {
            "order_count": len(floor_orders),
            "detail_count": len([d for d in details if d.floor_applied]),
            "total_amount": round(floor_amount_cents / 100, 2),
            "order_ids": sorted(floor_orders),
        }

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
            cap_stats=cap_stats,
            floor_stats=floor_stats,
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
            "cap_stats": getattr(trial, "cap_stats", {}),
            "floor_stats": getattr(trial, "floor_stats", {}),
        }


def reconcile_period(store: DataStore, period: str, export_file: Optional[str] = None) -> Dict[str, Any]:
    """结算周期对账检查：对比订单净额、确认明细、凭证、导出文件4类金额

    返回结构:
    - items: 各对账项的金额表 [{"name", "amount", "source"}]
    - diffs: 差异列表 [{"name1", "amount1", "name2", "amount2", "diff", "involved_orders", "involved_orgs", "is_expected"}]
    - ok: 是否完全一致（或只有预期的封顶保底差异）
    - next_action: 下一步建议
    """
    from .models import ConfirmStatus
    from .confirm_manager import ConfirmManager
    from .voucher_generator import VoucherGenerator
    import csv

    orders = store.load_orders(period)
    confirmed_details = store.load_confirmed_details(period)
    vouchers = store.load_vouchers(period)
    period_info = store.load_period(period)
    latest_trial = store.get_latest_trial(period)

    items = []
    diffs = []
    involved_orders_map = {}
    involved_orgs_map = {}

    order_net_cents = 0
    order_ids_with_details = set()
    order_map = {o.order_id: o for o in orders}
    for o in orders:
        order_net_cents += int(round(o.net_amount * 100))
    items.append({
        "name": "订单净金额合计",
        "amount": round(order_net_cents / 100, 2),
        "source": f"共 {len(orders)} 笔订单",
        "order_count": len(orders),
    })

    cap_stats = getattr(latest_trial, "cap_stats", {}) if latest_trial else {}
    floor_stats = getattr(latest_trial, "floor_stats", {}) if latest_trial else {}
    cap_expected_diff_cents = 0
    floor_expected_diff_cents = 0

    if cap_stats and cap_stats.get("order_ids"):
        for oid in cap_stats["order_ids"]:
            if oid in order_map:
                cap_expected_diff_cents += int(round(order_map[oid].net_amount * 100))
        cap_expected_diff_cents -= int(round(cap_stats["total_amount"] * 100))
    if floor_stats and floor_stats.get("order_ids"):
        for oid in floor_stats["order_ids"]:
            if oid in order_map:
                floor_expected_diff_cents += int(round(order_map[oid].net_amount * 100))
        floor_expected_diff_cents -= int(round(floor_stats["total_amount"] * 100))
    expected_capfloor_diff_cents = cap_expected_diff_cents + floor_expected_diff_cents

    trial_three_cents = 0
    trial_orders = set()
    if latest_trial:
        for d in latest_trial.details:
            trial_three_cents += int(round(d.final_amount * 100))
            trial_orders.add(d.order_id)
            order_ids_with_details.add(d.order_id)
        items.append({
            "name": "最新试算-三方合计",
            "amount": round(trial_three_cents / 100, 2),
            "source": f"试算ID: {latest_trial.trial_id}, 订单 {len(trial_orders)} 笔, 明细 {len(latest_trial.details)} 条",
            "order_count": len(trial_orders),
            "detail_count": len(latest_trial.details),
            "cap_stats": cap_stats,
            "floor_stats": floor_stats,
        })

    confirmed_three_cents = 0
    confirmed_orders = set()
    if confirmed_details:
        for d in confirmed_details:
            confirmed_three_cents += int(round(d.final_amount * 100))
            confirmed_orders.add(d.order_id)
            order_ids_with_details.add(d.order_id)
        items.append({
            "name": "已确认明细-三方合计",
            "amount": round(confirmed_three_cents / 100, 2),
            "source": f"订单 {len(confirmed_orders)} 笔, 明细 {len(confirmed_details)} 条",
            "order_count": len(confirmed_orders),
            "detail_count": len(confirmed_details),
        })

    voucher_cents = 0
    voucher_orders = set()
    if vouchers:
        for v in vouchers:
            voucher_cents += int(round(v.total_amount * 100))
        items.append({
            "name": "已生成凭证-合计",
            "amount": round(voucher_cents / 100, 2),
            "source": f"共 {len(vouchers)} 张凭证",
            "voucher_count": len(vouchers),
        })

    if export_file:
        export_cents = 0
        export_row_count = 0
        try:
            with open(export_file, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    amount_str = row.get("金额", row.get("总金额", row.get("total_amount", row.get("amount", "0"))))
                    try:
                        export_cents += int(round(float(amount_str) * 100))
                        export_row_count += 1
                    except (ValueError, TypeError):
                        continue
            items.append({
                "name": "导出文件-合计",
                "amount": round(export_cents / 100, 2),
                "source": f"文件: {export_file}, 共 {export_row_count} 行",
                "row_count": export_row_count,
            })
        except Exception as e:
            items.append({
                "name": "导出文件-合计",
                "amount": 0.0,
                "source": f"文件读取失败: {e}",
                "row_count": 0,
            })

    pairs = [
        ("订单净金额合计", "最新试算-三方合计"),
        ("最新试算-三方合计", "已确认明细-三方合计"),
        ("已确认明细-三方合计", "已生成凭证-合计"),
    ]
    if export_file:
        pairs.append(("已生成凭证-合计", "导出文件-合计"))

    item_map = {it["name"]: it for it in items}
    for name1, name2 in pairs:
        if name1 not in item_map or name2 not in item_map:
            continue
        a = item_map[name1]
        b = item_map[name2]
        diff_cents = int(round(a["amount"] * 100)) - int(round(b["amount"] * 100))
        if abs(diff_cents) >= 1:
            is_expected = False
            expected_note = ""
            if name1 == "订单净金额合计" and name2 == "最新试算-三方合计":
                if abs(diff_cents - expected_capfloor_diff_cents) <= 1:
                    is_expected = True
                    expected_note = "（封顶保底规则影响，属正常差异）"
            involved_orders = set()
            involved_orgs = set()
            if "确认" in name2 and confirmed_details:
                for d in confirmed_details:
                    involved_orders.add(d.order_id)
                    involved_orgs.add(d.org_id)
            elif "试算" in name2 and latest_trial:
                for d in latest_trial.details:
                    involved_orders.add(d.order_id)
                    involved_orgs.add(d.org_id)
            elif "凭证" in name2 and vouchers:
                for v in vouchers:
                    involved_orgs.add(v.org_id)
            elif "导出" in name2 and vouchers:
                for v in vouchers:
                    involved_orgs.add(v.org_id)

            diffs.append({
                "name1": name1,
                "amount1": a["amount"],
                "name2": name2,
                "amount2": b["amount"],
                "diff": round(diff_cents / 100, 2),
                "involved_orders": sorted(involved_orders)[:10],
                "involved_orgs": sorted(involved_orgs)[:10],
                "order_count_diff": a.get("order_count", 0) - b.get("order_count", 0),
                "is_expected": is_expected,
                "expected_note": expected_note,
            })

    status_text = period_info.status.value if period_info else "未知"
    confirm_mgr = ConfirmManager(store)
    exc_count = confirm_mgr.exception_handler.get_open_exception_count(period)

    next_actions = []
    if exc_count > 0:
        next_actions.append(f"存在 {exc_count} 条未处理异常，请先 handle-exception 处理")
    if not latest_trial:
        next_actions.append("请先执行 trial-calc 进行试算")
    elif period_info and period_info.status == ConfirmStatus.DRAFT and exc_count == 0:
        next_actions.append("请执行 confirm-split --confirm 确认分账")
    elif period_info and period_info.status == ConfirmStatus.CONFIRMED:
        unexpected_diffs = [d for d in diffs if not d["is_expected"]]
        if not unexpected_diffs:
            can_lock = confirm_mgr.can_lock(period)
            if can_lock.get("can_lock"):
                next_actions.append("对账一致（含封顶保底正常差异），请执行 confirm-split --lock 锁定结算周期")
            else:
                next_actions.append(can_lock.get("reason", "锁定前校验不通过，请检查"))
        else:
            next_actions.append("存在异常对账差异，请先检查数据一致性")
    if vouchers and period_info and period_info.status == ConfirmStatus.LOCKED:
        next_actions.append("对账一致，可正常导出凭证CSV和报表")

    unexpected_diffs = [d for d in diffs if not d["is_expected"]]
    ok = len(unexpected_diffs) == 0 and exc_count == 0

    return {
        "period": period,
        "status": status_text,
        "items": items,
        "diffs": diffs,
        "open_exception_count": exc_count,
        "ok": ok,
        "next_actions": next_actions,
        "expected_capfloor_diff": round(expected_capfloor_diff_cents / 100, 2),
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
