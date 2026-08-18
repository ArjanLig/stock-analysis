# Lazy Theta

**Stock analysis and portfolio management for options traders.**

Live at [lazytheta.io](https://lazytheta.io)

## What it does

- **Portfolio** — Live overview across every connected broker: deployment and dry powder, contribution per position, performance against the S&P since each purchase, and sector/country exposure
- **Watchlist** — DCF valuations using Damodaran methodology with automated SEC EDGAR data, peer comparison, and scenario analysis
- **Cost Basis** — Per-ticker cost basis and full trade history, for wheels (covered calls + cash-secured puts) and plain holdings alike, open and closed
- **Results** — Performance tracking with net liquidation history, benchmark comparison, and yearly returns
- **Screener** — Index universe (S&P 500, Nasdaq 100, Dow 30) filtered on quality: average ROCE ≥ 20% over the last 5-10 years and no net debt

## Built with

- [Streamlit](https://streamlit.io) — Web framework
- [Supabase](https://supabase.com) — Authentication and cloud storage (Row Level Security)
- [Tastytrade API](https://developer.tastytrade.com) — Portfolio data and margin calculations
- [SEC EDGAR](https://www.sec.gov/edgar) — Financial statements and company data

## Running locally

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Requires a `.streamlit/secrets.toml` with:

```toml
SUPABASE_URL = "your-supabase-url"
SUPABASE_ANON_KEY = "your-anon-key"
```

## Security

See [SECURITY.md](SECURITY.md) for security measures, data storage details, and how to report vulnerabilities.

## License

All rights reserved.
