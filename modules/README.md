# Bundled modules

These projects are maintained in the same Git repository as the Monitor dashboard. Their code and documentation are versioned together while each module keeps its own entrypoint and dependency manifest.

- `meteora-lp/meteora-radar`: Meteora meme-token screening and Feishu alerts.
- `token-reversal-monitor`: Streamlit reversal-signal monitor.
- `robinhoodpools`: Robinhood Chain pool indexing service and API.
- `robinhood-chain-lp-tools`: Uniswap v3/v4 range APR tools.

Local credentials, runtime state, databases, Python caches, and `node_modules` are excluded from Git.
