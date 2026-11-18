from pathlib import Path

from ai_trader.data.cot import parse_gold_report
from ai_trader.data.sentiment import parse_sentiment


def test_parser_reads_search_json():
    parsed = parse_sentiment(
        '{"bias":"up","summary":"buyers leaning","confidence":62,'
        '"posts":[{"handle":"trader","text":"gold bid","url":"https://x.com/trader/1"}]}'
    )
    assert parsed["bias"] == "up"
    assert parsed["posts"][0]["handle"] == "trader"


def test_sentiment_module_does_not_query_the_post_mailbox():
    text = Path("ai_trader/data/sentiment.py").read_text()
    assert "x_posts" not in text


def test_cot_parser_keeps_full_gold_and_skips_micro():
    gold = [
        "GOLD - COMMODITY EXCHANGE INC.",
        "261020",
        "2026-10-20",
        "088691",
        "CMX",
        "01",
        "088",
        "1000",
        "100",
        "250",
        "0",
        "0",
        "0",
        "400",
        "150",
    ]
    micro = [
        "MICRO GOLD - COMMODITY EXCHANGE INC.",
        "261020",
        "2026-10-20",
        "088695",
        "CMX",
        "01",
        "088",
        "10",
        "1",
        "1",
        "0",
        "0",
        "0",
        "2",
        "2",
    ]
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(micro)
    writer.writerow(gold)
    parsed = parse_gold_report(buffer.getvalue())
    assert parsed is not None
    assert parsed["report_date"] == "2026-10-20"
    assert parsed["commercial_net"] == 100 - 250
    assert parsed["managed_money_net"] == 400 - 150
    assert parsed["raw"]["name"].startswith("GOLD")
