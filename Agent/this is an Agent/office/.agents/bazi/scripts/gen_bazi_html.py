#!/usr/bin/env python3
"""
八字命理分析 HTML 报告生成器
输入 JSON 数据，输出自包含 HTML 网页并在浏览器打开。

用法：
  python3 gen_bazi_html.py --input /tmp/bazi_data.json --output ~/Desktop/八字分析.html

JSON 格式示例见文件末尾注释。
"""
import argparse
import json
import os
import sys
import webbrowser
from datetime import datetime

STAR_MAP = {"★": 1, "★★": 2, "★★★": 3, "★★★★": 4, "★★★★★": 5}

def stars_html(s, max_stars=5):
    n = STAR_MAP.get(s, 0) if isinstance(s, str) else int(s)
    filled = "★" * n
    empty = "☆" * (max_stars - n)
    color = ["#f44336","#ff9800","#ffc107","#8bc34a","#4caf50"][max(0, n-1)]
    return f'<span style="color:{color};font-size:1.1em">{filled}</span><span style="color:#444">{empty}</span>'

def generate_html(data, output_path):
    name      = data.get("name", "命主")
    birth     = data.get("birth", "")
    gender    = data.get("gender", "")
    school    = data.get("school", "换日派")
    rizhu     = data.get("rizhu", "")
    wuxing    = data.get("wuxing", {})          # {"金":2,"木":1,"水":3,"火":1,"土":1}
    yongshen  = data.get("yongshen", "")
    jishen    = data.get("jishen", "")
    shengru   = data.get("shengru", "身强")
    sizhu     = data.get("sizhu", [])           # [{col,tg,dz,shishen,canggan}×4]
    dayun     = data.get("dayun", [])           # [{seq,gz,age,guoyun,sy,cy,spq,total,key}]
    liuyear   = data.get("liuyear", [])         # [{year,gz,sy,cy,spq,key}]
    liuyue    = data.get("liuyue", [])          # [{month,astro,sy,cy,spq,key}]
    liuyin    = data.get("liuyin", [])          # [{day,astro,meaning}]
    summary   = data.get("summary", {})         # {career,wealth,love,health,advice}
    astrology_advice = data.get("astrology_advice", {})  # {character,career,love,growth}
    current_yun = data.get("current_yun", "")

    # ── 五行条形 ──
    wuxing_order = [("金","#c0c0c0"),("木","#4caf50"),("水","#2196f3"),("火","#f44336"),("土","#ff9800")]
    max_wx = max(wuxing.values()) if wuxing else 1
    wx_bars = ""
    for wx, color in wuxing_order:
        val = wuxing.get(wx, 0)
        pct = int(val / max_wx * 100)
        wx_bars += f"""
        <div class="wx-row">
          <span class="wx-label">{wx}</span>
          <div class="wx-bar-bg"><div class="wx-bar" style="width:{pct}%;background:{color}"></div></div>
          <span class="wx-val">{val}</span>
        </div>"""

    # ── 四柱表 ──
    cols = ["年柱","月柱","日柱","时柱"]
    sz_tg = sz_dz = sz_ss = sz_cg = ""
    for i, col in enumerate(sizhu):
        sz_tg += f'<td>{col.get("tg","")}<br><small style="color:#888">{col.get("tg_wuxing","")}</small></td>'
        sz_dz += f'<td>{col.get("dz","")}<br><small style="color:#888">{col.get("dz_wuxing","")}</small></td>'
        ss = col.get("shishen","—")
        ss_color = "#c9a84c" if i == 2 else ("#4caf50" if ss in ["食神","正财","正印","正官"] else "#e8dcc8")
        sz_ss += f'<td style="color:{ss_color}">{ss}</td>'
        sz_cg += f'<td style="font-size:.8em;color:#888">{col.get("canggan","")}</td>'

    sizhu_html = f"""
    <table class="sizhu-table">
      <tr><th></th>{''.join(f"<th>{c}</th>" for c in cols)}</tr>
      <tr><td class="row-label">天干</td>{sz_tg}</tr>
      <tr><td class="row-label">地支</td>{sz_dz}</tr>
      <tr><td class="row-label">十神</td>{sz_ss}</tr>
      <tr><td class="row-label">藏干</td>{sz_cg}</tr>
    </table>"""

    # ── 大运评分表 ──
    dy_rows = ""
    for dy in dayun:
        age  = dy.get("age","")
        gz   = dy.get("gz","")
        gy   = dy.get("guoyun","")
        sy   = dy.get("sy","")
        cy   = dy.get("cy","")
        spq  = dy.get("spq","")
        tot  = dy.get("total","")
        key  = dy.get("key","")
        is_cur = gz == current_yun
        row_style = 'class="cur-yun"' if is_cur else ""
        cur_badge = ' <span class="cur-badge">当前</span>' if is_cur else ""
        dy_rows += f"""<tr {row_style}>
          <td>{gz}{cur_badge}</td>
          <td>{age}岁</td>
          <td style="font-size:.8em;color:#888">{gy}</td>
          <td>{stars_html(sy)}</td>
          <td>{stars_html(cy)}</td>
          <td>{stars_html(spq)}</td>
          <td style="font-weight:bold">{stars_html(tot)}</td>
          <td style="font-size:.85em;color:#888">{key}</td>
        </tr>"""

    # ── 流年表 ──
    ly_rows = ""
    for ly in liuyear:
        ly_rows += f"""<tr>
          <td>{ly.get("year","")}</td>
          <td>{ly.get("gz","")}</td>
          <td>{stars_html(ly.get("sy",""))}</td>
          <td>{stars_html(ly.get("cy",""))}</td>
          <td>{stars_html(ly.get("spq",""))}</td>
          <td style="font-size:.85em;color:#888">{ly.get("key","")}</td>
        </tr>"""

    # ── 流月表 ──
    lyue_rows = ""
    for ym in liuyue:
        lyue_rows += f"""<tr>
          <td>{ym.get("month","")}</td>
          <td style="font-size:.8em;color:#888">{ym.get("astro","")}</td>
          <td>{stars_html(ym.get("sy",""))}</td>
          <td>{stars_html(ym.get("cy",""))}</td>
          <td>{stars_html(ym.get("spq",""))}</td>
          <td style="font-size:.85em;color:#888">{ym.get("key","")}</td>
        </tr>"""

    # ── 流日表 ──
    lyin_rows = ""
    for yd in liuyin:
        lyin_rows += f"""<tr>
          <td>{yd.get("day","")}</td>
          <td style="font-size:.8em;color:#888">{yd.get("astro","")}</td>
          <td style="font-size:.85em">{yd.get("meaning","")}</td>
        </tr>"""

    # ── 占星分析 ──
    sun_sign = data.get("sun_sign", "")
    moon_sign = data.get("moon_sign", "")
    rising_sign = data.get("rising_sign", "")
    astrology_summary = data.get("astrology_summary", "")
    astrology_scores = data.get("astrology_scores", [])

    # 占星评分表HTML
    astro_rows = ""
    if astrology_scores:
        for score in astrology_scores:
            star = score.get("star", "")
            traits = score.get("traits", "")
            warmth = score.get("warmth", "")
            expression = score.get("expression", "")
            social = score.get("social", "")
            creativity = score.get("creativity", "")
            stability = score.get("stability", "")
            key = score.get("key", "")
            astro_rows += f"""<tr>
              <td>{star}</td>
              <td style="font-size:.8em;color:#888">{traits}</td>
              <td>{stars_html(warmth)}</td>
              <td>{stars_html(expression)}</td>
              <td>{stars_html(social)}</td>
              <td>{stars_html(creativity)}</td>
              <td>{stars_html(stability)}</td>
              <td style="font-size:.85em;color:#888">{key}</td>
            </tr>"""

    astrology_html = ""
    if sun_sign or moon_sign or rising_sign or astrology_summary:
        astro_table = f"""<table class="astro-table">
    <tr><th>星座</th><th style="font-size:.75em">性格特质</th><th>温暖度</th><th>表达力</th><th>社交力</th><th>创意力</th><th>稳定性</th><th>核心特点</th></tr>
    {astro_rows}
  </table>""" if astro_rows else ""

        astrology_html = f"""
<div class="section">
  <h2>西 洋 占 星 分 析</h2>
  {astro_table}
  <div class="astro-info">
    <div class="astro-item"><div class="astro-label">太阳星座</div><div class="astro-value">{sun_sign}</div></div>
    <div class="astro-item"><div class="astro-label">月亮星座</div><div class="astro-value">{moon_sign}</div></div>
    <div class="astro-item"><div class="astro-label">上升星座</div><div class="astro-value">{rising_sign}</div></div>
  </div>
  <p style="color:#aaa;font-size:.9em;line-height:1.7;margin-top:12px">{astrology_summary}</p>
</div>"""

    # ── 综合建议 ──
    advice_html = ""
    icons = {"career":"💼","wealth":"💰","love":"❤️","health":"🏥","advice":"⭐"}
    labels = {"career":"事业","wealth":"财运","love":"感情","health":"健康","advice":"核心建议"}
    for k, label in labels.items():
        val = summary.get(k,"")
        if val:
            advice_html += f'<div class="advice-item"><span class="advice-icon">{icons[k]}</span><div><b>{label}</b><p>{val}</p></div></div>'

    # ── 占星综合建议 ──
    astro_advice_html = ""
    astro_icons = {"character":"✨","career":"🌟","love":"💫","growth":"🎯"}
    astro_labels = {"character":"性格特质","career":"事业方向","love":"感情建议","growth":"核心成长"}
    for k, label in astro_labels.items():
        val = astrology_advice.get(k,"")
        if val:
            astro_advice_html += f'<div class="advice-item"><span class="advice-icon">{astro_icons[k]}</span><div><b>{label}</b><p>{val}</p></div></div>'

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>{name} · 八字命理与星座报告</title>
<style>
  :root{{--gold:#c9a84c;--bg:#0a0c0f;--panel:#111418;--border:#2a2e35;--text:#e8dcc8}}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,"Segoe UI",SimSun,sans-serif;padding:30px 20px;max-width:900px;margin:0 auto}}
  h1{{color:var(--gold);font-size:1.8em;letter-spacing:.3em;margin-bottom:4px;text-align:center}}
  .subtitle{{color:#888;font-size:.85em;text-align:center;letter-spacing:.1em;margin-bottom:24px}}
  .section{{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:20px;margin-bottom:20px}}
  .section h2{{color:var(--gold);font-size:1em;letter-spacing:.2em;margin-bottom:14px;padding-bottom:8px;border-bottom:1px solid var(--border)}}
  .info-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}
  .info-item{{background:#0d1014;border-radius:6px;padding:10px 14px}}
  .info-item .label{{color:#666;font-size:.75em;margin-bottom:4px}}
  .info-item .value{{color:var(--gold);font-size:1em}}
  .sizhu-table{{width:100%;border-collapse:collapse;text-align:center}}
  .sizhu-table th{{background:#1a1d22;color:var(--gold);padding:8px;font-size:.85em}}
  .sizhu-table td{{padding:8px;border:1px solid var(--border);font-size:.9em}}
  .row-label{{color:#666;font-size:.8em;background:#0d1014}}
  .wx-row{{display:flex;align-items:center;margin-bottom:8px}}
  .wx-label{{width:24px;color:var(--gold);font-size:.9em}}
  .wx-bar-bg{{flex:1;height:8px;background:#1a1d22;border-radius:4px;margin:0 10px}}
  .wx-bar{{height:8px;border-radius:4px;transition:.3s}}
  .wx-val{{width:24px;color:#888;font-size:.85em;text-align:right}}
  .badge{{display:inline-block;padding:2px 10px;border-radius:12px;font-size:.8em;font-weight:bold;color:#fff;margin:2px}}
  .dayun-table,.liuyear-table{{width:100%;border-collapse:collapse;font-size:.88em}}
  .dayun-table th,.liuyear-table th{{background:#1a1d22;color:var(--gold);padding:8px 6px;text-align:center}}
  .dayun-table td,.liuyear-table td{{padding:7px 6px;border-bottom:1px solid var(--border);text-align:center;vertical-align:middle}}
  .cur-yun td{{background:#1a1e14;border-left:2px solid var(--gold)}}
  .cur-badge{{background:var(--gold);color:#000;font-size:.7em;padding:1px 6px;border-radius:8px;margin-left:4px;font-weight:bold}}
  .advice-item{{display:flex;gap:12px;padding:12px 0;border-bottom:1px solid var(--border)}}
  .advice-item:last-child{{border-bottom:none}}
  .advice-icon{{font-size:1.4em;flex-shrink:0;margin-top:2px}}
  .advice-item p{{color:#aaa;font-size:.9em;margin-top:4px;line-height:1.7}}
  .tags{{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}}
  .astro-info{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:16px}}
  .astro-item{{background:#0d1014;border-radius:6px;padding:12px 10px;text-align:center}}
  .astro-label{{color:#666;font-size:.75em;margin-bottom:6px;display:block}}
  .astro-value{{color:var(--gold);font-size:.95em;font-weight:bold}}
  .astro-table{{width:100%;border-collapse:collapse;font-size:.85em;margin-bottom:12px}}
  .astro-table th{{background:#1a1d22;color:var(--gold);padding:8px 6px;text-align:center}}
  .astro-table td{{padding:7px 6px;border-bottom:1px solid var(--border);text-align:center;vertical-align:middle}}
  .liuyue-table,.liuyin-table{{width:100%;border-collapse:collapse;font-size:.85em}}
  .liuyue-table th,.liuyin-table th{{background:#1a1d22;color:var(--gold);padding:8px 6px;text-align:center}}
  .liuyue-table td,.liuyin-table td{{padding:7px 6px;border-bottom:1px solid var(--border);text-align:center;vertical-align:middle}}
  .footer{{text-align:center;color:#444;font-size:.8em;margin-top:30px;padding-top:16px;border-top:1px solid var(--border)}}
</style>
</head>
<body>
<h1>{name} · 八字命理与星座报告</h1>
<p class="subtitle">{birth} · {gender} · {school} · {rizhu}日主 · {shengru} · {sun_sign} {moon_sign} {rising_sign}</p>

<div class="section">
  <h2>命 盘 信 息</h2>
  <div class="info-grid">
    <div class="info-item"><div class="label">用神</div><div class="value">{yongshen}</div></div>
    <div class="info-item"><div class="label">忌神</div><div class="value">{jishen}</div></div>
    <div class="info-item"><div class="label">身强/弱</div><div class="value">{shengru}</div></div>
  </div>
</div>

<div class="section">
  <h2>四 柱 排 盘</h2>
  {sizhu_html}
</div>

<div class="section">
  <h2>五 行 分 布</h2>
  {wx_bars}
</div>

<div class="section">
  <h2>大 运 综 合 评 分（含九运国运）</h2>
  <table class="dayun-table">
    <tr><th>大运</th><th>年龄</th><th>国运</th><th>事业</th><th>财富</th><th>生活品质</th><th>综合</th><th>关键词</th></tr>
    {dy_rows}
  </table>
</div>

{'<div class="section"><h2>近 期 流 年</h2><table class="liuyear-table"><tr><th>年份</th><th>干支</th><th>事业</th><th>财富</th><th>生活品质</th><th>关键词</th></tr>' + ly_rows + '</table></div>' if ly_rows else ''}

<div class="section">
  <h2>命 理 综 合 建 议</h2>
  {advice_html}
</div>

{astrology_html}

{'<div class="section"><h2>近 期 流 月（占星视角）</h2><table class="liuyue-table"><tr><th>月份</th><th>占星星位</th><th>事业</th><th>财富</th><th>生活品质</th><th>提示</th></tr>' + lyue_rows + '</table></div>' if lyue_rows else ''}

{'<div class="section"><h2>近 期 流 日（关键节点）</h2><table class="liuyin-table"><tr><th>日期</th><th>月亮星位</th><th>运势提示</th></tr>' + lyin_rows + '</table></div>' if lyin_rows else ''}

<div class="section">
  <h2>星 座 综 合 建 议</h2>
  {astro_advice_html}
</div>

<p class="footer">命理分析仅供文化研究参考，人生在于自身的努力与选择。<br>生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[✓] 已生成: {output_path}")
    return output_path


def open_browser(path):
    abs_path = os.path.abspath(path)
    url = f"file://{abs_path}"
    try:
        webbrowser.open(url)
        print("[✓] 已在浏览器打开")
    except Exception:
        print(f"[!] 请手动打开: {url}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="八字命理 HTML 报告生成器")
    parser.add_argument("--input",  required=True, help="JSON 数据文件路径")
    parser.add_argument("--output", default=os.path.expanduser("~/Desktop/八字分析.html"))
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)

    out = generate_html(data, args.output)
    if not args.no_open:
        open_browser(out)


# ── JSON 格式说明 ──────────────────────────────────────────────────────────────
# {
#   "name": "张译文",
#   "birth": "1999年8月15日 23:55",
#   "gender": "男",
#   "school": "换日派",
#   "rizhu": "庚",
#   "shengru": "身强偏旺",
#   "yongshen": "火（七杀）、水（食神）",
#   "jishen": "土（正印）、金（比劫）",
#   "wuxing": {"金": 2, "木": 1, "水": 3, "火": 1, "土": 1},
#   "sizhu": [
#     {"tg": "己", "tg_wuxing": "阴土", "dz": "卯", "dz_wuxing": "阴木", "shishen": "正印", "canggan": "乙木(正财)"},
#     {"tg": "壬", "tg_wuxing": "阳水", "dz": "申", "dz_wuxing": "阳金", "shishen": "食神", "canggan": "庚(比)壬(食)戊(印)"},
#     {"tg": "庚", "tg_wuxing": "阳金", "dz": "子", "dz_wuxing": "阳水", "shishen": "日主", "canggan": "癸水(伤官)"},
#     {"tg": "丙", "tg_wuxing": "阳火", "dz": "子", "dz_wuxing": "阳水", "shishen": "七杀", "canggan": "癸水(伤官)"}
#   ],
#   "current_yun": "庚午",
#   "dayun": [
#     {"gz":"壬申","age":"0–13","guoyun":"八白土(忌-1)","sy":"★★","cy":"★★","spq":"★★","total":"★★","key":"命盘本气"},
#     {"gz":"辛未","age":"13–23","guoyun":"八白→九紫","sy":"★★","cy":"★★","spq":"★★","total":"★★","key":"竞争压力大"},
#     {"gz":"庚午","age":"23–33","guoyun":"九紫火(用+1)","sy":"★★★★","cy":"★★★","spq":"★★★","total":"★★★","key":"蓄力播种期"},
#     {"gz":"己巳","age":"33–43","guoyun":"九紫鼎盛(用+1)","sy":"★★★★★","cy":"★★★★","spq":"★★★★","total":"★★★★","key":"第一爆发窗口"},
#     {"gz":"戊辰","age":"43–53","guoyun":"九紫/一白衔接","sy":"★★★","cy":"★★★","spq":"★★★","total":"★★★","key":"承上启下"},
#     {"gz":"丁卯","age":"53–63","guoyun":"一白水(用+1)","sy":"★★★★★","cy":"★★★★★","spq":"★★★★","total":"★★★★★","key":"人生巅峰"},
#     {"gz":"丙寅","age":"63–73","guoyun":"一白/二黑","sy":"★★★★","cy":"★★★★","spq":"★★★★","total":"★★★★","key":"晚年活跃"},
#     {"gz":"乙丑","age":"73–83","guoyun":"二黑土(忌-1)","sy":"★★","cy":"★★★","spq":"★★","total":"★★","key":"守成为主"},
#     {"gz":"甲子","age":"83–93","guoyun":"三碧木(喜+0.5)","sy":"★★★","cy":"★★★★","spq":"★★★★★","total":"★★★★","key":"高寿智慧"}
#   ],
#   "liuyear": [
#     {"year":"2026","gz":"丙午","sy":"★★★★","cy":"★★★","spq":"★★★","key":"事业冲刺，防冲动"},
#     {"year":"2027","gz":"丁未","sy":"★★★★","cy":"★★★","spq":"★★","key":"续强有压制"},
#     {"year":"2028","gz":"戊申","sy":"★★★","cy":"★★","spq":"★★","key":"充电整合年"}
#   ],
#   "summary": {
#     "career": "建禄格取食神用，最适合创意表达/策划分析/技术方向，33岁后拓展版图。",
#     "wealth": "正财星卯木弱，财来财去，需强制储蓄制度弥补命格缺陷。",
#     "love": "伤官见官，感情中自我意识强，当前子午冲波动明显，33岁后渐稳。",
#     "health": "水旺注意心血管，金旺注意肝胆，规律运动排解七杀压力。",
#     "advice": "大器晚成格局，33-43岁第一爆发，53岁绝对巅峰。现阶段最重要：选对赛道、积累实力、管好财务。"
#   }
# }
