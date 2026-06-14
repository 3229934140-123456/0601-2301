"""订单导入模块"""

import os
from typing import List, Tuple, Dict, Any
from datetime import datetime

import pandas as pd

from .models import Order, OrderStatus, SplitException, ExceptionType, generate_id
from .storage import DataStore
from .audit import write_audit


COLUMN_MAPPING = {
    "order_id": ["订单号", "order_id", "orderid", "订单编号", "交易单号"],
    "product_code": ["产品编码", "product_code", "productcode", "产品代码", "商品编码"],
    "product_name": ["产品名称", "product_name", "productname", "商品名称"],
    "order_amount": ["订单金额", "order_amount", "amount", "交易金额", "实付金额"],
    "refund_amount": ["退款金额", "refund_amount", "refund", "退款"],
    "discount_amount": ["折让金额", "discount_amount", "discount", "优惠金额", "折扣"],
    "provider_id": ["提供方ID", "provider_id", "providerid", "供方编码", "数据提供方编码"],
    "provider_name": ["提供方名称", "provider_name", "providername", "供方名称", "数据提供方"],
    "channel_id": ["渠道方ID", "channel_id", "channelid", "渠道编码"],
    "channel_name": ["渠道方名称", "channel_name", "channelname", "渠道名称"],
    "service_id": ["服务方ID", "service_id", "serviceid", "服务方编码"],
    "service_name": ["服务方名称", "service_name", "servicename", "服务方"],
    "order_time": ["下单时间", "order_time", "ordertime", "交易时间", "创建时间"],
    "status": ["订单状态", "status", "订单状态说明"],
}


def _map_columns(df_columns: List[str]) -> Dict[str, str]:
    mapping = {}
    lower_cols = {c.strip().lower(): c for c in df_columns}
    for target, candidates in COLUMN_MAPPING.items():
        for cand in candidates:
            if cand.strip().lower() in lower_cols:
                mapping[target] = lower_cols[cand.strip().lower()]
                break
    return mapping


def _read_file(file_path: str) -> pd.DataFrame:
    ext = os.path.splitext(file_path)[1].lower()
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(file_path, dtype=str)
    elif ext == ".csv":
        try:
            return pd.read_csv(file_path, dtype=str, encoding="utf-8-sig")
        except UnicodeDecodeError:
            return pd.read_csv(file_path, dtype=str, encoding="gbk")
    else:
        raise ValueError(f"不支持的文件格式: {ext}")


def _parse_status(status_str: str) -> OrderStatus:
    if not status_str:
        return OrderStatus.CONFIRMED
    s = str(status_str).strip().lower()
    if s in ("已退款", "refunded", "退款"):
        return OrderStatus.REFUNDED
    if s in ("已取消", "cancelled", "canceled", "取消"):
        return OrderStatus.CANCELLED
    if s in ("待确认", "pending", "待处理"):
        return OrderStatus.PENDING
    return OrderStatus.CONFIRMED


def _safe_str(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, float) and pd.isna(val):
        return ""
    s = str(val).strip()
    if s.lower() in ("nan", "none", "null"):
        return ""
    return s


def _to_float(val: Any) -> float:
    if val is None or (isinstance(val, float) and pd.isna(val)) or _safe_str(val) == "":
        return 0.0
    try:
        s = str(val).replace(",", "").replace("¥", "").replace("￥", "").strip()
        return float(s)
    except (ValueError, TypeError):
        return 0.0


class OrderImporter:
    """订单导入器"""

    def __init__(self, store: DataStore):
        self.store = store

    def import_orders(self, file_path: str, period: str) -> Dict[str, Any]:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文件不存在: {file_path}")

        df = _read_file(file_path)
        col_map = _map_columns(df.columns.tolist())

        required = ["order_id", "product_code", "order_amount"]
        missing_cols = [c for c in required if c not in col_map]
        if missing_cols:
            raise ValueError(
                f"缺少必要列: {', '.join(missing_cols)}。"
                f"支持的列名: {', '.join(COLUMN_MAPPING['order_id'])} 等"
            )

        existing_orders = self.store.load_orders(period)
        existing_ids = {o.order_id for o in existing_orders}

        new_orders: List[Order] = []
        exceptions: List[SplitException] = []
        duplicate_count = 0
        missing_info_count = 0
        invalid_amount_count = 0

        for idx, row in df.iterrows():
            def _get(col_name):
                return _safe_str(row[col_map[col_name]]) if col_map.get(col_name) and col_map[col_name] in row.index else ""

            row_dict = row.to_dict()
            order_id = _get("order_id")
            product_code = _get("product_code")
            order_amount = _to_float(row[col_map["order_amount"]]) if col_map.get("order_amount") and col_map["order_amount"] in row.index else 0.0

            if not order_id:
                continue

            if order_id in existing_ids:
                duplicate_count += 1
                exceptions.append(SplitException(
                    exception_id=generate_id("EXC"),
                    order_id=order_id,
                    exception_type=ExceptionType.DUPLICATE_ORDER,
                    description=f"订单号重复: {order_id}",
                    created_at=datetime.now().isoformat(timespec="seconds"),
                    period=period,
                ))
                continue

            missing_fields = []
            if not product_code:
                missing_fields.append("产品编码")
            provider_id = _get("provider_id")
            channel_id = _get("channel_id")
            service_id = _get("service_id")

            if not provider_id:
                missing_fields.append("提供方ID")
            if not channel_id:
                missing_fields.append("渠道方ID")
            if not service_id:
                missing_fields.append("服务方ID")

            if missing_fields:
                missing_info_count += 1
                exceptions.append(SplitException(
                    exception_id=generate_id("EXC"),
                    order_id=order_id,
                    exception_type=ExceptionType.MISSING_INFO,
                    description=f"订单缺失信息: {order_id}, 缺少: {', '.join(missing_fields)}",
                    created_at=datetime.now().isoformat(timespec="seconds"),
                    period=period,
                ))

            if order_amount <= 0:
                invalid_amount_count += 1
                exceptions.append(SplitException(
                    exception_id=generate_id("EXC"),
                    order_id=order_id,
                    exception_type=ExceptionType.INVALID_AMOUNT,
                    description=f"订单金额无效: {order_id}, 金额={order_amount}",
                    created_at=datetime.now().isoformat(timespec="seconds"),
                    period=period,
                ))

            order = Order(
                order_id=order_id,
                product_code=product_code,
                product_name=_get("product_name"),
                order_amount=order_amount,
                refund_amount=_to_float(row[col_map["refund_amount"]]) if col_map.get("refund_amount") and col_map["refund_amount"] in row.index else 0.0,
                discount_amount=_to_float(row[col_map["discount_amount"]]) if col_map.get("discount_amount") and col_map["discount_amount"] in row.index else 0.0,
                provider_id=provider_id,
                provider_name=_get("provider_name"),
                channel_id=channel_id,
                channel_name=_get("channel_name"),
                service_id=service_id,
                service_name=_get("service_name"),
                order_time=_get("order_time"),
                status=_parse_status(row[col_map["status"]]) if col_map.get("status") and col_map["status"] in row.index else OrderStatus.CONFIRMED,
                period=period,
                raw_data={k: str(v) for k, v in row_dict.items()},
            )

            if order.refund_amount > order.order_amount:
                exceptions.append(SplitException(
                    exception_id=generate_id("EXC"),
                    order_id=order_id,
                    exception_type=ExceptionType.REFUND_EXCEED,
                    description=f"退款金额超过订单金额: {order_id}, 订单={order.order_amount}, 退款={order.refund_amount}",
                    created_at=datetime.now().isoformat(timespec="seconds"),
                    period=period,
                ))

            new_orders.append(order)
            existing_ids.add(order_id)

        all_orders = existing_orders + new_orders
        self.store.save_orders(period, all_orders)

        if exceptions:
            old_exceptions = self.store.load_exceptions(period)
            self.store.save_exceptions(period, old_exceptions + exceptions)

        total_amount = sum(o.order_amount for o in new_orders)
        write_audit(
            self.store, period, "import_orders", operator="system",
            detail=f"导入文件 {os.path.basename(file_path)}，新增 {len(new_orders)} 笔，跳过重复 {duplicate_count} 笔",
            order_count=len(new_orders),
            amount=round(total_amount, 2),
            extra={"file": file_path, "duplicates": duplicate_count, "exceptions": len(exceptions)},
        )

        return {
            "total_rows": len(df),
            "imported": len(new_orders),
            "duplicates": duplicate_count,
            "missing_info": missing_info_count,
            "invalid_amount": invalid_amount_count,
            "exceptions": len(exceptions),
            "period_total": len(all_orders),
        }
