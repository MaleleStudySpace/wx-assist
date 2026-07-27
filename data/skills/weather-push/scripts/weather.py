#!/usr/bin/env python
"""weather-push skill — 用 wttr.in JSON API 获取天气，输出纯文本适合微信推送。

Args:
  --location  城市名 (必填，支持中文如"北京"、英文如"Beijing")
  --days      预报天数 1-3 (默认 2)
  --lang      语言 zh/en — zh 时城市名/星期用中文 (默认 zh)

Output: 纯文本，适合微信推送。出错时输出 [CRON 错误]
"""
import argparse
import json
import sys
import urllib.request
import urllib.error
import urllib.parse
from datetime import date, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

WEEKDAYS_ZH = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"]
WEEKDAYS_EN = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def get_weather(location: str, lang: str) -> dict:
    """调用 wttr.in JSON API 获取天气数据。"""
    quoted = urllib.parse.quote(location)
    url = f"https://wttr.in/{quoted}?format=j1&lang={lang}"
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"天气 API 返回 {e.code}: {e.reason}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"天气 API 不可达: {e.reason}")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"天气数据解析失败: {e}")


def _wd(date_str: str, lang: str) -> str:
    """日期 2026-07-27 → 星期几。"""
    try:
        dt = date.fromisoformat(date_str)
        idx = dt.weekday()
        return WEEKDAYS_ZH[idx] if lang == "zh" else WEEKDAYS_EN[idx]
    except Exception:
        return ""


def _day_label(date_str: str, lang: str, today: date) -> str:
    """生成日期标签: 今天 / 明天 / 周三 等。"""
    try:
        dt = date.fromisoformat(date_str)
    except Exception:
        return date_str
    if dt == today:
        return "今天" if lang == "zh" else "Today"
    if dt == today + timedelta(days=1):
        return "明天" if lang == "zh" else "Tomorrow"
    wd = _wd(date_str, lang)
    return f"{date_str[5:]} {wd}" if wd else date_str[5:]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--location", required=True)
    parser.add_argument("--days", type=int, default=2)
    parser.add_argument("--lang", default="zh")
    args = parser.parse_args()

    days = min(max(args.days, 1), 3)
    lang = args.lang if args.lang in ("zh", "en") else "zh"

    try:
        data = get_weather(args.location, lang)
    except RuntimeError as e:
        print(f"[CRON 错误] {e}")
        sys.exit(1)

    # 地点
    area = data.get("nearest_area", [{}])[0]
    city_parts = []
    for k in ("areaName", "region", "country"):
        v = area.get(k, [{}])[0].get("value", "")
        if v:
            city_parts.append(v)
    city = ", ".join(city_parts)
    lines = [f"📍 {city}"]

    # 当前天气
    cc = data.get("current_condition", [{}])[0]
    temp = cc.get("temp_C", "?")
    feels = cc.get("FeelsLikeC", "")
    desc = cc.get("weatherDesc", [{}])[0].get("value", "")
    humidity = cc.get("humidity", "")
    wind = cc.get("windspeedKmph", "")
    wind_dir = cc.get("winddir16Point", "")
    vis = cc.get("visibility", "")

    parts = [f"🌡 {temp}°C"]
    if feels and feels != temp:
        parts.append(f"(体感{feels}°)")
    if desc:
        parts.append(desc)
    if wind:
        parts.append(f"💨 {wind}km/h {wind_dir}")
    if humidity:
        parts.append(f"💧 {humidity}%")
    if vis:
        parts.append(f"👁 {vis}km")
    lines.append(" ".join(parts))

    # 逐日预报
    today = date.today()
    fc = data.get("weather", [])
    if not fc:
        lines.append("（未获取到预报数据）")
    else:
        for i, f in enumerate(fc[:days]):
            date_str = f.get("date", "")
            label = _day_label(date_str, lang, today)
            maxt = f.get("maxtempC", "?")
            mint = f.get("mintempC", "?")
            uv = f.get("uvIndex", "")
            sun_h = f.get("sunHour", "")
            astro = f.get("astronomy", [{}])[0]
            sr = astro.get("sunrise", "")
            ss = astro.get("sunset", "")

            lines.append(f"\n📅 {label}  ☀ {maxt}°C / {mint}°C")

            extra = []
            if uv:
                extra.append(f"UV {uv}")
            if sun_h:
                extra.append(f"日照{sun_h}h")
            if sr and ss:
                extra.append(f"日出{sr} 日落{ss}")
            if extra:
                lines.append("  " + " · ".join(extra))

            # 逐三小时精选（中午/傍晚）
            hourly = f.get("hourly", [])
            for h in hourly:
                h_time = h.get("time", "0").zfill(4)
                if h_time not in ("0900", "1200", "1500", "1800"):
                    continue
                h_temp = h.get("tempC", "?")
                h_desc = h.get("weatherDesc", [{}])[0].get("value", "")
                h_wind = h.get("windspeedKmph", "")
                h_humi = h.get("humidity", "")
                seg = f"  {h_time[:2]}:00  {h_temp}°C"
                if h_desc:
                    seg += f" {h_desc}"
                if h_wind:
                    seg += f" {h_wind}km/h"
                if h_humi:
                    seg += f" 💧{h_humi}%"
                lines.append(seg)

    lines.append("\n--- 数据来源: wttr.in ---")
    print("\n".join(lines))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[CRON 错误] 天气脚本异常: {e}")
        sys.exit(1)
