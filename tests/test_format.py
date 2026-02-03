
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from execution.scripts.send_daily_report import format_price_message

# Mock data
mock_prices = [
    {"month": "SEP/25", "price": 105.40, "change": 0.10, "pct_change": 0.09},
    {"month": "OCT/25", "price": 103.20, "change": -0.35, "pct_change": -0.34},
    {"month": "NOV/25", "price": 103.10, "change": -0.30, "pct_change": -0.29},
    {"month": "DEC/25", "price": 100.00, "change": 0.00, "pct_change": 0.00},
]

print("--- TESTING FORMAT ---")
message = format_price_message(mock_prices)
print(message)
print("----------------------")
