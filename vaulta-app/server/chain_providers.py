"""Blockchain monitoring and wallet/custody provider interfaces (M-crypto).

Business logic (crypto_deposits, crypto_withdrawals, crypto_recon) talks ONLY to BlockchainProvider and WalletProvider.
It never imports a vendor SDK, an RPC client or a signing library.

  development  MockBlockchainProvider + MockWalletProvider   in-memory chains, no real funds, no keys
  staging      Testnet* providers                            must be wired to testnet RPC + test custody (stubs refuse until configured)
  production   Rpc* / SecureCustodyProvider                 must be wired to real nodes/indexers and an external custody/HSM/MPC service

The Live/Testnet classes are deliberately unimplemented: they raise ChainProviderNotConfigured instead of pretending a transaction happened.
No class in this module accepts, stores or returns a private key, seed phrase or signing secret.
"""
import hashlib, secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
from . import util, chain_addresses as ca
from .util import ApiError

class ChainProviderNotConfigured(ApiError):
    def __init__(self, what): super().__init__(503, 'provider_not_configured', f'{what} is not configured. Nothing was sent or credited.')

@dataclass(frozen=True)
class ChainTransfer:
    """One transfer EVENT. A single transaction can contain several (EVM logs, UTXO outputs, Solana instructions)."""
    network: str; tx_hash: str; event_index: int
    sender: Optional[str]; recipient: str
    contract: Optional[str]                 # None = native asset (BTC/ETH/SOL)
    amount: int                             # minor units of that asset on that network
    block_number: Optional[int]; block_hash: Optional[str]
    confirmations: int; success: bool = True
    memo: Optional[str] = None
    commitment: Optional[str] = None        # solana-style: processed | confirmed | finalized
    timestamp: Optional[str] = None

@dataclass
class VerifyResult:
    ok: bool; reasons: list = field(default_factory=list); transfer: Optional[ChainTransfer] = None

class BlockchainProvider(ABC):
    name = 'abstract'; mode = 'abstract'
    def prepare_network(self, net_row): """Hook called with each registered network row before use."""
    @abstractmethod
    def get_transaction(self, network, tx_hash): """-> list[ChainTransfer] for every transfer event in the tx, or None if the tx is unknown."""
    @abstractmethod
    def get_confirmations(self, network, tx_hash): """-> int (0 = in mempool) or None if unknown."""
    @abstractmethod
    def get_block(self, network, ref): """-> {'number','hash','timestamp'} or None."""
    @abstractmethod
    def get_balance(self, network, address, contract=None, min_confirmations=1): """-> int minor units, counting only value with >= min_confirmations."""
    @abstractmethod
    def latest_block(self, network): """-> int"""
    @abstractmethod
    def list_incoming(self, network, address, from_block=0): """-> every transfer TO address (any asset, any contract, incl. unconfirmed), block >= from_block."""
    @abstractmethod
    def verify_ownership(self, network, address, message, signature): """-> bool. Signed-message proof that the signer controls `address`."""
    def verify_transaction(self, network, tx_hash, event_index, *, recipient, contract=None, amount=None):
        """Independently re-fetch the transfer and compare every field that matters. Never trusts the caller's claims."""
        evs = self.get_transaction(network, tx_hash)
        if not evs: return VerifyResult(False, ['transaction_not_found'])
        ev = next((e for e in evs if e.event_index == event_index), None)
        if ev is None: return VerifyResult(False, ['event_not_found'])
        why = []
        if not ev.success: why.append('transaction_failed_on_chain')
        if ca.address_key(self._fmt(network), ev.recipient) != ca.address_key(self._fmt(network), recipient): why.append('recipient_mismatch')
        if (ev.contract or '').lower() != (contract or '').lower(): why.append('contract_mismatch')
        if amount is not None and int(ev.amount) != int(amount): why.append('amount_mismatch')
        return VerifyResult(not why, why, ev)
    def _fmt(self, network): return 'evm'

class WalletProvider(ABC):
    """Signing/custody boundary. Vaulta asks for a transfer; the provider (outside this process) holds keys and signs."""
    name = 'abstract'; mode = 'abstract'
    @abstractmethod
    def get_wallet_address(self, ledger_code, wallet_type='deposit'): """-> the address the custody system reports for this wallet role, or None."""
    @abstractmethod
    def controls_address(self, network, address): """-> True only if the custody system itself confirms it controls `address`."""
    @abstractmethod
    def get_balance(self, network, address, contract=None): """-> int minor units as reported by custody."""
    @abstractmethod
    def create_transaction(self, network, from_address, to_address, amount, contract=None, client_ref=None): """-> prepared transaction dict (no secret material)."""
    @abstractmethod
    def submit_transaction(self, prepared): """-> {'tx_hash','status', 'network_fee'}; signing happens inside the custody system."""
    @abstractmethod
    def get_transaction(self, network, tx_hash): """-> {'status','confirmations',...} or None."""

# =====================================================================================================
#  Mock chain (development and tests only)
# =====================================================================================================
class MockChain:
    """Deterministic in-memory simulation of several blockchains. Holds no keys. Never reachable from production."""
    def __init__(self): self.nets = {}; self._n = 0
    def register(self, code, fmt, testnet=True):
        self.nets.setdefault(code, {'fmt': fmt, 'height': 0, 'blocks': {}, 'txs': {}, 'testnet': testnet, 'order': []})
    def _net(self, code):
        if code not in self.nets: raise ApiError(404, 'unknown_network', f'Mock chain has no network {code!r}')
        return self.nets[code]
    def _hash(self, code, tag):
        self._n += 1; h = hashlib.sha256(f'{code}|{tag}|{self._n}|{secrets.token_hex(4)}'.encode()).digest()
        f = self._net(code)['fmt']
        return '0x' + h.hex() if f == 'evm' else ca.b58encode(h + h) if f == 'solana' else h.hex()
    def fund(self, network, to, amount, *, sender='external-sender', contract=None, memo=None, event_index=0, mine=False, success=True):
        return self.send(network, [dict(index=event_index, sender=sender, recipient=to, contract=contract, amount=int(amount), memo=memo)], mine=mine, success=success)
    def send(self, network, events, *, mine=False, success=True):
        n = self._net(network); h = self._hash(network, 'tx')
        n['txs'][h] = {'hash': h, 'events': [dict(e) for e in events], 'block': None, 'success': success, 'ts': util.now()}; n['order'].append(h)
        if mine: self.mine(network, 1)
        return h
    def mine(self, network, count=1):
        n = self._net(network)
        for _ in range(count):
            n['height'] += 1; n['blocks'][n['height']] = self._hash(network, f"blk{n['height']}")
            for t in n['txs'].values():
                if t['block'] is None: t['block'] = n['height']
    def reorg(self, network, tx_hash, mode='unconfirm'):
        n = self._net(network); t = n['txs'].get(tx_hash)
        if t is None: raise ApiError(404, 'unknown_tx', 'no such tx')
        if mode == 'drop': del n['txs'][tx_hash]; n['order'].remove(tx_hash); return
        t['block'] = None
        if mode == 'remine': self.mine(network, 1)
    def confirmations(self, network, tx):
        n = self._net(network); return 0 if tx['block'] is None else n['height'] - tx['block'] + 1
    def balance(self, network, address, contract, min_conf):
        n = self._net(network); f = n['fmt']; k = ca.address_key(f, address); tot = 0
        for t in n['txs'].values():
            if not t['success']: continue
            if min_conf > 0 and self.confirmations(network, t) < min_conf: continue
            for e in t['events']:
                if (e.get('contract') or '').lower() != (contract or '').lower(): continue
                if ca.address_key(f, e['recipient']) == k: tot += e['amount']
                if e.get('sender') and ca.address_key(f, e['sender']) == k: tot -= e['amount']
        return tot

class MockBlockchainProvider(BlockchainProvider):
    name = 'mock-chain'; mode = 'mock'
    def __init__(self, chain=None): self.chain = chain or MockChain()
    def prepare_network(self, net):
        if net.get('address_format'): self.chain.register(net['network_code'], net['address_format'], bool(net.get('is_testnet', 1)))
    def _fmt(self, network): return self.chain._net(network)['fmt']
    def _transfers(self, network, t):
        n = self.chain._net(network); conf = self.chain.confirmations(network, t); blk = t['block']
        com = None
        if n['fmt'] == 'solana': com = 'finalized' if conf >= 32 else 'confirmed' if conf >= 1 else 'processed'
        return [ChainTransfer(network, t['hash'], e['index'], e.get('sender'), e['recipient'], e.get('contract'), int(e['amount']), blk, n['blocks'].get(blk) if blk else None,
                              conf, t['success'], e.get('memo'), com, t['ts']) for e in t['events']]
    def get_transaction(self, network, tx_hash):
        t = self.chain._net(network)['txs'].get(tx_hash); return self._transfers(network, t) if t else None
    def get_confirmations(self, network, tx_hash):
        t = self.chain._net(network)['txs'].get(tx_hash); return None if t is None else self.chain.confirmations(network, t)
    def get_block(self, network, ref):
        n = self.chain._net(network)
        if isinstance(ref, int): h = n['blocks'].get(ref); return {'number': ref, 'hash': h, 'timestamp': None} if h else None
        for k, h in n['blocks'].items():
            if h == ref: return {'number': k, 'hash': h, 'timestamp': None}
        return None
    def get_balance(self, network, address, contract=None, min_confirmations=1): return self.chain.balance(network, address, contract, min_confirmations)
    def latest_block(self, network): return self.chain._net(network)['height']
    def list_incoming(self, network, address, from_block=0):
        n = self.chain._net(network); f = n['fmt']; k = ca.address_key(f, address); out = []
        for h in n['order']:
            t = n['txs'].get(h)
            if not t: continue
            if t['block'] is not None and t['block'] < from_block: continue
            out += [x for x in self._transfers(network, t) if ca.address_key(f, x.recipient) == k]
        return out
    def verify_ownership(self, network, address, message, signature): return secrets.compare_digest(str(signature or ''), mock_sign(address, message))

def mock_sign(address, message):
    """Development-only stand-in for a wallet's signed-message proof. NOT cryptography; refuses to exist outside the mock provider's use."""
    return 'mocksig:' + hashlib.sha256(f'{address}|{message}'.encode()).hexdigest()[:40]

class MockWalletProvider(WalletProvider):
    """Pretends to be a custody system. It holds NO keys: 'signing' just moves balances on the in-memory chain."""
    name = 'mock-wallet'; mode = 'mock'
    def __init__(self, chain, fee_minor=0): self.chain = chain; self.fee_minor = fee_minor; self.foreign = set(); self.fail_next = False; self._prep = {}; self.addresses = {}
    def get_wallet_address(self, ledger_code, wallet_type='deposit'): return self.addresses.get((ledger_code, wallet_type))
    def controls_address(self, network, address): return address not in self.foreign
    def get_balance(self, network, address, contract=None): return self.chain.balance(network, address, contract, 1)
    def create_transaction(self, network, from_address, to_address, amount, contract=None, client_ref=None):
        bal = self.chain.balance(network, from_address, contract, 1)
        if bal < int(amount): raise ApiError(409, 'insufficient_custody_balance', 'The custody wallet does not hold enough to cover this transfer.')
        pid = 'prep_' + secrets.token_hex(6)
        self._prep[pid] = dict(id=pid, network=network, from_address=from_address, to_address=to_address, amount=int(amount), contract=contract, client_ref=client_ref)
        return dict(self._prep[pid])
    def submit_transaction(self, prepared):
        p = self._prep.get(prepared.get('id'))
        if not p: raise ApiError(404, 'unknown_prepared_tx', 'Unknown prepared transaction')
        if self.fail_next: self.fail_next = False; raise ApiError(502, 'custody_rejected', 'Custody provider rejected the transaction')
        evs = [dict(index=0, sender=p['from_address'], recipient=p['to_address'], contract=p['contract'], amount=p['amount'])]
        if self.fee_minor and not p['contract']: evs.append(dict(index=1, sender=p['from_address'], recipient='NETWORK-FEE-SINK', contract=None, amount=self.fee_minor))
        h = self.chain.send(p['network'], evs)
        return {'tx_hash': h, 'status': 'broadcast', 'network_fee': str(self.fee_minor if not p['contract'] else 0), 'provider_ref': p['id']}
    def get_transaction(self, network, tx_hash):
        t = self.chain._net(network)['txs'].get(tx_hash)
        return None if not t else {'status': 'confirmed' if t['block'] else 'pending', 'confirmations': self.chain.confirmations(network, t), 'success': t['success']}

# =====================================================================================================
#  Real-world providers: intentionally unimplemented adapters
# =====================================================================================================
class _Unconfigured:
    def _no(self, what): raise ChainProviderNotConfigured(what)

class RpcBlockchainProvider(_Unconfigured, BlockchainProvider):
    """Adapter point for production/staging node or indexer access. Implement against your own infrastructure; until then every call refuses."""
    name = 'rpc-chain'
    def __init__(self, mode): self.mode = mode
    def get_transaction(self, *a, **k): self._no('Blockchain provider')
    def get_confirmations(self, *a, **k): self._no('Blockchain provider')
    def get_block(self, *a, **k): self._no('Blockchain provider')
    def get_balance(self, *a, **k): self._no('Blockchain provider')
    def latest_block(self, *a, **k): self._no('Blockchain provider')
    def list_incoming(self, *a, **k): self._no('Blockchain provider')
    def verify_ownership(self, *a, **k): self._no('Blockchain provider')

class UnconfiguredWalletProvider(_Unconfigured, WalletProvider):
    """TestnetWalletProvider (staging) / SecureCustodyProvider (production): private keys live in the external custody system, never here."""
    def __init__(self, name, mode): self.name = name; self.mode = mode
    def get_wallet_address(self, *a, **k): self._no(self.name)
    def controls_address(self, *a, **k): self._no(self.name)
    def get_balance(self, *a, **k): self._no(self.name)
    def create_transaction(self, *a, **k): self._no(self.name)
    def submit_transaction(self, *a, **k): self._no(self.name)
    def get_transaction(self, *a, **k): self._no(self.name)

def TestnetWalletProvider(): return UnconfiguredWalletProvider('TestnetWalletProvider', 'testnet')
def SecureCustodyProvider(): return UnconfiguredWalletProvider('SecureCustodyProvider', 'live')

def build(mode):
    """mode: mock | testnet | live  ->  (BlockchainProvider, WalletProvider)"""
    if mode == 'mock':
        ch = MockChain(); return MockBlockchainProvider(ch), MockWalletProvider(ch)
    if mode == 'testnet': return RpcBlockchainProvider('testnet'), TestnetWalletProvider()
    if mode == 'live': return RpcBlockchainProvider('live'), SecureCustodyProvider()
    raise RuntimeError(f'unknown CRYPTO_PROVIDER_MODE {mode!r}')
