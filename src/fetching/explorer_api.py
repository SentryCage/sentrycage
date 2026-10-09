"""
API client for blockchain explorer APIs using Etherscan API V2
Migrated to V2 unified endpoint with chainid parameter support
"""
from typing import Dict, List, Any, Optional
import logging
import os

import requests

from src.config.settings import ETHERSCAN_API_KEY
from src.config.wallet_registry import Chain

# Configure logger
logger = logging.getLogger(__name__)

class ExplorerError(RuntimeError):
    pass


class ExplorerTimeout(ExplorerError):
    pass


class InvalidExplorerResponse(ExplorerError):
    pass


class IncompleteExplorerScan(ExplorerError):
    pass


def wallet_transaction_pages(client, address, chain, start_block, end_block, *, page_size=100, max_pages=1000):
    """Scan a fixed block range in ascending order, retaining completeness on errors.

    Etherscan V2 txlist pagination: https://docs.etherscan.io/api-reference/endpoint/txlist
    A short/empty page terminates the scan. Full pages require another request.
    """
    if page_size < 1 or max_pages < 1 or start_block < 0 or end_block < start_block:
        raise ValueError('Invalid explorer scan bounds')
    seen = {}
    last_block = start_block
    for page in range(1, max_pages + 1):
        rows = client.get_wallet_transactions(address=address, chain=chain, start_block=start_block,
                                              end_block=end_block, page=page, offset=page_size, sort='asc')
        if not isinstance(rows, list) or len(rows) > page_size:
            raise InvalidExplorerResponse('Invalid explorer page size or format')
        fresh = []
        for tx in rows:
            try:
                block = int(tx['blockNumber'])
                tx_hash = tx['hash'].lower()
                if not tx_hash or not isinstance(tx['from'], str) or (tx['to'] is not None and not isinstance(tx['to'], str)):
                    raise ValueError
                if int(tx['value']) < 0 or block < last_block or not start_block <= block <= end_block:
                    raise ValueError
            except (KeyError, TypeError, ValueError, AttributeError):
                raise InvalidExplorerResponse('Explorer page has invalid fields, ordering or block bounds') from None
            last_block = block
            identity = tuple(str(tx.get(key, '')) for key in ('from', 'to', 'value', 'blockNumber', 'input', 'isError'))
            if tx_hash in seen:
                if seen[tx_hash] != identity:
                    raise InvalidExplorerResponse('Explorer returned conflicting copies of a transaction')
                continue
            seen[tx_hash] = identity
            fresh.append(dict(tx, hash=tx_hash))
        if rows and not fresh and len(rows) == page_size:
            raise IncompleteExplorerScan('Explorer pagination made no progress; checkpoint retained')
        if fresh:
            yield fresh
        if len(rows) < page_size:
            return
    raise IncompleteExplorerScan('Explorer page budget exhausted; checkpoint retained; narrow the range or raise EXPLORER_MAX_PAGES')


class ExplorerAPIClient:
    """Client for interacting with Etherscan API V2 (unified multichain endpoint)"""

    # V2 uses a single unified base URL
    BASE_URL = "https://api.etherscan.io/v2/api"

    # Chain ID mapping for V2 API
    CHAIN_IDS = {
        Chain.ETHEREUM: 1,
        Chain.ARBITRUM: 42161
    }
    
    def __init__(self, api_key=None, timeout=10):
        """Initialize the explorer API client"""
        self.api_key = api_key if api_key is not None else os.getenv('ETHERSCAN_API_KEY', ETHERSCAN_API_KEY)
        self.timeout = timeout
        self.session = requests.Session()

        # Check if API key is configured (V2 uses single key for all chains)
        if not self.api_key:
            logger.warning("No Etherscan API key configured. Set ETHERSCAN_API_KEY in your environment.")
    
    def _make_request(
        self,
        chain: Chain,
        action: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Make a request to the Etherscan API V2

        Args:
            chain: The blockchain to query
            action: API action to perform
            params: Additional parameters for the request

        Returns:
            API response data
        """
        chain_id = self.CHAIN_IDS.get(chain)
        if chain_id is None:
            raise ExplorerError('Unsupported explorer chain')
        if not self.api_key:
            raise ExplorerError('ETHERSCAN_API_KEY is missing')
        request_params = {
            'chainid': chain_id,
            'module': 'account' if action in ('txlist', 'txlistinternal', 'tokentx') else 'proxy',
            'action': action,
            'apikey': self.api_key,
        }
        request_params.update(params or {})
        try:
            response = self.session.get(self.BASE_URL, params=request_params, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
        except requests.Timeout:
            raise ExplorerTimeout('Explorer request timed out; retry or check connectivity') from None
        except ValueError:
            raise InvalidExplorerResponse('Explorer returned invalid JSON') from None
        except requests.RequestException:
            raise ExplorerError('Explorer HTTP/connection error; check access and connectivity') from None
        if not isinstance(data, dict):
            raise InvalidExplorerResponse('Explorer returned a non-object response')
        if 'error' in data:
            raise ExplorerError('Explorer returned an API error; check key and service access')
        if data.get('status') == '0' and data.get('message') != 'No transactions found':
            raise ExplorerError('Explorer rejected request; check key, rate limits and chain access')
        return data

    def _transaction_result(self, response):
        result = response.get('result')
        if response.get('status') == '0' and response.get('message') == 'No transactions found' and result == []:
            return []
        if response.get('status') != '1' or not isinstance(result, list):
            raise InvalidExplorerResponse('Explorer transaction result must be a successful list')
        for tx in result:
            if not isinstance(tx, dict) or not all(key in tx for key in ('hash', 'from', 'to', 'value')):
                raise InvalidExplorerResponse('Explorer returned an incomplete transaction')
            try:
                if not isinstance(tx['hash'], str) or not tx['hash'] or not isinstance(tx['from'], str):
                    raise ValueError
                if tx['to'] is not None and not isinstance(tx['to'], str):
                    raise ValueError
                if int(tx['value']) < 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise InvalidExplorerResponse('Explorer returned invalid transaction fields') from None
        return result

    def get_wallet_transactions(
        self, 
        address: str, 
        chain: Chain, 
        start_block: int = 0, 
        end_block: int = 99999999,
        page: int = 1,
        offset: int = 100,
        sort: str = "desc"
    ) -> List[Dict[str, Any]]:
        """
        Get transactions for a wallet address
        
        Args:
            address: Wallet address to check
            chain: The blockchain to query
            start_block: Starting block
            end_block: Ending block
            page: Page number
            offset: Number of results per page
            sort: Sort direction ('asc' or 'desc')
            
        Returns:
            List of transactions
        """
        params = {
            "address": address,
            "startblock": start_block,
            "endblock": end_block,
            "page": page,
            "offset": offset,
            "sort": sort
        }
        
        response = self._make_request(chain, "txlist", params)
        
        return self._transaction_result(response)

    def get_token_transfers(
        self,
        address: str,
        chain: Chain,
        contract_address: Optional[str] = None,
        start_block: int = 0,
        end_block: int = 99999999,
        page: int = 1,
        offset: int = 100,
        sort: str = "desc"
    ) -> List[Dict[str, Any]]:
        """
        Get token transfers for a wallet address
        
        Args:
            address: Wallet address to check
            chain: The blockchain to query
            contract_address: Specific ERC20 token contract (optional)
            start_block: Starting block
            end_block: Ending block
            page: Page number
            offset: Number of results per page
            sort: Sort direction ('asc' or 'desc')
            
        Returns:
            List of token transfers
        """
        params = {
            "address": address,
            "startblock": start_block,
            "endblock": end_block,
            "page": page,
            "offset": offset,
            "sort": sort
        }
        
        if contract_address:
            params["contractaddress"] = contract_address
            action = "tokentx"  # ERC20 token transfers
        else:
            action = "tokentx"  # All token transfers
            
        response = self._make_request(chain, action, params)
        
        return self._transaction_result(response)

    def get_internal_transactions(
        self,
        address: str,
        chain: Chain,
        start_block: int = 0,
        end_block: int = 99999999,
        page: int = 1,
        offset: int = 100,
        sort: str = "desc"
    ) -> List[Dict[str, Any]]:
        """
        Get internal transactions for a wallet address
        
        Args:
            address: Wallet address to check
            chain: The blockchain to query
            start_block: Starting block
            end_block: Ending block
            page: Page number
            offset: Number of results per page
            sort: Sort direction ('asc' or 'desc')
            
        Returns:
            List of internal transactions
        """
        params = {
            "address": address,
            "startblock": start_block,
            "endblock": end_block,
            "page": page,
            "offset": offset,
            "sort": sort
        }
        
        response = self._make_request(chain, "txlistinternal", params)
        
        return self._transaction_result(response)
