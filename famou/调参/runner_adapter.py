"""Command-line adapter template for production training evaluation.

Replace this template with the real training adapter. It is invoked as:
    python runner_adapter.py /absolute/path/to/candidate_init.py

On success, print exactly one JSON object to stdout:
    {"wmape": 0.12, "bias_ratio": -0.02}

Training logs should go to stderr. Do not return fabricated metrics.
"""

import sys

RUNNER_ADAPTER_IS_TEMPLATE = True


def main() -> None:
    raise SystemExit(
        "请在页面评测器代码中提供真实训练适配器；" "它必须输出包含 wmape 和 bias_ratio 的 JSON。"
    )


if __name__ == "__main__":
    main()
