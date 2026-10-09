# Guida alle prove manuali di SentryCage

Il percorso di installazione e avvio rimane quello del [README](../README.md).
Questa checklist aggiunge risultati attesi e verifiche dei cinque incrementi offline.
Eseguire i comandi dalla radice del repository, con Bash su Linux/WSL e Python 3.12.
Preparare `.venv` seguendo il README, se manca. Non serve il comando `sqlite3`.

Le sezioni 1–4 non richiedono credenziali: usano dati sintetici, SQLite e destinatari
locali. Non avviare il monitoraggio reale durante queste prove. Le sezioni 5–7 sono
prove aggiuntive da eseguire solo quando i servizi necessari sono disponibili.
Un risultato simulato non certifica RPC, explorer, Telegram o AI reali.

## 1. Interfaccia e demo senza credenziali

```bash
.venv/bin/python main.py --help
env -i PATH="$PATH" PYTHONUTF8=1 .venv/bin/python main.py --demo
echo "Codice demo: $?"
```

Verificare che l'help presenti `--demo`, `--doctor`, `--once`, `--test` e `--preview`.
La demo deve terminare con codice **0** e stampare:

```text
SentryCage — DEMO — dati sintetici / synthetic data
SIGNAL: accumulation | 500.00 ETH | confidence 0.90
SQLITE: 1 signal saved and read back; isolated temporary database removed on exit
```

Sono presenti anche evento grezzo e messaggio HTML. Confrontarli con
[la cattura della demo](assets/demo-output.txt): il timestamp di generazione del
segnale può cambiare; hash e transazione sono sintetici. Il markup HTML letterale
in console è previsto. La demo ignora `.env`, usa un database temporaneo ed elimina
quel database al termine; non deve produrre invii Telegram.

## 2. Configurazione errata, prima di inizializzare i servizi

Questa prova chiama direttamente il validatore, senza la CLI live che carica `.env`.

```bash
env -i PATH="$PATH" PYTHONUTF8=1 .venv/bin/python - <<'PY'
from src.config.settings import Settings, ConfigurationError
try:
    Settings.from_env({})
except ConfigurationError as error:
    print("Errore previsto:", error)
else:
    raise AssertionError("La configurazione vuota è stata accettata")
PY
```

Atteso: errore che nomina `ETHEREUM_RPC_URL`, senza valori sensibili. Il comando
termina con 0 perché intercetta l'errore previsto; nella CLI live un errore di
configurazione restituisce **2**. Non vengono creati client o database.

## 3. Recupero, outbox, registro wallet e provenienza

Eseguire i tre blocchi seguenti **nella stessa shell**, nell'ordine indicato.
La directory temporanea viene stampata e conservata per l'ispezione; per ripetere
la prova ripartire dal primo blocco con una directory nuova.

### 3a. Lasciare un evento nel diario, poi terminare il processo

```bash
export SENTRY_MANUAL_DIR="$(mktemp -d -t sentrycage-manual-XXXXXXXX)"
echo "$SENTRY_MANUAL_DIR"
env -i PATH="$PATH" PYTHONUTF8=1 SENTRY_MANUAL_DIR="$SENTRY_MANUAL_DIR" .venv/bin/python - <<'PY'
import os
from pathlib import Path
from src.config.wallet_registry import WalletInfo, WalletCategory, Chain
from src.orchestration.pipeline import SQLiteStorage

store = SQLiteStorage(str(Path(os.environ['SENTRY_MANUAL_DIR']) / 'manual.db'))
address = '0x' + '77' * 20
store.wallet_registry.put(WalletInfo(
    address=address, category=WalletCategory.WHALE, name='Manual wallet',
    chains=[Chain.ETHEREUM], threshold_override=100.0,
))
raw = dict(hash='0x' + 'ab' * 32, **{'from': '0x' + '11' * 20},
           to=address, value=str(500 * 10**18), input='0x',
           blockNumber='15', timeStamp='1791547200', isError='0')
store.stage(raw, 'ethereum', address)
assert len(store.pending_work()) == 1 and store.read() == []
store.save_checkpoint('ethereum', 14)
print('STAGED: 1 pending event; 0 signals; checkpoint 14')
PY
```

Atteso: `STAGED: 1 pending event; 0 signals; checkpoint 14`.
Il processo è terminato senza analizzare l'evento. Questo verifica il confine tra
stage e ripresa; gli arresti bruschi e il rollback atomico sono anche coperti dalla
suite indicata in sezione 4.

### 3b. Riprendere e simulare un errore del destinatario

```bash
env -i PATH="$PATH" PYTHONUTF8=1 SENTRY_MANUAL_DIR="$SENTRY_MANUAL_DIR" .venv/bin/python - <<'PY'
import asyncio
import os
from pathlib import Path
from src.orchestration.pipeline import SQLiteStorage, TransactionPipeline, DeliveryError
from src.database.sqlite import get_connection

class FailingRecipient:
    delivery_key = 'manual-local'
    async def send_signal(self, signal):
        raise TimeoutError('Simulated local delivery failure')

async def run():
    store = SQLiteStorage(str(Path(os.environ['SENTRY_MANUAL_DIR']) / 'manual.db'))
    assert store.load_checkpoint('ethereum') == 14
    pipeline = TransactionPipeline(store, FailingRecipient(), clock=lambda: 0)
    try:
        await pipeline.recover(['ethereum'])
    except DeliveryError:
        print('EXPECTED: delivery pending')
    else:
        raise AssertionError('La consegna simulata doveva fallire')
    assert not store.pending_work()
    rows = store.read()
    assert len(rows) == 1 and rows[0]['signal_type'] == 'accumulation'
    assert rows[0]['metadata']['provenance']['origin'] == 'rules'
    assert rows[0]['metadata']['provenance']['provider'] is None
    pending = store.pending_notifications('manual-local')
    assert len(pending) == 1 and pending[0]['attempts'] == 1
    assert pending[0]['next_attempt'] == 5
    assert pending[0]['last_error'] == 'TimeoutError'
    with get_connection(store.db_path) as con:
        for query in (
            'SELECT tx_hash, chain, state FROM transaction_work',
            'SELECT signal_type, source, value_eth FROM signals',
            'SELECT state, attempts, last_error, next_attempt FROM notification_outbox',
        ):
            print([dict(row) for row in con.execute(query)])
    print('PERSISTED: 1 signal; completed work; pending outbox; rules provenance')

asyncio.run(run())
PY
```

Atteso: lavoro `completed`, segnale `accumulation` da 500 ETH, source
`whale_tracker`, outbox `pending`, un tentativo, `TimeoutError`, prossimo tentativo
5. L'orologio è simulato per evitare attese. Il checkpoint 14 persiste tra processi;
questa prova non esegue una scansione e quindi non ne dimostra l'avanzamento.

### 3c. Riavviare, ritentare, verificare deduplica e rimozione wallet

```bash
env -i PATH="$PATH" PYTHONUTF8=1 SENTRY_MANUAL_DIR="$SENTRY_MANUAL_DIR" .venv/bin/python - <<'PY'
import asyncio
import json
import os
from pathlib import Path
from src.config.wallet_registry import Chain
from src.orchestration.pipeline import SQLiteStorage, TransactionPipeline
from src.database.sqlite import get_connection

class LocalRecipient:
    delivery_key = 'manual-local'
    def __init__(self):
        self.messages = []
    async def send_signal(self, signal):
        self.messages.append(signal.get_message())
        print('LOCAL DELIVERY:', signal.to_dict()['transaction_hash'])
        return True

async def run():
    path = str(Path(os.environ['SENTRY_MANUAL_DIR']) / 'manual.db')
    store = SQLiteStorage(path)
    pending = store.pending_notifications('manual-local')[0]
    target = LocalRecipient()
    pipeline = TransactionPipeline(store, target, clock=lambda: 10)
    assert (await pipeline.recover(['ethereum'])).events == 0
    await pipeline.flush_notifications()
    assert target.messages == [pending['message']]
    with get_connection(path) as con:
        work = con.execute('SELECT * FROM transaction_work').fetchone()
        outbox = con.execute('SELECT state, attempts FROM notification_outbox').fetchone()
        print('OUTBOX:', dict(outbox))
        assert outbox['state'] == 'sent' and outbox['attempts'] == 2
        raw = json.loads(work['raw_json'])
    assert (await pipeline.process([raw], 'ethereum')).events == 0
    assert len(store.read()) == 1 and len(target.messages) == 1
    address = '0x' + '77' * 20
    assert store.wallet_registry.lookup(address, Chain.ETHEREUM).name == 'Manual wallet'
    checkpoint_scope = store.wallet_registry.scope('ethereum')
    store.wallet_registry.remove(address, Chain.ETHEREUM)
    restarted = SQLiteStorage(path)
    assert restarted.wallet_registry.lookup(address, Chain.ETHEREUM) is None
    assert restarted.load_checkpoint('ethereum', checkpoint_scope) == 14
    print('PASS: immutable retry; no duplicate signal; wallet removal survives restart')

asyncio.run(run())
PY
```

Atteso: una sola consegna **locale**, outbox `sent` con due tentativi, un solo
segnale dopo replay, wallet rimosso anche dopo riavvio e checkpoint ancora 14.
Il checkpoint verificato appartiene allo scope precedente alla rimozione del wallet.
Il retry usa il messaggio salvato, senza rigenerarlo. Nessun messaggio Telegram è
stato inviato. Il database rimane in `$SENTRY_MANUAL_DIR/manual.db`.

## 4. Paginazione e regressioni offline

Questa prova esercita manualmente l'iteratore del prodotto con un explorer locale:
pagina piena seguita da pagina vuota, poi pagina ripetuta senza progresso.

```bash
env -i PATH="$PATH" PYTHONUTF8=1 .venv/bin/python - <<'PY'
from src.config.wallet_registry import Chain
from src.fetching.explorer_api import wallet_transaction_pages, IncompleteExplorerScan

row = dict(hash='0x' + 'ab' * 32, **{'from': '0x' + '11' * 20},
           to='0x' + '77' * 20, value='1', blockNumber='15', input='0x', isError='0')
class LocalExplorer:
    def __init__(self, repeat=False):
        self.repeat = repeat
        self.calls = []
    def get_wallet_transactions(self, **kwargs):
        self.calls.append(kwargs['page'])
        assert kwargs['sort'] == 'asc' and kwargs['offset'] == 1
        return [row] if kwargs['page'] == 1 or self.repeat else []

def scan(client):
    return list(wallet_transaction_pages(client, row['to'], Chain.ETHEREUM,
                                        10, 20, page_size=1, max_pages=3))
client = LocalExplorer()
assert scan(client) == [[row]] and client.calls == [1, 2]
print('PASS: full page followed by empty page; calls [1, 2]')
try:
    scan(LocalExplorer(repeat=True))
except IncompleteExplorerScan:
    print('PASS: repeated full page rejected')
else:
    raise AssertionError('Pagina ripetuta accettata')
PY
```

Per controllare rollback, arresti bruschi, lease, checkpoint dopo scansioni fallite,
migrazione wallet, riferimenti AI invalidi e cause fallback, eseguire anche le
regressioni. Questi sono **test automatici offline**, non prove manuali dei servizi:

```bash
.venv/bin/python -m pytest tests/integration/test_recovery.py tests/integration/test_notification_outbox.py tests/integration/test_explorer_pagination.py tests/integration/test_wallet_registry.py tests/integration/test_signal_provenance.py -q
```

Atteso: nessun fallimento. Per suite completa e coverage usare i comandi del README.

## 5. RPC ed explorer reali, senza notifiche

Richiede credenziali RPC/explorer. Preparare personalmente `.env` secondo il README,
scegliendo un **database di prova separato** nel valore `DB_PATH`. Mantenere
`ENABLE_REASONING=false`, `NOTIFICATIONS_ENABLED=false` e inizialmente solo Ethereum.
Il percorso deve essere un file, non `:memory:`. Non stampare né condividere `.env`.

```bash
.venv/bin/python main.py --doctor
echo "Codice doctor: $?"
.venv/bin/python main.py --once
echo "Codice once: $?"
.venv/bin/python main.py --once
echo "Codice secondo once: $?"
```

Atteso: doctor **0** con verifiche abilitate riuscite. Once **0** senza nuovi eventi
o **10** con nuovi eventi, anche sotto soglia; **2** indica configurazione errata,
**3** indica errore operativo. Un intervallo senza eventi è valido e non dimostra
la generazione di un alert. Notifiche disabilitate: nuovi segnali hanno outbox
`disabled`, vecchi invii pendenti restano conservati.

Per ispezionare il database, impostare il percorso scelto (senza leggere `.env`):

```bash
export SENTRY_MANUAL_DB='/percorso/assoluto/del/database-di-prova.db'
.venv/bin/python - <<'PY'
import json
import os
import sqlite3
from pathlib import Path

path = Path(os.environ['SENTRY_MANUAL_DB']).resolve()
with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as con:
    con.row_factory = sqlite3.Row
    for table in ('scan_checkpoints', 'scan_checkpoint_heads'):
        print(table, [dict(row) for row in con.execute('SELECT * FROM ' + table)])
    print('outbox', [dict(row) for row in con.execute(
        'SELECT state, COUNT(*) AS total FROM notification_outbox GROUP BY state')])
    for row in con.execute('SELECT transaction_hash, chain, source, metadata FROM signals ORDER BY created_at DESC LIMIT 5'):
        print(row['transaction_hash'], row['chain'], row['source'],
              json.loads(row['metadata'] or '{}').get('provenance'))
PY
```

Annotare checkpoint prima/dopo: una scansione localmente completa può avanzarlo,
anche se non genera segnali. Un secondo once può trovare altri eventi perché la
chain avanza; la deduplica riguarda gli stessi hash nella stessa chain.
Per wallet personalizzati usare l'esempio del README sul medesimo `DB_PATH`.
Un cambio registro crea uno scope diverso e conserva intervalli incompleti.

Per una prova di guasto controllato, interrompere temporaneamente l'accesso al solo
servizio RPC/explorer di prova, ripetere once, poi ripristinare l'accesso. Atteso:
codice 3 e nessun avanzamento del checkpoint della chain che non ha completato la
scansione; altre chain già completate possono avanzare. Il programma non recupera
automaticamente riorganizzazioni e non rileva omissioni silenziose dell'explorer.

## 6. Telegram e AI reali, facoltativi

**Telegram:** usare solo un bot e una chat autorizzati per questi messaggi.
Configurare localmente `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` e
`NOTIFICATIONS_ENABLED=true`. Doctor verifica accesso, ma non prova l'invio.
Per una prova intenzionale di consegna sintetica:

```bash
.venv/bin/python main.py --test
echo "Codice test Telegram: $?"
```

Atteso: **0** e un messaggio marcato `SYNTHETIC TEST` nella chat scelta.
`TEST_CHAT_ID`, se configurato, prevale su `TELEGRAM_CHAT_ID`. `--test` non verifica
acquisizione o outbox; once/live usano la pipeline completa e possono inviare
messaggi reali. Un cambio destinazione lascia pendenti i vecchi messaggi, senza
redirigerli. Invio riuscito con conferma persa può produrre duplicati al retry.

**AI:** abilitare `ENABLE_REASONING=true` e configurare provider/modello seguendo
[la guida AI](../AI_REASONING_GUIDE.md) e [la configurazione](configuration.md).
Eseguire doctor, poi once sul database di prova e ispezionare i nuovi segnali con
il comando della sezione 5. Doctor non esegue inferenza e non certifica AI cloud
o provider fallback; cloud AI può quindi produrre codice 3 anche se configurata.

Atteso, se un evento significativo produce output AI valido: source `ai_reasoning`,
provenance origin `ai`, provider/modello effettivi e hash/chain corretti. Se si
attiva il fallback: source `whale_tracker`, origin `rules_fallback`, causa in
`fallback_reason` e messaggio `Rule-based fallback`. Senza nuovi segnali la prova
è **non osservata**, non superata. Verificare un guasto del provider solo su un
endpoint di prova; un provider alternativo disponibile può riuscire al suo posto.
Provider cloud configurati possono ricevere dati e addebitare richieste.

## 7. Docker e registrazione dell'esito

Per build, demo con `--network none`, persistenza volume e profilo AI seguire
[DOCKER_SETUP.md](../DOCKER_SETUP.md). La prova container richiede Docker/Compose
funzionanti; non è stata eseguita nell'ambiente WSL corrente.

Registrare per ogni prova commit, data, Python/sistema, comando, codice di uscita,
risultato osservato e ambiente (offline simulato oppure servizio reale).
Usare **PASS**, **FAIL**, **NON OSSERVATO** o **BLOCCATO**, spiegando gli ultimi tre.
Non allegare token, URL con credenziali o configurazioni complete.

| Prova | Risultato atteso | Esito / evidenza |
| --- | --- | --- |
| Help e demo | Modalità presenti, codice 0, 1 segnale sintetico | |
| Validazione | Configurazione vuota respinta prima dei servizi | |
| Recupero | Evento pending recuperato da un nuovo processo | |
| Outbox | Errore persistito, retry identico, sent al secondo tentativo | |
| Wallet | Registro condiviso, rimozione persiste al riavvio | |
| Provenienza offline | rules, provider assente | |
| Paginazione offline | Pagina terminale richiesta, ripetizione respinta | |
| Servizi reali | Doctor riuscito, once e checkpoint coerenti | |
| Telegram reale | Messaggio nella destinazione autorizzata | |
| AI reale | Provenienza coerente su un nuovo segnale osservato | |
| Docker | Build, demo isolata e persistenza riuscite | |

Validazione di questa guida, 9 ottobre 2026: blocchi offline 1–4 eseguiti con
Python 3.12.3 su Linux/WSL; le prove reali 5–7 rimangono da eseguire. Il database
manuale temporaneo può essere eliminato dopo la raccolta delle evidenze.
