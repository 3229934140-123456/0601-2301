"""CLI命令行入口"""

import os
import sys
import json
from typing import Optional

import click
from tabulate import tabulate

from .storage import DataStore
from .order_importer import OrderImporter
from .rule_manager import RuleManager
from .trial_calculator import SplitCalculator
from .exception_handler import ExceptionHandler
from .confirm_manager import ConfirmManager
from .voucher_generator import VoucherGenerator
from .report_generator import ReportGenerator
from .models import SplitRule


def _get_store(data_dir: str = None) -> DataStore:
    return DataStore(data_dir)


def _print_json(data):
    click.echo(json.dumps(data, ensure_ascii=False, indent=2))


def _print_table(rows, headers=None, floatfmt=".2f"):
    if not rows:
        click.echo("(无数据)")
        return
    click.echo(tabulate(rows, headers=headers or "keys", tablefmt="grid", floatfmt=floatfmt))


@click.group(help="数据要素流通收益分账自动化工具")
@click.option("--data-dir", type=click.Path(), default=None, help="数据目录路径，默认为 ./data")
@click.pass_context
def main(ctx: click.Context, data_dir: str):
    ctx.ensure_object(dict)
    ctx.obj["store"] = _get_store(data_dir)


# ============ 1. 订单导入 ============

@main.command("import-orders", help="导入交易订单（支持CSV/Excel）")
@click.option("--file", "file_path", type=click.Path(exists=True), required=True, help="订单文件路径")
@click.option("--period", required=True, help="结算周期，如 2026-05")
@click.option("--json", "as_json", is_flag=True, help="以JSON格式输出")
@click.pass_context
def import_orders(ctx: click.Context, file_path: str, period: str, as_json: bool):
    store = ctx.obj["store"]
    importer = OrderImporter(store)
    try:
        result = importer.import_orders(file_path, period)
        if as_json:
            _print_json(result)
        else:
            click.echo(f"✓ 订单导入完成 - 周期: {period}")
            rows = [
                ["文件总行数", result["total_rows"]],
                ["本次导入成功", result["imported"]],
                ["重复订单跳过", result["duplicates"]],
                ["缺失信息", result["missing_info"]],
                ["金额无效", result["invalid_amount"]],
                ["新增异常数", result["exceptions"]],
                ["周期内累计订单", result["period_total"]],
            ]
            _print_table(rows, headers=["项目", "数量"])
    except Exception as e:
        click.echo(f"✗ 导入失败: {e}", err=True)
        sys.exit(1)


# ============ 2. 规则配置 ============

@main.command("config-rule", help="配置分成规则")
@click.option("--product", "product_code", required=True, help="产品编码")
@click.option("--provider", type=float, required=True, help="提供方分成比例（0-1）")
@click.option("--channel", type=float, required=True, help="渠道方分成比例（0-1）")
@click.option("--service", type=float, required=True, help="服务方分成比例（0-1）")
@click.option("--cap", type=float, default=None, help="单笔分账封顶金额")
@click.option("--floor", type=float, default=None, help="单笔分账保底金额")
@click.option("--desc", default="", help="规则描述")
@click.option("--json", "as_json", is_flag=True, help="以JSON格式输出")
@click.pass_context
def config_rule(
    ctx: click.Context, product_code: str, provider: float, channel: float,
    service: float, cap: Optional[float], floor: Optional[float], desc: str, as_json: bool
):
    store = ctx.obj["store"]
    manager = RuleManager(store)
    rule = SplitRule(
        product_code=product_code,
        provider_rate=provider,
        channel_rate=channel,
        service_rate=service,
        cap_amount=cap,
        floor_amount=floor,
        description=desc,
    )
    result = manager.add_rule(rule)
    if as_json:
        _print_json(result)
    else:
        if result["success"]:
            action = "更新" if result["action"] == "update" else "新增"
            click.echo(f"✓ 规则{action}成功")
            r = result["rule"]
            rows = [
                ["产品编码", r["product_code"]],
                ["提供方比例", f"{r['provider_rate']:.4f}"],
                ["渠道方比例", f"{r['channel_rate']:.4f}"],
                ["服务方比例", f"{r['service_rate']:.4f}"],
                ["封顶金额", r["cap_amount"] if r["cap_amount"] is not None else "无"],
                ["保底金额", r["floor_amount"] if r["floor_amount"] is not None else "无"],
                ["描述", r["description"] or "-"],
            ]
            _print_table(rows, headers=["项目", "值"])
        else:
            click.echo("✗ 规则配置失败:")
            for err in result["errors"]:
                click.echo(f"  - {err}")
            sys.exit(1)


@main.command("list-rules", help="查看所有分成规则")
@click.pass_context
def list_rules(ctx: click.Context):
    store = ctx.obj["store"]
    manager = RuleManager(store)
    rules = manager.list_rules()
    if not rules:
        click.echo("(暂无规则配置)")
        return
    rows = []
    for r in rules:
        rows.append({
            "产品编码": r["product_code"],
            "提供方": f"{r['provider_rate']:.2%}",
            "渠道方": f"{r['channel_rate']:.2%}",
            "服务方": f"{r['service_rate']:.2%}",
            "封顶": r["cap_amount"] if r["cap_amount"] is not None else "-",
            "保底": r["floor_amount"] if r["floor_amount"] is not None else "-",
            "描述": r["description"] or "-",
        })
    _print_table(rows)


@main.command("delete-rule", help="删除分成规则")
@click.option("--product", "product_code", required=True, help="产品编码")
@click.pass_context
def delete_rule(ctx: click.Context, product_code: str):
    store = ctx.obj["store"]
    manager = RuleManager(store)
    result = manager.delete_rule(product_code)
    if result["success"]:
        click.echo(f"✓ {result['message']}")
    else:
        click.echo(f"✗ {result['message']}")
        sys.exit(1)


# ============ 3. 试算校验 ============

@main.command("trial-calc", help="执行分账试算")
@click.option("--period", required=True, help="结算周期")
@click.option("--json", "as_json", is_flag=True, help="以JSON格式输出")
@click.pass_context
def trial_calc(ctx: click.Context, period: str, as_json: bool):
    store = ctx.obj["store"]
    calculator = SplitCalculator(store)
    try:
        trial = calculator.run_trial(period)
        summary = calculator.generate_trial_summary(trial)
        if as_json:
            _print_json(summary)
        else:
            click.echo(f"✓ 试算完成 - 试算ID: {summary['trial_id']}")
            click.echo()
            click.echo("=== 试算汇总 ===")
            rows = [
                ["参与订单数", summary["order_count"]],
                ["分账明细数", summary["detail_count"]],
                ["异常数量", summary["exception_count"]],
                ["订单净金额合计", summary["total_amount"]],
                ["提供方分成合计", summary["provider_total"]],
                ["渠道方分成合计", summary["channel_total"]],
                ["服务方分成合计", summary["service_total"]],
                ["三方合计与净额差", summary["diff"]],
            ]
            _print_table(rows, headers=["项目", "值"])
            click.echo()
            if summary["org_summary"]:
                click.echo("=== 按机构汇总 ===")
                org_rows = []
                for s in summary["org_summary"]:
                    role_map = {"provider": "提供方", "channel": "渠道方", "service": "服务方"}
                    org_rows.append({
                        "角色": role_map.get(s["role"], s["role"]),
                        "机构ID": s["org_id"],
                        "机构名称": s["org_name"] or "-",
                        "订单数": s["order_count"],
                        "订单金额": s["order_amount"],
                        "净金额": s["net_amount"],
                        "分成金额": s["split_amount"],
                        "平均比例": f"{s['avg_rate']:.2%}",
                    })
                _print_table(org_rows)
            click.echo()
            if summary["exceptions_by_type"]:
                click.echo("=== 异常分布 ===")
                type_map = {
                    "duplicate_order": "重复订单",
                    "missing_info": "缺失信息",
                    "missing_rule": "缺失规则",
                    "invalid_amount": "金额无效",
                    "refund_exceed": "退款超额",
                    "other": "其他",
                }
                exc_rows = [
                    {"异常类型": type_map.get(k, k), "数量": v}
                    for k, v in summary["exceptions_by_type"].items()
                ]
                _print_table(exc_rows)
    except Exception as e:
        click.echo(f"✗ 试算失败: {e}", err=True)
        sys.exit(1)


# ============ 4. 异常处理 ============

@main.command("handle-exception", help="查看和处理异常")
@click.option("--period", required=True, help="结算周期")
@click.option("--status", type=click.Choice(["open", "fixed", "ignored"]), default=None, help="按状态过滤")
@click.option("--fix", "fix_id", default=None, help="标记指定异常为已修复")
@click.option("--ignore", "ignore_id", default=None, help="忽略指定异常")
@click.option("--note", default="", help="修复/忽略说明")
@click.option("--fix-order", is_flag=True, help="修正订单字段（需配合 --order-id --field --value）")
@click.option("--order-id", default=None, help="订单号（用于修正订单）")
@click.option("--field", default=None, help="要修正的订单字段名")
@click.option("--value", default=None, help="字段新值")
@click.option("--json", "as_json", is_flag=True, help="以JSON格式输出")
@click.pass_context
def handle_exception(
    ctx: click.Context, period: str, status: Optional[str],
    fix_id: Optional[str], ignore_id: Optional[str], note: str,
    fix_order: bool, order_id: Optional[str], field: Optional[str],
    value: Optional[str], as_json: bool
):
    store = ctx.obj["store"]
    handler = ExceptionHandler(store)

    if fix_id:
        result = handler.fix_exception(period, fix_id, note)
        if as_json:
            _print_json(result)
        else:
            if result["success"]:
                click.echo(f"✓ 异常 {fix_id} 已标记为已修复")
            else:
                click.echo(f"✗ {result['message']}")
        return

    if ignore_id:
        result = handler.ignore_exception(period, ignore_id, note)
        if as_json:
            _print_json(result)
        else:
            if result["success"]:
                click.echo(f"✓ 异常 {ignore_id} 已标记为已忽略")
            else:
                click.echo(f"✗ {result['message']}")
        return

    if fix_order:
        if not order_id or not field or not value:
            click.echo("✗ 修正订单需同时提供 --order-id --field --value")
            sys.exit(1)
        result = handler.fix_order_field(period, order_id, field, value)
        if as_json:
            _print_json(result)
        else:
            if result["success"]:
                click.echo(f"✓ 订单 {order_id} 字段 {field} 已更新为 {value}")
            else:
                click.echo(f"✗ {result['message']}")
        return

    exceptions = handler.list_exceptions(period, status)
    if as_json:
        _print_json(exceptions)
    else:
        stats = handler.get_exception_stats(period)
        click.echo(f"=== 异常统计 - 周期 {period} ===")
        stat_rows = [
            ["待处理(open)", stats.get("open", 0)],
            ["已修复(fixed)", stats.get("fixed", 0)],
            ["已忽略(ignored)", stats.get("ignored", 0)],
            ["总计", sum(stats.values())],
        ]
        _print_table(stat_rows, headers=["状态", "数量"])
        click.echo()
        if exceptions:
            click.echo("=== 异常列表 ===")
            type_map = {
                "duplicate_order": "重复订单",
                "missing_info": "缺失信息",
                "missing_rule": "缺失规则",
                "invalid_amount": "金额无效",
                "refund_exceed": "退款超额",
                "other": "其他",
            }
            status_map = {"open": "待处理", "fixed": "已修复", "ignored": "已忽略"}
            rows = []
            for e in exceptions:
                rows.append({
                    "异常ID": e["exception_id"],
                    "订单号": e["order_id"] or "-",
                    "类型": type_map.get(e["exception_type"], e["exception_type"]),
                    "状态": status_map.get(e["status"], e["status"]),
                    "描述": e["description"],
                    "修复说明": e.get("fix_note") or "-",
                })
            _print_table(rows)
        else:
            click.echo("(暂无异常记录)")


# ============ 5. 分账确认 ============

@main.command("confirm-split", help="确认分账结果 / 锁定结算周期")
@click.option("--period", required=True, help="结算周期")
@click.option("--confirm", "do_confirm", is_flag=True, help="执行分账确认")
@click.option("--lock", is_flag=True, help="锁定结算周期（确认后才能锁定）")
@click.option("--reset", is_flag=True, help="重置为草稿状态（未锁定时）")
@click.option("--list-periods", "list_all", is_flag=True, help="列出所有结算周期状态")
@click.option("--operator", default="system", help="操作人标识")
@click.option("--json", "as_json", is_flag=True, help="以JSON格式输出")
@click.pass_context
def confirm_split(
    ctx: click.Context, period: str, do_confirm: bool, lock: bool,
    reset: bool, list_all: bool, operator: str, as_json: bool
):
    store = ctx.obj["store"]
    manager = ConfirmManager(store)

    if list_all:
        periods = manager.list_all_periods()
        if as_json:
            _print_json(periods)
        else:
            if not periods:
                click.echo("(暂无结算周期)")
                return
            status_map = {
                "draft": "草稿", "pending": "待确认",
                "confirmed": "已确认", "locked": "已锁定"
            }
            rows = []
            for p in periods:
                rows.append({
                    "周期": p["period"],
                    "状态": status_map.get(p["status"], p["status"]),
                    "试算次数": p["trial_count"],
                    "最近试算": p.get("last_trial_at") or "-",
                    "待处理异常": p.get("open_exception_count", 0),
                    "确认人": p.get("confirmed_by") or "-",
                    "确认时间": p.get("confirmed_at") or "-",
                    "锁定时间": p.get("locked_at") or "-",
                })
            _print_table(rows)
        return

    if do_confirm:
        result = manager.confirm_split(period, operator)
        if as_json:
            _print_json(result)
        else:
            if result["success"]:
                click.echo(f"✓ 分账确认成功 - 周期: {period}")
                rows = [
                    ["分账明细数", result["detail_count"]],
                    ["订单净金额合计", result["total_amount"]],
                    ["提供方分成", result["provider_total"]],
                    ["渠道方分成", result["channel_total"]],
                    ["服务方分成", result["service_total"]],
                ]
                _print_table(rows, headers=["项目", "值"])
            else:
                click.echo(f"✗ 确认失败: {result['message']}")
                sys.exit(1)
        return

    if lock:
        result = manager.lock_period(period, operator)
        if as_json:
            _print_json(result)
        else:
            if result["success"]:
                click.echo(f"✓ 结算周期 {period} 已锁定")
            else:
                click.echo(f"✗ 锁定失败: {result['message']}")
                sys.exit(1)
        return

    if reset:
        result = manager.reset_to_draft(period, operator)
        if as_json:
            _print_json(result)
        else:
            if result["success"]:
                click.echo(f"✓ 周期 {period} 已重置为草稿状态")
            else:
                click.echo(f"✗ 重置失败: {result['message']}")
                sys.exit(1)
        return

    check = manager.can_confirm(period)
    if as_json:
        _print_json(check)
    else:
        if check["can_confirm"]:
            click.echo(f"✓ 周期 {period} 可以执行分账确认")
            click.echo("  使用 --confirm 参数执行确认")
        else:
            click.echo(f"✗ 周期 {period} 暂不能确认: {check['reason']}")


# ============ 6. 凭证生成 ============

@main.command("gen-voucher", help="生成付款凭证")
@click.option("--period", required=True, help="结算周期")
@click.option("--generate", "do_generate", is_flag=True, help="执行凭证生成")
@click.option("--approve", "approve_id", default=None, help="审批指定凭证")
@click.option("--pay", "pay_id", default=None, help="标记指定凭证为已付款")
@click.option("--export", "export_path", type=click.Path(), default=None, help="导出凭证CSV到指定路径")
@click.option("--operator", default="system", help="操作人标识")
@click.option("--json", "as_json", is_flag=True, help="以JSON格式输出")
@click.pass_context
def gen_voucher(
    ctx: click.Context, period: str, do_generate: bool,
    approve_id: Optional[str], pay_id: Optional[str],
    export_path: Optional[str], operator: str, as_json: bool
):
    store = ctx.obj["store"]
    generator = VoucherGenerator(store)

    if do_generate:
        result = generator.generate_vouchers(period, operator)
        if as_json:
            _print_json(result)
        else:
            if result["success"]:
                click.echo(f"✓ 凭证生成成功 - 共 {result['voucher_count']} 张，合计 {result['total_amount']}")
                click.echo()
                status_map = {"created": "待审批", "approved": "已审批", "paid": "已付款"}
                role_map = {"provider": "提供方", "channel": "渠道方", "service": "服务方"}
                rows = []
                for v in result["vouchers"]:
                    rows.append({
                        "凭证号": v["voucher_id"],
                        "角色": role_map.get(v["role"], v["role"]),
                        "机构ID": v["org_id"],
                        "机构名称": v["org_name"] or "-",
                        "订单数": v["order_count"],
                        "金额": v["total_amount"],
                        "状态": status_map.get(v["status"], v["status"]),
                    })
                _print_table(rows)
            else:
                click.echo(f"✗ 凭证生成失败: {result['message']}")
                sys.exit(1)
        return

    if approve_id:
        result = generator.approve_voucher(period, approve_id, operator)
        if as_json:
            _print_json(result)
        else:
            if result["success"]:
                click.echo(f"✓ 凭证 {approve_id} 已审批通过")
            else:
                click.echo(f"✗ 审批失败: {result['message']}")
                sys.exit(1)
        return

    if pay_id:
        result = generator.mark_paid(period, pay_id, operator)
        if as_json:
            _print_json(result)
        else:
            if result["success"]:
                click.echo(f"✓ 凭证 {pay_id} 已标记为已付款")
            else:
                click.echo(f"✗ 标记失败: {result['message']}")
                sys.exit(1)
        return

    if export_path:
        result = generator.export_vouchers_csv(period, export_path)
        if as_json:
            _print_json(result)
        else:
            if result["success"]:
                click.echo(f"✓ 凭证已导出到 {result['path']}（{result['count']} 条）")
            else:
                click.echo(f"✗ 导出失败: {result['message']}")
                sys.exit(1)
        return

    vouchers = generator.list_vouchers(period)
    if as_json:
        _print_json(vouchers)
    else:
        if not vouchers:
            click.echo(f"(周期 {period} 暂无凭证，使用 --generate 生成)")
            return
        status_map = {"created": "待审批", "approved": "已审批", "paid": "已付款"}
        role_map = {"provider": "提供方", "channel": "渠道方", "service": "服务方"}
        rows = []
        for v in vouchers:
            rows.append({
                "凭证号": v["voucher_id"],
                "角色": role_map.get(v["role"], v["role"]),
                "机构ID": v["org_id"],
                "机构名称": v["org_name"] or "-",
                "订单数": v["order_count"],
                "金额": v["total_amount"],
                "状态": status_map.get(v["status"], v["status"]),
                "创建时间": v["created_at"],
            })
        _print_table(rows)


# ============ 7. 汇总报表 ============

@main.command("report", help="生成汇总报表")
@click.option("--period", required=True, help="结算周期")
@click.option("--overview", is_flag=True, help="查看所有周期概览")
@click.option("--role-totals", is_flag=True, help="按角色汇总金额")
@click.option("--export-details", type=click.Path(), default=None, help="导出分账明细CSV")
@click.option("--export-recon", type=click.Path(), default=None, help="导出对账文件CSV")
@click.option("--export-history", type=click.Path(), default=None, help="导出计算历史CSV")
@click.option("--json", "as_json", is_flag=True, help="以JSON格式输出")
@click.pass_context
def report(
    ctx: click.Context, period: str, overview: bool, role_totals: bool,
    export_details: Optional[str], export_recon: Optional[str],
    export_history: Optional[str], as_json: bool
):
    store = ctx.obj["store"]
    generator = ReportGenerator(store)

    if overview:
        data = generator.get_period_overview()
        if as_json:
            _print_json(data)
        else:
            if not data:
                click.echo("(暂无周期数据)")
                return
            status_map = {
                "draft": "草稿", "pending": "待确认",
                "confirmed": "已确认", "locked": "已锁定"
            }
            rows = []
            for p in data:
                rows.append({
                    "周期": p["period"],
                    "状态": status_map.get(p["status"], p["status"]),
                    "提供方": p["provider_amount"],
                    "渠道方": p["channel_amount"],
                    "服务方": p["service_amount"],
                    "总计": p["grand_total"],
                    "试算次数": p["trial_count"],
                    "待处理异常": p.get("open_exception_count", 0),
                })
            _print_table(rows)
        return

    if export_details:
        result = generator.export_details(period, export_details)
        if as_json:
            _print_json(result)
        else:
            if result["success"]:
                click.echo(f"✓ 分账明细已导出到 {result['path']}（{result['detail_count']} 条）")
            else:
                click.echo(f"✗ 导出失败: {result['message']}")
                sys.exit(1)
        return

    if export_recon:
        result = generator.export_reconciliation(period, export_recon)
        if as_json:
            _print_json(result)
        else:
            if result["success"]:
                click.echo(f"✓ 对账文件已导出到 {result['path']}（{result['row_count']} 条）")
            else:
                click.echo(f"✗ 导出失败: {result['message']}")
                sys.exit(1)
        return

    if export_history:
        result = generator.export_calc_history(period, export_history)
        if as_json:
            _print_json(result)
        else:
            if result["success"]:
                click.echo(f"✓ 计算历史已导出到 {result['path']}（{result['record_count']} 条）")
            else:
                click.echo(f"✗ 导出失败: {result['message']}")
                sys.exit(1)
        return

    if role_totals:
        data = generator.get_role_totals(period)
        if as_json:
            _print_json(data)
        else:
            click.echo(f"=== 角色汇总 - 周期 {period} ===")
            rows = [
                ["提供方", data["provider"]["org_count"], data["provider"]["order_count"], data["provider"]["amount"]],
                ["渠道方", data["channel"]["org_count"], data["channel"]["order_count"], data["channel"]["amount"]],
                ["服务方", data["service"]["org_count"], data["service"]["order_count"], data["service"]["amount"]],
                ["总计", "-", "-", data["grand_total"]],
            ]
            _print_table(rows, headers=["角色", "机构数", "订单数", "分成金额"])
        return

    summary = generator.get_org_summary(period)
    history = generator.get_calc_history(period)
    if as_json:
        _print_json({"org_summary": summary, "calc_history": history})
    else:
        click.echo(f"=== 机构汇总 - 周期 {period} ===")
        if not summary:
            click.echo("(暂无汇总数据，请先执行试算或确认分账)")
        else:
            role_map = {"provider": "提供方", "channel": "渠道方", "service": "服务方"}
            rows = []
            for s in summary:
                rows.append({
                    "角色": role_map.get(s["role"], s["role"]),
                    "机构ID": s["org_id"],
                    "机构名称": s["org_name"] or "-",
                    "订单数": s["order_count"],
                    "订单金额": s["total_order_amount"],
                    "净金额": s["total_net_amount"],
                    "分成金额": s["total_split_amount"],
                    "平均比例": f"{s['avg_rate']:.2%}",
                })
            _print_table(rows)
        click.echo()
        if history:
            click.echo(f"=== 计算历史（最近 {len(history)} 次）===")
            h_rows = []
            for h in history[:10]:
                h_rows.append({
                    "试算ID": h["trial_id"],
                    "时间": h["created_at"],
                    "订单数": h["order_count"],
                    "明细数": h["detail_count"],
                    "异常数": h["exception_count"],
                    "总金额": h["total_amount"],
                    "提供方": h["provider_total"],
                    "渠道方": h["channel_total"],
                    "服务方": h["service_total"],
                })
            _print_table(h_rows)


if __name__ == "__main__":
    main()
