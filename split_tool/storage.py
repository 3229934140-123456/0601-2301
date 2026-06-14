"""数据持久化管理模块"""

import json
import os
from typing import List, Dict, Any, Optional
from pathlib import Path

from .models import (
    Order, SplitRule, SplitDetail, SplitException,
    SettlementPeriod, PaymentVoucher, TrialRecord,
    AuditRecord, VoucherAdjustment
)


class DataStore:
    """基于JSON文件的数据持久化存储"""

    def __init__(self, base_dir: str = None):
        if base_dir is None:
            base_dir = os.path.join(os.getcwd(), "data")
        self.base_dir = Path(base_dir)
        self._ensure_dirs()

    def _ensure_dirs(self):
        dirs = [
            "orders", "rules", "trials", "exceptions",
            "confirmed", "vouchers", "reports", "periods",
            "audit", "adjustments"
        ]
        for d in dirs:
            (self.base_dir / d).mkdir(parents=True, exist_ok=True)

    def _path(self, *parts: str) -> Path:
        return self.base_dir.joinpath(*parts)

    def _read_json(self, path: Path) -> Any:
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_json(self, path: Path, data: Any):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ============ 订单管理 ============

    def save_orders(self, period: str, orders: List[Order]):
        data = [o.to_dict() for o in orders]
        self._write_json(self._path("orders", f"{period}.json"), data)

    def load_orders(self, period: str) -> List[Order]:
        data = self._read_json(self._path("orders", f"{period}.json"))
        if not data:
            return []
        return [Order.from_dict(d) for d in data]

    def get_all_periods(self) -> List[str]:
        periods = set()
        for f in self._path("orders").glob("*.json"):
            periods.add(f.stem)
        for f in self._path("trials").glob("*.json"):
            periods.add(f.stem.split("_")[0])
        return sorted(periods)

    # ============ 规则管理 ============

    def save_rules(self, rules: List[SplitRule]):
        data = [r.to_dict() for r in rules]
        self._write_json(self._path("rules", "split_rules.json"), data)

    def load_rules(self) -> List[SplitRule]:
        data = self._read_json(self._path("rules", "split_rules.json"))
        if not data:
            return []
        return [SplitRule.from_dict(d) for d in data]

    def get_rule(self, product_code: str) -> Optional[SplitRule]:
        rules = self.load_rules()
        for r in rules:
            if r.product_code == product_code:
                return r
        return None

    # ============ 试算记录管理 ============

    def save_trial(self, trial: TrialRecord):
        filename = f"{trial.period}_{trial.trial_id}.json"
        self._write_json(self._path("trials", filename), trial.to_dict())

    def load_trials(self, period: str = None) -> List[TrialRecord]:
        trials = []
        pattern = f"{period}_*.json" if period else "*.json"
        for f in self._path("trials").glob(pattern):
            data = self._read_json(f)
            if data:
                trials.append(TrialRecord.from_dict(data))
        return sorted(trials, key=lambda t: t.created_at, reverse=True)

    def get_latest_trial(self, period: str) -> Optional[TrialRecord]:
        trials = self.load_trials(period)
        return trials[0] if trials else None

    # ============ 异常管理 ============

    def save_exceptions(self, period: str, exceptions: List[SplitException]):
        data = [e.to_dict() for e in exceptions]
        self._write_json(self._path("exceptions", f"{period}.json"), data)

    def load_exceptions(self, period: str) -> List[SplitException]:
        data = self._read_json(self._path("exceptions", f"{period}.json"))
        if not data:
            return []
        return [SplitException.from_dict(d) for d in data]

    # ============ 结算周期管理 ============

    def save_period(self, period: SettlementPeriod):
        self._write_json(self._path("periods", f"{period.period}.json"), period.to_dict())

    def load_period(self, period_str: str) -> Optional[SettlementPeriod]:
        data = self._read_json(self._path("periods", f"{period_str}.json"))
        if not data:
            return None
        return SettlementPeriod.from_dict(data)

    def load_all_periods(self) -> List[SettlementPeriod]:
        periods = []
        for f in self._path("periods").glob("*.json"):
            data = self._read_json(f)
            if data:
                periods.append(SettlementPeriod.from_dict(data))
        return sorted(periods, key=lambda p: p.period)

    # ============ 已确认分账管理 ============

    def save_confirmed_details(self, period: str, details: List[SplitDetail]):
        data = [d.to_dict() for d in details]
        self._write_json(self._path("confirmed", f"{period}_details.json"), data)

    def load_confirmed_details(self, period: str) -> List[SplitDetail]:
        data = self._read_json(self._path("confirmed", f"{period}_details.json"))
        if not data:
            return []
        return [SplitDetail.from_dict(d) for d in data]

    # ============ 凭证管理 ============

    def save_vouchers(self, period: str, vouchers: List[PaymentVoucher]):
        data = [v.to_dict() for v in vouchers]
        self._write_json(self._path("vouchers", f"{period}.json"), data)

    def load_vouchers(self, period: str) -> List[PaymentVoucher]:
        data = self._read_json(self._path("vouchers", f"{period}.json"))
        if not data:
            return []
        return [PaymentVoucher.from_dict(d) for d in data]

    def save_vouchers_csv(self, period: str, vouchers: List[PaymentVoucher], output_path: str):
        import csv
        with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "凭证号", "结算周期", "角色", "机构ID", "机构名称",
                "订单数量", "总金额", "状态", "创建时间", "备注"
            ])
            for v in vouchers:
                writer.writerow([
                    v.voucher_id, v.period, v.role.value,
                    v.org_id, v.org_name, v.order_count,
                    round(v.total_amount, 2), v.status.value,
                    v.created_at, v.remark
                ])

    # ============ 报表导出 ============

    def save_details_csv(self, period: str, details: List[SplitDetail], output_path: str):
        import csv
        with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "明细ID", "订单号", "产品编码", "角色", "机构ID", "机构名称",
                "订单金额", "净金额", "分成比例", "原始分成", "最终分成", "备注"
            ])
            for d in details:
                writer.writerow([
                    d.detail_id, d.order_id, d.product_code, d.role.value,
                    d.org_id, d.org_name, round(d.order_amount, 2),
                    round(d.net_amount, 2), d.rate, round(d.raw_amount, 2),
                    round(d.final_amount, 2), d.remark
                ])

    def save_reconciliation_csv(self, period: str, summary_data: List[Dict], output_path: str):
        import csv
        with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "结算周期", "角色", "机构ID", "机构名称", "订单数量",
                "订单总金额", "净金额合计", "分成总金额", "平均分成比例"
            ])
            for row in summary_data:
                writer.writerow([
                    row["period"], row["role"], row["org_id"], row["org_name"],
                    row["order_count"], round(row["total_order_amount"], 2),
                    round(row["total_net_amount"], 2), round(row["total_split_amount"], 2),
                    round(row["avg_rate"], 4)
                ])

    def save_calc_history(self, period: str, history: List[Dict], output_path: str):
        import csv
        with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "试算ID", "创建时间", "订单数量", "明细数量", "异常数量",
                "订单总金额", "提供方分成", "渠道方分成", "服务方分成"
            ])
            for h in history:
                writer.writerow([
                    h["trial_id"], h["created_at"], h["order_count"],
                    h["detail_count"], h["exception_count"],
                    round(h["total_amount"], 2), round(h["provider_total"], 2),
                    round(h["channel_total"], 2), round(h["service_total"], 2)
                ])

    # ============ 审计日志管理 ============

    def append_audit_log(self, record: AuditRecord):
        path = self._path("audit", f"{record.period}.json")
        existing = self._read_json(path) or []
        existing.append(record.to_dict())
        self._write_json(path, existing)

    def load_audit_log(self, period: str) -> List[AuditRecord]:
        data = self._read_json(self._path("audit", f"{period}.json"))
        if not data:
            return []
        return [AuditRecord.from_dict(d) for d in data]

    # ============ 凭证调整记录管理 ============

    def save_adjustments(self, period: str, adjustments: List[VoucherAdjustment]):
        data = [a.to_dict() for a in adjustments]
        self._write_json(self._path("adjustments", f"{period}.json"), data)

    def load_adjustments(self, period: str) -> List[VoucherAdjustment]:
        data = self._read_json(self._path("adjustments", f"{period}.json"))
        if not data:
            return []
        return [VoucherAdjustment.from_dict(d) for d in data]

    def append_adjustments(self, period: str, new_adjustments: List[VoucherAdjustment]):
        existing = self.load_adjustments(period)
        existing.extend(new_adjustments)
        self.save_adjustments(period, existing)
