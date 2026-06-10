# 🥤 Vending Sales Bot

> Report di vendita automatici per distributori automatici, direttamente su Telegram.

Un bot Python che effettua il login sul portale **MatiPay**, raccoglie le transazioni dei distributori e invia su Telegram report chiari e grafici: incassi giornalieri, andamento settimanale e mensile, allarmi, e — novità — **classifica delle spirali più vendute** e **distribuzione oraria delle vendite**.

<p>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.8+-3776AB?logo=python&logoColor=white">
  <img alt="Telegram" src="https://img.shields.io/badge/Telegram-Bot-26A5E4?logo=telegram&logoColor=white">
  <img alt="Status" src="https://img.shields.io/badge/status-active-success">
</p>

---

## ✨ Funzionalità

| | |
|---|---|
| 📊 **Report incassi** | Totale vendite, incasso ed errori per macchina, su periodo a scelta |
| 📈 **Grafico 7 giorni** | Incasso cash giornaliero per distributore |
| 🗓️ **Andamento mensile** | Trend a valore e volume (a fine mese) |
| 🔁 **Settimana su settimana** | Confronto con variazione percentuale |
| 🏷️ **Top spirali** | Classifica per spirale (qta + incasso), nel report settimanale |
| 🕐 **Fascia oraria** | Istogramma delle vendite per ora, con evidenza del picco |
| 🚨 **Allarmi** | Guasti, sold-out, credito e altre notifiche del portale |

> Le statistiche per spirale e per ora sono calcolate dalle transazioni **già scaricate**: nessuna richiesta aggiuntiva al portale.

## 🤖 Comandi Telegram

| Comando | Periodo |
|---|---|
| `/oggi` | Oggi |
| `/ieri` | Ieri |
| `/ultimi7gg` | Ultimi 7 giorni *(+ top spirali e fascia oraria)* |
| `/ultimi30gg` | Ultimi 30 giorni |
| `/questomese` | Mese corrente |
| `/mescorso` | Mese precedente |
| `/allarmi` | Allarmi e notifiche attive |

## 🚀 Avvio rapido

```bash
# 1. Dipendenze
pip install -r requirements.txt

# 2. Configurazione
cp config.example.json config.json   # poi inserisci credenziali e token

# 3. Report automatico (cron / scheduler)
python matipay.py
```

Esempio anteprima delle statistiche per spirale e oraria, senza inviare nulla:

```bash
python prova_prodotti.py last7     # today | yesterday | last7 | last30 | thismonth | lastmonth
```

## ⚙️ Configurazione

`config.json` (vedi [`config.example.json`](config.example.json)):

```jsonc
{
  "matipay": {
    "hostname": "https://vendingapp.matipay.com/smart-vending-webapp/login",
    "user": "…",
    "pass": "…"
  },
  "matipay_bot_config": {
    "token_id":   "<telegram-bot-token>",
    "channel_id": "<telegram-chat-id>"
  },
  "matipay_machine_names": {
    "000001": "Snack",
    "000002": "Drink"
  }
}
```

## 📦 Struttura

| File | Descrizione |
|---|---|
| [`matipay.py`](matipay.py) | Bot principale: API MatiPay, report, grafici, invio Telegram |
| [`prova_prodotti.py`](prova_prodotti.py) | Anteprima statistiche per spirale e fascia oraria |
| [`download_matipay.py`](download_matipay.py) | Dump di pagine e JS del portale (analisi/ispezione) |
| [`cloud8816.py`](cloud8816.py) | Integrazione alternativa via Selenium (portale mcf88) |

---

<sub>⚠️ `config.json` contiene credenziali e va tenuto fuori dal versioning.</sub>