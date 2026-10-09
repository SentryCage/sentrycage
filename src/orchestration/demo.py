"""Credential-free synthetic event using the production pipeline."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from src.config.wallet_registry import ALL_WALLETS
from src.orchestration.pipeline import TransactionPipeline, SQLiteStorage


DEMO_TRANSACTION = {
    'hash': '0x' + 'ab' * 32,
    'from': '0x' + '11' * 20,
    'to': ALL_WALLETS[1].address,
    'value': str(500 * 10**18),
    'input': '0x',
    'blockNumber': '12345678',
    'timeStamp': '1791547200',
    'isError': '0',
}


class ConsoleRecipient:
    async def send_signal(self, signal):
        print('ALERT — DEMO — dati sintetici / synthetic data')
        print(signal.get_message())
        return True


async def run_demo():
    print('SentryCage — DEMO — dati sintetici / synthetic data')
    print('EVENT: ' + json.dumps(DEMO_TRANSACTION, sort_keys=True))
    with TemporaryDirectory(prefix='sentrycage-demo-') as directory:
        storage = SQLiteStorage(str(Path(directory)/'demo.db'))
        pipeline = TransactionPipeline(storage, ConsoleRecipient(), threshold=100)
        result = await pipeline.process([DEMO_TRANSACTION], 'ethereum')
        rows = storage.read()
        for signal in result.signals:
            print(f'SIGNAL: {signal.signal_type.value} | {signal.value_eth:.2f} ETH | confidence {signal.confidence:.2f}')
        if len(rows) != len(result.signals) or not rows:
            raise RuntimeError('Demo persistence verification failed')
        print(f'SQLITE: {len(rows)} signal saved and read back; isolated temporary database removed on exit')
    return 0
