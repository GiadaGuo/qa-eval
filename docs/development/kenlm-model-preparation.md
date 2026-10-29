# KenLM 模型准备与配置（02080101 文本困惑程度）

`text_perplexity` v1.0.0 使用 [KenLM](https://kheafield.com/code/kenlm/) 计算困惑度。
本文说明依赖安装、模型获取、配置方式与不可执行语义。

## 一、依赖安装

KenLM 是**可选依赖**（Python 包需现场编译 C++，macOS/Linux 均可）：

```bash
uv sync --extra kenlm     # 或 pip install "kenlm>=0.3.0,<1.0"
```

- 描述符 `runtime.dependencies = ["kenlm"]`：未安装时 `TrustedCalculatorLoader`
  拒绝加载（`CalculatorLoadError: dependencies are unavailable`），
  Registry Readiness 显示 `CALCULATOR_LOAD_ERROR`，而非静默降级。
- 已安装但未配置模型时，Calculator 在 `validate` 阶段抛出
  `KenlmModelError(code=KENLM_MODEL_UNAVAILABLE)`，明确报错
  「No KenLM model configured; refusing to fabricate perplexity」。

## 二、模型要求与格式

| 项 | 要求 |
| --- | --- |
| 支持格式 | KenLM 二进制（`.bin`，推荐，加载快）或 ARPA 文本（`.arpa`） |
| 语言 | 中文模型；当前分词器 `han_character`（`[A-Za-z0-9]+` 英数连串 + 单汉字），模型需按**字符级**训练 |
| 语料 | 需记录语料来源、时间范围与分词口径，写入模型目录的 `README`，保证 PPL 数值可解释、可复现 |

ARPA 文本必须包含 `<s>` / `</s>` / `<unk>` 特殊词（KenLM 加载强校验）。

## 三、获取方式（不自动下载来源不明的模型）

**平台不内置、不自动下载任何模型文件。** 生产模型由数据团队用受控语料训练：

```bash
# 1) 准备字符级分词后的语料（每行一篇，字间空格）
# 2) 编译 KenLM lmplz 并训练（示例：3-gram）
lmplz -o 3 --verbose header < corpus.char.txt > zh-char-v1.arpa
# 3) 转二进制加速加载
build_binary zh-char-v1.arpa zh-char-v1.bin
```

测试用途的确定性小模型已入库：
`tests/fixtures/kenlm/test-zh-char.arpa`（由同目录 `build_test_arpa.py`
确定性生成，仅覆盖 8 句政策新闻语料的字符 3-gram，**不可用于业务评测**）。

## 四、配置

两处配置模型路径，参数级优先：

1. **Calculator 参数**（Quality Contract item 的 `parameters.model_path`）：
   显式指定本指标使用的模型文件路径；
2. **全局设置** `Settings.kenlm_model_path`（环境变量 `QA_EVAL_KENLM_MODEL_PATH`）：
   作为未传参数时的默认路径。

模型按 `(realpath, mtime_ns, size)` 进程内缓存，同 Worker 进程复用；
文件变化自动重载。诊断与证据中记录模型 sha256 指纹、n-gram 阶数、
分词器版本与 BOS/EOS 口径，保证结果可追溯。

## 五、计算口径

- KenLM `score()` 返回 **log10**；PPL = `10 ** (-Σlog10P / N)`（自然指数换算已做底数转换，不得直接 `math.exp(log10)`）。
- 事件数 N = token 数 + 1（EOS 计入，BOS 仅作条件），与 KenLM `query` 工具一致。
- 溢出钳制在 `1e300`；`full_scores` 的 OOV 标志聚合成 `oov_token_ratio` 供解读。
- **PPL 原始值不是 0~1 的质量分数**：Calculator 只输出原始统计
  （mean/median/p90/p95、高 PPL 记录占比、OOV 占比），不存在也不预设
  PPL→质量分映射；Report 评分层因 `mean_perplexity` 无上界而如实标记「不可评分」。
