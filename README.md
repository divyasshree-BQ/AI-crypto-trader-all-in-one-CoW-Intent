# Complete AI Crypto Trading Bot — CoW Protocol Intents & Limit Orders on Base

An autonomous crypto trading bot that uses AI (GPT-4o or Claude) to make trading decisions from market and liquidity data, and executes via **CoW Protocol** (MEV-protected intents and limit orders) on Base.

## Features

- AI-powered decision making using GPT-4o or Claude Sonnet
- **CoW Protocol intents for MEV-protected trades** (solver-based execution)
- Liquidity flow tracking for smart money detection
- Real-time slippage awareness and execution cost analysis
- Automated position management (buy, hold, sell)
- Portfolio tracking with PnL calculations

## Architecture

The bot operates in continuous cycles, performing the following steps:

1. Fetches trade data from the market
2. Analyzes liquidity events to detect smart money flows
3. Calculates slippage data for execution cost awareness
4. Sends market data to AI for analysis and decision making
5. **Submits trades as CoW Protocol intents** (signed orders sent to solvers)
6. **Monitors intent fulfillment** by solvers competing for best execution
7. Manages open positions and risk limits
8. Tracks portfolio performance and success metrics

### CoW Protocol Integration

Instead of direct on-chain swaps, the bot uses CoW Protocol's intent-based trading:

```
AI Decision → Create Intent → EIP-712 Sign → Submit to CoW API → Solvers Compete → Settlement
```

| Before (Direct Uniswap) | After (CoW Intents) |
|-------------------------|---------------------|
| Build tx → Sign → Send to mempool | Build intent → Sign → Send to CoW API |
| Exposed to MEV (frontrunning) | MEV-protected (solvers execute privately) |
| You pay gas for every swap | Gasless orders (only pay for approval) |
| Single route (Uniswap V3) | Solvers find best route across DEXs |
| Tx can fail on-chain (lose gas) | Intent fails off-chain (no gas lost) |

**Benefits:**
- **MEV Protection**: Trades are executed by solvers, not exposed to frontrunning
- **Better Prices**: Solvers compete to find the best execution path
- **Gasless Orders**: You only pay gas for token approval (one-time per token)
- **Coincidence of Wants**: Orders can be matched peer-to-peer when possible

## Safety Features

- Portfolio value limit: $10.00
- Maximum position size: $1.00
- Daily loss limit: $2.00
- Minimum confidence threshold: 10%
- Maximum open positions: 3
- Automatic position closure on stop-loss or target

## Requirements

- Python 3.8+
- Web3 wallet with private key
- Base mainnet RPC access (Infura or similar)
- BItquery API key for market and liquidity data (create at https://account.BItquery.io/user/api_v2/access_tokens)
- OpenAI API key or Anthropic API key
- Initial USDC balance on Base chain

## Installation

1. Clone the repository
```bash
git clone https://github.com/divyasshree-BQ/AI-crypto-trader-all-in-one-CoW-Intent
cd AI-crypto-trader-all-in-one-CoW-Intent
```

2. Install dependencies
```bash
pip install -r requirements.txt
```

3. Get API Keys
- BItquery: Create an API key at https://account.BItquery.io/user/api_v2/access_tokens
- OpenAI: Get your API key from https://platform.openai.com/api-keys
- Anthropic (optional): Get from https://console.anthropic.com/
- Infura: Get Base RPC endpoint from https://infura.io/

4. Configure environment variables
Copy `.env.example` to `.env` and fill in your values:
```
RPC_URL=https://base-mainnet.infura.io/v3/YOUR_INFURA_API_KEY
CHAIN_ID=8453
PRIVATE_KEY=your_wallet_private_key
ANTHROPIC_API_KEY=sk-ant-... (optional)
OPENAI_API_KEY=sk-proj-...
BItquery_API_KEY=ory_at_...
PORTFOLIO_SIZE_USD=10
MAX_POSITION_SIZE_USD=1
SLIPPAGE_TOLERANCE=1.0
GAS_LIMIT=300000
MAX_GAS_PRICE_GWEI=50
DAILY_LOSS_LIMIT_USD=2
MAX_OPEN_POSITIONS=3
MIN_CONFIDENCE_THRESHOLD=10
```

## Usage

Run the bot with:
```bash
python3 main.py
```

The bot will:
- Load environment variables and validate configuration
- Initialize wallet connection on Base mainnet (Chain ID: 8453)
- Display current balance and safety limits
- Start the trading loop with 60-second intervals

## Configuration

Key settings can be configured via environment variables:

- **RPC_URL**: Base mainnet RPC endpoint (Infura recommended)
- **CHAIN_ID**: Blockchain chain ID (8453 for Base)
- **BItquery_API_KEY**: Required for fetching trade and liquidity data
- **AI provider**: Supports OpenAI (GPT-4o) or Anthropic (Claude)
- **PORTFOLIO_SIZE_USD**: Maximum portfolio value in USD
- **MAX_POSITION_SIZE_USD**: Maximum size per position in USD
- **DAILY_LOSS_LIMIT_USD**: Maximum daily loss before stopping
- **MAX_OPEN_POSITIONS**: Maximum number of concurrent positions
- **MIN_CONFIDENCE_THRESHOLD**: Minimum AI confidence % to execute trades
- **SLIPPAGE_TOLERANCE**: Acceptable slippage percentage
- **GAS_LIMIT**: Maximum gas units per transaction
- **MAX_GAS_PRICE_GWEI**: Maximum gas price in Gwei


## Trading Logic

The AI analyzes multiple data sources:
- Recent trade volumes and price movements
- Liquidity addition/removal events
- Slippage estimates and execution costs
- Market maker activity
- Buy vs sell pressure

Based on this analysis, the AI generates trading actions with:
- Confidence level (0-100%)
- Reasoning for the decision
- Entry price and position size
- Target price for profit taking
- Stop loss price for risk management

## Performance Tracking

The bot tracks:
- Number of open positions
- Daily PnL (Profit and Loss)
- AI success rate
- Individual trade outcomes
- Gas costs per transaction

## Example Output

```
AI Trading Bot V2 - Enhanced with Liquidity Intelligence
============================================================
Features:
   • AI-powered decisions (GPT-4o)
   • Liquidity flow tracking (smart money)
   • Real slippage awareness (execution costs)
   • Enhanced risk management
============================================================
Wallet: ADDRESSS_HERE
Chain ID: 8453
Balance: 0.003603 ETH
AI Provider: OpenAI (GPT-4o)
Trade Execution: CoW Protocol Intents (MEV-protected)
Safety Limits:
   - Portfolio: $10.0
   - Max Position: $1.0
   - Daily Loss Limit: $2.0
   - Min Confidence: 10%

Running trading loop with enhanced AI...
```

### CoW Intent Submission Example

```
AI Action: BUY WETH
   Confidence: 50%
   Reasoning: WETH shows strong buy volume and recent liquidity events...
   Entry: $2920.77
   Target: $2950.00
   Stop Loss: $2900.00
   Position Size: $1.00

   Creating CoW intent...
   Sell: 0x833589fC... Amount: 1000000
   Buy: 0x42000000...
   CoW Quote received:
   - Sell: 1.00 USDC
   - Buy: 0.00034232 WETH
   Vault relayer already approved
   Order signed with eip712
   CoW Order submitted: 0xe6b1404d35d8b4eb7a...
   Track at: https://explorer.cow.fi/base/orders/0xe6b1404d...

Cycle 2:
   Checking 1 pending intent(s)...
   Intent fulfilled: 0xe6b1404d35d8b4eb7a...
   Position opened: WETH
```

## Risk Disclaimer

This bot trades with real cryptocurrency on mainnet. Use at your own risk:
- Cryptocurrency trading carries significant risk
- Past performance does not guarantee future results
- Start with small amounts to test the bot
- Monitor the bot's performance regularly
- Be aware of gas costs on Base chain
- AI decisions may not always be profitable

## Technical Details

- **Blockchain**: Base (Chain ID: 8453)
- **DEX**: Uniswap V3 (via CoW Protocol solvers)
- **Trade Execution**: CoW Protocol Intents
- **CoW Settlement**: 0x9008D19f58AAbD9eD0D60971565AA8510560ab41
- **CoW Vault Relayer**: 0xC92E8bdf79f0507f65a392b0ab4667716BFE0110
- **CoW API**: https://api.cow.fi/base/api/v1
- **RPC**: https://mainnet.base.org

### Order Tracking

After submitting an intent, you can track it at:
```
https://explorer.cow.fi/base/orders/{order_uid}
```

## License

MIT License

## Contributing

Contributions welcome. Please test thoroughly before submitting pull requests.

## Support

For issues or questions, please open an issue on GitHub.
