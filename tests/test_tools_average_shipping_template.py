import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SAMPLE_SHIPPING_TEXT = """预估运费
￥35.1
￥32.1
￥49.07
￥49.07
￥62.61
￥35.1
￥49.07
￥35.7
￥32.95
￥32.95
￥33.05
￥35.8
￥33.05
￥36.15
￥50.56
￥50.9
￥50.75
￥35.95
￥33.1
￥36.25
￥48.05
￥35.75
￥32.95
￥36.2
￥35.8
￥51.4
￥36.1
￥36.4
￥35.85
￥36.35
￥50.55
￥36.4
￥35.8
￥50.75
￥36.1
￥36.83
￥50.8
￥36.35
￥36.1
￥51.2
￥35.9
￥76.4
￥36.69
￥36.2
￥50.6
￥49.5
￥51.15
￥36.3
￥36.25
￥36.35
￥35.95
￥36.25
￥64.5
￥50.85
￥25.75
￥49.95
￥35.85
￥35.9
￥48.75
￥49.5
￥35.8"""


def _extract_script() -> str:
    template = (ROOT / "web" / "templates" / "tools.html").read_text(encoding="utf-8")
    match = re.search(r'<script id="averageShippingToolScript">(.*?)</script>', template, re.S)
    assert match, "average shipping script must use a stable script id"
    return match.group(1)


def _run_average_text(text: str) -> dict[str, str]:
    script = _extract_script()
    node_code = (
        script
        + "\nconst result = globalThis.averageShippingTool.averageText(process.argv[1]);"
        + "\nconsole.log(JSON.stringify(result));"
    )
    result = subprocess.run(
        ["node", "-e", node_code, text],
        text=True,
        capture_output=True,
        check=True,
    )
    import json

    return json.loads(result.stdout)


def test_average_shipping_sample_calculates_one_decimal():
    result = _run_average_text(SAMPLE_SHIPPING_TEXT)

    assert result["display"] == "41.5"
    assert result["count"] == 61


def test_average_shipping_ignores_non_numeric_lines_and_rounds_to_one_decimal():
    result = _run_average_text("预估运费\n￥81.56\n备注\n￥81.64")

    assert result["display"] == "81.6"
    assert result["count"] == 2


def test_average_shipping_empty_input_has_placeholder_display():
    result = _run_average_text("预估运费\n备注")

    assert result["display"] == "--"
    assert result["count"] == 0
