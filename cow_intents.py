"""
CoW Protocol Intent Client for Base Chain
Implements EIP-712 signed orders for solver-based trade execution
"""

import os
import time
import requests
from typing import Dict, Optional, Tuple
from datetime import datetime
from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3

from config import COW_PROTOCOL_BASE, ERC20_ABI


# EIP-712 Domain for CoW Protocol
COW_DOMAIN = {
    "name": "Gnosis Protocol",
    "version": "v2",
    "chainId": COW_PROTOCOL_BASE["chain_id"],
    "verifyingContract": COW_PROTOCOL_BASE["settlement"],
}

# EIP-712 Order Types
COW_ORDER_TYPES = {
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ],
    "Order": [
        {"name": "sellToken", "type": "address"},
        {"name": "buyToken", "type": "address"},
        {"name": "receiver", "type": "address"},
        {"name": "sellAmount", "type": "uint256"},
        {"name": "buyAmount", "type": "uint256"},
        {"name": "validTo", "type": "uint32"},
        {"name": "appData", "type": "bytes32"},
        {"name": "feeAmount", "type": "uint256"},
        {"name": "kind", "type": "string"},
        {"name": "partiallyFillable", "type": "bool"},
        {"name": "sellTokenBalance", "type": "string"},
        {"name": "buyTokenBalance", "type": "string"},
    ],
}


class CowIntentClient:
    """
    Client for creating and submitting CoW Protocol intents (orders).
    Handles EIP-712 signing, quote fetching, and order status tracking.
    """

    def __init__(self, w3: Web3, account: Account):
        """
        Initialize the CoW intent client.

        Args:
            w3: Web3 instance connected to Base
            account: Account object with private key for signing
        """
        self.w3 = w3
        self.account = account
        self.wallet_address = account.address
        self.api_url = COW_PROTOCOL_BASE["api_url"]
        self.vault_relayer = COW_PROTOCOL_BASE["vault_relayer"]
        self.order_validity = int(os.getenv("COW_ORDER_VALIDITY_SECONDS", 1200))  # 20 min default

    def get_quote(
        self,
        sell_token: str,
        buy_token: str,
        sell_amount: int,
        kind: str = "sell"
    ) -> Optional[Dict]:
        """
        Get a quote from CoW API for a trade.

        Args:
            sell_token: Address of token to sell
            buy_token: Address of token to buy
            sell_amount: Amount to sell in wei
            kind: "sell" or "buy" order type

        Returns:
            Quote dict with buyAmount, feeAmount, etc. or None if failed
        """
        try:
            url = f"{self.api_url}/quote"
            payload = {
                "sellToken": sell_token,
                "buyToken": buy_token,
                "receiver": self.wallet_address,
                "appData": COW_PROTOCOL_BASE["app_data_json"],  # Full JSON document
                "appDataHash": COW_PROTOCOL_BASE["app_data"],   # keccak256 hash
                "partiallyFillable": False,
                "sellTokenBalance": "erc20",
                "buyTokenBalance": "erc20",
                "from": self.wallet_address,
                "kind": kind,
                "sellAmountBeforeFee": str(sell_amount),
            }

            response = requests.post(url, json=payload, timeout=30)

            if response.status_code == 200:
                quote = response.json()
                print(f"   CoW Quote received:")
                print(f"   - Sell: {int(quote['quote']['sellAmount']) / 1e18:.6f}")
                print(f"   - Buy: {int(quote['quote']['buyAmount']) / 1e18:.8f}")
                print(f"   - Fee: {int(quote['quote']['feeAmount']) / 1e18:.8f}")
                return quote
            else:
                print(f"   CoW Quote error: {response.status_code} - {response.text}")
                return None

        except Exception as e:
            print(f"   CoW Quote failed: {e}")
            return None

    def create_order(
        self,
        sell_token: str,
        buy_token: str,
        sell_amount: int,
        buy_amount: int,
        fee_amount: int,
        valid_to: Optional[int] = None,
        kind: str = "sell",
    ) -> Dict:
        """
        Create an order struct for signing.

        Args:
            sell_token: Address of token to sell
            buy_token: Address of token to buy
            sell_amount: Amount to sell (after fee) in wei
            buy_amount: Minimum amount to receive in wei
            fee_amount: Protocol fee in wei
            valid_to: Unix timestamp for order expiry
            kind: "sell" or "buy"

        Returns:
            Order dict ready for EIP-712 signing
        """
        if valid_to is None:
            valid_to = int(time.time()) + self.order_validity

        order = {
            "sellToken": Web3.to_checksum_address(sell_token),
            "buyToken": Web3.to_checksum_address(buy_token),
            "receiver": self.wallet_address,
            "sellAmount": sell_amount,
            "buyAmount": buy_amount,
            "validTo": valid_to,
            "appData": COW_PROTOCOL_BASE["app_data"],
            "feeAmount": fee_amount,
            "kind": kind,
            "partiallyFillable": False,
            "sellTokenBalance": "erc20",
            "buyTokenBalance": "erc20",
        }

        return order

    def sign_order(self, order: Dict) -> Tuple[str, str]:
        """
        Sign an order using EIP-712.

        Args:
            order: Order dict to sign

        Returns:
            Tuple of (signature, signing_scheme)
        """
        # Build the typed data structure for EIP-712
        typed_data = {
            "types": COW_ORDER_TYPES,
            "primaryType": "Order",
            "domain": COW_DOMAIN,
            "message": order,
        }

        # Sign using eth_account's encode_typed_data
        signable = encode_typed_data(full_message=typed_data)
        signed = self.account.sign_message(signable)

        # CoW expects signature as hex string
        signature = signed.signature.hex()
        if not signature.startswith("0x"):
            signature = "0x" + signature

        return signature, "eip712"

    def submit_order(self, order: Dict, signature: str) -> Optional[str]:
        """
        Submit a signed order to CoW API.

        Args:
            order: Order dict
            signature: EIP-712 signature hex string

        Returns:
            Order UID if successful, None otherwise
        """
        try:
            url = f"{self.api_url}/orders"

            # API expects amounts as strings
            payload = {
                "sellToken": order["sellToken"],
                "buyToken": order["buyToken"],
                "receiver": order["receiver"],
                "sellAmount": str(order["sellAmount"]),
                "buyAmount": str(order["buyAmount"]),
                "validTo": order["validTo"],
                "appData": COW_PROTOCOL_BASE["app_data_json"],  # Full JSON document
                "appDataHash": COW_PROTOCOL_BASE["app_data"],   # keccak256 hash
                "feeAmount": str(order["feeAmount"]),
                "kind": order["kind"],
                "partiallyFillable": order["partiallyFillable"],
                "sellTokenBalance": order["sellTokenBalance"],
                "buyTokenBalance": order["buyTokenBalance"],
                "signingScheme": "eip712",
                "signature": signature,
                "from": self.wallet_address,
            }

            response = requests.post(url, json=payload, timeout=30)

            if response.status_code == 201:
                order_uid = response.json()
                print(f"   CoW Order submitted: {order_uid[:20]}...")
                return order_uid
            else:
                print(f"   CoW Submit error: {response.status_code} - {response.text}")
                return None

        except Exception as e:
            print(f"   CoW Submit failed: {e}")
            return None

    def get_order_status(self, order_uid: str) -> Optional[Dict]:
        """
        Get the status of an order.

        Args:
            order_uid: The order UID returned from submit

        Returns:
            Order status dict or None if failed
        """
        try:
            url = f"{self.api_url}/orders/{order_uid}"
            response = requests.get(url, timeout=30)

            if response.status_code == 200:
                return response.json()
            else:
                return None

        except Exception as e:
            print(f"   CoW Status check failed: {e}")
            return None

    def is_order_fulfilled(self, order_uid: str) -> Tuple[bool, str]:
        """
        Check if an order has been fulfilled.

        Args:
            order_uid: The order UID

        Returns:
            Tuple of (is_fulfilled, status_string)
        """
        status = self.get_order_status(order_uid)
        if status is None:
            return False, "unknown"

        order_status = status.get("status", "unknown")

        # CoW order statuses: open, fulfilled, cancelled, expired
        if order_status == "fulfilled":
            return True, "fulfilled"
        elif order_status in ["cancelled", "expired"]:
            return False, order_status
        else:
            return False, order_status

    def cancel_order(self, order_uid: str) -> bool:
        """
        Cancel a pending order.

        Args:
            order_uid: The order UID to cancel

        Returns:
            True if cancelled successfully
        """
        try:
            # Cancellation requires signing the order UID
            # For now, we just let orders expire
            print(f"   Order cancellation not implemented - order will expire")
            return False
        except Exception as e:
            print(f"   CoW Cancel failed: {e}")
            return False

    def approve_vault_relayer(self, token_address: str, amount: int) -> bool:
        """
        Approve the CoW vault relayer to spend tokens.

        Args:
            token_address: Token contract address
            amount: Amount to approve (use max uint256 for unlimited)

        Returns:
            True if approval successful or already approved
        """
        try:
            token = self.w3.eth.contract(
                address=Web3.to_checksum_address(token_address),
                abi=ERC20_ABI
            )

            # Check current allowance
            allowance = token.functions.allowance(
                self.wallet_address,
                self.vault_relayer
            ).call()

            if allowance >= amount:
                print(f"   Vault relayer already approved")
                return True

            # Build approval transaction
            print(f"   Approving vault relayer...")
            approve_tx = token.functions.approve(
                Web3.to_checksum_address(self.vault_relayer),
                amount
            ).build_transaction({
                'from': self.wallet_address,
                'gas': 100000,
                'gasPrice': self.w3.eth.gas_price,
                'nonce': self.w3.eth.get_transaction_count(self.wallet_address),
            })

            # Sign and send
            signed_tx = self.account.sign_transaction(approve_tx)
            raw_tx = getattr(signed_tx, 'rawTransaction', None) or getattr(signed_tx, 'raw_transaction', None)
            tx_hash = self.w3.eth.send_raw_transaction(raw_tx)

            # Wait for receipt
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

            if receipt['status'] == 1:
                print(f"   Vault relayer approved")
                return True
            else:
                print(f"   Vault relayer approval failed")
                return False

        except Exception as e:
            print(f"   Vault relayer approval error: {e}")
            return False

    def execute_intent(
        self,
        sell_token: str,
        buy_token: str,
        sell_amount: int,
        slippage_bps: int = 50
    ) -> Optional[str]:
        """
        Execute a complete intent flow: quote -> approve -> sign -> submit.

        Args:
            sell_token: Address of token to sell
            buy_token: Address of token to buy
            sell_amount: Amount to sell in wei
            slippage_bps: Slippage tolerance in basis points (default 0.5%)

        Returns:
            Order UID if successful, None otherwise
        """
        print(f"\n   Creating CoW intent...")
        print(f"   Sell: {sell_token[:10]}... Amount: {sell_amount}")
        print(f"   Buy: {buy_token[:10]}...")

        # 1. Get quote
        quote = self.get_quote(sell_token, buy_token, sell_amount)
        if not quote:
            return None

        quote_data = quote.get("quote", {})

        # Extract amounts from quote
        quoted_sell_amount = int(quote_data.get("sellAmount", 0))
        quoted_buy_amount = int(quote_data.get("buyAmount", 0))
        # Note: feeAmount must be 0 in the order - CoW Protocol handles fees internally

        # Apply slippage to buy amount
        min_buy_amount = int(quoted_buy_amount * (10000 - slippage_bps) / 10000)

        # 2. Approve vault relayer
        if not self.approve_vault_relayer(sell_token, sell_amount):
            return None

        # 3. Create order (fee_amount must be 0)
        order = self.create_order(
            sell_token=sell_token,
            buy_token=buy_token,
            sell_amount=quoted_sell_amount,
            buy_amount=min_buy_amount,
            fee_amount=0,  # Must be zero - CoW handles fees internally
        )

        # 4. Sign order
        signature, scheme = self.sign_order(order)
        print(f"   Order signed with {scheme}")

        # 5. Submit order
        order_uid = self.submit_order(order, signature)

        if order_uid:
            print(f"   Intent submitted successfully")
            print(f"   Track at: https://explorer.cow.fi/base/orders/{order_uid}")

        return order_uid

    def execute_limit_order(
        self,
        sell_token: str,
        buy_token: str,
        sell_amount: int,
        limit_price: float,
        sell_token_decimals: int = 18,
        buy_token_decimals: int = 18,
        valid_hours: int = 24
    ) -> Optional[str]:
        """
        Execute a limit order - only fills when price reaches target.

        This is a TRUE limit order that sits on CoW until:
        1. Price reaches the limit (then solvers fill it)
        2. Order expires (no gas lost)

        Args:
            sell_token: Address of token to sell
            buy_token: Address of token to buy
            sell_amount: Amount to sell in wei
            limit_price: Target price (buy_token per sell_token)
            sell_token_decimals: Decimals of sell token
            buy_token_decimals: Decimals of buy token
            valid_hours: How long the order stays active (default 24h)

        Returns:
            Order UID if successful, None otherwise
        """
        print(f"\n   Creating CoW LIMIT ORDER...")
        print(f"   Sell: {sell_token[:10]}... Amount: {sell_amount}")
        print(f"   Buy: {buy_token[:10]}...")
        print(f"   Limit Price: {limit_price}")
        print(f"   Valid for: {valid_hours} hours")

        # Calculate buy amount based on limit price
        # For a limit buy: sell_amount (USDC) * limit_price = buy_amount (WETH)
        # Example: 1 USDC at limit price 0.00035 WETH/USDC = 0.00035 WETH
        sell_amount_normalized = sell_amount / (10 ** sell_token_decimals)
        buy_amount_normalized = sell_amount_normalized * limit_price
        min_buy_amount = int(buy_amount_normalized * (10 ** buy_token_decimals))

        print(f"   Calculated min buy: {min_buy_amount / (10 ** buy_token_decimals):.8f}")

        # Approve vault relayer
        if not self.approve_vault_relayer(sell_token, sell_amount):
            return None

        # Create order with longer validity for limit orders
        valid_to = int(time.time()) + (valid_hours * 3600)

        order = self.create_order(
            sell_token=sell_token,
            buy_token=buy_token,
            sell_amount=sell_amount,
            buy_amount=min_buy_amount,
            fee_amount=0,
            valid_to=valid_to,
        )

        # Sign order
        signature, scheme = self.sign_order(order)
        print(f"   Order signed with {scheme}")

        # Submit order
        order_uid = self.submit_order(order, signature)

        if order_uid:
            print(f"   LIMIT ORDER submitted!")
            print(f"   Will fill when price reaches {limit_price}")
            print(f"   Expires: {valid_hours}h from now")
            print(f"   Track at: https://explorer.cow.fi/base/orders/{order_uid}")

        return order_uid
