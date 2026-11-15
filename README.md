# cbbtc-sunday-rider

[![make.fun · btc-race-24h-20261115-0828](https://make.fun/badge/btc-race-24h-20261115-0828.svg)](https://make.fun/bounty/btc-race-24h-20261115-0828?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261115-0828)
[![rank](https://make.fun/badge/btc-race-24h-20261115-0828/steveone23/cbbtc-sunday-rider/rank.svg)](https://make.fun/bounty/btc-race-24h-20261115-0828/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261115-0828)
[![reward](https://make.fun/badge/btc-race-24h-20261115-0828/steveone23/cbbtc-sunday-rider/reward.svg)](https://make.fun/bounty/btc-race-24h-20261115-0828?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261115-0828)


An entry for the make.fun bounty [Race a BTC trading bot live for 24 hours](https://make.fun/bounty/btc-race-24h-20261115-0828).

A long-only bot that trades Coinbase Wrapped BTC (cbBTC) against USDC, one-minute bar by bar, over
the agent-fun/bars-replay/v1 protocol. It holds either cash or one position, opened by one of two
modules:

- Ladder module: when the price is above its moving average, buys half of its equity, adds the
  other half on a small pullback, banks a small net profit target and gives up on a stop loss. It
  runs until it has banked or stopped once.
- Washout module: buys a bounce off a sharp drop from the recent high with all remaining cash, on
  top of a running ladder if there is one, and exits on a profit target, an armed trailing stop or
  a stop loss. It stays armed after the ladder is done.

Entries and exits are split into small slices over consecutive bars, the bot is flat before the
window ends, and price history is seeded from `data/seed.json` so the indicators are ready on the
first bar. The bot is deterministic, uses the Python standard library only and never touches the
network.

## Run

The grader runs it as:

    python3 -P -s main.py

It reads the harness messages on stdin and writes one reply per batch on stdout, through the
vendored SDK shim in `vendor/makefun_bars.py`.

To rebuild the seed history (build time only, fetches public Binance klines):

    python tools/make_seed.py

## License

MIT, see [LICENSE](LICENSE).
