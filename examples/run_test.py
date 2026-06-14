"""完整的端到端测试脚本"""

import os
import sys
import shutil

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from split_tool.storage import DataStore
from split_tool.order_importer import OrderImporter
from split_tool.rule_manager import RuleManager
from split_tool.trial_calculator import SplitCalculator
from split_tool.exception_handler import ExceptionHandler
from split_tool.confirm_manager import ConfirmManager
from split_tool.voucher_generator import VoucherGenerator
from split_tool.report_generator import ReportGenerator
from split_tool.models import SplitRule


def run_test():
    test_data_dir = os.path.join(os.path.dirname(__file__), "..", "test_data")
    if os.path.exists(test_data_dir):
        shutil.rmtree(test_data_dir)

    print("=" * 60)
    print("数据要素流通收益分账自动化工具 - 端到端测试")
    print("=" * 60)

    store = DataStore(test_data_dir)
    period = "2026-05"

    # Step 1: 导入订单
    print("\n【步骤1】导入订单")
    print("-" * 40)
    importer = OrderImporter(store)
    sample_file = os.path.join(os.path.dirname(__file__), "sample_orders_2026-05.csv")
    result = importer.import_orders(sample_file, period)
    print(f"  文件总行数: {result['total_rows']}")
    print(f"  导入成功: {result['imported']}")
    print(f"  重复订单: {result['duplicates']}")
    print(f"  缺失信息: {result['missing_info']}")
    print(f"  金额无效: {result['invalid_amount']}")
    print(f"  新增异常: {result['exceptions']}")

    # Step 2: 配置规则
    print("\n【步骤2】配置分成规则")
    print("-" * 40)
    rule_manager = RuleManager(store)
    rules_config = [
        ("DATA001", 0.60, 0.25, 0.15, None, None, "企业信用画像"),
        ("DATA002", 0.55, 0.30, 0.15, 50000, 1000, "金融风控评分模型"),
        ("DATA003", 0.65, 0.20, 0.15, None, None, "消费者行为洞察"),
        ("DATA004", 0.50, 0.30, 0.20, None, None, "物流轨迹数据"),
        ("DATA005", 0.58, 0.27, 0.15, None, None, "医疗影像数据"),
    ]
    for pc, pr, cr, sr, cap, floor, desc in rules_config:
        rule = SplitRule(
            product_code=pc,
            provider_rate=pr,
            channel_rate=cr,
            service_rate=sr,
            cap_amount=cap,
            floor_amount=floor,
            description=desc,
        )
        r = rule_manager.add_rule(rule)
        if r["success"]:
            print(f"  ✓ {pc}: 提供方{pr:.0%} 渠道方{cr:.0%} 服务方{sr:.0%}")
        else:
            print(f"  ✗ {pc}: {r['errors']}")

    # Step 3: 执行试算
    print("\n【步骤3】执行分账试算")
    print("-" * 40)
    calculator = SplitCalculator(store)
    trial = calculator.run_trial(period)
    summary = calculator.generate_trial_summary(trial)
    print(f"  试算ID: {summary['trial_id']}")
    print(f"  参与订单数: {summary['order_count']}")
    print(f"  分账明细数: {summary['detail_count']}")
    print(f"  异常数量: {summary['exception_count']}")
    print(f"  订单净金额合计: {summary['total_amount']:.2f}")
    print(f"  提供方分成: {summary['provider_total']:.2f}")
    print(f"  渠道方分成: {summary['channel_total']:.2f}")
    print(f"  服务方分成: {summary['service_total']:.2f}")
    print(f"  差异(净额-三方合计): {summary['diff']:.2f}")

    # Step 4: 处理异常
    print("\n【步骤4】处理异常")
    print("-" * 40)
    handler = ExceptionHandler(store)
    exceptions = handler.list_exceptions(period)
    print(f"  异常总数: {len(exceptions)}")
    fixed_count = 0
    for exc in exceptions:
        if exc["status"] == "open":
            if exc["exception_type"] == "missing_rule":
                continue
            r = handler.fix_exception(period, exc["exception_id"], f"测试自动修复-{exc['exception_type']}")
            if r["success"]:
                fixed_count += 1
    print(f"  自动修复: {fixed_count}")
    stats = handler.get_exception_stats(period)
    print(f"  状态统计: {stats}")

    # 再次试算（排除已修复/忽略的异常订单）
    print("\n【步骤3-重试】修复异常后再次试算")
    print("-" * 40)
    trial2 = calculator.run_trial(period)
    summary2 = calculator.generate_trial_summary(trial2)
    print(f"  参与订单数: {summary2['order_count']}")
    print(f"  分账明细数: {summary2['detail_count']}")
    print(f"  异常数量: {summary2['exception_count']}")
    print(f"  订单净金额合计: {summary2['total_amount']:.2f}")
    print(f"  提供方分成: {summary2['provider_total']:.2f}")
    print(f"  渠道方分成: {summary2['channel_total']:.2f}")
    print(f"  服务方分成: {summary2['service_total']:.2f}")

    # 忽略剩余异常
    for exc in handler.list_exceptions(period, "open"):
        handler.ignore_exception(period, exc["exception_id"], "缺少规则，暂不处理")

    # Step 5: 确认分账
    print("\n【步骤5】确认分账")
    print("-" * 40)
    confirm_mgr = ConfirmManager(store)
    check = confirm_mgr.can_confirm(period)
    print(f"  可确认状态: {check['can_confirm']}")
    if check["can_confirm"]:
        result = confirm_mgr.confirm_split(period, "test_operator")
        if result["success"]:
            print(f"  ✓ 分账确认成功")
            print(f"    明细数: {result['detail_count']}")
            print(f"    总金额: {result['total_amount']:.2f}")
            print(f"    提供方: {result['provider_total']:.2f}")
            print(f"    渠道方: {result['channel_total']:.2f}")
            print(f"    服务方: {result['service_total']:.2f}")
        else:
            print(f"  ✗ 确认失败: {result['message']}")
    else:
        print(f"  ✗ 不可确认: {check['reason']}")

    # Step 6: 生成凭证
    print("\n【步骤6】生成付款凭证")
    print("-" * 40)
    voucher_gen = VoucherGenerator(store)
    result = voucher_gen.generate_vouchers(period, "test_operator")
    if result["success"]:
        print(f"  ✓ 生成凭证 {result['voucher_count']} 张，合计 {result['total_amount']:.2f}")
        for v in result["vouchers"]:
            role_map = {"provider": "提供方", "channel": "渠道方", "service": "服务方"}
            print(f"    - {v['voucher_id']} | {role_map.get(v['role'])} | {v['org_name']} | {v['order_count']}笔 | {v['total_amount']:.2f}")
    else:
        print(f"  ✗ 生成失败: {result['message']}")

    # 审批和标记付款
    vouchers = voucher_gen.list_vouchers(period)
    for v in vouchers[:2]:
        r = voucher_gen.approve_voucher(period, v["voucher_id"], "test_operator")
        if r["success"]:
            print(f"  ✓ 凭证 {v['voucher_id']} 已审批")
            r2 = voucher_gen.mark_paid(period, v["voucher_id"], "test_operator")
            if r2["success"]:
                print(f"  ✓ 凭证 {v['voucher_id']} 已标记付款")

    # Step 7: 汇总报表
    print("\n【步骤7】汇总报表")
    print("-" * 40)
    reporter = ReportGenerator(store)
    role_totals = reporter.get_role_totals(period)
    print(f"  提供方机构数: {role_totals['provider']['org_count']}, 分成合计: {role_totals['provider']['amount']:.2f}")
    print(f"  渠道方机构数: {role_totals['channel']['org_count']}, 分成合计: {role_totals['channel']['amount']:.2f}")
    print(f"  服务方机构数: {role_totals['service']['org_count']}, 分成合计: {role_totals['service']['amount']:.2f}")
    print(f"  总计: {role_totals['grand_total']:.2f}")

    # 机构汇总
    print("\n  按机构汇总:")
    org_summary = reporter.get_org_summary(period)
    role_map = {"provider": "提供方", "channel": "渠道方", "service": "服务方"}
    for s in org_summary:
        print(f"    {role_map.get(s['role'])} | {s['org_id']} {s['org_name']} | {s['order_count']}笔 | {s['total_split_amount']:.2f}")

    # 计算历史
    history = reporter.get_calc_history(period)
    print(f"\n  计算历史: {len(history)} 次试算")
    for h in history:
        print(f"    {h['trial_id']} | {h['created_at']} | 订单{h['order_count']} | 异常{h['exception_count']} | {h['total_amount']:.2f}")

    # 锁定周期
    print("\n【步骤8】锁定结算周期")
    print("-" * 40)
    lock_result = confirm_mgr.lock_period(period, "test_operator")
    if lock_result["success"]:
        print(f"  ✓ 周期 {period} 已锁定")
    else:
        print(f"  ✗ 锁定失败: {lock_result.get('message')}")

    # 导出测试
    print("\n【步骤9】导出文件测试")
    print("-" * 40)
    exports_dir = os.path.join(test_data_dir, "exports")
    os.makedirs(exports_dir, exist_ok=True)

    r1 = reporter.export_details(period, os.path.join(exports_dir, "details.csv"))
    print(f"  分账明细: {'✓' if r1['success'] else '✗'} {r1.get('detail_count', 0)}条")

    r2 = reporter.export_reconciliation(period, os.path.join(exports_dir, "reconciliation.csv"))
    print(f"  对账文件: {'✓' if r2['success'] else '✗'} {r2.get('row_count', 0)}条")

    r3 = reporter.export_calc_history(period, os.path.join(exports_dir, "calc_history.csv"))
    print(f"  计算历史: {'✓' if r3['success'] else '✗'} {r3.get('record_count', 0)}条")

    r4 = voucher_gen.export_vouchers_csv(period, os.path.join(exports_dir, "vouchers.csv"))
    print(f"  付款凭证: {'✓' if r4['success'] else '✗'} {r4.get('count', 0)}条")

    print("\n" + "=" * 60)
    print("✓ 所有测试完成！")
    print(f"  测试数据目录: {os.path.abspath(test_data_dir)}")
    print("=" * 60)


if __name__ == "__main__":
    run_test()
