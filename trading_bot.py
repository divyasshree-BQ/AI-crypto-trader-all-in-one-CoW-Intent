#!/usr/bin/env python3
"""
AI Trading Bot V2 - Enhanced with Liquidity Intelligence
Uses liquidity flows and slippage data for smarter AI decisions
APIs → Parse → Pass ALL to AI → Let AI Find Opportunities
"""

import os
import json
import time
from datetime import datetime
from typing import Dict, List, Optional
from dotenv import load_dotenv
from web3 import Web3
import anthropic
try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
from config import BASE_MAINNET, SWAPROUTER_ABI, ERC20_ABI, FEE_TIERS, COW_PROTOCOL_BASE
from liquidity_data import get_enhanced_market_data
from cow_intents import CowIntentClient

load_dotenv()


class AITradingBotV2:
    """
    Enhanced AI Trading Bot with:
    - Liquidity flow analysis (smart money tracking)
    - Slippage awareness (real execution costs)
    - Better AI prompting (more context, better decisions)
    - Improved risk management
    """

    def __init__(self):
        """Initialize the enhanced AI trading bot"""

        # Load configuration
        self.rpc_url = os.getenv('RPC_URL')
        self.private_key = os.getenv('PRIVATE_KEY')
        self.chain_id = int(os.getenv('CHAIN_ID', 8453))
        self.anthropic_api_key = os.getenv('ANTHROPIC_API_KEY')
        self.openai_api_key = os.getenv('OPENAI_API_KEY')
        
        # AI provider tracking
        self.use_openai = False  # Start with Anthropic, switch to OpenAI if needed

        # Enhanced trading parameters
        self.portfolio_size = float(os.getenv('PORTFOLIO_SIZE_USD', 10))
        self.max_position_size = float(os.getenv('MAX_POSITION_SIZE_USD', 1.5))
        self.slippage_tolerance = float(os.getenv('SLIPPAGE_TOLERANCE', 1.0))
        self.gas_limit = int(os.getenv('GAS_LIMIT', 300000))
        self.max_gas_price_gwei = int(os.getenv('MAX_GAS_PRICE_GWEI', 50))
        self.daily_loss_limit = float(os.getenv('DAILY_LOSS_LIMIT_USD', 3))
        self.max_open_positions = int(os.getenv('MAX_OPEN_POSITIONS', 2))
        # TESTING MODE: Lowered confidence threshold to 30% for testing purposes
        self.min_confidence = int(os.getenv('MIN_CONFIDENCE_THRESHOLD', 30))

        # Validate and initialize
        self._validate_config()
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
        if not self.w3.is_connected():
            raise Exception("❌ Failed to connect to RPC")

        self.account = self.w3.eth.account.from_key(self.private_key)
        self.wallet_address = self.account.address
        self.dex_config = BASE_MAINNET

        self.router = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.dex_config['router_v3']),
            abi=SWAPROUTER_ABI
        )

        # Initialize AI clients
        # COMMENTED OUT: Not adding more Anthropic credits for now
        # self.claude = anthropic.Anthropic(api_key=self.anthropic_api_key) if self.anthropic_api_key else None
        self.claude = None  # Force OpenAI usage

        if OPENAI_AVAILABLE and self.openai_api_key:
            self.openai_client = openai.OpenAI(api_key=self.openai_api_key)
        else:
            self.openai_client = None

        # Use OpenAI as primary provider
        self.use_openai = True
        print("ℹ️  Using OpenAI as primary AI provider (Anthropic disabled)")

        # Trading state
        self.open_positions = []
        self.closed_positions = []
        self.daily_pnl = 0.0
        self.is_trading_enabled = True
        self.signal_history = []  # Track AI decisions for learning

        # CoW Protocol intent client
        self.cow_client = CowIntentClient(self.w3, self.account)
        self.pending_intents = []  # Track orders awaiting fulfillment

        print(f"✅ AI Trading Bot V2 Initialized")
        print(f"📍 Wallet: {self.wallet_address}")
        print(f"⛓️  Chain ID: {self.chain_id}")
        print(f"💰 Balance: {self._get_balance():.6f} ETH")
        if self.use_openai:
            ai_provider = "OpenAI (GPT-4o)"
        else:
            ai_provider = "Anthropic (Claude Sonnet 4)"
        print(f"🤖 AI Provider: {ai_provider}")
        print(f"🐮 Trade Execution: CoW Protocol Intents (MEV-protected)")
        print(f"🔒 Safety Limits:")
        print(f"   - Portfolio: ${self.portfolio_size}")
        print(f"   - Max Position: ${self.max_position_size}")
        print(f"   - Daily Loss Limit: ${self.daily_loss_limit}")
        print(f"   - Min Confidence: {self.min_confidence}%")

    def _validate_config(self):
        """Validate configuration"""
        required = {
            'RPC_URL': self.rpc_url,
            'PRIVATE_KEY': self.private_key,
        }
        missing = [k for k, v in required.items() if not v or 'your_' in str(v)]
        if missing:
            raise Exception(f"❌ Missing config: {', '.join(missing)}")
        
        # At least one AI API key must be present
        if not self.anthropic_api_key and not self.openai_api_key:
            raise Exception("❌ Missing config: At least one of ANTHROPIC_API_KEY or OPENAI_API_KEY must be set")

    def _get_balance(self) -> float:
        """Get ETH balance"""
        return float(self.w3.from_wei(
            self.w3.eth.get_balance(self.wallet_address),
            'ether'
        ))

    def _get_wallet_portfolio(self, market_data: Optional[Dict] = None) -> List[Dict]:
        """
        Get minimal wallet portfolio - only essential data for AI
        Returns: [{"s": "SYMBOL", "usd": value}, ...]
        Filters out dust (< $0.01) to save tokens
        """
        portfolio = []
        dust_threshold = 0.01  # Ignore balances < $0.01
        
        # Add ETH balance
        eth_balance = self._get_balance()
        eth_price = self._get_token_price_usd('WETH', market_data)
        eth_usd = eth_balance * eth_price
        if eth_usd >= dust_threshold:
            portfolio.append({'s': 'ETH', 'usd': round(eth_usd, 2)})
        
        # Check all known tokens from config
        for token_symbol, token_address in self.dex_config['tokens'].items():
            try:
                token = self.w3.eth.contract(
                    address=Web3.to_checksum_address(token_address),
                    abi=ERC20_ABI
                )
                decimals = self._get_token_decimals(token_address)
                balance_raw = token.functions.balanceOf(self.wallet_address).call()
                balance = balance_raw / (10 ** decimals)
                
                # Only include tokens with meaningful balance
                if balance > 0:
                    token_price = self._get_token_price_usd(token_symbol, market_data)
                    balance_usd = balance * token_price
                    if balance_usd >= dust_threshold:
                        portfolio.append({'s': token_symbol, 'usd': round(balance_usd, 2)})
            except Exception:
                # Skip tokens that fail
                continue
        
        # Sort by USD value (descending)
        portfolio.sort(key=lambda x: x['usd'], reverse=True)
        
        return portfolio

    def _check_safety_limits(self) -> bool:
        """Check trading safety limits"""
        if not self.is_trading_enabled:
            return False

        if self.daily_pnl <= -self.daily_loss_limit:
            print(f"🛑 Daily loss limit: ${self.daily_pnl:.2f}")
            self.is_trading_enabled = False
            return False

        if len(self.open_positions) >= self.max_open_positions:
            return False

        if self._get_balance() < 0.001:
            return False

        return True

    def _remove_smartcontract_fields(self, obj):
        """
        Remove SmartContract fields from data before sending to AI.
        Keep only Symbol for token identification - we'll map to contract later.
        """
        if obj is None:
            return None
        
        if isinstance(obj, dict):
            filtered = {}
            for k, v in obj.items():
                # Skip SmartContract fields - AI doesn't need them
                if k == 'SmartContract' or k == 'contract_address':
                    continue
                # Recursively filter nested objects
                filtered[k] = self._remove_smartcontract_fields(v)
            return filtered
        
        elif isinstance(obj, list):
            return [self._remove_smartcontract_fields(item) for item in obj]
        
        else:
            return obj

    def _to_compact_format(self, obj, depth=0):
        """
        Convert data to ultra-compact format for AI consumption.
        Removes unnecessary JSON syntax (quotes, brackets) where possible.
        """
        if obj is None:
            return "null"
        
        if isinstance(obj, dict):
            if depth > 3:  # Limit nesting to prevent too much recursion
                return json.dumps(obj, separators=(',', ':'))
            items = []
            for k, v in obj.items():
                # Remove quotes from simple keys (alphanumeric + underscore)
                if k.replace('_', '').replace('-', '').isalnum():
                    key = k
                else:
                    key = f'"{k}"'
                items.append(f"{key}:{self._to_compact_format(v, depth+1)}")
            return "{" + ",".join(items) + "}"
        
        elif isinstance(obj, list):
            if depth > 3:
                return json.dumps(obj, separators=(',', ':'))
            items = [self._to_compact_format(item, depth+1) for item in obj]
            return "[" + ",".join(items) + "]"
        
        elif isinstance(obj, str):
            # Only quote strings that need it (contain special chars or spaces)
            if any(c in obj for c in [' ', ',', ':', '{', '}', '[', ']', '"', "'"]):
                return json.dumps(obj)
            return obj
        
        elif isinstance(obj, (int, float)):
            return str(obj)
        
        elif isinstance(obj, bool):
            return "true" if obj else "false"
        
        else:
            return json.dumps(obj, separators=(',', ':'))

    def generate_ai_actions(self, market_data: Dict) -> List[Dict]:
        """
        Generate AI-driven actions - AI decides ALL actions (open, close, hold, market make, etc.)
        Code only executes what AI decides - no hardcoded logic.
        
        Key improvements:
        - Liquidity flow analysis (smart money tracking)
        - Slippage awareness (execution quality)
        - Historical signal performance (learning)
        - Market regime detection (adapt strategy)
        - Fully AI-driven decision making
        """
        try:
            # Extract raw data from new format - pass all 3 sources directly to AI
            trade_data = market_data.get('trade_data', {})
            liquidity_events = market_data.get('liquidity_events', [])
            slippage_data = market_data.get('slippage_data', [])

            # Calculate recent AI performance
            recent_signals = self.signal_history[-10:] if self.signal_history else []
            successful_signals = [s for s in recent_signals if s.get('outcome') == 'success']
            ai_accuracy = len(successful_signals) / len(recent_signals) if recent_signals else 0

            # Filter out SmartContract fields before sending to AI - only pass Symbol
            # We'll map symbol to contract_address from trade_data when executing trades
            trade_data_for_ai = self._remove_smartcontract_fields(trade_data) if trade_data else {}
            liquidity_events_for_ai = self._remove_smartcontract_fields(liquidity_events) if liquidity_events else []
            slippage_data_for_ai = self._remove_smartcontract_fields(slippage_data) if slippage_data else []

            # Prepare data for AI - use ultra-compact format to minimize tokens
            trade_data_json = self._to_compact_format(trade_data_for_ai) if trade_data_for_ai else "{}"
            liquidity_events_json = self._to_compact_format(liquidity_events_for_ai) if liquidity_events_for_ai else "[]"
            slippage_data_json = self._to_compact_format(slippage_data_for_ai) if slippage_data_for_ai else "[]"
            
            # Prepare minimal open positions info for AI (only essential fields)
            open_positions_info = []
            for pos in self.open_positions:
                current_price = self._get_token_price_usd(pos['token_out'], market_data) or pos['entry_price']
                open_positions_info.append({
                    'm': pos['token_out'],  # market
                    'a': pos['action'],  # action
                    'e': round(pos['entry_price'], 8),  # entry_price
                    't': round(pos['target_price'], 8),  # target_price
                    's': round(pos['stop_loss'], 8),  # stop_loss
                    'c': round(current_price, 8),  # current_price
                    'v': round(pos['amount_usd'], 2)  # value_usd
                })
            positions_json = self._to_compact_format(open_positions_info) if open_positions_info else "[]"
            
            # Get minimal wallet portfolio (only essential data)
            wallet_portfolio = self._get_wallet_portfolio(market_data)
            portfolio_json = self._to_compact_format(wallet_portfolio) if wallet_portfolio else "[]"
            total_portfolio_value = sum(p['usd'] for p in wallet_portfolio)
            
            prompt = f"""You are an expert crypto trading AI with FULL CONTROL. You decide ALL actions - the system only executes what you decide.

⚠️ TESTING MODE: Be lenient - find ANY reasonable trading opportunity to test the system.

AVAILABLE DATA:
TRADE DATA (raw JSON):
{trade_data_json}

LIQUIDITY EVENTS (raw JSON):
{liquidity_events_json}

SLIPPAGE DATA (raw JSON):
{slippage_data_json}

OPEN POSITIONS (m=market,a=action,e=entry,t=target,s=stop,c=current,v=value_usd):
{positions_json}

WALLET BALANCES (s=symbol, usd=value):
{portfolio_json}

PORTFOLIO STATUS:
- Total Portfolio Value: ${total_portfolio_value:.2f}
- Open Positions: {len(self.open_positions)}/{self.max_open_positions}
- Daily PnL: ${self.daily_pnl:.2f}
- Available Capital: ${self.portfolio_size - sum(p.get('amount_usd', 0) for p in self.open_positions):.2f}
- Wallet Balance: {self._get_balance():.6f} ETH

YOUR PERFORMANCE:
- Actions generated: {len(recent_signals)}
- Success rate: {ai_accuracy*100:.1f}%
- Analyze your past decisions and adapt your strategy.

YOUR FULL CONTROL - DECIDE ANY ACTION:

IMPORTANT: You can see the COMPLETE WALLET PORTFOLIO above. Use this to:
- Know which tokens you have available to trade FROM
- Understand your total capital and position sizing
- Make decisions based on your actual holdings, not assumptions

1. POSITION MANAGEMENT:
   - CLOSE: Close an open position (any reason - target hit, stop loss, market conditions, etc.)
   - HOLD: Keep position open (explicitly state if you want to hold)
   - PARTIAL_CLOSE: Close part of a position (specify amount_usd or percentage)

2. NEW TRADES (MARKET ORDERS - execute immediately):
   - BUY: Open a long position at current market price
     * Use tokens from your portfolio as input (check portfolio balances)
     * Specify which token to use as input if you have multiple options
   - SELL: Open a short position (if supported)

3. LIMIT ORDERS (execute when price target is reached):
   - LIMIT_BUY: Buy only when price drops to your target
     * Requires: limit_price (the price you want to buy at)
     * Order sits on CoW Protocol until price is reached or expires (24h)
     * NO GAS COST if order expires unfilled
   - LIMIT_SELL: Sell only when price rises to your target
     * Requires: limit_price (the price you want to sell at)

4. MARKET MAKING (if you see opportunity):
   - MARKET_MAKE: Provide liquidity at specific price range
   - Specify: token, price_range_min, price_range_max, amount_usd

5. RISK MANAGEMENT:
   - ADJUST_STOP_LOSS: Modify stop loss for existing position
   - ADJUST_TARGET: Modify target price for existing position

6. WAIT:
   - HOLD: Explicitly wait (or return empty array [])

CONSTRAINTS:
- Minimum confidence: {self.min_confidence}% (LOW for testing)
- Gas cost per action: ~$0.10
- Use token SYMBOL only (contract address mapped automatically)
- Max position size: ${self.max_position_size}
- Max open positions: {self.max_open_positions}

YOUR TASK:
Analyze ALL data and decide what actions to take. You have FULL CONTROL.
- Review open positions - should any be closed, adjusted, or held?
- Look for new opportunities - any trades to open?
- Consider market making - any liquidity opportunities?
- Consider risk - any stop losses or targets to adjust?

Return an array of actions. Each action must have: action, market, confidence, reasoning.
For CLOSE/HOLD/ADJUST actions, only need: action, market, confidence, reasoning.
For BUY/SELL/MARKET_MAKE, also need: entry_price, target_price, stop_loss (and amount_usd if different from default).
For LIMIT_BUY/LIMIT_SELL, also need: limit_price, target_price, stop_loss (and amount_usd if different from default).

Be lenient in testing mode - find opportunities to test the system.

OUTPUT FORMAT (JSON only, no markdown):
[
  {{
    "action": "CLOSE",
    "market": "SYMBOL",
    "confidence": 85,
    "reasoning": "Target reached / Stop loss hit / Market conditions changed"
  }},
  {{
    "action": "BUY",
    "market": "SYMBOL",
    "confidence": 45,
    "entry_price": 1.23,
    "target_price": 1.29,
    "stop_loss": 1.19,
    "reasoning": "Found opportunity based on [analysis]"
  }},
  {{
    "action": "HOLD",
    "market": "SYMBOL",
    "confidence": 80,
    "reasoning": "Position performing well, waiting for target"
  }},
  {{
    "action": "MARKET_MAKE",
    "market": "SYMBOL",
    "confidence": 60,
    "price_range_min": 1.20,
    "price_range_max": 1.25,
    "amount_usd": 0.5,
    "reasoning": "Good liquidity opportunity at this range"
  }},
  {{
    "action": "LIMIT_BUY",
    "market": "WETH",
    "confidence": 70,
    "limit_price": 0.00035,
    "target_price": 0.00038,
    "stop_loss": 0.00032,
    "reasoning": "Set limit buy at support level, wait for dip"
  }}
]
"""

            # COMMENTED OUT: Anthropic API calls disabled
            # if not self.use_openai and self.claude:
            #     try:
            #         # Call Claude API
            #         message = self.claude.messages.create(
            #             model="claude-sonnet-4-20250514",
            #             max_tokens=2500,
            #             temperature=0.7,
            #             messages=[{"role": "user", "content": prompt}]
            #         )
            #         response_text = message.content[0].text
            #     except Exception as e:
            #         print(f"⚠️  Anthropic API error: {e}. Switching to OpenAI...")
            #         self.use_openai = True
            #         response_text = self._generate_with_openai(prompt)

            # Use OpenAI directly
            if self.use_openai and self.openai_client:
                response_text = self._generate_with_openai(prompt, model="gpt-4o")
            else:
                raise Exception("❌ No AI client available. Please set OPENAI_API_KEY")

            # Extract JSON
            start_idx = response_text.find('[')
            end_idx = response_text.rfind(']') + 1

            if start_idx == -1 or end_idx <= start_idx:
                print("⚠️  AI did not generate actions (waiting for better conditions)")
                return []

            actions = json.loads(response_text[start_idx:end_idx])

            # Validate and enrich actions - flexible validation based on action type
            validated_actions = []
            for action_data in actions:
                action = action_data.get('action', '').upper()
                
                # All actions require: action, market, confidence, reasoning
                base_required = ['action', 'market', 'confidence']
                if not all(field in action_data for field in base_required):
                    print(f"⚠️  Skipping invalid action (missing base fields): {action_data.get('market', 'unknown')}")
                    continue
                
                # Check confidence threshold
                if action_data['confidence'] < self.min_confidence:
                    print(f"⚠️  Skipping {action} {action_data.get('market')} - confidence {action_data['confidence']}% below threshold {self.min_confidence}%")
                    continue
                
                # Action-specific validation
                if action in ['CLOSE', 'HOLD', 'PARTIAL_CLOSE']:
                    # Position management actions - check if position exists
                    market_symbol = action_data['market'].upper()
                    position_exists = any(p['token_out'].upper() == market_symbol for p in self.open_positions)
                    if not position_exists and action != 'HOLD':
                        print(f"⚠️  {action} signal for {market_symbol} but no open position found, skipping")
                        continue
                    validated_actions.append(action_data)
                    continue
                
                elif action in ['BUY', 'SELL', 'MARKET_MAKE']:
                    # Trading actions - require price fields
                    required = ['action', 'market', 'confidence', 'entry_price', 'target_price', 'stop_loss']
                    if not all(field in action_data for field in required):
                        print(f"⚠️  Skipping invalid {action} action (missing price fields): {action_data.get('market', 'unknown')}")
                        continue
                    
                    # Map symbol to contract_address
                    market_symbol = action_data['market'].upper()
                    contract_address = self._find_contract_address(market_symbol, trade_data, liquidity_events, slippage_data)
                    
                    if contract_address:
                        action_data['contract_address'] = contract_address
                    else:
                        print(f"⚠️  Could not map symbol {market_symbol} to contract address, skipping")
                        continue
                    
                    validated_actions.append(action_data)
                    continue
                
                elif action in ['LIMIT_BUY', 'LIMIT_SELL']:
                    # Limit order actions - require limit_price
                    required = ['action', 'market', 'confidence', 'limit_price', 'target_price', 'stop_loss']
                    if not all(field in action_data for field in required):
                        print(f"⚠️  Skipping invalid {action} action (missing fields): {action_data.get('market', 'unknown')}")
                        continue

                    # Map symbol to contract_address
                    market_symbol = action_data['market'].upper()
                    contract_address = self._find_contract_address(market_symbol, trade_data, liquidity_events, slippage_data)

                    if contract_address:
                        action_data['contract_address'] = contract_address
                    else:
                        print(f"⚠️  Could not map symbol {market_symbol} to contract address, skipping")
                        continue

                    validated_actions.append(action_data)
                    continue

                elif action in ['ADJUST_STOP_LOSS', 'ADJUST_TARGET']:
                    # Risk management actions - require new value
                    if 'new_value' not in action_data:
                        print(f"⚠️  Skipping {action} - missing new_value field")
                        continue

                    market_symbol = action_data['market'].upper()
                    position_exists = any(p['token_out'].upper() == market_symbol for p in self.open_positions)
                    if not position_exists:
                        print(f"⚠️  {action} signal for {market_symbol} but no open position found, skipping")
                        continue

                    validated_actions.append(action_data)
                    continue

                else:
                    print(f"⚠️  Unknown action type: {action}, skipping")
                    continue

            # Store actions for performance tracking
            for action in validated_actions:
                self.signal_history.append({
                    'timestamp': datetime.now().isoformat(),
                    'signal': action,
                    'outcome': 'pending'  # Will update later
                })

            return validated_actions

        except Exception as e:
            print(f"❌ Error generating AI actions: {e}")
            import traceback
            traceback.print_exc()
            return []

    def _generate_with_openai(self, prompt: str, model: str = "gpt-4o") -> str:
        """Generate response using OpenAI API"""
        if not self.openai_client:
            raise Exception("❌ OpenAI client not available")
        
        try:
            response = self.openai_client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are an advanced crypto trading AI. Always respond with valid JSON arrays only."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=2500,
                temperature=0.7
            )
            return response.choices[0].message.content
        except Exception as e:
            raise Exception(f"OpenAI API error: {e}")

    def _get_gas_price(self) -> Optional[int]:
        """Get current gas price with safety limit"""
        gas_price = self.w3.eth.gas_price
        gas_price_gwei = self.w3.from_wei(gas_price, 'gwei')

        if gas_price_gwei > self.max_gas_price_gwei:
            print(f"⚠️  Gas price too high: {gas_price_gwei} Gwei (max: {self.max_gas_price_gwei})")
            return None

        return gas_price

    def _build_transaction_params(self, gas: int, gas_price: Optional[int] = None, value: int = 0) -> Dict:
        """
        Build common transaction parameters.
        
        Args:
            gas: Gas limit for the transaction
            gas_price: Gas price (if None, uses current gas price)
            value: ETH value to send (default: 0)
        
        Returns:
            Dictionary with transaction parameters
        """
        if gas_price is None:
            gas_price = self.w3.eth.gas_price
        
        return {
            'from': self.wallet_address,
            'gas': gas,
            'gasPrice': gas_price,
            'nonce': self.w3.eth.get_transaction_count(self.wallet_address),
            'value': value
        }

    def _sign_and_send_transaction(self, transaction: Dict, show_progress: bool = True) -> Optional[object]:
        """
        Sign and send a transaction.
        
        Args:
            transaction: Built transaction dictionary
            show_progress: Whether to print progress messages
        
        Returns:
            Transaction hash (HexBytes object), or None if failed
        """
        try:
            if show_progress:
                print(f"   ✍️  Signing transaction...")
            signed_tx = self.account.sign_transaction(transaction)
            
            if show_progress:
                print(f"   📤 Sending transaction...")
            raw_tx = getattr(signed_tx, 'rawTransaction', None) or getattr(signed_tx, 'raw_transaction', None)
            if raw_tx is None:
                raise Exception("Could not access raw transaction")
            
            tx_hash = self.w3.eth.send_raw_transaction(raw_tx)
            return tx_hash
        except Exception as e:
            if show_progress:
                print(f"   ❌ Failed to sign/send transaction: {e}")
            return None

    def _wait_for_transaction_receipt(self, tx_hash: object, timeout: int = 120, show_progress: bool = True) -> Optional[Dict]:
        """
        Wait for transaction receipt with error handling.
        
        Args:
            tx_hash: Transaction hash (HexBytes object)
            timeout: Timeout in seconds (default: 120)
            show_progress: Whether to print progress messages
        
        Returns:
            Transaction receipt dictionary, or None if failed
        """
        basescan_url = "https://basescan.org"
        tx_hash_hex = tx_hash.hex()
        if not tx_hash_hex.startswith('0x'):
            tx_hash_hex = '0x' + tx_hash_hex
        
        if show_progress:
            print(f"   ⏳ Waiting for confirmation...")
            print(f"   🔗 TX Hash: {tx_hash_hex}")
            print(f"   🔍 View on Basescan: {basescan_url}/tx/{tx_hash_hex}")
        
        try:
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout, poll_latency=2)
            return receipt
        except Exception as e:
            try:
                tx_status = self.w3.eth.get_transaction(tx_hash)
                if tx_status:
                    if show_progress:
                        print(f"   ⚠️  Transaction pending...")
                    receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60, poll_latency=2)
                    return receipt
                else:
                    if show_progress:
                        print(f"   ❌ Transaction not found: {e}")
                    return None
            except Exception as e2:
                if show_progress:
                    print(f"   ❌ Transaction failed: {e2}")
                return None

    def _get_token_address(self, symbol: str, contract_address: Optional[str] = None) -> Optional[str]:
        """Get token address from symbol or use provided contract address"""
        if contract_address:
            try:
                return Web3.to_checksum_address(contract_address)
            except:
                pass

        symbol_upper = symbol.upper()
        if symbol_upper == 'ETH':
            symbol_upper = 'WETH'
        return self.dex_config['tokens'].get(symbol_upper)

    def _get_token_decimals(self, token_address: str) -> int:
        """Get token decimals"""
        try:
            token = self.w3.eth.contract(
                address=Web3.to_checksum_address(token_address),
                abi=ERC20_ABI
            )
            return token.functions.decimals().call()
        except:
            return 18

    def _find_contract_address(self, market_symbol: str, trade_data: Dict, liquidity_events: List, slippage_data: List) -> Optional[str]:
        """Find contract address for a token symbol from various data sources"""
        contract_address = None
        
        # Try to find contract address from trade_data
        markets = trade_data.get('top_markets', []) if trade_data else []
        for m in markets:
            if m.get('symbol', '').upper() == market_symbol:
                contract_address = m.get('contract_address', '')
                break
        
        # Also check liquidity_events and slippage_data for contract mapping
        if not contract_address:
            for event in liquidity_events or []:
                pool = event.get('PoolEvent', {}).get('Pool', {})
                for currency_key in ['CurrencyA', 'CurrencyB']:
                    currency = pool.get(currency_key, {})
                    if currency.get('Symbol', '').upper() == market_symbol:
                        contract_address = currency.get('SmartContract', '')
                        break
                if contract_address:
                    break
        
        if not contract_address:
            for slippage in slippage_data or []:
                price_data = slippage.get('Price', {})
                pool = price_data.get('Pool', {})
                for currency_key in ['CurrencyA', 'CurrencyB']:
                    currency = pool.get(currency_key, {})
                    if currency.get('Symbol', '').upper() == market_symbol:
                        contract_address = currency.get('SmartContract', '')
                        break
                if contract_address:
                    break
        
        return contract_address

    def _get_token_price_usd(self, token_symbol: str, market_data: Optional[Dict] = None) -> float:
        """Get token price in USD from market data"""
        if market_data:
            # Handle new format with trade_data
            trade_data = market_data.get('trade_data', {})
            markets = trade_data.get('top_markets', []) if trade_data else []
            
            # Fallback to old format for compatibility
            if not markets and market_data.get('top_markets'):
                markets = market_data['top_markets']
            
            for market in markets:
                if market['symbol'].upper() == token_symbol.upper():
                    price = market.get('recent_price', 0)
                    if price and price > 0:
                        return float(price)

        if token_symbol in ['USDC', 'DAI', 'USDT']:
            return 1.0
        if token_symbol == 'WETH':
            return 3000.0
        return 1.0

    def _find_input_token(self, target_token_address: str, market_data: Optional[Dict] = None) -> Optional[Dict]:
        """Find which token to trade from by checking balances"""
        priority_tokens = ['USDC', 'WETH', 'DAI', 'USDT']

        for token_symbol in priority_tokens:
            token_address = self.dex_config['tokens'].get(token_symbol)
            if not token_address or token_address.lower() == target_token_address.lower():
                continue

            try:
                token = self.w3.eth.contract(
                    address=Web3.to_checksum_address(token_address),
                    abi=ERC20_ABI
                )
                decimals = self._get_token_decimals(token_address)
                balance = token.functions.balanceOf(self.wallet_address).call()
                balance_formatted = balance / (10 ** decimals)

                token_price_usd = self._get_token_price_usd(token_symbol, market_data)
                position_size_token = self.max_position_size / token_price_usd

                if balance_formatted >= position_size_token:
                    return {
                        'symbol': token_symbol,
                        'address': token_address,
                        'decimals': decimals,
                        'balance': balance,
                        'balance_formatted': balance_formatted
                    }
            except:
                continue

        return None

    def _approve_token(self, token_address: str, amount: int) -> bool:
        """Approve router to spend tokens"""
        try:
            token = self.w3.eth.contract(
                address=Web3.to_checksum_address(token_address),
                abi=ERC20_ABI
            )

            allowance = token.functions.allowance(
                self.wallet_address,
                self.router.address
            ).call()

            if allowance >= amount:
                print(f"   ✓ Token already approved")
                return True

            print(f"   📝 Approving token spend...")
            approve_tx = token.functions.approve(
                self.router.address,
                amount
            ).build_transaction(self._build_transaction_params(gas=100000))

            tx_hash = self._sign_and_send_transaction(approve_tx)
            if tx_hash is None:
                return False

            receipt = self._wait_for_transaction_receipt(tx_hash, timeout=120, show_progress=False)
            if receipt is None:
                print(f"   ⚠️  Approval pending")
                return False

            if receipt['status'] == 1:
                print(f"   ✓ Approval confirmed")
                return True
            return False

        except Exception as e:
            print(f"   ❌ Approval failed: {e}")
            return False

    def _execute_close(self, signal: Dict, market_data: Dict) -> Optional[str]:
        """Execute closing a position based on AI signal"""
        market_symbol = signal['market'].upper()
        
        # Find the open position
        position = None
        for pos in self.open_positions:
            if pos['token_out'].upper() == market_symbol:
                position = pos
                break
        
        if not position:
            print(f"   ❌ No open position found for {market_symbol}")
            return None
        
        print(f"\n🤖 AI Signal: CLOSE {signal['market']}")
        print(f"   Confidence: {signal['confidence']}%")
        print(f"   Reasoning: {signal.get('reasoning', 'N/A')[:100]}...")
        print(f"   Entry: ${position['entry_price']:.8f}")
        print(f"   Target: ${position['target_price']:.8f}")
        print(f"   Stop Loss: ${position['stop_loss']:.8f}")
        
        # Get current price
        current_price = self._get_token_price_usd(market_symbol, market_data)
        if not current_price or current_price <= 0:
            print(f"   ❌ Could not get current price for {market_symbol}")
            return None
        
        print(f"   Current: ${current_price:.8f}")
        
        # Close the position
        close_success = self._close_position(position, market_data, current_price)
        
        if close_success:
            # Calculate PnL
            pnl_pct = ((current_price - position['entry_price']) / position['entry_price']) * 100
            pnl_usd = (current_price - position['entry_price']) / position['entry_price'] * position['amount_usd']
            self.daily_pnl += pnl_usd
            
            # Update position with close info
            position['close_price'] = current_price
            position['close_reason'] = signal.get('reasoning', 'AI decision')
            position['pnl_usd'] = pnl_usd
            position['pnl_pct'] = pnl_pct
            position['closed_at'] = datetime.now().isoformat()
            
            # Move to closed positions
            self.open_positions.remove(position)
            self.closed_positions.append(position)
            
            print(f"   ✅ Position closed")
            print(f"   💰 PnL: ${pnl_usd:.4f} ({pnl_pct:+.2f}%)")
            return "closed"
        
        return None

    def execute_action(self, action_data: Dict, market_data: Dict) -> Optional[str]:
        """
        Execute any AI-decided action. Fully AI-driven - code only executes.
        Routes to appropriate handler based on action type.
        """
        action = action_data.get('action', '').upper()
        
        print(f"\n🤖 AI Action: {action} {action_data.get('market', 'N/A')}")
        print(f"   Confidence: {action_data.get('confidence', 0)}%")
        print(f"   Reasoning: {action_data.get('reasoning', 'N/A')[:100]}...")
        
        # Route to appropriate handler
        if action == 'CLOSE':
            return self._execute_close(action_data, market_data)
        elif action == 'HOLD':
            return self._execute_hold(action_data, market_data)
        elif action == 'PARTIAL_CLOSE':
            return self._execute_partial_close(action_data, market_data)
        elif action == 'BUY':
            return self._execute_buy(action_data, market_data)
        elif action == 'SELL':
            return self._execute_sell(action_data, market_data)
        elif action == 'MARKET_MAKE':
            return self._execute_market_make(action_data, market_data)
        elif action == 'ADJUST_STOP_LOSS':
            return self._execute_adjust_stop_loss(action_data, market_data)
        elif action == 'ADJUST_TARGET':
            return self._execute_adjust_target(action_data, market_data)
        elif action == 'LIMIT_BUY':
            return self._execute_limit_buy(action_data, market_data)
        elif action == 'LIMIT_SELL':
            return self._execute_limit_sell(action_data, market_data)
        else:
            print(f"   ❌ Unknown action type: {action}")
            return None

    def _execute_hold(self, action_data: Dict, market_data: Dict) -> Optional[str]:
        """Execute HOLD action - explicitly keep position open"""
        market_symbol = action_data['market'].upper()
        position = next((p for p in self.open_positions if p['token_out'].upper() == market_symbol), None)
        
        if position:
            current_price = self._get_token_price_usd(market_symbol, market_data)
            print(f"   ✅ Holding position: {market_symbol}")
            print(f"   Entry: ${position['entry_price']:.8f}, Current: ${current_price:.8f}")
            return "held"
        else:
            print(f"   ⚠️  No position to hold for {market_symbol}")
            return None

    def _execute_partial_close(self, action_data: Dict, market_data: Dict) -> Optional[str]:
        """Execute PARTIAL_CLOSE action - close part of a position"""
        # TODO: Implement partial close logic
        print(f"   ⚠️  PARTIAL_CLOSE not yet implemented, closing full position instead")
        return self._execute_close(action_data, market_data)

    def _execute_buy(self, action_data: Dict, market_data: Dict) -> Optional[str]:
        """Execute BUY action - open a long position"""
        return self._execute_trade(action_data, market_data, 'BUY')

    def _execute_sell(self, action_data: Dict, market_data: Dict) -> Optional[str]:
        """Execute SELL action - open a short position (if supported)"""
        # For now, treat SELL as closing a position or swapping to stablecoin
        print(f"   ⚠️  SELL action - treating as swap to stablecoin")
        return self._execute_trade(action_data, market_data, 'SELL')

    def _execute_market_make(self, action_data: Dict, market_data: Dict) -> Optional[str]:
        """Execute MARKET_MAKE action - provide liquidity"""
        # TODO: Implement market making logic (Uniswap V3 range orders)
        print(f"   ⚠️  MARKET_MAKE not yet implemented")
        return None

    def _execute_adjust_stop_loss(self, action_data: Dict, market_data: Dict) -> Optional[str]:
        """Execute ADJUST_STOP_LOSS action - modify stop loss"""
        market_symbol = action_data['market'].upper()
        position = next((p for p in self.open_positions if p['token_out'].upper() == market_symbol), None)
        
        if position:
            new_stop_loss = action_data.get('new_value')
            old_stop_loss = position['stop_loss']
            position['stop_loss'] = new_stop_loss
            print(f"   ✅ Adjusted stop loss for {market_symbol}: ${old_stop_loss:.8f} → ${new_stop_loss:.8f}")
            return "adjusted"
        else:
            print(f"   ❌ No position found for {market_symbol}")
            return None

    def _execute_adjust_target(self, action_data: Dict, market_data: Dict) -> Optional[str]:
        """Execute ADJUST_TARGET action - modify target price"""
        market_symbol = action_data['market'].upper()
        position = next((p for p in self.open_positions if p['token_out'].upper() == market_symbol), None)

        if position:
            new_target = action_data.get('new_value')
            old_target = position['target_price']
            position['target_price'] = new_target
            print(f"   ✅ Adjusted target for {market_symbol}: ${old_target:.8f} → ${new_target:.8f}")
            return "adjusted"
        else:
            print(f"   ❌ No position found for {market_symbol}")
            return None

    def _execute_limit_buy(self, action_data: Dict, market_data: Dict) -> Optional[str]:
        """Execute LIMIT_BUY action - place a limit order to buy at target price"""
        print(f"   📊 Limit Price: {action_data['limit_price']}")
        print(f"   🎯 Target: {action_data['target_price']}")
        print(f"   🛑 Stop Loss: {action_data['stop_loss']}")

        position_size = action_data.get('amount_usd', self.max_position_size)
        print(f"   💰 Position Size: ${position_size:.2f}")

        # Safety checks
        if not self._check_safety_limits():
            return None

        try:
            # Get token addresses
            token_out_address = self._get_token_address(
                action_data['market'],
                action_data.get('contract_address')
            )

            if not token_out_address:
                print(f"   ❌ Unknown token: {action_data['market']}")
                return None

            # Find input token (USDC)
            input_token = self._find_input_token(token_out_address, market_data)
            if not input_token:
                print(f"   ❌ No sufficient balance in any supported token")
                return None

            token_in_address = input_token['address']
            token_in_decimals = input_token['decimals']
            token_in_symbol = input_token['symbol']

            print(f"   💰 {token_in_symbol} Balance: {input_token['balance_formatted']:.6f}")

            # Calculate amounts
            token_in_price_usd = self._get_token_price_usd(token_in_symbol, market_data)
            position_size_token = position_size / token_in_price_usd
            amount_in = int(position_size_token * (10 ** token_in_decimals))

            if input_token['balance'] < amount_in:
                print(f"   ❌ Insufficient {token_in_symbol} balance")
                return None

            # Get decimals for buy token
            token_out_decimals = self._get_token_decimals(token_out_address)

            print(f"   📝 Creating LIMIT ORDER: {position_size_token:.6f} {token_in_symbol} → {action_data['market']}")
            print(f"   📊 Will execute when price reaches: {action_data['limit_price']}")

            # Execute limit order via CoW Protocol
            order_uid = self.cow_client.execute_limit_order(
                sell_token=token_in_address,
                buy_token=token_out_address,
                sell_amount=amount_in,
                limit_price=action_data['limit_price'],
                sell_token_decimals=token_in_decimals,
                buy_token_decimals=token_out_decimals,
                valid_hours=24  # 24 hour validity
            )

            if order_uid:
                print(f"   🐮 LIMIT ORDER submitted!")
                print(f"   📋 Order UID: {order_uid[:20]}...")

                # Track pending limit order
                pending_intent = {
                    'order_uid': order_uid,
                    'market': action_data['market'],
                    'action': 'LIMIT_BUY',
                    'limit_price': action_data['limit_price'],
                    'target_price': action_data['target_price'],
                    'stop_loss': action_data['stop_loss'],
                    'confidence': action_data['confidence'],
                    'reasoning': action_data.get('reasoning', ''),
                    'timestamp': datetime.now().isoformat(),
                    'amount_usd': position_size,
                    'token_in': token_in_symbol,
                    'token_out': action_data['market'],
                    'contract_address': token_out_address,
                    'type': 'limit_open'
                }
                self.pending_intents.append(pending_intent)

                return order_uid
            else:
                print(f"   ❌ Failed to submit limit order")
                return None

        except Exception as e:
            print(f"   ❌ Error executing limit order: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _execute_limit_sell(self, action_data: Dict, market_data: Dict) -> Optional[str]:
        """Execute LIMIT_SELL action - place a limit order to sell at target price"""
        print(f"   📊 Limit Price: {action_data['limit_price']}")
        print(f"   🎯 Target: {action_data['target_price']}")
        print(f"   🛑 Stop Loss: {action_data['stop_loss']}")

        # For limit sell, we need to have the token already
        market_symbol = action_data['market'].upper()
        position = next((p for p in self.open_positions if p['token_out'].upper() == market_symbol), None)

        if not position:
            print(f"   ❌ No {market_symbol} position to sell")
            return None

        try:
            token_address = self._get_token_address(market_symbol, position.get('contract_address'))
            if not token_address:
                print(f"   ❌ Could not find address for {market_symbol}")
                return None

            # Get token balance
            token = self.w3.eth.contract(
                address=Web3.to_checksum_address(token_address),
                abi=ERC20_ABI
            )
            decimals = self._get_token_decimals(token_address)
            balance = token.functions.balanceOf(self.wallet_address).call()

            if balance == 0:
                print(f"   ❌ No {market_symbol} balance to sell")
                return None

            # USDC address
            usdc_address = self.dex_config['tokens'].get('USDC')
            if not usdc_address:
                print(f"   ❌ USDC not configured")
                return None

            balance_formatted = balance / (10 ** decimals)
            print(f"   💰 {market_symbol} Balance: {balance_formatted:.8f}")
            print(f"   📝 Creating LIMIT SELL: {balance_formatted:.8f} {market_symbol} → USDC")
            print(f"   📊 Will execute when price reaches: {action_data['limit_price']}")

            # Execute limit order via CoW Protocol
            order_uid = self.cow_client.execute_limit_order(
                sell_token=token_address,
                buy_token=usdc_address,
                sell_amount=balance,
                limit_price=action_data['limit_price'],
                sell_token_decimals=decimals,
                buy_token_decimals=6,  # USDC has 6 decimals
                valid_hours=24
            )

            if order_uid:
                print(f"   🐮 LIMIT SELL submitted!")
                print(f"   📋 Order UID: {order_uid[:20]}...")

                # Track pending limit sell
                pending_intent = {
                    'order_uid': order_uid,
                    'position': position,
                    'limit_price': action_data['limit_price'],
                    'timestamp': datetime.now().isoformat(),
                    'type': 'limit_close'
                }
                self.pending_intents.append(pending_intent)

                return order_uid
            else:
                print(f"   ❌ Failed to submit limit sell order")
                return None

        except Exception as e:
            print(f"   ❌ Error executing limit sell: {e}")
            import traceback
            traceback.print_exc()
            return None

    def execute_trade(self, signal: Dict, market_data: Dict) -> Optional[str]:
        """Legacy method - redirects to execute_action"""
        return self.execute_action(signal, market_data)

    def _execute_trade(self, action_data: Dict, market_data: Dict, trade_type: str) -> Optional[str]:
        """Execute a BUY or SELL trade via CoW Protocol intent"""
        # Show raw slippage data if available
        if 'slippage_bps' in action_data:
            print(f"   Slippage: {action_data['slippage_bps']} bps")

        print(f"   Entry: ${action_data['entry_price']:.8f}")
        print(f"   Target: ${action_data['target_price']:.8f}")
        print(f"   Stop Loss: ${action_data['stop_loss']:.8f}")

        # Use custom amount if specified, otherwise use default
        position_size = action_data.get('amount_usd', self.max_position_size)
        print(f"   Position Size: ${position_size:.6f}")

        # Safety checks
        if not self._check_safety_limits():
            return None

        try:
            # Get token addresses
            token_out_address = self._get_token_address(
                action_data['market'],
                action_data.get('contract_address')
            )

            if not token_out_address:
                print(f"   ❌ Unknown token: {action_data['market']}")
                return None

            # Find input token
            input_token = self._find_input_token(token_out_address, market_data)
            if not input_token:
                print(f"   ❌ No sufficient balance in any supported token")
                return None

            token_in_address = input_token['address']
            token_in_decimals = input_token['decimals']
            token_in_symbol = input_token['symbol']

            print(f"   💰 {token_in_symbol} Balance: {input_token['balance_formatted']:.6f}")

            # Calculate amounts
            token_in_price_usd = self._get_token_price_usd(token_in_symbol, market_data)
            position_size_token = position_size / token_in_price_usd
            amount_in = int(position_size_token * (10 ** token_in_decimals))

            if input_token['balance'] < amount_in:
                print(f"   ❌ Insufficient {token_in_symbol} balance: {input_token['balance_formatted']:.6f} < {position_size_token:.6f}")
                return None

            print(f"   💱 Creating CoW intent: {amount_in / (10**token_in_decimals):.6f} {token_in_symbol} → {action_data['market']}")

            # Execute via CoW Protocol intent
            slippage_bps = int(self.slippage_tolerance * 100)  # Convert % to bps
            order_uid = self.cow_client.execute_intent(
                sell_token=token_in_address,
                buy_token=token_out_address,
                sell_amount=amount_in,
                slippage_bps=slippage_bps
            )

            if order_uid:
                print(f"   🐮 CoW intent submitted!")
                print(f"   📋 Order UID: {order_uid[:20]}...")

                # Track pending intent
                pending_intent = {
                    'order_uid': order_uid,
                    'market': action_data['market'],
                    'action': action_data['action'],
                    'entry_price': action_data['entry_price'],
                    'target_price': action_data['target_price'],
                    'stop_loss': action_data['stop_loss'],
                    'confidence': action_data['confidence'],
                    'reasoning': action_data.get('reasoning', ''),
                    'timestamp': datetime.now().isoformat(),
                    'amount_usd': position_size,
                    'token_in': token_in_symbol,
                    'token_out': action_data['market'],
                    'contract_address': token_out_address,
                    'slippage_bps': action_data.get('slippage_bps', slippage_bps),
                    'type': 'open'  # Mark as opening position
                }
                self.pending_intents.append(pending_intent)

                return order_uid
            else:
                print(f"   ❌ Failed to submit CoW intent")
                return None

        except Exception as e:
            print(f"   ❌ Error executing intent: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _close_position(self, position: Dict, market_data: Dict, current_price: float) -> bool:
        """Close a position by swapping the token back to USDC via CoW Protocol intent"""
        try:
            token_symbol = position['token_out']
            token_address = self._get_token_address(token_symbol, position.get('contract_address'))

            if not token_address:
                print(f"   ❌ Could not find address for {token_symbol}")
                return False

            # Get token balance
            token = self.w3.eth.contract(
                address=Web3.to_checksum_address(token_address),
                abi=ERC20_ABI
            )
            decimals = self._get_token_decimals(token_address)
            balance = token.functions.balanceOf(self.wallet_address).call()

            if balance == 0:
                print(f"   ⚠️  No balance to close")
                return False

            # Find USDC address
            usdc_address = self.dex_config['tokens'].get('USDC')
            if not usdc_address:
                print(f"   ❌ USDC not configured")
                return False

            balance_formatted = balance / (10 ** decimals)
            expected_usdc = balance_formatted * current_price

            print(f"   💱 Creating close intent: {balance_formatted:.8f} {token_symbol} → USDC")
            print(f"   📊 Expected USDC: ~${expected_usdc:.4f}")

            # Execute via CoW Protocol intent
            slippage_bps = int(self.slippage_tolerance * 100)
            order_uid = self.cow_client.execute_intent(
                sell_token=token_address,
                buy_token=usdc_address,
                sell_amount=balance,
                slippage_bps=slippage_bps
            )

            if order_uid:
                print(f"   🐮 Close intent submitted!")
                print(f"   📋 Order UID: {order_uid[:20]}...")

                # Track pending close intent
                pending_intent = {
                    'order_uid': order_uid,
                    'position': position,
                    'current_price': current_price,
                    'timestamp': datetime.now().isoformat(),
                    'type': 'close'  # Mark as closing position
                }
                self.pending_intents.append(pending_intent)

                return True
            else:
                print(f"   ❌ Failed to submit close intent")
                return False

        except Exception as e:
            print(f"   ❌ Error closing position: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _check_pending_intents(self, market_data: Dict) -> None:
        """Check status of pending CoW Protocol intents and finalize positions"""
        if not self.pending_intents:
            return

        print(f"\n🐮 Checking {len(self.pending_intents)} pending intent(s)...")

        for intent in self.pending_intents[:]:  # Copy list to allow removal during iteration
            order_uid = intent['order_uid']
            is_fulfilled, status = self.cow_client.is_order_fulfilled(order_uid)

            if is_fulfilled:
                print(f"   ✅ Intent fulfilled: {order_uid[:20]}...")
                self._finalize_intent(intent, market_data)
                self.pending_intents.remove(intent)

            elif status in ['expired', 'cancelled']:
                print(f"   ⚠️  Intent {status}: {order_uid[:20]}...")
                self.pending_intents.remove(intent)

            else:
                # Still pending (open)
                print(f"   ⏳ Intent pending: {order_uid[:20]}... ({status})")

    def _finalize_intent(self, intent: Dict, market_data: Dict) -> None:
        """Finalize a fulfilled intent by recording the position or completing the close"""
        intent_type = intent.get('type', 'open')

        if intent_type == 'open':
            # Opening a new position (market order)
            position = {
                'market': intent['market'],
                'action': intent['action'],
                'entry_price': intent['entry_price'],
                'target_price': intent['target_price'],
                'stop_loss': intent['stop_loss'],
                'confidence': intent['confidence'],
                'reasoning': intent.get('reasoning', ''),
                'order_uid': intent['order_uid'],
                'timestamp': intent['timestamp'],
                'fulfilled_at': datetime.now().isoformat(),
                'amount_usd': intent['amount_usd'],
                'token_in': intent['token_in'],
                'token_out': intent['token_out'],
                'contract_address': intent['contract_address'],
                'slippage_bps': intent.get('slippage_bps', 0)
            }
            self.open_positions.append(position)
            print(f"   📊 Position opened: {intent['market']}")

        elif intent_type == 'limit_open':
            # Limit buy order filled
            position = {
                'market': intent['market'],
                'action': intent['action'],
                'entry_price': intent['limit_price'],  # Filled at limit price
                'target_price': intent['target_price'],
                'stop_loss': intent['stop_loss'],
                'confidence': intent['confidence'],
                'reasoning': intent.get('reasoning', ''),
                'order_uid': intent['order_uid'],
                'timestamp': intent['timestamp'],
                'fulfilled_at': datetime.now().isoformat(),
                'amount_usd': intent['amount_usd'],
                'token_in': intent['token_in'],
                'token_out': intent['token_out'],
                'contract_address': intent['contract_address'],
                'order_type': 'limit'
            }
            self.open_positions.append(position)
            print(f"   📊 LIMIT ORDER filled: {intent['market']} at {intent['limit_price']}")

        elif intent_type == 'close':
            # Closing an existing position (market order)
            position = intent['position']
            current_price = intent['current_price']

            # Calculate PnL
            pnl_pct = ((current_price - position['entry_price']) / position['entry_price']) * 100
            pnl_usd = (current_price - position['entry_price']) / position['entry_price'] * position['amount_usd']
            self.daily_pnl += pnl_usd

            # Update position with close info
            position['close_price'] = current_price
            position['close_order_uid'] = intent['order_uid']
            position['pnl_usd'] = pnl_usd
            position['pnl_pct'] = pnl_pct
            position['closed_at'] = datetime.now().isoformat()

            # Move to closed positions
            if position in self.open_positions:
                self.open_positions.remove(position)
            self.closed_positions.append(position)

            print(f"   📊 Position closed: {position['token_out']}")
            print(f"   💰 PnL: ${pnl_usd:.4f} ({pnl_pct:+.2f}%)")

        elif intent_type == 'limit_close':
            # Limit sell order filled
            position = intent['position']
            limit_price = intent['limit_price']

            # Calculate PnL using limit price
            pnl_pct = ((limit_price - position['entry_price']) / position['entry_price']) * 100
            pnl_usd = (limit_price - position['entry_price']) / position['entry_price'] * position['amount_usd']
            self.daily_pnl += pnl_usd

            # Update position with close info
            position['close_price'] = limit_price
            position['close_order_uid'] = intent['order_uid']
            position['pnl_usd'] = pnl_usd
            position['pnl_pct'] = pnl_pct
            position['closed_at'] = datetime.now().isoformat()
            position['close_type'] = 'limit'

            # Move to closed positions
            if position in self.open_positions:
                self.open_positions.remove(position)
            self.closed_positions.append(position)

            print(f"   📊 LIMIT SELL filled: {position['token_out']} at {limit_price}")
            print(f"   💰 PnL: ${pnl_usd:.4f} ({pnl_pct:+.2f}%)")

    def run(self, interval: int = 60):
        """Main trading loop with enhanced AI"""
        print(f"\n🚀 Starting AI Trading Bot V2 (interval: {interval}s)")
        print("=" * 60)

        cycle = 0
        while True:
            try:
                cycle += 1
                print(f"\n{'='*60}")
                print(f"📊 Cycle {cycle} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"{'='*60}")

                # Check pending intents first
                self._check_pending_intents({})

                # Fetch ENHANCED market data
                market_data = get_enhanced_market_data()

                if not market_data:
                    print("⚠️  No market data, skipping cycle")
                    time.sleep(interval)
                    continue

                # # Pretty print market summary
                # print_enhanced_market_summary(market_data)

                # Generate AI actions - AI decides ALL actions (open, close, hold, etc.)
                print("\n🤖 Asking AI to analyze markets and decide actions...")
                actions = self.generate_ai_actions(market_data)

                if actions:
                    print(f"\n✨ AI generated {len(actions)} action(s)")
                    for action in actions:
                        # Only check safety limits for opening new positions
                        if action.get('action', '').upper() in ['BUY', 'SELL', 'MARKET_MAKE']:
                            if not self._check_safety_limits():
                                print(f"   ⚠️  Safety limits reached, skipping {action.get('action')} action")
                                continue
                        self.execute_action(action, market_data)
                        time.sleep(2)
                else:
                    print("⏸️  AI decided to wait (no actions needed)")

                # Status
                print(f"\n📈 Portfolio Status:")
                print(f"   Open Positions: {len(self.open_positions)}/{self.max_open_positions}")
                print(f"   Pending Intents: {len(self.pending_intents)}")
                print(f"   Daily PnL: ${self.daily_pnl:.4f}")
                print(f"   AI Success Rate: {len([s for s in self.signal_history[-20:] if s.get('outcome')=='success'])/max(len(self.signal_history[-20:]), 1)*100:.1f}%")

                # Wait
                time.sleep(interval)

            except KeyboardInterrupt:
                print("\n🛑 Shutting down...")
                break
            except Exception as e:
                print(f"❌ Error: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(10)


if __name__ == "__main__":
    print("🤖 AI Trading Bot V2 - Enhanced with Liquidity Intelligence")
    print("=" * 60)
    print("⚠️  This version uses:")
    print("   • CoW Protocol intents (MEV-protected, solver-based)")
    print("   • Liquidity flow analysis (smart money tracking)")
    print("   • Real slippage data (execution quality)")
    print("   • Enhanced AI prompting (better context)")
    print("   • Performance tracking (AI learns from results)")
    print("=" * 60)

    response = input("\n🟢 Type 'START' to begin: ")
    if response.strip().upper() == 'START':
        bot = AITradingBotV2()
        bot.run(interval=60)
    else:
        print("❌ Cancelled")
