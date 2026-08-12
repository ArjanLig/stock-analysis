# Lazy Theta

**Stock analysis and portfolio management for options traders.**

Live at [lazytheta.io](https://lazytheta.io)

## What it does

- **Portfolio** — Live portfolio overview with Greeks, Beta-Weighted Delta, margin requirements, and exposure analysis via Tastytrade API
- **Watchlist** — DCF valuations using Damodaran methodology with automated SEC EDGAR data, peer comparison, and scenario analysis
- **Cost Basis** — Per-ticker cost basis and full trade history, for wheels (covered calls + cash-secured puts) and plain holdings alike, open and closed
- **Results** — Performance tracking with net liquidation history, benchmark comparison, and yearly returns

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
