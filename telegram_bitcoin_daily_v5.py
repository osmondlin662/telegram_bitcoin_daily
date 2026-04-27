import json
import os
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import requests


TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

TW_TZ = timezone(timedelta(hours=8))
COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"
COINGECKO_RANGE_URL = "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart/range"
FEAR_GREED_URL = "https://api.alternative.me/fng/"
NEWS_RSS_URL = (
    "https://news.google.com/rss/search"
    "?q=Bitcoin&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
)
HISTORY_FILE = "history.json"


def get_btc_market_data():
    params = {
        "ids": "bitcoin",
        "vs_currencies": "usd",
        "include_24hr_change": "true",
        "include_24hr_vol": "true",
        "include_market_cap": "true",
    }
    response = requests.get(COINGECKO_URL, params=params, timeout=20)
    response.raise_for_status()
    data = response.json()["bitcoin"]

    price = data.get("usd")
    change_24h = data.get("usd_24h_change")
    volume_24h = data.get("usd_24h_vol")
    market_cap = data.get("usd_market_cap")
    change_4h = get_btc_change_4h(price)

    if change_24h is None:
        momentum = "未知"
    elif change_24h >= 2:
        momentum = "偏強"
    elif change_24h <= -2:
        momentum = "偏弱"
    else:
        momentum = "盤整"

    return {
        "price": price,
        "change_24h": change_24h,
        "change_4h": change_4h,
        "volume_24h": volume_24h,
        "market_cap": market_cap,
        "momentum": momentum,
    }


def get_btc_change_4h(current_price):
    if current_price is None:
        return None

    now_utc = datetime.now(timezone.utc)
    four_hours_ago = now_utc - timedelta(hours=4, minutes=10)
    params = {
        "vs_currency": "usd",
        "from": int(four_hours_ago.timestamp()),
        "to": int(now_utc.timestamp()),
    }

    response = requests.get(COINGECKO_RANGE_URL, params=params, timeout=20)
    response.raise_for_status()
    prices = response.json().get("prices", [])

    if not prices:
        return None

    start_price = prices[0][1]
    if not start_price:
        return None

    return {
        "start_price": start_price,
        "current_price": current_price,
        "difference": current_price - start_price,
    }


def get_fear_and_greed():
    response = requests.get(FEAR_GREED_URL, timeout=20)
    response.raise_for_status()
    item = response.json()["data"][0]

    value = int(item["value"])
    classification = item["value_classification"]

    if value >= 75:
        market = "過熱"
    elif value <= 25:
        market = "恐慌"
    else:
        market = "中性"

    return {
        "value": value,
        "classification": classification,
        "market": market,
    }


def get_today_news(limit=3):
    response = requests.get(NEWS_RSS_URL, timeout=20)
    response.raise_for_status()

    root = ElementTree.fromstring(response.content)
    today = datetime.now(TW_TZ).date()
    items = []

    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()

        if not title or not link or not pub_date:
            continue

        try:
            published_dt = parsedate_to_datetime(pub_date).astimezone(TW_TZ)
        except Exception:
            continue

        if published_dt.date() != today:
            continue

        items.append(
            {
                "title": title,
                "link": link,
                "time": published_dt.strftime("%H:%M"),
            }
        )

        if len(items) >= limit:
            break

    return items


def format_number(value, decimals=2):
    if value is None:
        return "N/A"
    return f"{value:,.{decimals}f}"


def get_market_state(market, sentiment):
    change = market["change_24h"]
    fear = sentiment["value"]

    if change is None:
        return "未知"

    if change > 2 and fear > 70:
        return "⚠️ 過熱（可能出貨區）"
    if change < -2 and fear < 30:
        return "🟢 恐慌（可能吸籌）"
    if -2 <= change <= 2:
        return "🟡 盤整"
    return "中性"


def get_alerts(market, sentiment):
    alerts = []
    change = market["change_24h"]
    fear = sentiment["value"]
    change_4h = market["change_4h"]

    if change is not None and abs(change) > 5:
        alerts.append("🚨 24h 價格波動過大")
    if change_4h is not None and abs(change_4h["difference"]) > 3000:
        alerts.append("⏱️ 4h 價格波動偏大")
    if fear > 80:
        alerts.append("🔥 市場極度貪婪")
    if fear < 20:
        alerts.append("❄️ 極度恐慌")

    return alerts


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return {}

    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def update_history(today, market, sentiment):
    history = load_history()
    last_date = history.get("date")
    current_price = market["price"]
    current_fear = sentiment["value"]

    up_streak = 1
    hot_streak = 1 if current_fear > 70 else 0
    fear_trend = "首次記錄"

    if last_date != today:
        last_price = history.get("price")
        last_fear = history.get("fear")

        if current_price is not None and last_price is not None and current_price > last_price:
            up_streak = history.get("up_streak", 0) + 1

        if current_fear > 70:
            hot_streak = history.get("hot_streak", 0) + 1 if last_fear is not None and last_fear > 70 else 1

        if last_fear is not None:
            if current_fear > last_fear:
                fear_trend = "情緒升溫"
            elif current_fear < last_fear:
                fear_trend = "情緒降溫"
            else:
                fear_trend = "情緒持平"

        history = {
            "date": today,
            "price": current_price,
            "fear": current_fear,
            "up_streak": up_streak,
            "hot_streak": hot_streak,
        }

        with open(HISTORY_FILE, "w", encoding="utf-8") as fh:
            json.dump(history, fh, ensure_ascii=False, indent=2)
    else:
        up_streak = history.get("up_streak", up_streak)
        hot_streak = history.get("hot_streak", hot_streak)
        last_fear = history.get("fear")

        if last_fear is not None:
            if current_fear > last_fear:
                fear_trend = "情緒升溫"
            elif current_fear < last_fear:
                fear_trend = "情緒降溫"
            else:
                fear_trend = "情緒持平"

    return {
        "up_streak": up_streak,
        "hot_streak": hot_streak,
        "fear_trend": fear_trend,
    }


def format_price_move(change_4h):
    if change_4h is None:
        return "N/A"

    start_price = format_number(change_4h["start_price"], 0)
    current_price = format_number(change_4h["current_price"], 0)
    difference = change_4h["difference"]

    if difference > 0:
        direction = f"漲了 ${format_number(difference)}"
    elif difference < 0:
        direction = f"跌了 ${format_number(abs(difference))}"
    else:
        direction = "持平"

    return f" ${start_price} > 現在 ${current_price}（{direction}）"


def build_message():
    today = datetime.now(TW_TZ).strftime("%Y-%m-%d")
    market = get_btc_market_data()
    sentiment = get_fear_and_greed()
    news = get_today_news(limit=3)
    state = get_market_state(market, sentiment)
    trend = update_history(today, market, sentiment)
    alerts = get_alerts(market, sentiment)

    lines = [
        f"【Bitcoin Daily Brief】{today}",
        "",
        "1. 現貨市場",
        f"- BTC 價格：${format_number(market['price'])}",
        f"- 4h 漲跌：{format_price_move(market['change_4h'])}",
        f"- 24h 漲跌：{format_number(market['change_24h'])}%",
        f"- 24h 成交量：${format_number(market['volume_24h'], 0)}",
        f"- 市值：${format_number(market['market_cap'], 0)}",
        f"- 動能：{market['momentum']}",
        "",
        "2. 情緒",
        f"- Fear & Greed：{sentiment['value']} ({sentiment['classification']})",
        f"- 市場：{sentiment['market']}",
        "",
        "3. 判斷",
        f"- 市場狀態：{state}",
        "",
        "4. 趨勢記憶",
        f"- 連續上漲天數：{trend['up_streak']} 天",
        f"- 情緒過熱連續天數：{trend['hot_streak']} 天",
        f"- 情緒變化：{trend['fear_trend']}",
        "",
        "5. 風險提示",
    ]

    if alerts:
        for alert in alerts:
            lines.append(f"- {alert}")
    else:
        lines.append("- 無明顯警報")

    lines.extend(["", "6. 今日新聞"])

    if news:
        for idx, item in enumerate(news, start=1):
            lines.append(f"{idx}. {item['title']}")
            lines.append(f"時間：{item['time']}")
            lines.append(item["link"])
            lines.append("")
    else:
        lines.append("- 今天尚未抓到 Bitcoin 相關新聞")

    return "\n".join(lines).strip()


def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    response = requests.post(
        url,
        data={"chat_id": CHAT_ID, "text": message},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def main():
    message = build_message()
    print(message)
    result = send_telegram_message(message)
    print(result)


if __name__ == "__main__":
    main()


