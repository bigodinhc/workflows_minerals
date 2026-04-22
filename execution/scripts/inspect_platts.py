#!/usr/bin/env python3
"""Inspect Platts actor output — runs the Apify actor and dumps what it collected.

Usage: python -m execution.scripts.inspect_platts [--target-date DD/MM/YYYY]

Does NOT touch Redis or Telegram. Pure read-only inspection.
"""
import argparse
import json
import os
import sys
from datetime import datetime

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from execution.integrations.apify_client import ApifyClient

ACTOR_ID = os.getenv("APIFY_PLATTS_ACTOR_ID", "bigodeio05/platts-scrap-full-news")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-date", type=str, default="",
                        help="Data alvo DD/MM/YYYY. Vazio = hoje.")
    args = parser.parse_args()

    today_br = args.target_date or datetime.now().strftime("%d/%m/%Y")
    print(f"🔍 Rodando actor {ACTOR_ID} para data: {today_br}")

    run_input = {
        "username": os.getenv("PLATTS_USERNAME", ""),
        "password": os.getenv("PLATTS_PASSWORD", ""),
        "sources": ["allInsights", "ironOreTopic", "rmw"],
        "includeFlash": True,
        "includeLatest": True,
        "maxArticles": 50,
        "maxArticlesPerRmwTab": 5,
        "latestMaxItems": 15,
        "dateFilter": "today",
        "concurrency": 2,
        "dedupArticles": True,
    }
    if args.target_date:
        run_input["targetDate"] = args.target_date
        run_input["dateFormat"] = "BR"
        run_input["dateFilter"] = "all"

    client = ApifyClient()
    dataset_id = client.run_actor(ACTOR_ID, run_input, memory_mbytes=8192, timeout_secs=900)
    items = client.get_dataset_items(dataset_id)

    if not items:
        print("❌ Nenhum item retornado pelo actor.")
        return

    wrapper = items[0] if isinstance(items[0], dict) else {}

    # Summary
    summary = wrapper.get("summary", {})
    print(f"\n{'='*60}")
    print(f"📊 SUMMARY DO ACTOR")
    print(f"{'='*60}")
    print(json.dumps(summary, indent=2, default=str))

    # Flash
    flash = wrapper.get("flash", [])
    print(f"\n🔴 FLASH: {len(flash)} item(s)")
    for f in flash:
        print(f"   • {f.get('title', '?')[:80]}")

    # Top News
    top_news = wrapper.get("topNews", [])
    print(f"\n⭐ TOP NEWS: {len(top_news)} artigos")
    for a in top_news:
        print(f"   • [{a.get('source', '?')}] {a.get('title', '?')[:80]}")
        print(f"     📅 {a.get('publishDate', '?')} | ✍️ {a.get('author', '?')}")

    # Latest
    latest = wrapper.get("latest", [])
    print(f"\n📰 LATEST: {len(latest)} artigos")
    for a in latest:
        print(f"   • [{a.get('source', '?')}] {a.get('title', '?')[:80]}")
        print(f"     📅 {a.get('publishDate', '?')}")

    # News & Insights (ironOreTopic)
    news_insights = wrapper.get("newsInsights", [])
    print(f"\n📃 NEWS & INSIGHTS (Iron Ore Topic): {len(news_insights)} artigos")
    for a in news_insights:
        print(f"   • {a.get('title', '?')[:80]}")
        print(f"     📅 {a.get('publishDate', '?')}")

    # RMW — o mais importante
    rmw = wrapper.get("rmw", [])
    total_rmw = sum(len(g.get("articles", [])) for g in rmw)
    print(f"\n{'='*60}")
    print(f"📋 RAW MATERIALS WORKSPACE: {total_rmw} artigos em {len(rmw)} tabs")
    print(f"{'='*60}")
    for group in rmw:
        tab_name = group.get("tabName", "?")
        articles = group.get("articles", [])
        print(f"\n   🗂️ Tab: {tab_name} ({len(articles)} artigos)")
        for a in articles:
            title = a.get("title", "?")[:80]
            date = a.get("gridDateTime", a.get("publishDate", "?"))
            source = a.get("source", "?")
            iodex = a.get("metadata", {}).get("iodexPrice", "")
            price_str = f" | IODEX ${iodex}" if iodex else ""
            words = a.get("metadata", {}).get("wordCount", "?")
            print(f"      • {title}")
            print(f"        📅 {date} | 📝 {words} palavras{price_str}")

    # Tab names encontradas
    tab_names = [g.get("tabName", "?") for g in rmw]
    print(f"\n{'='*60}")
    print(f"📑 TABS RMW ENCONTRADAS: {len(tab_names)}")
    print(f"{'='*60}")
    for i, t in enumerate(tab_names, 1):
        has_articles = len(rmw[i-1].get("articles", []))
        print(f"   {i}. {t} ({has_articles} artigos)")

    # Verificação BOT Summary
    bot_tabs = [t for t in tab_names if "bot" in t.lower() or "summary" in t.lower()]
    if bot_tabs:
        print(f"\n✅ BOT Summary tab(s) encontrada(s): {bot_tabs}")
    else:
        print(f"\n⚠️  Nenhuma tab com 'BOT' ou 'Summary' no nome encontrada!")
        print(f"   Tabs disponíveis: {tab_names}")

    # All articles flat count
    all_articles = wrapper.get("allArticles", [])
    print(f"\n📦 TOTAL allArticles (flat): {len(all_articles)}")

    # Precos
    prices = summary.get("pricesFound", [])
    iodex_prices = summary.get("iodexPrices", [])
    if prices:
        print(f"\n💰 Preços encontrados: {prices[:10]}")
    if iodex_prices:
        print(f"💎 IODEX: {iodex_prices}")


if __name__ == "__main__":
    main()
