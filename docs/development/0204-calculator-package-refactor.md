# `policy_structure_compliance` Package 重构记录

## 结论

`policy_structure_compliance@1.0.0` 已从三个平级模块重组为独立 package。重构只改变模块边界和
导入路径，不改变 Metric、参数、检测规则、Flag、聚合或 Evidence schema。简单 Calculator 继续
保持单文件。

重构前：

```text
calculators/
├── policy_structure_compliance.py
├── policy_structure_rules.py
└── policy_structure_schema.py
```

重构后：

```text
calculators/policy_structure_compliance/
├── __init__.py
├── calculator.py
├── html_source_map.py
├── parsers.py
└── schema.py
```

没有新增 `validators.py`：四组 validator 当前直接维护 Calculator 聚合计数和 Evidence 结构，拆出会
增加跨模块状态传递且没有第二个消费者。原 `rules.py` 同时承担 lexical source-map 和文种结构解析，
职责与依赖边界清晰，故拆成 `html_source_map.py` 和 `parsers.py`。

## Loader、入口和指纹

`TrustedCalculatorLoader` 使用 `importlib.import_module()`，支持 package。Descriptor entrypoint 保持：

```text
qa_eval.calculators.policy_structure_compliance:PolicyStructureComplianceCalculator
```

`__init__.py` 导出注册类，因此 Registry、历史入口字符串和外部 Calculator import 不需要改变。

Code digest 不是自动依赖追踪；它只哈希 `source_paths()` 显式声明的文件。重构后的 Calculator 声明
package 内全部五个 Python 文件，并有测试断言 `source_paths()` 与 package `*.py` 集合完全相等。
因此修改 `parsers.py`、source-map、schema、入口或 package export 都会改变 digest；新增 Python
模块但忘记加入 source paths 也会使该测试失败。

## 历史兼容边界

- 已完成 Run 的持久化 MetricResult/Evidence 不需要重新加载 Calculator，不受影响。
- Descriptor entrypoint 字符串未变，package import 兼容。
- Registry sync 会原地更新同一 `calculator_key@version` 的 descriptor 和 digest。历史 Physical Plan
  保存创建时的 entrypoint/digest；若它尚未执行或被重试，新代码与旧 digest 不一致时 Loader 会按
  设计拒绝执行。这能防止用新代码冒充旧版本，但说明当前 Registry 没有保留同版本历史代码包。
- 若需要长期重放旧 Physical Plan，应发布新的 Calculator patch version，并保留旧代码/descriptor，
  或引入按 digest 寻址的不可变代码制品；本次没有绕过 digest 校验。

## 等价性验证

重构前保存的 `0204-policy-structure-validation.json` 与重构后重新执行 270 条固定样本的结果，在排除
非确定性的 performance timing 后完全相等。专项 unit、Registry/Loader、Planner 和 E2E 测试继续
覆盖四 Flag、observations、Evidence、原 0204 兼容及 source-map 坐标。

