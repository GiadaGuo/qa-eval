"""生成 02080101 测试用 KenLM ARPA 模型（确定性、可复现）。

产出 ``test-zh-char.arpa``：字符级 3-gram 中文小模型，仅供单元/集成测试
与链路验证使用，**不是**生产质量语言模型。生产模型准备方式见
``docs/development/kenlm-model-preparation.md``。

用法：``.venv/bin/python tests/fixtures/kenlm/build_test_arpa.py``

模型口径与 :mod:`qa_eval.calculators.text_perplexity` 保持一致：
每个汉字一个 token（han_character 分词），语料为若干政策新闻风格短句。
"""

from __future__ import annotations

import math
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "test-zh-char.arpa"

# 语料：政策新闻风格短句（字符级 token 化）
CORPUS = [
    "我们坚持改革开放的政策",
    "我们坚持社会主义现代化建设",
    "国家发展和现代化建设",
    "改革开放和发展社会主义市场经济",
    "我们完善社会主义市场经济体制",
    "国家发展现代化经济",
    "坚持改革开放和社会主义现代化建设",
    "我们发展经济和改善人民生活",
]

BOS, EOS, UNK = "<s>", "</s>", "<unk>"
BIGRAM_LAMBDA = 0.8
TRIGRAM_LAMBDA = 0.7


def log10(value: float) -> str:
    return f"{math.log10(max(value, 1e-9)):.6f}"


def main() -> None:
    sentences = [[BOS, *line, EOS] for line in CORPUS]
    unigrams: Counter[str] = Counter()
    bigrams: Counter[tuple[str, str]] = Counter()
    trigrams: Counter[tuple[str, str, str]] = Counter()
    for sentence in sentences:
        unigrams.update(sentence)
        bigrams.update(zip(sentence, sentence[1:]))
        trigrams.update(zip(sentence, sentence[1:], sentence[2:]))

    # 词表：句首/句尾标记 + <unk> + 全部语料字符
    vocab = [BOS, EOS, UNK, *sorted({token for sentence in sentences for token in sentence if token not in {BOS, EOS}})]
    # </s> 计数使用句数（每句一个），<s> 同理；<unk> 记 1 保证可加载
    total = sum(unigrams[token] for token in vocab if token not in {BOS, UNK})

    uni_prob = {
        token: (unigrams[token] / total if token not in {BOS, UNK} else 1e-9)
        for token in vocab
    }
    # P(b|a) = λ * MLE + (1-λ) * P(b)，保证 Σ ≤ 1、backoff ≥ 0
    backoff = {}
    for token in vocab:
        if token in {EOS, BOS}:
            continue
        seen = sum(bigrams[(token, b)] for b in vocab)
        if seen:
            mass = sum(
                BIGRAM_LAMBDA * bigrams[(token, b)] / seen + (1 - BIGRAM_LAMBDA) * uni_prob[b]
                for b in vocab
                if bigrams[(token, b)]
            )
            backoff[token] = max(1.0 - mass, 0.0)

    lines = ["\\data\\"]
    lines.append(f"ngram 1={len(vocab)}")
    lines.append(f"ngram 2={len(bigrams)}")
    lines.append(f"ngram 3={len(trigrams)}")
    lines.append("")
    lines.append("\\1-grams:")
    for token in vocab:
        # <s> 只作条件，概率记 -99；backoff 仅对可作条件的 token 有意义
        if token == BOS:
            lines.append(f"-99.000000\t{token}")
        elif token == EOS:
            lines.append(f"{log10(uni_prob[token])}\t{token}")
        else:
            lines.append(
                f"{log10(uni_prob[token])}\t{token}\t{log10(backoff.get(token, 1.0))}"
            )
    lines.append("")
    lines.append("\\2-grams:")
    for (a, b), count in sorted(bigrams.items()):
        if a == BOS:
            conditional = uni_prob[b]
        elif b == EOS:
            conditional = count / unigrams[a]
        else:
            seen = sum(bigrams[(a, x)] for x in vocab if bigrams[(a, x)])
            conditional = (
                BIGRAM_LAMBDA * count / seen + (1 - BIGRAM_LAMBDA) * uni_prob[b]
            )
        lines.append(f"{log10(conditional)}\t{a} {b}")
    lines.append("")
    lines.append("\\3-grams:")
    for (a, b, c), count in sorted(trigrams.items()):
        bigram_seen = bigrams[(a, b)]
        conditional = TRIGRAM_LAMBDA * count / bigram_seen + (1 - TRIGRAM_LAMBDA) * (
            BIGRAM_LAMBDA * bigrams[(b, c)] / max(unigrams[b], 1)
            if bigrams[(b, c)]
            else uni_prob[c]
        )
        lines.append(f"{log10(conditional)}\t{a} {b} {c}")
    lines.append("")
    lines.append("\\end\\")
    OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT} ({OUTPUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
