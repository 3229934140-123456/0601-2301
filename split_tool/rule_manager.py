"""分成规则配置模块"""

from typing import List, Dict, Any, Optional

from .models import SplitRule
from .storage import DataStore


class RuleManager:
    """分成规则管理器"""

    def __init__(self, store: DataStore):
        self.store = store

    def add_rule(self, rule: SplitRule) -> Dict[str, Any]:
        errors = rule.validate()
        if errors:
            return {"success": False, "errors": errors}

        rules = self.store.load_rules()
        for i, r in enumerate(rules):
            if r.product_code == rule.product_code:
                rules[i] = rule
                self.store.save_rules(rules)
                return {"success": True, "action": "update", "rule": rule.to_dict()}

        rules.append(rule)
        self.store.save_rules(rules)
        return {"success": True, "action": "add", "rule": rule.to_dict()}

    def delete_rule(self, product_code: str) -> Dict[str, Any]:
        rules = self.store.load_rules()
        new_rules = [r for r in rules if r.product_code != product_code]
        if len(new_rules) == len(rules):
            return {"success": False, "message": f"未找到产品编码为 {product_code} 的规则"}
        self.store.save_rules(new_rules)
        return {"success": True, "message": f"已删除产品 {product_code} 的规则"}

    def get_rule(self, product_code: str) -> Optional[SplitRule]:
        return self.store.get_rule(product_code)

    def list_rules(self) -> List[Dict[str, Any]]:
        rules = self.store.load_rules()
        return [r.to_dict() for r in rules]

    def get_or_create_default(self, product_code: str) -> Optional[SplitRule]:
        rule = self.get_rule(product_code)
        if rule:
            return rule
        return None
