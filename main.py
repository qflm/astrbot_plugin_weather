"""
astrbot_plugin_weather —— AstrBot 天气插件

指令：
    /天气 地点     查询该地点今天的天气
    /天气帮助      查看使用说明

数据来源：Open-Meteo（https://open-meteo.com）
    地理编码  https://geocoding-api.open-meteo.com/v1/search
    天气预报  https://api.open-meteo.com/v1/forecast
完全免费、无需 API Key、无需注册。
"""

from __future__ import annotations

import datetime
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

try:  # 兼容不同 AstrBot 版本
    from astrbot.api import AstrBotConfig
except Exception:  # pragma: no cover
    AstrBotConfig = dict  # type: ignore


PLUGIN_NAME = "astrbot_plugin_weather"

GEO_API = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_API = "https://api.open-meteo.com/v1/forecast"

HEADERS = {"User-Agent": "astrbot-plugin-weather/1.0.0"}

CURRENT_FIELDS = (
    "temperature_2m,relative_humidity_2m,apparent_temperature,"
    "is_day,precipitation,weather_code,wind_speed_10m,wind_direction_10m"
)
DAILY_FIELDS = (
    "weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,"
    "uv_index_max,precipitation_probability_max,wind_speed_10m_max"
)

# WMO 天气代码 -> (中文描述, 白天图标, 夜间图标)
WMO: Dict[int, Tuple[str, str, str]] = {
    0: ("晴", "☀️", "🌙"),
    1: ("晴间多云", "🌤", "🌙"),
    2: ("多云", "⛅", "☁️"),
    3: ("阴", "☁️", "☁️"),
    45: ("雾", "🌫", "🌫"),
    48: ("雾凇", "🌫", "🌫"),
    51: ("小毛毛雨", "🌦", "🌦"),
    53: ("毛毛雨", "🌦", "🌦"),
    55: ("大毛毛雨", "🌦", "🌦"),
    56: ("冻毛毛雨", "🌧", "🌧"),
    57: ("强冻毛毛雨", "🌧", "🌧"),
    61: ("小雨", "🌦", "🌦"),
    63: ("中雨", "🌧", "🌧"),
    65: ("大雨", "🌧", "🌧"),
    66: ("冻雨", "🌧", "🌧"),
    67: ("强冻雨", "🌧", "🌧"),
    71: ("小雪", "🌨", "🌨"),
    73: ("中雪", "🌨", "🌨"),
    75: ("大雪", "❄️", "❄️"),
    77: ("雪粒", "🌨", "🌨"),
    80: ("小阵雨", "🌦", "🌦"),
    81: ("阵雨", "🌧", "🌧"),
    82: ("强阵雨", "⛈", "⛈"),
    85: ("小阵雪", "🌨", "🌨"),
    86: ("强阵雪", "❄️", "❄️"),
    95: ("雷阵雨", "⛈", "⛈"),
    96: ("雷阵雨伴小冰雹", "⛈", "⛈"),
    99: ("雷阵雨伴大冰雹", "⛈", "⛈"),
}

DIRS = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]

COORD_RE = re.compile(r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*[,，]\s*(-?\d{1,3}(?:\.\d+)?)\s*$")

# Open-Meteo 的地理编码基于 GeoNames，中文库里不少国外城市的译名缺失或指向同名小村庄
# （例如「纽约」查不到、「东京」会命中江苏的同名村），这里给常用城市做英文名映射兜底。
CITY_ALIAS: Dict[str, str] = {
    # 日韩
    "东京": "Tokyo", "大阪": "Osaka", "京都": "Kyoto", "名古屋": "Nagoya",
    "札幌": "Sapporo", "福冈": "Fukuoka", "冲绳": "Okinawa",
    "首尔": "Seoul", "釜山": "Busan",
    # 北美
    "纽约": "New York City", "洛杉矶": "Los Angeles", "旧金山": "San Francisco",
    "西雅图": "Seattle", "芝加哥": "Chicago", "波士顿": "Boston",
    "华盛顿": "Washington", "拉斯维加斯": "Las Vegas", "夏威夷": "Hawaii",
    "多伦多": "Toronto", "温哥华": "Vancouver", "蒙特利尔": "Montreal", "渥太华": "Ottawa",
    # 欧洲
    "伦敦": "London", "曼彻斯特": "Manchester", "爱丁堡": "Edinburgh",
    "巴黎": "Paris", "柏林": "Berlin", "慕尼黑": "Munich", "法兰克福": "Frankfurt",
    "罗马": "Rome", "米兰": "Milan", "威尼斯": "Venice",
    "马德里": "Madrid", "巴塞罗那": "Barcelona",
    "莫斯科": "Moscow", "圣彼得堡": "Saint Petersburg",
    "阿姆斯特丹": "Amsterdam", "苏黎世": "Zurich", "维也纳": "Vienna",
    "伊斯坦布尔": "Istanbul",
    # 亚太 / 中东 / 非洲 / 南美
    "迪拜": "Dubai", "新加坡": "Singapore", "曼谷": "Bangkok",
    "吉隆坡": "Kuala Lumpur", "雅加达": "Jakarta", "河内": "Hanoi",
    "胡志明市": "Ho Chi Minh City", "马尼拉": "Manila", "新德里": "New Delhi", "孟买": "Mumbai",
    "悉尼": "Sydney", "墨尔本": "Melbourne", "布里斯班": "Brisbane", "珀斯": "Perth",
    "奥克兰": "Auckland", "开罗": "Cairo", "约翰内斯堡": "Johannesburg",
    "圣保罗": "Sao Paulo", "里约热内卢": "Rio de Janeiro", "布宜诺斯艾利斯": "Buenos Aires",
}



def help_text(default_city: str = "", forecast_days: int = 3) -> str:
    tip = f"（未填地点时默认查「{default_city}」）" if default_city else ""
    tail = f"· 同时给出未来 {forecast_days} 天预报\n" if forecast_days > 0 else ""
    return (
        "🌤 天气查询 · 使用说明\n"
        "──────────────\n"
        "1️⃣ /天气 地点 —— 查询今天 + 未来预报\n"
        "      例：/天气 北京\n"
        "      例：/天气 上海市\n"
        "      例：/天气 39.9,116.4\n"
        f"{tip}\n"
        "2️⃣ /天气帮助 —— 显示本说明\n"
        "──────────────\n"
        f"{tail}"
        "· 数据来自 Open-Meteo，免费无需 Key\n"
        "· 支持中文、英文地名\n"
        "· 同名地点可加省份，如：/天气 山西朝阳"
    )


def wmo_desc(code: Any, is_day: bool = True) -> Tuple[str, str]:
    try:
        code = int(code)
    except (TypeError, ValueError):
        return ("未知", "🌡")
    item = WMO.get(code)
    if not item:
        return ("未知", "🌡")
    desc, day_icon, night_icon = item
    return desc, (day_icon if is_day else night_icon)


def fmt_num(value: Any, digits: int = 1) -> str:
    try:
        num = round(float(value), digits)
    except (TypeError, ValueError):
        return "-"
    if digits <= 0 or num == int(num):
        return str(int(num))
    return f"{num:.{digits}f}"


def wind_dir(deg: Any) -> str:
    try:
        deg = float(deg) % 360
    except (TypeError, ValueError):
        return ""
    return DIRS[int((deg + 22.5) // 45) % 8]


def beaufort(kmh: Any) -> int:
    """km/h -> 蒲福风力等级"""
    try:
        v = float(kmh)
    except (TypeError, ValueError):
        return 0
    limits = [1, 6, 12, 20, 29, 39, 50, 62, 75, 89, 103, 118]
    for level, upper in enumerate(limits):
        if v < upper:
            return level
    return 12


def uv_level(uv: Any) -> str:
    try:
        v = float(uv)
    except (TypeError, ValueError):
        return "-"
    if v < 3:
        return "低"
    if v < 6:
        return "中等"
    if v < 8:
        return "高"
    if v < 11:
        return "很高"
    return "极高"


def fmt_clock(iso: str) -> str:
    """2026-10-28T06:38 -> 06:38"""
    if not isinstance(iso, str) or "T" not in iso:
        return "-"
    return iso.split("T", 1)[1][:5]


WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def fmt_day(iso_date: str) -> str:
    """2026-10-29 -> 10-29 周四"""
    raw = str(iso_date or "")
    try:
        d = datetime.date.fromisoformat(raw[:10])
    except (ValueError, TypeError):
        return raw[5:10] or raw or "-"
    return f"{d.month:02d}-{d.day:02d} {WEEKDAYS[d.weekday()]}"


def fmt_coord(lat: float, lon: float) -> str:
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"{abs(lat):.2f}°{ns} {abs(lon):.2f}°{ew}"


def pick_best(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """结果首位若是个人口极少或无人口数据的同名小地方，而列表里有大城市，则提升大城市。

    GeoNames 对同名地点的排序经常把小村庄排在城市前面（例如「朝阳」）。
    """
    best = results[0]
    if (best.get("population") or 0) >= 100_000:
        return best
    for item in results[1:]:
        if (item.get("population") or 0) >= 500_000:
            return item
    return best


def place_label(item: Dict[str, Any], with_country: bool = True) -> str:
    name = item.get("name") or ""
    admin = item.get("admin1") or ""
    country = item.get("country") or ""
    if admin and name and name not in admin:
        label = f"{admin} {name}"
    else:
        label = admin or name
    if with_country and item.get("country_code") != "CN" and country:
        label = f"{label}·{country}" if label else country
    return label or "未知地点"


@register(
    PLUGIN_NAME,
    "qflm",
    "天气查询：/天气 地点 查询今天与未来 3 天天气，数据来自 Open-Meteo（免费无需 Key）",
    "1.0.1",
)
class WeatherPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config if config is not None else {}

        self.default_city = str(self.config.get("default_city", "") or "").strip()
        self.language = str(self.config.get("language", "zh") or "zh")
        self.timeout = self._cfg_int("timeout", 10, 3, 60)
        self.cache_minutes = self._cfg_int("cache_minutes", 10, 0, 1440)
        self.forecast_days = self._cfg_int("forecast_days", 3, 0, 7)
        self.geo_count = self._cfg_int("geo_count", 5, 1, 20)
        self.show_source = bool(self.config.get("show_source", True))
        self.show_alternatives = bool(self.config.get("show_alternatives", True))

        self._http: Optional[aiohttp.ClientSession] = None
        self._geo_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
        self._wx_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}

    # ------------------------------------------------------------------ 工具

    def _cfg_int(self, key: str, default: int, lo: int, hi: int) -> int:
        try:
            val = int(self.config.get(key, default))
        except (TypeError, ValueError):
            val = default
        return max(lo, min(hi, val))

    @staticmethod
    def _norm(text: str) -> str:
        t = (text or "").strip()
        return re.sub(r"^[/!！。．.\s]+", "", t).strip()

    def _extract_keyword(self, event: AstrMessageEvent, cmd: str) -> str:
        text = self._norm(getattr(event, "message_str", "") or "")
        if text.startswith(cmd):
            text = text[len(cmd):]
        return text.strip()

    async def _session(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession(trust_env=True)
        return self._http

    async def _get_json(self, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        session = await self._session()
        full = f"{url}?{urlencode(params)}"
        async with session.get(
            full,
            headers=HEADERS,
            timeout=aiohttp.ClientTimeout(total=self.timeout),
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            return await resp.json(content_type=None)

    # ------------------------------------------------------------ 地理编码

    async def geocode(self, keyword: str) -> List[Dict[str, Any]]:
        key = f"{keyword}|{self.language}"
        now = time.time()
        cached = self._geo_cache.get(key)
        if cached and now - cached[0] < 86400:
            return cached[1]

        data = await self._get_json(
            GEO_API,
            {
                "name": keyword,
                "count": self.geo_count,
                "language": self.language,
                "format": "json",
            },
        )
        results = [r for r in (data.get("results") or []) if r.get("latitude") is not None]
        self._geo_cache[key] = (now, results)
        return results

    # ------------------------------------------------------------ 天气数据

    async def get_weather(self, lat: float, lon: float, days: int = 1) -> Dict[str, Any]:
        key = f"{round(lat, 3)},{round(lon, 3)},{days}"
        now = time.time()
        if self.cache_minutes > 0:
            cached = self._wx_cache.get(key)
            if cached and now - cached[0] < self.cache_minutes * 60:
                return cached[1]

        data = await self._get_json(
            FORECAST_API,
            {
                "latitude": lat,
                "longitude": lon,
                "current": CURRENT_FIELDS,
                "daily": DAILY_FIELDS,
                "timezone": "auto",
                "forecast_days": max(1, days),
            },
        )
        if self.cache_minutes > 0:
            self._wx_cache[key] = (now, data)
        return data

    # -------------------------------------------------------------- 渲染

    def render(self, place: str, data: Dict[str, Any], alternatives: List[str]) -> str:
        cur = data.get("current") or {}
        daily = data.get("daily") or {}

        def first(name: str, default=None):
            vals = daily.get(name) or []
            return vals[0] if vals else default

        is_day = bool(cur.get("is_day", 1))
        cur_desc, cur_icon = wmo_desc(cur.get("weather_code"), is_day)
        day_desc, day_icon = wmo_desc(first("weather_code"), True)

        lines = [f"{cur_icon} {place} · 今日天气", "──────────────"]
        lines.append(
            f"🌡 当前 {fmt_num(cur.get('temperature_2m'))}°C"
            f"（体感 {fmt_num(cur.get('apparent_temperature'))}°C）　{cur_desc}"
        )
        speed = cur.get("wind_speed_10m")
        lines.append(
            f"💧 湿度 {fmt_num(cur.get('relative_humidity_2m'), 0)}%　"
            f"🌬 {wind_dir(cur.get('wind_direction_10m'))}风 "
            f"{beaufort(speed)} 级（{fmt_num(speed)} km/h）"
        )
        lines.append("")
        lines.append(
            f"📅 今日 {fmt_num(first('temperature_2m_min'))}° ~ "
            f"{fmt_num(first('temperature_2m_max'))}°　{day_icon} {day_desc}"
        )
        pop = first("precipitation_probability_max")
        if pop is not None:
            lines.append(f"🌧 降水概率 {fmt_num(pop, 0)}%")
        uv = first("uv_index_max")
        if uv is not None:
            lines.append(f"🕶 紫外线 {fmt_num(uv)}（{uv_level(uv)}）")
        sunrise, sunset = first("sunrise"), first("sunset")
        if sunrise and sunset:
            lines.append(f"🌅 日出 {fmt_clock(sunrise)}　🌇 日落 {fmt_clock(sunset)}")

        rows = self._forecast_rows(daily)
        if rows:
            lines.append("")
            lines.append(f"📆 未来 {len(rows)} 天")
            lines.extend(rows)

        if self.show_alternatives and alternatives:
            lines.append("")
            lines.append("💡 也可以查：" + " · ".join(alternatives[:2]))
        if self.show_source:
            lines.append("")
            lines.append("数据来源 Open-Meteo")
        return "\n".join(lines)

    def _forecast_rows(self, daily: Dict[str, Any]) -> List[str]:
        """把 daily 数组里「明天起」的 N 天渲染成一行一天的紧凑格式"""
        if self.forecast_days <= 0:
            return []

        def col(name: str) -> List[Any]:
            return daily.get(name) or []

        dates = col("time")
        codes = col("weather_code")
        tmax = col("temperature_2m_max")
        tmin = col("temperature_2m_min")
        pops = col("precipitation_probability_max")

        rows: List[str] = []
        # index 0 是今天，未来预报从 1 开始
        for i in range(1, min(len(dates), self.forecast_days + 1)):
            desc, icon = wmo_desc(codes[i] if i < len(codes) else None, True)
            lo = fmt_num(tmin[i], 0) if i < len(tmin) else "-"
            hi = fmt_num(tmax[i], 0) if i < len(tmax) else "-"
            pop_val = pops[i] if i < len(pops) else None
            pop = f"{fmt_num(pop_val, 0)}%" if pop_val is not None else "-"
            rows.append(
                f"{fmt_day(dates[i])}　{icon} {desc}　{lo}°~{hi}°　💧{pop}"
            )
        return rows

    # -------------------------------------------------------------- 指令

    @filter.command("天气帮助")
    async def cmd_help(self, event: AstrMessageEvent):
        """天气帮助"""
        yield event.plain_result(help_text(self.default_city, self.forecast_days))

    @filter.command("天气")
    async def cmd_weather(self, event: AstrMessageEvent):
        """天气 <地点>

        注意：AstrBot 的 CommandFilter 会跳过 self 和 event，把其余参数当指令参数解析，
        所以这里不能写 *args（VAR_POSITIONAL 会触发 `_empty() takes no arguments`）。
        地点统一从 event.message_str 里取。
        """
        keyword = self._extract_keyword(event, "天气")
        if keyword in ("帮助", "help", "?", "？"):
            yield event.plain_result(help_text(self.default_city, self.forecast_days))
            return

        if not keyword:
            if not self.default_city:
                yield event.plain_result(help_text(self.default_city, self.forecast_days))
                return
            keyword = self.default_city

        try:
            # 支持直接输入经纬度
            m = COORD_RE.match(keyword)
            if m:
                lat, lon = float(m.group(1)), float(m.group(2))
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    yield event.plain_result("⚠️ 经纬度超出范围了，纬度 -90~90，经度 -180~180")
                    return
                place = fmt_coord(lat, lon)
                alternatives: List[str] = []
            else:
                # 国外城市先用英文名兜底，查不到再退回原词
                query = CITY_ALIAS.get(keyword, keyword)
                results = await self.geocode(query)
                if not results and query != keyword:
                    results = await self.geocode(keyword)
                if not results:
                    yield event.plain_result(
                        f"🔍 没找到「{keyword}」这个地点\n"
                        "试试更完整的名称，比如「北京市」「上海市」或「Tokyo」"
                    )
                    return
                best = pick_best(results)
                lat, lon = float(best["latitude"]), float(best["longitude"])
                place = place_label(best)
                # 只提示同国家的同名地点，避免出现「东京 → 巴布亚新几内亚 Tokyo」这种噪音；
                # 走了英文别名的情况也不再提示（那批结果基本都是无关同名地）
                alternatives = (
                    []
                    if query != keyword
                    else [
                        place_label(r)
                        for r in results
                        if r is not best
                        and r.get("country_code") == best.get("country_code")
                        and place_label(r) != place
                    ]
                )

            data = await self.get_weather(lat, lon, self.forecast_days + 1)
        except Exception as e:
            logger.error(f"[天气] 查询失败: {e}")
            yield event.plain_result("❌ 天气服务暂时不可用，请稍后再试")
            return

        yield event.plain_result(self.render(place, data, alternatives))

    # ------------------------------------------------------------ 生命周期

    async def terminate(self):
        if self._http and not self._http.closed:
            try:
                await self._http.close()
            except Exception:
                pass
        self._http = None
