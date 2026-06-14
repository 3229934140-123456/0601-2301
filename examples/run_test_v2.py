"""改进功能验证测试"""

import os
import sys
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from split_tool.storage import DataStore
from split_tool.order_importer import OrderImporter
from split_tool.rule_manager import RuleManager
from split_tool.trial_calculator import SplitCalculator
from split_tool.exception_handler import ExceptionHandler
from split_tool.confirm_manager import ConfirmManager
from split_tool.voucher_generator import VoucherGenerator
from split_tool.models import SplitRule, ExceptionStatus


def run_test():
    test_data_dir = os.path.join(os.path.dirname(__file__), "..", "test_data_v2")
    if os.path.exists(test_data_dir):
        shutil.rmtree(test_data_dir)

    print("=" * 70)
    print("改进功能验证测试")
    print("=" * 70)

    store = DataStore(test_data_dir)
    period = "2026-05"

    print("\n【1/6】导入订单（包含缺失机构、0金额等异常）")
    print("-" * 50)
    importer = OrderImporter(store)
    sample_file = os.path.join(os.path.dirname(__file__), "sample_orders_2026-05.csv")
    result = importer.import_orders(sample_file, period)
    print(f"  总行数: {result['total_rows']}, 导入: {result['imported']}")
    print(f"  缺失信息: {result['missing_info']}, 金额无效: {result['invalid_amount']}")
    print(f"  新增异常: {result['exceptions']}")

    print("\n【2/6】检查缺失机构异常能否直接标记为修复（应该失败）")
    print("-" * 50)
    handler = ExceptionHandler(store)
    exceptions = handler.list_exceptions(period, "open")
    missing_info_exc = next((e for e in exceptions if e["exception_type"] == "missing_info"), None)
    if missing_info_exc:
        print(f"  找到缺失信息异常: {missing_info_exc['exception_id']}")
        print(f"  描述: {missing_info_exc['description']}")
        r = handler.fix_exception(period, missing_info_exc["exception_id"], "尝试直接修复")
        print(f"  直接标记修复结果: {'成功(不对)' if r['success'] else '失败(正确)'} - {r.get('message', '')}")

    print("\n【3/6】先修正订单机构字段，再修复异常（应该成功）")
    print("-" * 50)
    if missing_info_exc:
        order_id = missing_info_exc["order_id"]
        r = handler.fix_order_field(period, order_id, "channel_id", "CHN999")
        print(f"  修正channel_id: {'成功' if r['success'] else '失败'} - {r.get('message', '')}")
        r = handler.fix_order_field(period, order_id, "channel_name", "补全渠道有限公司")
        print(f"  修正channel_name: {'成功' if r['success'] else '失败'}")
        r = handler.fix_exception(period, missing_info_exc["exception_id"], "已补全渠道方ID")
        print(f"  再次标记修复: {'成功(正确)' if r['success'] else '失败(不对)'} - {r.get('message', '')}")

    print("\n【4/6】检查0金额订单，修正好金额")
    print("-" * 50)
    amount_exc = next((e for e in handler.list_exceptions(period, "open") if e["exception_type"] == "invalid_amount"), None)
    if amount_exc:
        print(f"  找到金额无效异常: {amount_exc['exception_id']}, 订单: {amount_exc['order_id']}")
        r = handler.fix_exception(period, amount_exc["exception_id"], "直接修复")
        print(f"  直接标记修复: {'成功(不对)' if r['success'] else '失败(正确)'} - {r.get('message', '')}")
        r = handler.fix_order_field(period, amount_exc["order_id"], "order_amount", "8888.88")
        print(f"  修正order_amount为8888.88: {'成功' if r['success'] else '失败'}")
        r = handler.fix_exception(period, amount_exc["exception_id"], "已修正金额")
        print(f"  再次标记修复: {'成功(正确)' if r['success'] else '失败(不对)'} - {r.get('message', '')}")

    print("\n【5/6】执行第一次试算，验证缺失规则异常进入handle-exception")
    print("-" * 50)
    rule_manager = RuleManager(store)
    rules_config = [
        ("DATA001", 0.60, 0.25, 0.15, None, None),
        ("DATA002", 0.55, 0.30, 0.15, 50000, 1000),
        ("DATA003", 0.65, 0.20, 0.15, None, None),
    ]
    for pc, pr, cr, sr, cap, floor in rules_config:
        rule_manager.add_rule(SplitRule(
            product_code=pc, provider_rate=pr, channel_rate=cr, service_rate=sr,
            cap_amount=cap, floor_amount=floor
        ))
    print(f"  配置了 {len(rules_config)} 条规则（故意少配几条）")

    calculator = SplitCalculator(store)
    trial = calculator.run_trial(period)
    summary = calculator.generate_trial_summary(trial)
    print(f"  参与订单数: {summary['order_count']}, 分账明细: {summary['detail_count']}")
    print(f"  待处理异常数: {summary['exception_count']}")
    print(f"  排除订单数: {len(summary.get('excluded_orders', []))}")
    if summary.get("excluded_orders"):
        for o in summary["excluded_orders"][:3]:
            print(f"    - {o['order_id']}: {o['reason'][:40]}")
    if summary.get("exceptions_by_type"):
        print(f"  异常分布: {summary['exceptions_by_type']}")

    print("\n  验证缺失规则异常是否持久化到store:")
    store_exceptions = handler.list_exceptions(period)
    missing_rule = [e for e in store_exceptions if e["exception_type"] == "missing_rule"]
    print(f"    store中缺失规则异常: {len(missing_rule)} 条")
    for e in missing_rule[:2]:
        print(f"      - {e['exception_id']}: {e['description']}")

    print("\n【5.5】尝试确认分账（有未处理异常应该失败）")
    print("-" * 50)
    confirm_mgr = ConfirmManager(store)
    check = confirm_mgr.can_confirm(period)
    print(f"  可确认: {check['can_confirm']} - {check.get('reason', '')}")

    print("\n【6/6】补全规则和剩余异常，跑完整流程：试算→确认→生成凭证")
    print("-" * 50)
    for exc in handler.list_exceptions(period, "open"):
        if exc["exception_type"] == "missing_rule":
            handler.ignore_exception(period, exc["exception_id"], "该产品暂无结算计划")
        else:
            handler.ignore_exception(period, exc["exception_id"], "忽略")
    print("  已忽略所有未处理异常")

    trial2 = calculator.run_trial(period)
    s2 = calculator.generate_trial_summary(trial2)
    print(f"  二次试算: 订单{s2['order_count']}, 明细{s2['detail_count']}, 待处理异常{s2['exception_count']}")
    print(f"  净额: {s2['total_amount']}, 提供方:{s2['provider_total']}, 渠道方:{s2['channel_total']}, 服务方:{s2['service_total']}")

    result = confirm_mgr.confirm_split(period, "tester")
    if result["success"]:
        print(f"  分账确认成功: 明细{result['detail_count']}条, 总金额{result['total_amount']}")
    else:
        print(f"  确认失败: {result.get('message')}")

    voucher_gen = VoucherGenerator(store)
    v_result = voucher_gen.generate_vouchers(period, "tester")
    if v_result["success"]:
        print(f"  凭证生成成功: {v_result['voucher_count']}张, 合计{v_result['total_amount']}")
        diff = abs(result["total_amount"] - v_result["total_amount"])
        print(f"  确认总金额 vs 凭证总金额: 差异 {diff:.2f} {'✓ 一致' if diff < 0.01 else '✗ 不一致'}")
        has_empty = any(not v["org_id"] for v in v_result["vouchers"])
        print(f"  凭证是否存在空机构ID: {'是(有问题)' if has_empty else '否(正确)'}")
    else:
        print(f"  凭证生成: {v_result.get('message')}")

    print("\n" + "=" * 70)
    print("✓ 改进功能验证测试完成")
    print("=" * 70)


if __name__ == "__main__":
    run_test()
