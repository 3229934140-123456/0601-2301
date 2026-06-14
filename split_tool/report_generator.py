"""汇总报表模块"""

from typing import List, Dict, Any
from collections import defaultdict

from .models import SplitDetail, SplitRole, ConfirmStatus
from .storage import DataStore
from .confirm_manager import ConfirmManager


class ReportGenerator:
    """报表生成器"""

    def __init__(self, store: DataStore):
        self.store = store
        self.confirm_manager = ConfirmManager(store)

    def get_org_summary(self, period: str, confirmed_only: bool = True) -> List[Dict[str, Any]]:
        if confirmed_only:
            details = self.store.load_confirmed_details(period)
            if not details:
                trial = self.store.get_latest_trial(period)
                if trial:
                    details = trial.details
        else:
            trial = self.store.get_latest_trial(period)
            details = trial.details if trial else []

        if not details:
            return []

        grouped = defaultdict(lambda: {
            "order_count": 0,
            "total_order_amount": 0.0,
            "total_net_amount": 0.0,
            "total_split_amount": 0.0,
            "rates": [],
        })

        for d in details:
            key = (d.role, d.org_id)
            g = grouped[key]
            g["order_count"] += 1
            g["total_order_amount"] += d.order_amount
            g["total_net_amount"] += d.net_amount
            g["total_split_amount"] += d.final_amount
            g["rates"].append(d.rate)
            g["org_name"] = d.org_name

        result = []
        for (role, org_id), g in grouped.items():
            avg_rate = sum(g["rates"]) / len(g["rates"]) if g["rates"] else 0
            result.append({
                "period": period,
                "role": role.value,
                "org_id": org_id,
                "org_name": g.get("org_name", ""),
                "order_count": g["order_count"],
                "total_order_amount": round(g["total_order_amount"], 2),
                "total_net_amount": round(g["total_net_amount"], 2),
                "total_split_amount": round(g["total_split_amount"], 2),
                "avg_rate": round(avg_rate, 4),
            })

        return sorted(result, key=lambda x: (x["role"], x["org_id"]))

    def get_role_totals(self, period: str, confirmed_only: bool = True) -> Dict[str, Any]:
        summary = self.get_org_summary(period, confirmed_only)
        totals = {
            "provider": {"org_count": 0, "order_count": 0, "amount": 0.0},
            "channel": {"org_count": 0, "order_count": 0, "amount": 0.0},
            "service": {"org_count": 0, "order_count": 0, "amount": 0.0},
        }
        for s in summary:
            role = s["role"]
            if role in totals:
                totals[role]["org_count"] += 1
                totals[role]["order_count"] += s["order_count"]
                totals[role]["amount"] += s["total_split_amount"]

        overall = sum(t["amount"] for t in totals.values())
        return {
            "period": period,
            "provider": {k: (round(v, 2) if k == "amount" else v) for k, v in totals["provider"].items()},
            "channel": {k: (round(v, 2) if k == "amount" else v) for k, v in totals["channel"].items()},
            "service": {k: (round(v, 2) if k == "amount" else v) for k, v in totals["service"].items()},
            "grand_total": round(overall, 2),
        }

    def get_calc_history(self, period: str) -> List[Dict[str, Any]]:
        trials = self.store.load_trials(period)
        return [
            {
                "trial_id": t.trial_id,
                "created_at": t.created_at,
                "order_count": t.order_count,
                "detail_count": t.detail_count,
                "exception_count": t.exception_count,
                "total_amount": t.total_amount,
                "provider_total": t.provider_total,
                "channel_total": t.channel_total,
                "service_total": t.service_total,
            }
            for t in trials
        ]

    def get_period_overview(self) -> List[Dict[str, Any]]:
        periods_data = self.confirm_manager.list_all_periods()
        result = []
        for p in periods_data:
            role_totals = self.get_role_totals(p["period"])
            p.update({
                "provider_amount": role_totals["provider"]["amount"],
                "channel_amount": role_totals["channel"]["amount"],
                "service_amount": role_totals["service"]["amount"],
                "grand_total": role_totals["grand_total"],
            })
            result.append(p)
        return result

    def export_details(self, period: str, output_path: str) -> Dict[str, Any]:
        details = self.store.load_confirmed_details(period)
        if not details:
            trial = self.store.get_latest_trial(period)
            if trial:
                details = trial.details
            else:
                return {"success": False, "message": "未找到分账明细"}
        self.store.save_details_csv(period, details, output_path)
        return {"success": True, "path": output_path, "detail_count": len(details)}

    def export_reconciliation(self, period: str, output_path: str) -> Dict[str, Any]:
        summary = self.get_org_summary(period)
        if not summary:
            return {"success": False, "message": "未找到汇总数据"}
        self.store.save_reconciliation_csv(period, summary, output_path)
        return {"success": True, "path": output_path, "row_count": len(summary)}

    def export_calc_history(self, period: str, output_path: str) -> Dict[str, Any]:
        history = self.get_calc_history(period)
        if not history:
            return {"success": False, "message": "未找到计算历史"}
        self.store.save_calc_history(period, history, output_path)
        return {"success": True, "path": output_path, "record_count": len(history)}
