# 数据要素流通收益分账自动化工具

## 简介

本工具供平台财务人员按交易订单计算数据产品提供方、渠道方和服务方的收益分成。

## 功能模块

1. **订单导入** - 导入交易订单，支持CSV/Excel格式
2. **规则配置** - 配置分成比例、封顶保底、退款折让等规则
3. **试算校验** - 执行分账试算，生成试算清单，检测异常
4. **异常处理** - 处理重复订单、缺失信息等异常，支持人工修正
5. **分账确认** - 锁定结算周期，确认分账结果
6. **凭证生成** - 生成付款凭证
7. **汇总报表** - 按机构汇总，导出对账文件，追踪计算历史

## 安装

```bash
pip install -r requirements.txt
```

## 使用方法

```bash
# 查看帮助
python -m split_tool --help

# 导入订单
python -m split_tool import-orders --file orders.xlsx --period 2026-05

# 配置分成规则
python -m split_tool config-rule --product DATA001 --provider 0.6 --channel 0.25 --service 0.15

# 执行试算
python -m split_tool trial-calc --period 2026-05

# 处理异常
python -m split_tool handle-exception --period 2026-05

# 确认分账
python -m split_tool confirm-split --period 2026-05

# 生成凭证
python -m split_tool gen-voucher --period 2026-05

# 汇总报表
python -m split_tool report --period 2026-05
```

## 数据目录结构

```
data/
├── orders/              # 原始订单数据
├── rules/               # 分成规则配置
├── trials/              # 试算记录
├── exceptions/          # 异常记录
├── confirmed/           # 已确认分账
├── vouchers/            # 付款凭证
└── reports/             # 汇总报表
```
