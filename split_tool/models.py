"""核心数据模型定义"""

from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum
import uuid


class OrderStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"


class SplitRole(str, Enum):
    PROVIDER = "provider"
    CHANNEL = "channel"
    SERVICE = "service"


class ExceptionType(str, Enum):
    DUPLICATE_ORDER = "duplicate_order"
    MISSING_INFO = "missing_info"
    MISSING_RULE = "missing_rule"
    INVALID_AMOUNT = "invalid_amount"
    REFUND_EXCEED = "refund_exceed"
    OTHER = "other"


class ExceptionStatus(str, Enum):
    OPEN = "open"
    FIXED = "fixed"
    IGNORED = "ignored"


class ConfirmStatus(str, Enum):
    DRAFT = "draft"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    LOCKED = "locked"


class VoucherStatus(str, Enum):
    CREATED = "created"
    APPROVED = "approved"
    PAID = "paid"


def generate_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12].upper()}"


@dataclass
class Order:
    order_id: str
    product_code: str
    product_name: str
    order_amount: float
    refund_amount: float = 0.0
    discount_amount: float = 0.0
    provider_id: str = ""
    provider_name: str = ""
    channel_id: str = ""
    channel_name: str = ""
    service_id: str = ""
    service_name: str = ""
    order_time: str = ""
    status: OrderStatus = OrderStatus.CONFIRMED
    period: str = ""
    raw_data: Dict[str, Any] = field(default_factory=dict)

    @property
    def net_amount(self) -> float:
        return max(0.0, self.order_amount - self.refund_amount - self.discount_amount)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["net_amount"] = self.net_amount
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Order":
        status = OrderStatus(d.get("status", "confirmed"))
        return cls(
            order_id=d["order_id"],
            product_code=d["product_code"],
            product_name=d.get("product_name", ""),
            order_amount=float(d.get("order_amount", 0)),
            refund_amount=float(d.get("refund_amount", 0)),
            discount_amount=float(d.get("discount_amount", 0)),
            provider_id=d.get("provider_id", ""),
            provider_name=d.get("provider_name", ""),
            channel_id=d.get("channel_id", ""),
            channel_name=d.get("channel_name", ""),
            service_id=d.get("service_id", ""),
            service_name=d.get("service_name", ""),
            order_time=d.get("order_time", ""),
            status=status,
            period=d.get("period", ""),
            raw_data=d.get("raw_data", {}),
        )


@dataclass
class SplitRule:
    product_code: str
    provider_rate: float
    channel_rate: float
    service_rate: float
    cap_amount: Optional[float] = None
    floor_amount: Optional[float] = None
    effective_date: str = ""
    expiry_date: str = ""
    description: str = ""

    def validate(self) -> List[str]:
        errors = []
        total = self.provider_rate + self.channel_rate + self.service_rate
        if abs(total - 1.0) > 0.0001:
            errors.append(f"分成比例之和必须等于1.0，当前为{total}")
        for rate, name in [
            (self.provider_rate, "提供方"),
            (self.channel_rate, "渠道方"),
            (self.service_rate, "服务方"),
        ]:
            if rate < 0 or rate > 1:
                errors.append(f"{name}分成比例必须在0-1之间，当前为{rate}")
        if self.cap_amount is not None and self.floor_amount is not None:
            if self.cap_amount < self.floor_amount:
                errors.append("封顶金额不能小于保底金额")
        return errors

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SplitRule":
        return cls(
            product_code=d["product_code"],
            provider_rate=float(d["provider_rate"]),
            channel_rate=float(d["channel_rate"]),
            service_rate=float(d["service_rate"]),
            cap_amount=float(d["cap_amount"]) if d.get("cap_amount") is not None else None,
            floor_amount=float(d["floor_amount"]) if d.get("floor_amount") is not None else None,
            effective_date=d.get("effective_date", ""),
            expiry_date=d.get("expiry_date", ""),
            description=d.get("description", ""),
        )


@dataclass
class SplitDetail:
    detail_id: str
    order_id: str
    product_code: str
    role: SplitRole
    org_id: str
    org_name: str
    order_amount: float
    net_amount: float
    rate: float
    raw_amount: float
    final_amount: float
    period: str
    remark: str = ""
    cap_applied: int = 0
    floor_applied: int = 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["role"] = self.role.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SplitDetail":
        return cls(
            detail_id=d["detail_id"],
            order_id=d["order_id"],
            product_code=d["product_code"],
            role=SplitRole(d["role"]),
            org_id=d["org_id"],
            org_name=d.get("org_name", ""),
            order_amount=float(d.get("order_amount", 0)),
            net_amount=float(d.get("net_amount", 0)),
            rate=float(d.get("rate", 0)),
            raw_amount=float(d.get("raw_amount", 0)),
            final_amount=float(d.get("final_amount", 0)),
            period=d.get("period", ""),
            remark=d.get("remark", ""),
            cap_applied=int(d.get("cap_applied", 0)),
            floor_applied=int(d.get("floor_applied", 0)),
        )


@dataclass
class SplitException:
    exception_id: str
    order_id: str
    exception_type: ExceptionType
    description: str
    status: ExceptionStatus = ExceptionStatus.OPEN
    fix_note: str = ""
    fixed_by: str = ""
    fixed_at: str = ""
    created_at: str = ""
    period: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["exception_type"] = self.exception_type.value
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SplitException":
        return cls(
            exception_id=d["exception_id"],
            order_id=d.get("order_id", ""),
            exception_type=ExceptionType(d["exception_type"]),
            description=d["description"],
            status=ExceptionStatus(d.get("status", "open")),
            fix_note=d.get("fix_note", ""),
            fixed_by=d.get("fixed_by", ""),
            fixed_at=d.get("fixed_at", ""),
            created_at=d.get("created_at", ""),
            period=d.get("period", ""),
        )


@dataclass
class SettlementPeriod:
    period: str
    status: ConfirmStatus = ConfirmStatus.DRAFT
    locked_at: str = ""
    locked_by: str = ""
    confirmed_by: str = ""
    confirmed_at: str = ""
    trial_count: int = 0
    last_trial_at: str = ""
    remark: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SettlementPeriod":
        return cls(
            period=d["period"],
            status=ConfirmStatus(d.get("status", "draft")),
            locked_at=d.get("locked_at", ""),
            locked_by=d.get("locked_by", ""),
            confirmed_by=d.get("confirmed_by", ""),
            confirmed_at=d.get("confirmed_at", ""),
            trial_count=int(d.get("trial_count", 0)),
            last_trial_at=d.get("last_trial_at", ""),
            remark=d.get("remark", ""),
        )


@dataclass
class PaymentVoucher:
    voucher_id: str
    period: str
    role: SplitRole
    org_id: str
    org_name: str
    total_amount: float
    order_count: int
    status: VoucherStatus = VoucherStatus.CREATED
    created_at: str = ""
    approved_by: str = ""
    approved_at: str = ""
    paid_at: str = ""
    remark: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["role"] = self.role.value
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PaymentVoucher":
        return cls(
            voucher_id=d["voucher_id"],
            period=d["period"],
            role=SplitRole(d["role"]),
            org_id=d["org_id"],
            org_name=d.get("org_name", ""),
            total_amount=float(d.get("total_amount", 0)),
            order_count=int(d.get("order_count", 0)),
            status=VoucherStatus(d.get("status", "created")),
            created_at=d.get("created_at", ""),
            approved_by=d.get("approved_by", ""),
            approved_at=d.get("approved_at", ""),
            paid_at=d.get("paid_at", ""),
            remark=d.get("remark", ""),
        )


@dataclass
class TrialRecord:
    trial_id: str
    period: str
    created_at: str
    order_count: int
    detail_count: int
    exception_count: int
    total_amount: float
    provider_total: float
    channel_total: float
    service_total: float
    details: List[SplitDetail] = field(default_factory=list)
    exceptions: List[SplitException] = field(default_factory=list)
    excluded_orders: List[Dict[str, Any]] = field(default_factory=list)
    cap_stats: Dict[str, Any] = field(default_factory=dict)
    floor_stats: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trial_id": self.trial_id,
            "period": self.period,
            "created_at": self.created_at,
            "order_count": self.order_count,
            "detail_count": self.detail_count,
            "exception_count": self.exception_count,
            "total_amount": self.total_amount,
            "provider_total": self.provider_total,
            "channel_total": self.channel_total,
            "service_total": self.service_total,
            "details": [d.to_dict() for d in self.details],
            "exceptions": [e.to_dict() for e in self.exceptions],
            "excluded_orders": self.excluded_orders,
            "cap_stats": self.cap_stats,
            "floor_stats": self.floor_stats,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TrialRecord":
        return cls(
            trial_id=d["trial_id"],
            period=d["period"],
            created_at=d["created_at"],
            order_count=int(d.get("order_count", 0)),
            detail_count=int(d.get("detail_count", 0)),
            exception_count=int(d.get("exception_count", 0)),
            total_amount=float(d.get("total_amount", 0)),
            provider_total=float(d.get("provider_total", 0)),
            channel_total=float(d.get("channel_total", 0)),
            service_total=float(d.get("service_total", 0)),
            details=[SplitDetail.from_dict(x) for x in d.get("details", [])],
            exceptions=[SplitException.from_dict(x) for x in d.get("exceptions", [])],
            excluded_orders=d.get("excluded_orders", []),
            cap_stats=d.get("cap_stats", {}),
            floor_stats=d.get("floor_stats", {}),
        )


@dataclass
class OrgSummary:
    period: str
    role: SplitRole
    org_id: str
    org_name: str
    order_count: int
    total_order_amount: float
    total_net_amount: float
    total_split_amount: float
    avg_rate: float

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["role"] = self.role.value
        return d
